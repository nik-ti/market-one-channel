"""Reads a channel's PIPELINE list out of its profile.py as text — same
reasoning as nodes.py's prompt extraction: this read-only dashboard has no
business importing the pipeline (module-level code that needs API keys and
config this process has no reason to depend on) just to learn station names.
"""

from __future__ import annotations

import re

import paths

# What every channel declares today, and the fallback if a profile.py can't
# be parsed for some reason — never crash the dashboard over this.
DEFAULT_PIPELINE = [
    "dedup", "sorter", "fetch_article", "story_organizer",
    "gatekeeper", "writer", "editor", "publish",
]


def channel_pipeline(channel: str) -> list[str]:
    """The station names in channels/<channel>/profile.py's PIPELINE, in
    order, as literal strings — not the STAGES functions they route to."""
    text: str | None
    try:
        text = (paths.channel_dir(channel) / "profile.py").read_text(encoding="utf-8")
    except OSError:
        text = None
    if not text:
        return list(DEFAULT_PIPELINE)

    match = re.search(r'^PIPELINE\s*=\s*\[(.*?)\]', text, re.DOTALL | re.MULTILINE)
    if not match:
        return list(DEFAULT_PIPELINE)

    stations = re.findall(r'["\']([^"\']+)["\']', match.group(1))
    return stations or list(DEFAULT_PIPELINE)
