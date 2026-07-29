# --- Turning text into numbers, so we can compare MEANING ---
#
# WHAT THIS FILE DOES
#   Sends text away and gets back a long list of numbers (1536 of them) that
#   represents what the text means. Two texts about the same thing come back as
#   similar lists even if they share no words at all. That is the only way to
#   tell that these two are one story:
#
#       "BREAKING: SEC approves spot ETH ETF"
#       "Regulator green-lights ether exchange-traded funds"
#
#   Comparing the words gets you nowhere. Comparing the meaning works.
#
# WHERE IT FITS
#   The bottom layer of duplicate check number 4 (see nodes/dedup.py). This file
#   knows nothing about news — it only turns text into numbers and measures the
#   distance between them.
#
# THE MEASUREMENT, IN PLAIN TERMS
#   Picture each list of numbers as an arrow pointing somewhere in space. Two
#   texts that mean the same thing produce arrows pointing in nearly the same
#   direction. We measure the angle between them and turn it into a score:
#   1.0 means identical direction, 0.0 means completely unrelated. Above about
#   0.86 we treat them as the same story.
#
# WHY OPENROUTER AND NOT OPENAI DIRECTLY
#   OpenRouter does serve this, despite an out-of-date comment in the
#   news-trader project on this machine claiming otherwise. news-saas has been
#   using it in production for weeks. That means one API key covers everything
#   in this project.
#
#   The cost is negligible: roughly $0.02 per million words, so about $0.000002
#   an item. Even a very busy day is a fraction of a cent.
#
# THE ONE RULE IN THIS FILE
#   Nothing here may ever raise. Every failure returns None, and callers read
#   that as "the meaning check isn't available right now" and let the item
#   through. A duplicate post is a small embarrassment; a silent channel because
#   an API had a bad afternoon is worse.
#
# DEPENDENCIES
#   httpx, numpy. Uses OPENROUTER_API_KEY and EMBEDDING_MODEL from config.

from __future__ import annotations

import httpx
import numpy as np

import config
from utils import logger as log_setup

log = log_setup.get("embeddings")

_TIMEOUT = httpx.Timeout(connect=10.0, read=30.0, write=15.0, pool=5.0)

# Numbers are stored as 32-bit decimals. Half the size of the default 64-bit
# with no meaningful loss of accuracy for this purpose — and these get stored on
# every single item, so the saving adds up.
_DTYPE = np.float32

# Longest text we send. The model's real limit is much higher, but the meaning
# of a news story is in its opening; sending more costs more and adds nothing.
_MAX_CHARS = 2000

# How many texts we send in one request.
_BATCH_SIZE = 64


# --- Send text away and get numbers back ---
async def embed(texts: list[str]) -> list[list[float]] | None:
    """Turn a list of texts into a list of meaning-vectors.

    Returns EXACTLY one vector per input, in the same order — or None if
    anything went wrong. Never a partial list: a short list would silently pair
    vectors with the wrong text, which would poison every future comparison in a
    way that is almost impossible to notice. All or nothing is the safe rule.
    """
    if not texts:
        return []

    if not config.OPENROUTER_API_KEY:
        log.warning("OPENROUTER_API_KEY is not set — the meaning check is unavailable")
        return None

    # Trim and tidy each input.
    prepared = [" ".join((t or "").split())[:_MAX_CHARS] or " " for t in texts]

    vectors: list[list[float]] = []
    for start in range(0, len(prepared), _BATCH_SIZE):
        batch = prepared[start:start + _BATCH_SIZE]
        got = await _embed_batch(batch)
        if got is None:
            return None    # all or nothing, see the docstring
        vectors.extend(got)

    return vectors


async def _embed_batch(batch: list[str]) -> list[list[float]] | None:
    """Send one batch. Returns the vectors, or None on any failure."""
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            response = await client.post(
                f"{config.OPENROUTER_BASE_URL}/embeddings",
                headers={
                    "Authorization": f"Bearer {config.OPENROUTER_API_KEY}",
                    "Content-Type": "application/json",
                    "X-Title": "news-channel",
                },
                json={"model": config.EMBEDDING_MODEL, "input": batch},
            )

        if response.status_code != 200:
            log.warning("Embedding request failed (HTTP %s): %s",
                        response.status_code, response.text[:200])
            return None

        rows = response.json().get("data", [])

        # Guard against getting a different number of answers than questions —
        # if that ever happened, every vector after the gap would be attached to
        # the wrong text.
        if len(rows) != len(batch):
            log.warning("Asked for %d vectors, got %d — discarding the batch",
                        len(batch), len(rows))
            return None

        # Sort by the index the service echoes back rather than trusting the
        # order they arrived in. Same reasoning as above.
        ordered = sorted(rows, key=lambda row: row.get("index", 0))
        return [row["embedding"] for row in ordered]

    except Exception as error:  # noqa: BLE001 - this must never break the pipeline
        log.warning("Embedding request failed: %s", error)
        return None


async def embed_one(text: str) -> list[float] | None:
    """Turn a single piece of text into a meaning-vector, or None on failure."""
    result = await embed([text])
    return result[0] if result else None


# --- Storing vectors in the database ---
def to_blob(vector: list[float]) -> bytes:
    """Pack a vector into raw bytes so SQLite can store it in one cell."""
    return np.asarray(vector, dtype=_DTYPE).tobytes()


def from_blob(blob: bytes) -> np.ndarray:
    """Unpack a stored vector back into numbers."""
    return np.frombuffer(blob, dtype=_DTYPE)


# --- Find the closest matches ---
def most_similar(
    query: list[float],
    candidates: list[tuple[int, bytes]],
    top_k: int = 1,
    threshold: float = 0.86,
) -> list[tuple[int, float]]:
    """Find which stored items mean most nearly the same as `query`.

    Args:
        query:      the meaning-vector of the new item.
        candidates: [(item id, stored vector), ...] to compare against.
        top_k:      how many matches to return, best first.
        threshold:  ignore anything below this score (1.0 = identical meaning).

    Returns:
        [(item id, score), ...], best first. Empty if nothing is close enough.

    This compares against every stored vector one by one. That sounds slow but
    it is simple arithmetic on a few hundred short lists — well under a
    millisecond. A specialised database for this would be worth it at hundreds
    of thousands of items, which is years away.
    """
    if not candidates:
        return []

    try:
        query_vector = np.asarray(query, dtype=_DTYPE)
        query_length = np.linalg.norm(query_vector)
        if query_length == 0:
            return []
        query_vector = query_vector / query_length      # make it length 1

        # Guard against a mismatch in vector size. If you ever change
        # EMBEDDING_MODEL, older stored vectors will be a different length and
        # comparing them would produce confident nonsense. Skip those instead.
        expected = query_vector.shape[0]
        usable = [(item_id, blob) for item_id, blob in candidates
                  if len(blob) == expected * np.dtype(_DTYPE).itemsize]
        if len(usable) < len(candidates):
            log.info("Ignoring %d stored vector(s) of a different size "
                     "(the embedding model was probably changed)",
                     len(candidates) - len(usable))
        if not usable:
            return []

        matrix = np.stack([from_blob(blob) for _, blob in usable])
        lengths = np.linalg.norm(matrix, axis=1)
        lengths[lengths == 0] = 1e-9                    # avoid dividing by zero

        # The dot product of two length-1 arrows IS the similarity score.
        scores = (matrix @ query_vector) / lengths

        results: list[tuple[int, float]] = []
        for index in np.argsort(-scores)[:top_k]:       # highest score first
            score = float(scores[index])
            if score < threshold:
                break                                   # sorted, so we're done
            results.append((usable[index][0], score))
        return results

    except Exception as error:  # noqa: BLE001
        log.warning("Similarity comparison failed: %s", error)
        return []
