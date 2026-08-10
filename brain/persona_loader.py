"""
PERSONA LOADER — loads the channel voice and recent posts for the writer.

PURPOSE: The writer needs two pieces of context beyond the source material:
         1. The persona file (brain/persona.md): the channel's voice, rules,
            and attitude.
         2. Recent published posts: concrete examples so the next post sounds
            like it came from the same hand.

The file is read once and cached. In production the service stays up for days,
so caching is fine; during development you can restart the service or touch
persona.md to reload.
"""

from __future__ import annotations

import config
from utils import db, logger as log_setup

log = log_setup.get("persona")

_persona_text: str | None = None


def _visible_text(post_html: str) -> str:
    """Strip HTML tags and the source link line for a readable voice sample."""
    import re
    # Drop the source link line if present.
    text = re.sub(r"\n?🔗 .*$", "", post_html, flags=re.MULTILINE)
    # Drop HTML tags.
    text = re.sub(r"<[^>]+>", "", text)
    # Collapse whitespace.
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def load_persona() -> str:
    """Return the persona markdown, or a fallback notice if the file is missing.

    Missing persona is not an error — the writer's built-in prompt still works.
    """
    global _persona_text
    if _persona_text is not None:
        return _persona_text

    path = config.PERSONA_PATH
    if path.exists():
        _persona_text = path.read_text(encoding="utf-8").strip()
        log.info("Loaded persona from %s (%d chars)", path, len(_persona_text))
    else:
        _persona_text = (
            "No persona file found. Use the channel's default plain, factual, "
            "wire-style voice."
        )
        log.warning("Persona file not found at %s — using fallback", path)
    return _persona_text


def get_recent_posts(n: int | None = None) -> list[str]:
    """Return the most recent published posts as plain text examples."""
    if n is None:
        n = config.PERSONA_RECENT_POSTS
    rows = db.get_recent_sent_posts(n)
    return [_visible_text(row["post_html"]) for row in rows if row["post_html"].strip()]


def reset_cache() -> None:
    """Force the persona file to be re-read on the next call."""
    global _persona_text
    _persona_text = None
