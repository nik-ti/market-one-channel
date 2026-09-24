"""Where the channel project lives, found rather than counted.

Both halves of this API used to locate the project by walking a fixed number
of parent directories, which broke the moment the dashboard moved one level
deeper. This looks for the markers instead.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

from dotenv import dotenv_values


def project_root() -> Path:
    """The channel project's root: the directory holding config.py and schema.sql."""
    for candidate in Path(__file__).resolve().parents:
        if (candidate / "config.py").is_file() and (candidate / "schema.sql").is_file():
            return candidate
    raise RuntimeError("Could not find the project root from " + str(Path(__file__)))


ROOT_DIR = project_root()
ENV_PATH = ROOT_DIR / ".env"


def active_channel() -> str:
    env = dotenv_values(ENV_PATH) if ENV_PATH.exists() else {}
    return env.get("CHANNEL") or os.environ.get("CHANNEL") or "markets"


def channel_dir(channel: str | None = None) -> Path:
    return ROOT_DIR / "channels" / (channel or active_channel())


def database_path(channel: str | None = None) -> Path:
    """A channel's database, read from its profile without importing it.

    Defaults to the one named in .env, so callers that serve a single channel
    need not pass anything.
    """
    profile = channel_dir(channel) / "profile.py"
    try:
        match = re.search(r'^DB_FILENAME\s*=\s*["\'](.+?)["\']',
                          profile.read_text(), re.M)
    except OSError:
        match = None
    return ROOT_DIR / "data" / (match.group(1) if match else "markets.db")


def list_channel_names() -> list[str]:
    """Every channel this project defines: a directory under channels/ with
    its own profile.py. Sorted for a stable, predictable switcher order."""
    channels_root = ROOT_DIR / "channels"
    if not channels_root.is_dir():
        return []
    return sorted(
        entry.name for entry in channels_root.iterdir()
        if entry.is_dir() and (entry / "profile.py").is_file()
    )


def database_ready(channel: str | None = None) -> bool:
    """Whether this channel's database file exists yet. ai_news does not
    have one until its pipeline has run for the first time."""
    return database_path(channel).exists()
