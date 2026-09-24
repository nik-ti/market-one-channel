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


def channel_dir() -> Path:
    return ROOT_DIR / "channels" / active_channel()


def database_path() -> Path:
    """The active channel's database, read from its profile without importing it."""
    profile = channel_dir() / "profile.py"
    try:
        match = re.search(r'^DB_FILENAME\s*=\s*["\'](.+?)["\']',
                          profile.read_text(), re.M)
    except OSError:
        match = None
    return ROOT_DIR / "data" / (match.group(1) if match else "markets.db")
