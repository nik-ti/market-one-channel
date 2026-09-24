"""Turns a dashboard request's ?channel= into one of this project's real
channels, and lists them for the switcher.

The dashboard used to read whichever channel .env named CHANNEL. Now every
endpoint can be asked for any channel, so "the active one" is gone — the
fallback below is a fixed default (markets), never .env, matching the spec's
"defaulting to markets" rather than "defaulting to whatever .env says".
"""

from __future__ import annotations

import re

import paths

DEFAULT_CHANNEL = "markets"


def resolve_channel(requested: str | None) -> str:
    """The channel a request means. An unknown or missing name falls back to
    the default rather than erroring — a stale bookmark or an old shared
    link should degrade gracefully, not break."""
    names = paths.list_channel_names()
    if requested and requested in names:
        return requested
    if DEFAULT_CHANNEL in names:
        return DEFAULT_CHANNEL
    return names[0] if names else DEFAULT_CHANNEL


def _profile_text(channel: str) -> str | None:
    try:
        return (paths.channel_dir(channel) / "profile.py").read_text(encoding="utf-8")
    except OSError:
        return None


def _display_name(channel: str) -> str:
    """A human label. Prefers the channel's own NAME constant, falling back
    to a title-cased version of the directory name — never a made-up name."""
    text = _profile_text(channel)
    source = channel
    if text:
        match = re.search(r'^NAME\s*=\s*["\']([^"\']+)["\']', text, re.M)
        if match:
            source = match.group(1)
    words = [w for w in re.split(r"[_\-]+", source) if w]
    return " ".join(w.upper() if w.lower() == "ai" else w.capitalize() for w in words)


def list_channels() -> list[dict]:
    """Every channel plus whether it has a database yet — what the
    dashboard's switcher needs and nothing more."""
    return [
        {"id": name, "name": _display_name(name), "ready": paths.database_ready(name)}
        for name in paths.list_channel_names()
    ]
