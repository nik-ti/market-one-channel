"""Logging setup, called once from main.py and from each tool."""

from __future__ import annotations

import logging
import sys

import config

# These log a line per HTTP request. Left alone, that grows the log file to
# hundreds of megabytes — there is a 180 MB example of it on this machine.
_NOISY = ("httpx", "httpcore", "asyncio", "telegram", "telegram.ext", "redis")

_configured = False


def setup(level: str | None = None, to_file: bool = True) -> None:
    """Set up logging for the whole program. Safe to call more than once.

    Args:
        level: "DEBUG", "INFO", "WARNING"... Defaults to LOG_LEVEL from .env.
        to_file: also write to logs/news-channel.log. Tools set this to False
                 because they are meant to print to your terminal, not pollute
                 the service's log.
    """
    global _configured
    if _configured:
        return

    level_name = (level or config.LOG_LEVEL).upper()
    level_value = getattr(logging, level_name, logging.INFO)

    # "13:45:02 | news-channel.rss | INFO | Polled coindesk: 3 new"
    formatter = logging.Formatter(
        fmt="%(asctime)s | %(name)s | %(levelname)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    root = logging.getLogger()
    root.setLevel(level_value)

    console = logging.StreamHandler(sys.stdout)
    console.setFormatter(formatter)
    root.addHandler(console)

    # Only when started by hand. Under systemd the service file already
    # redirects stdout into the same log file, so writing to it here as well
    # put every line in twice — which it did, until this check.
    if to_file and sys.stdout.isatty():
        config.LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        file_handler = logging.FileHandler(config.LOG_PATH, encoding="utf-8")
        file_handler.setFormatter(formatter)
        root.addHandler(file_handler)

    for noisy in _NOISY:
        logging.getLogger(noisy).setLevel(logging.WARNING)

    _configured = True


def get(name: str) -> logging.Logger:
    """Get a logger for one part of the program, e.g. get("rss")."""
    return logging.getLogger(f"news-channel.{name}")
