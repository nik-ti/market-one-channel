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

import asyncio
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

# HTTP replies that mean "try again shortly" rather than "this will never work".
# 429 is the one that actually costs us news: OpenRouter routes to a provider
# that is rate-limited upstream, and without a retry the item burns all three of
# its attempts inside the same few-minute window and is marked failed. Four real
# stories were lost that way in two weeks, including a BLS payrolls print.
#
# 400/401/404 are deliberately absent — a bad model id or a wrong key is not
# going to fix itself, and retrying only wastes the caller's timeout.
_RETRYABLE = {408, 409, 429, 500, 502, 503, 504}

# Short on purpose. The judge wraps this in a 40-second timeout, so the whole
# retry budget has to fit inside that with room for the model to think.
_BACKOFF_SECONDS = (1.0, 3.0)


class LLMError(RuntimeError):
    """A model call failed. Callers usually leave the item for the next cycle."""


async def _post(payload: dict) -> dict:
    """Send one request to OpenRouter, retrying the failures worth retrying.

    Raises LLMError once the retries are used up, or immediately for a reply
    that retrying cannot fix. See _RETRYABLE.
    """
    if not config.OPENROUTER_API_KEY:
        raise LLMError("OPENROUTER_API_KEY is not set in .env")

    headers = {
        "Authorization": f"Bearer {config.OPENROUTER_API_KEY}",
        "Content-Type": "application/json",
        # Separates this project's spend on the OpenRouter dashboard.
        "X-Title": "news-channel",
    }

    last = ""
    for attempt in range(len(_BACKOFF_SECONDS) + 1):
        try:
            async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
                response = await client.post(
                    f"{config.OPENROUTER_BASE_URL}/chat/completions",
                    headers=headers, json=payload,
                )
        except Exception as error:  # noqa: BLE001
            last = f"could not reach OpenRouter: {error}"
            if attempt < len(_BACKOFF_SECONDS):
                await asyncio.sleep(_BACKOFF_SECONDS[attempt])
                continue
            raise LLMError(last) from error

        if response.status_code == 200:
            try:
                return response.json()
            except Exception as error:  # noqa: BLE001
                raise LLMError(f"OpenRouter sent back something that isn't JSON: {error}") from error

        last = f"OpenRouter returned HTTP {response.status_code}: {response.text[:300]}"
        if response.status_code not in _RETRYABLE or attempt >= len(_BACKOFF_SECONDS):
            raise LLMError(last)

        # Honour Retry-After when the server names a short wait; ignore a long
        # one, because waiting a minute here would blow the caller's timeout
        # and the fallback model is the better answer at that point.
        wait = _BACKOFF_SECONDS[attempt]
        try:
            named = float(response.headers.get("retry-after", ""))
            if 0 < named <= 5:
                wait = named
        except ValueError:
            pass
        log.warning("OpenRouter returned %s — retrying in %.0fs (attempt %d of %d)",
                    response.status_code, wait, attempt + 1, len(_BACKOFF_SECONDS) + 1)
        await asyncio.sleep(wait)

    raise LLMError(last)


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
    fallbacks: list[str] | None = None,
) -> str:
    """Ask a model for prose, trying `fallbacks` if it fails.

    A cut-off answer is retried once against the same model, then gives up.
    """
    chain = [model, *(fallbacks or [])]
    last: Exception | None = None
    for position, candidate in enumerate(chain):
        try:
            return await _chat_text_once(model=candidate, system=system, user=user,
                                         temperature=temperature, max_tokens=max_tokens)
        except LLMError as error:
            last = error
            if position + 1 < len(chain):
                log.warning("Model %s failed (%s) — falling back to %s",
                            candidate, str(error)[:120], chain[position + 1])
    raise last if last else LLMError("no model was tried")


async def _chat_text_once(
    *, model: str, system: str, user: str,
    temperature: float = 0.3, max_tokens: int = 900,
) -> str:
    """One model's attempt at prose. Raises LLMError if it fails."""
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
    fallbacks: list[str] | None = None,
) -> dict:
    """Ask a model for a structured answer, trying `fallbacks` if it fails.

    A schema turns on the provider's "strict" mode, which is what lets the
    editor offer a fixed list of rejection reasons and be sure the model cannot
    invent one. Models that refuse strict mode fall back to plain JSON.

    THE FALLBACK IS OURS, NOT OPENROUTER'S. Its `models` array only reroutes
    within one request and does nothing for a model id it rejects outright, so
    the second model is tried here where the behaviour is visible and testable.
    Every model in the list must be able to do the same job — for the editor
    that means a DIFFERENT lab from the writer, and one that accepts the strict
    schema. See EDITOR_FALLBACK_MODEL in config.py.
    """
    chain = [model, *(fallbacks or [])]
    last: Exception | None = None
    for position, candidate in enumerate(chain):
        try:
            return await _chat_json_once(
                model=candidate, system=system, user=user, schema=schema,
                schema_name=schema_name, temperature=temperature,
                max_tokens=max_tokens,
            )
        except LLMError as error:
            last = error
            if position + 1 < len(chain):
                log.warning("Model %s failed (%s) — falling back to %s",
                            candidate, str(error)[:120], chain[position + 1])
    raise last if last else LLMError("no model was tried")


async def _chat_json_once(
    *, model: str, system: str, user: str, schema: dict | None = None,
    schema_name: str = "output", temperature: float = 0.0, max_tokens: int = 500,
) -> dict:
    """One model's attempt at a structured answer. Raises LLMError if it fails."""
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
