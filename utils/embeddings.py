"""Turns text into numbers so we can compare MEANING rather than words.

Two texts about the same thing come back as similar lists of numbers even when
they share no words — which is the only way to tell that "BREAKING: SEC approves
spot ETH ETF" and "Regulator green-lights ether exchange-traded funds" are one
story. Each is an arrow in space; we measure the angle between them, where 1.0
is identical and 0.0 unrelated.

THE ONE RULE HERE: nothing may ever raise. Every failure returns None, and
callers read that as "the meaning check is unavailable" and let the item
through. Cost is about $0.000002 an item, so a busy day is a fraction of a cent.
"""

from __future__ import annotations

import httpx
import numpy as np

import config
from utils import logger as log_setup

log = log_setup.get("embeddings")

_TIMEOUT = httpx.Timeout(connect=10.0, read=30.0, write=15.0, pool=5.0)

# Half the size of the default 64-bit, with no meaningful loss here, and these
# are stored on every single item.
_DTYPE = np.float32

# The model allows much more, but a news story's meaning is in its opening.
_MAX_CHARS = 2000

# How many texts we send in one request.
_BATCH_SIZE = 64


async def embed(texts: list[str]) -> list[list[float]] | None:
    """Turn texts into meaning-vectors: exactly one per input, in order, or None.

    Never a partial list — that would silently pair vectors with the wrong text
    and poison every future comparison in a way nobody would notice.
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

        # A different count of answers than questions would attach every
        # vector after the gap to the wrong text.
        if len(rows) != len(batch):
            log.warning("Asked for %d vectors, got %d — discarding the batch",
                        len(batch), len(rows))
            return None

        # Trust the echoed index, not the arrival order. Same reasoning.
        ordered = sorted(rows, key=lambda row: row.get("index", 0))
        return [row["embedding"] for row in ordered]

    except Exception as error:  # noqa: BLE001 - this must never break the pipeline
        log.warning("Embedding request failed: %s", error)
        return None


async def embed_one(text: str) -> list[float] | None:
    """Turn a single piece of text into a meaning-vector, or None on failure."""
    result = await embed([text])
    return result[0] if result else None


def to_blob(vector: list[float]) -> bytes:
    """Pack a vector into raw bytes so SQLite can store it in one cell."""
    return np.asarray(vector, dtype=_DTYPE).tobytes()


def from_blob(blob: bytes) -> np.ndarray:
    """Unpack a stored vector back into numbers."""
    return np.frombuffer(blob, dtype=_DTYPE)


def most_similar(
    query: list[float],
    candidates: list[tuple[int, bytes]],
    top_k: int = 1,
    threshold: float = 0.86,
) -> list[tuple[int, float]]:
    """Return [(item id, score), ...] best first, for whatever clears `threshold`.

    Compares against every candidate one by one. That sounds slow but it is
    arithmetic on a few hundred short lists — well under a millisecond. A vector
    database would be worth it at hundreds of thousands of items, years away.
    """
    if not candidates:
        return []

    try:
        query_vector = np.asarray(query, dtype=_DTYPE)
        query_length = np.linalg.norm(query_vector)
        if query_length == 0:
            return []
        query_vector = query_vector / query_length      # make it length 1

        # After an EMBEDDING_MODEL change, older stored vectors are a different
        # length and comparing them produces confident nonsense. Skip those.
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
