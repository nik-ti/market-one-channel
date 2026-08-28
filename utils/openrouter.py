"""One place that sends text to a model and gets an answer back.

Every AI step goes through here, so timeouts, retries and error handling are
written once. OpenRouter is a middleman: one key pays for every provider, and
switching models is a one-line change in config.py.

THE TRUNCATION TRAP is the most important thing in this file. A model that hits
its length limit mid-sentence does NOT report an error — it returns the
half-finished text as though it were complete. Without the finish_reason check
the channel would occasionally publish a post that stops in the middle of a
...exactly like that.
"""

from __future__ import annotations

import json

import httpx

import config
from utils import logger as log_setup

log = log_setup.get("llm")

# Generous read timeout (a model writing paragraphs takes a while), short
# connect timeout (failing to connect at all should be noticed immediately).
_TIMEOUT = httpx.Timeout(connect=10.0, read=60.0, write=15.0, pool=5.0)

# The reasons a model gives for stopping that mean "I ran out of room", as
# opposed to "I finished". Different providers word it differently.
_TRUNCATED = {"length", "max_tokens", "max_output_tokens"}


class LLMError(RuntimeError):
    """A model call failed. Callers usually leave the item for the next cycle."""


async def _post(payload: dict) -> dict:
    """Send a request to OpenRouter and return the raw reply. Raises LLMError on failure."""
    if not config.OPENROUTER_API_KEY:
        raise LLMError("OPENROUTER_API_KEY is not set in .env")

    headers = {
        "Authorization": f"Bearer {config.OPENROUTER_API_KEY}",
        "Content-Type": "application/json",
        # Separates this project's spend on the OpenRouter dashboard.
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


def _extract(data: dict) -> tuple[str, str]:
    """Return (the text the model wrote, why it stopped)."""
    try:
        choice = data["choices"][0]
    except (KeyError, IndexError) as error:
        raise LLMError(f"unexpected reply shape from OpenRouter: {str(data)[:300]}") from error

    content = (choice.get("message") or {}).get("content") or ""
    reason = str(choice.get("finish_reason") or "").lower()
    return content, reason


async def chat_text(
    *, model: str, system: str, user: str,
    temperature: float = 0.3, max_tokens: int = 900,
) -> str:
    """Ask a model for prose. A cut-off answer is retried once, then gives up."""
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


async def chat_json(
    *, model: str, system: str, user: str, schema: dict | None = None,
    schema_name: str = "output", temperature: float = 0.0, max_tokens: int = 500,
) -> dict:
    """Ask a model for a structured answer.

    A schema turns on the provider's "strict" mode, which is what lets the
    editor offer a fixed list of rejection reasons and be sure the model cannot
    invent one. Models that refuse strict mode fall back to plain JSON.
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
        # Not every model understands strict mode.
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
        # Unreadable nearly always means cut off partway, and not every provider
        # admits that in finish_reason. One more go with double the room.
        log.warning("Model %s returned unreadable JSON — retrying with more room", model)
        payload["max_tokens"] = max_tokens * 2
        data = await _post(payload)
        content, reason = _extract(data)
        if reason in _TRUNCATED:
            raise LLMError(f"model {model} ran out of room even with double the budget")
        return parse_json(content)


def parse_json(raw: str) -> dict:
    """Read JSON out of a model's reply, coping with code fences and preambles."""
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
