"""Loads the channel voice (brain/persona.md) and recent posts for the writer.

The persona file is read once and cached, so a change to it needs a service
restart to take effect.
"""

from __future__ import annotations

import config
from utils import db, logger as log_setup

log = log_setup.get("persona")

_persona_text: str | None = None


def _visible_text(post_html: str) -> str:
    """Strip HTML tags and the source link line for a readable voice sample."""
    import re
    # Drop the source credit line if present. Two spellings, because posts sent
    # before the byline change still carry the old 🔗 form and they are still
    # perfectly good voice samples.
    text = re.sub(r"\n?(?:🔗|—) .*$", "", post_html, flags=re.MULTILINE)
    # Drop HTML tags.
    text = re.sub(r"<[^>]+>", "", text)
    # Collapse whitespace.
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def load_persona() -> str:
    """Return the persona markdown. A missing file is not an error."""
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


