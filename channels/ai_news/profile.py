"""The AI news channel. A skeleton — nothing here is filled in yet.

Copied from channels/markets/profile.py, which is the worked example. The
machinery in nodes/, brain/ and utils/ is shared and needs no changes to run
this channel; what belongs here is only what makes it a different channel.

Still to decide, in roughly this order:
  - what it covers, and what makes an item important enough to post (rubric.md)
  - its voice (persona.md)
  - its sources
  - whether it wants stations the markets channel does not, such as one that
    reads the images and clips a post carries. That station goes in a nodes.py
    beside this file and is named in PIPELINE below; nothing shared changes.
"""

from __future__ import annotations

from pathlib import Path

HERE = Path(__file__).resolve().parent

NAME = "ai_news"

DB_FILENAME = "ai-news.db"
LOG_FILENAME = "ai-news.log"

# Its own bot and its own channel, so its own .env keys.
BOT_TOKEN_KEY = "AI_TELEGRAM_BOT_TOKEN"
CHANNEL_ID_KEY = "AI_CHANNEL_ID"

PERSONA_PATH = HERE / "persona.md"
RUBRIC_PATH = HERE / "rubric.md"

MIN_IMPORTANCE = 4

# Placeholders. The markets channel's third axis is "which market reprices",
# which is the wrong question here — what replaces it is part of writing the
# rubric, and the schema is built from whatever these say.
TOPICS = ("other",)
MARKETS = ("none",)
VALID_TOPICS = ("other",)

SOURCES: list[dict] = []
X_ACCOUNTS: dict[str, str] = {}
NO_MEDIA_SOURCES: set[str] = set()

# The economic calendar is a markets thing.
USE_ECONOMIC_CALENDAR = False

# The same stations as the markets channel for now. A media-reading station
# would slot in after fetch_article, once it exists in nodes.py beside this file.
PIPELINE = [
    "dedup",
    "sorter",
    "fetch_article",
    "story_organizer",
    "gatekeeper",
    "writer",
    "editor",
    "repeat_check",
    "publish",
]
