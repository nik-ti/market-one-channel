# --- Talking to the AI models, through OpenRouter ---
#
# WHAT THIS FILE DOES
#   One place that sends text to an AI model and gets an answer back. Every AI
#   step in this project (sorting, writer, editor) goes through here, so timeouts,
#   retries and error handling are written once rather than three times.
#
# WHERE IT FITS
#   Underneath nodes/sorter.py, nodes/writer.py and nodes/editor.py.
#
# WHAT OPENROUTER IS
#   A middleman. Instead of holding separate accounts with Google, OpenAI and
#   Anthropic, you hold one with OpenRouter and it forwards your request to
#   whichever model you name. Switching models becomes a one-line change in
#   config.py, and one key pays for all of them.
#
# THE TRUNCATION TRAP
#   The most important thing in this file is the finish_reason check.
#   When a model hits its length limit mid-sentence, it does NOT report an
#   error — it returns the half-finished text as though it were complete. So
#   without this check, the channel would occasionally publish a post that stops
#   in the middle of a
#   ...exactly like that. We check for it and retry instead.
#
# DEPENDENCIES
#   httpx. Uses OPENROUTER_API_KEY and OPENROUTER_BASE_URL from config.

from __future__ import annotations

import json

import httpx

import config
from utils import logger as log_setup

log = log_setup.get("llm")

# Generous read timeout because a model writing several paragraphs genuinely
# takes a while; short connect timeout because failing to connect at all should
# be noticed immediately.
_TIMEOUT = httpx.Timeout(connect=10.0, read=60.0, write=15.0, pool=5.0)

# The reasons a model gives for stopping that mean "I ran out of room", as
# opposed to "I finished". Different providers word it differently.
_TRUNCATED = {"length", "max_tokens", "max_output_tokens"}


class LLMError(RuntimeError):
    """Raised when a model call fails in a way retrying won't fix.

    Callers catch this and decide what to do — usually leave the item alone and
    try again on the next cycle, rather than publishing something broken.
    """


# --- Send one request to a model ---
async def _post(payload: dict) -> dict:
    """Send a request to OpenRouter and return the raw reply. Raises LLMError on failure."""
    if not config.OPENROUTER_API_KEY:
        raise LLMError("OPENROUTER_API_KEY is not set in .env")

    headers = {
        "Authorization": f"Bearer {config.OPENROUTER_API_KEY}",
        "Content-Type": "application/json",
        # OpenRouter shows this on your usage dashboard, so spend from this
        # project is separable from the other projects sharing the account.
        "X-Title": "news-channel",
    }

    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            response = await client.post(
                f"{config.OPENROUTER_BASE_URL}/chat/completions",
                headers=headers, json=payload,
            )
    except Exception as error:  # noqa: BLE001
        raise LLMError(f"could not reach OpenRouter: {error}") from error

    if response.status_code != 200:
        raise LLMError(f"OpenRouter returned HTTP {response.status_code}: {response.text[:300]}")

    try:
        return response.json()
    except Exception as error:  # noqa: BLE001
        raise LLMError(f"OpenRouter sent back something that isn't JSON: {error}") from error


# --- Pull the text and the stop-reason out of a reply ---
def _extract(data: dict) -> tuple[str, str]:
    """Return (the text the model wrote, why it stopped)."""
    try:
        choice = data["choices"][0]
    except (KeyError, IndexError) as error:
        raise LLMError(f"unexpected reply shape from OpenRouter: {str(data)[:300]}") from error

    content = (choice.get("message") or {}).get("content") or ""
    reason = str(choice.get("finish_reason") or "").lower()
    return content, reason


# --- Ask a model for ordinary text ---
async def chat_text(
    *, model: str, system: str, user: str,
    temperature: float = 0.3, max_tokens: int = 900,
) -> str:
    """Ask a model a question and get its written answer back.

    A cut-off answer is retried once, then gives up with an error rather than
    handing back half a sentence. See the note about the truncation trap above.
    """
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "temperature": temperature,
        "max_tokens": max_tokens,
    }

    reason = ""
    for attempt in (1, 2):
        data = await _post(payload)
        content, reason = _extract(data)
        if reason not in _TRUNCATED:
            return content.strip()
        log.warning("Model %s ran out of room (attempt %d of 2) — retrying", model, attempt)

    raise LLMError(f"model {model} kept running out of room (stopped because: {reason})")


# --- Ask a model for a structured answer ---
async def chat_json(
    *, model: str, system: str, user: str, schema: dict | None = None,
    schema_name: str = "output", temperature: float = 0.0, max_tokens: int = 500,
) -> dict:
    """Ask a model a question and get a structured answer back as a dictionary.

    When a schema is supplied we use "strict" mode, which makes the provider
    enforce the shape rather than politely requesting it. That is what lets the
    editor node offer a fixed list of rejection reasons and be certain the model
    cannot invent a new one.

    Some models don't support strict mode; if the request is refused for that
    reason we fall back to plain "give me JSON" mode and parse it ourselves.
    """
    payload: dict = {
        "model": model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "temperature": temperature,
        "max_tokens": max_tokens,
    }

    if schema is not None:
        payload["response_format"] = {
            "type": "json_schema",
            "json_schema": {"name": schema_name, "strict": True, "schema": schema},
        }
    else:
        payload["response_format"] = {"type": "json_object"}

    try:
        data = await _post(payload)
    except LLMError as error:
        # Not every model understands strict mode. Try again without it rather
        # than failing outright.
        if schema is not None and ("response_format" in str(error) or "json_schema" in str(error)):
            log.warning("Model %s rejected strict mode — retrying without it", model)
            payload["response_format"] = {"type": "json_object"}
            data = await _post(payload)
        else:
            raise

    content, reason = _extract(data)
    if reason in _TRUNCATED:
        raise LLMError(f"model {model} ran out of room before finishing its answer")

    try:
        return parse_json(content)
    except LLMError:
        # An unreadable answer is nearly always one that got cut off partway —
        # and not every provider admits to that in finish_reason, so we cannot
        # rely on the check above alone. Give it one more go with double the
        # room. If it fails again, the caller's own error handling takes over.
        log.warning("Model %s returned unreadable JSON — retrying with more room", model)
        payload["max_tokens"] = max_tokens * 2
        data = await _post(payload)
        content, reason = _extract(data)
        if reason in _TRUNCATED:
            raise LLMError(f"model {model} ran out of room even with double the budget")
        return parse_json(content)


# --- Turn a model's reply into a dictionary ---
def parse_json(raw: str) -> dict:
    """Read JSON out of a model's reply, coping with the usual mess.

    Models often wrap JSON in a markdown code fence, or add a sentence like
    "Here's the result:" before it. We strip the fence, try to parse, and if
    that fails, look for the outermost curly brackets and parse what's between.
    """
    text = (raw or "").strip()

    # Remove ```json ... ``` fencing.
    if text.startswith("```"):
        lines = [line for line in text.splitlines() if not line.strip().startswith("```")]
        text = "\n".join(lines).strip()

    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # Fall back to whatever sits between the first { and the last }.
    start, end = text.find("{"), text.rfind("}")
    if start != -1 and end != -1 and end > start:
        try:
            return json.loads(text[start:end + 1])
        except json.JSONDecodeError:
            pass

    raise LLMError(f"could not read JSON from the model's reply: {raw[:300]}")
