# --- Logging setup: what gets written to the log file and the screen ---
#
# WHAT THIS FILE DOES
#   Configures Python's logging once, at startup, so every other file can just
#   ask for a logger and have it behave sensibly.
#
# WHERE IT FITS
#   Called once from main.py (and from each tool in tools/). Nothing else needs
#   to think about it.
#
# WHY THE "NOISY LIBRARIES" SECTION MATTERS
#   Libraries like httpx log a line for every single HTTP request. With systemd
#   appending everything to one file forever, that is how a log quietly grows to
#   hundreds of megabytes. There is a 180 MB example of exactly this on this
#   machine right now. So we turn their chatter down to warnings only.
#
# DEPENDENCIES
#   Python's built-in logging. Reads LOG_LEVEL and LOG_PATH from config.

from __future__ import annotations

import logging
import sys

import config

# Libraries that log every request or every internal step. We only want to hear
# from these when something is actually wrong.
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

    # Always print to the screen. Under systemd this is what lands in the log
    # file; in your terminal it is what you actually watch.
    console = logging.StreamHandler(sys.stdout)
    console.setFormatter(formatter)
    root.addHandler(console)

    # Additionally write to our own log file — but ONLY when you started the
    # program yourself in a terminal.
    #
    # Under systemd, the service file already redirects everything printed to
    # the screen into logs/news-channel.log. If we ALSO wrote to that file
    # directly, every single line would appear in it twice. (It did, until this
    # check was added.) A terminal is detectable because stdout is attached to
    # one; systemd's is not.
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
