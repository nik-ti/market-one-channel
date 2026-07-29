# --- Every setting for the news channel ---
#
# Secrets live in .env next door, not here. Everything below either reads from
# .env with a sensible default, or is a list you edit directly.
#
# Full explanations of each setting are in .env.example and README.md.

from __future__ import annotations

import os
from pathlib import Path

from dotenv import dotenv_values

HERE = Path(__file__).resolve().parent
DB_PATH = HERE / "data" / "news.db"
SCHEMA_PATH = HERE / "schema.sql"
LOG_PATH = HERE / "logs" / "news-channel.log"

_ENV = dotenv_values(HERE / ".env")


def _get(name: str, default: str = "") -> str:
    """Read a setting from .env, then the environment, then the default."""
    value = _ENV.get(name) or os.environ.get(name)
    return (value if value is not None else default).strip()


def _get_int(name: str, default: int) -> int:
    """Read a whole-number setting."""
    try:
        return int(_get(name, str(default)))
    except ValueError:
        return default


def _get_float(name: str, default: float) -> float:
    """Read a decimal setting."""
    try:
        return float(_get(name, str(default)))
    except ValueError:
        return default


# =============================================================================
# SECRETS (from .env)
# =============================================================================

TELEGRAM_BOT_TOKEN = _get("TELEGRAM_BOT_TOKEN")
CHANNEL_ID = _get("CHANNEL_ID")          # "@name" or "-100..."
ERROR_CHAT_ID = _get("ERROR_CHAT_ID")    # your DM, for error alerts
OPENROUTER_API_KEY = _get("OPENROUTER_API_KEY")
OPENROUTER_BASE_URL = _get("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1")


# =============================================================================
# FEEDS  ← edit this list
# =============================================================================
# name  = short nickname, must be unique
# topic = "crypto" or "geopolitics" (the AI can override it)
#
# After changing this, run: python3 tools/check_sources.py

SOURCES = [
    # ── Crypto ──
    {"name": "coindesk",       "topic": "crypto", "url": "https://www.coindesk.com/arc/outboundfeeds/rss/"},
    {"name": "theblock",       "topic": "crypto", "url": "https://www.theblock.co/rss.xml"},

    # ── Geopolitics ──
    {"name": "guardian_world", "topic": "geopolitics", "url": "https://www.theguardian.com/world/rss"},
    {"name": "aljazeera",      "topic": "geopolitics", "url": "https://www.aljazeera.com/xml/rss/all.xml"},

]


# =============================================================================
# X ACCOUNTS  ← edit this list
# =============================================================================
# Adding an account needs BOTH:
#   1. /home/nikita/trading/infra/tweet-relay/accounts.txt  (then restart tweet-relay)
#   2. this dictionary
# Removing it from here alone mutes it for this channel only.

X_ACCOUNTS = {
    "WatcherGuru":   "crypto",
    "crypto_banter": "crypto",
    "TreeNewsFeed":  "crypto",
    "BLS_gov":       "geopolitics",
}


# =============================================================================
# POST APPEARANCE
# =============================================================================
# ONE emoji per post, and it is the first character. Nothing else.
#
# It is added by code and never by the AI — the writer is told to use no emoji
# at all, and any it produces anyway are stripped before sending. A single
# consistent mark is the channel's whole visual signature, and a model choosing
# decorative emoji per post destroys that: it once put a 🔥 on a drone strike.
#
# There are no hashtags. They were dropped on purpose: two tags covering the
# whole channel sorted posts the way this desk thinks rather than the way a
# reader does, and every post carried a line of tag that said almost nothing.

TOPIC_EMOJI = {
    "crypto":      "🪙",
    "geopolitics": "🌍",
}

VALID_TOPICS = tuple(TOPIC_EMOJI.keys())

# Shown when a topic somehow isn't one of the above. It should never appear —
# if you see it in the channel, the sorter returned something unexpected.
FALLBACK_EMOJI = "📰"

# Short sources — typically an X post of a few sentences — become BRIEF posts:
# they lead with ⚡ instead of the topic emoji and are kept much shorter, because
# there is nothing to pad a longer post with except invention.
BRIEF_SOURCE_CHARS = _get_int("BRIEF_SOURCE_CHARS", 400)
BRIEF_EMOJI = "⚡"


# =============================================================================
# WHAT GETS PUBLISHED
# =============================================================================

# The first AI node scores every story 1-5 for market impact. Only stories at or
# above this number are published. This is the main control on volume and on
# how trivial the channel feels.
#   5 = only the biggest events        4 = market-moving news (recommended)
#   3 = ordinary news too              1 = everything
# The scale is defined in nodes/sorter.py — change both together.
MIN_IMPORTANCE = _get_int("MIN_IMPORTANCE", 4)


# =============================================================================
# TIMING AND LIMITS
# =============================================================================

POLL_MINUTES = _get_int("POLL_MINUTES", 10)

# Reading tweets from the shared relay
REDIS_URL = _get("REDIS_URL", "redis://localhost:6379/0")
TWEET_STREAM_KEY = _get("TWEET_STREAM_KEY", "tweets:stream")
# Must stay different from the trading bot's group ("sniper-ingest").
TWEET_STREAM_GROUP = _get("TWEET_STREAM_GROUP", "news-channel")
# Guards that stop a restart flooding the channel with old tweets.
X_MAX_AGE_MINUTES = _get_int("X_MAX_AGE_MINUTES", 45)
X_MAX_BURST = _get_int("X_MAX_BURST", 25)

# Articles older than this are ignored. Guards against two real cases: a feed
# that has been abandoned but still serves its old contents, and a newly added
# feed handing us its whole back catalogue on the first poll.
ARTICLE_MAX_AGE_HOURS = _get_int("ARTICLE_MAX_AGE_HOURS", 24)

# Publishing pace
PUBLISH_TICK_SECONDS = _get_int("PUBLISH_TICK_SECONDS", 120)
MAX_POSTS_PER_TICK = _get_int("MAX_POSTS_PER_TICK", 2)
SECONDS_BETWEEN_SENDS = _get_int("SECONDS_BETWEEN_SENDS", 3)
MIN_SECONDS_BETWEEN_POSTS = _get_int("MIN_SECONDS_BETWEEN_POSTS", 90)
MAX_POSTS_PER_HOUR = _get_int("MAX_POSTS_PER_HOUR", 12)
MAX_POSTS_PER_DAY = _get_int("MAX_POSTS_PER_DAY", 60)

# Queue housekeeping. Items waiting longer than this are dropped unposted —
# without it the queue grows forever and the channel posts stale news.
QUEUE_TTL_MINUTES = _get_int("QUEUE_TTL_MINUTES", 90)
MAX_QUEUE_SIZE = _get_int("MAX_QUEUE_SIZE", 200)
MAX_ATTEMPTS = _get_int("MAX_ATTEMPTS", 3)


# =============================================================================
# DUPLICATE DETECTION
# =============================================================================
# Four checks, cheapest first — see nodes/dedup.py.

# Check 3: how alike two headlines must be (0-100) to count as the same story.
FUZZY_THRESHOLD = _get_int("FUZZY_THRESHOLD", 92)
FUZZY_WINDOW_HOURS = _get_int("FUZZY_WINDOW_HOURS", 24)

# Check 4: same MEANING rather than same words (0.0-1.0). Measured, not guessed:
# across 188 real articles from these feeds, unrelated pairs sat below 0.79 and
# genuine duplicates fell between 0.806 and 0.884. Lower it if duplicates slip
# through; raise it if different stories are being merged.
COSINE_THRESHOLD = _get_float("COSINE_THRESHOLD", 0.80)
COSINE_WINDOW_HOURS = _get_int("COSINE_WINDOW_HOURS", 48)

EMBEDDING_MODEL = _get("EMBEDDING_MODEL", "openai/text-embedding-3-small")
EMBEDDING_DIM = _get_int("EMBEDDING_DIM", 1536)


# =============================================================================
# AI MODELS
# =============================================================================
# Prompts live at the top of each node file, not here.

# 1. Scores importance and topic. Runs on everything, but that still only costs
# about $1.50 a month — so this is chosen for JUDGEMENT, not for being cheap.
# It is the node that decides what matters, which is worth paying for.
SORTER_MODEL = _get("SORTER_MODEL", "deepseek/deepseek-v3.2")

# 2. Writes the post in the house style. DeepSeek's output is $0.40/M against
# Gemini Flash's $2.50/M, and the writer is output-heavy, so this roughly halves
# the biggest cost in the pipeline.
WRITER_MODEL = _get("WRITER_MODEL", "deepseek/deepseek-v3.2")

# 3. Checks the finished post against its source.
#
# Chosen by testing 9 models on 7 source/post pairs, including a real
# hallucination this node caught on the live channel. MiniMax M2.7 scored 7/7
# with nothing missed, at 2.5s per post — the same accuracy as gpt-5-mini but
# three times faster and half the output price.
#
# Others tested: mistral-medium-3.1 also 7/7 but dearer; deepseek-v3.2 missed a
# falsehood; qwen3.5-plus missed three and took 32s; z-ai/glm-4.7 errored on 5
# of 7 calls because it REJECTS the strict schema — as does claude-haiku-4.5.
# That schema is what stops the editor inventing its own rejection reasons, so
# any model that cannot do it is disqualified no matter how good it is.
#
# Keep this a DIFFERENT lab from the writer — a model judges its own prose badly.
EDITOR_MODEL = _get("EDITOR_MODEL", "minimax/minimax-m2.7")

# Alerts you if the editor starts rejecting an unusual share of posts.
EDITOR_DECLINE_ALERT_RATE = _get_float("EDITOR_DECLINE_ALERT_RATE", 0.5)
EDITOR_DECLINE_WINDOW = _get_int("EDITOR_DECLINE_WINDOW", 20)


# =============================================================================
# MISC
# =============================================================================

LOG_LEVEL = _get("LOG_LEVEL", "INFO")
MAX_BODY_CHARS = _get_int("MAX_BODY_CHARS", 5000)


def check(require_telegram: bool = False, require_openrouter: bool = False) -> list[str]:
    """Return a list of configuration problems — empty means all good."""
    problems: list[str] = []

    if require_telegram:
        if not TELEGRAM_BOT_TOKEN:
            problems.append("TELEGRAM_BOT_TOKEN is missing from .env")
        if not CHANNEL_ID:
            problems.append("CHANNEL_ID is missing from .env — nowhere to post")

    if require_openrouter and not OPENROUTER_API_KEY:
        problems.append("OPENROUTER_API_KEY is missing from .env")

    names = [s["name"] for s in SOURCES]
    duplicates = {n for n in names if names.count(n) > 1}
    if duplicates:
        problems.append(f"Duplicate source names in SOURCES: {sorted(duplicates)}")

    for source in SOURCES:
        if source["topic"] not in VALID_TOPICS:
            problems.append(f"Source '{source['name']}' has unknown topic "
                            f"'{source['topic']}' (expected {VALID_TOPICS})")
    for handle, topic in X_ACCOUNTS.items():
        if topic not in VALID_TOPICS:
            problems.append(f"X account '{handle}' has unknown topic "
                            f"'{topic}' (expected {VALID_TOPICS})")

    if not 1 <= MIN_IMPORTANCE <= 5:
        problems.append(f"MIN_IMPORTANCE must be between 1 and 5, not {MIN_IMPORTANCE}")

    return problems
