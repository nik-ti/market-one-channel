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
SCHEMA_PATH = HERE / "schema.sql"

_ENV = dotenv_values(HERE / ".env")


# Set once the active channel is known, a few lines below. Until then lookups
# are unprefixed, which is how CHANNEL itself gets read.
_PREFIX = ""


def _get(name: str, default: str = "") -> str:
    """Read a setting, preferring this channel's own value.

    MAX_POSTS_PER_HOUR is shared by every channel; MARKETS_MAX_POSTS_PER_HOUR
    belongs to one. Without that, a second channel silently inherits the first
    one's pacing with no way to say otherwise.
    """
    for key in ((_PREFIX + name) if _PREFIX else "", name):
        if not key:
            continue
        value = _ENV.get(key) or os.environ.get(key)
        if value is not None:
            return value.strip()
    return default.strip()


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


def _get_bool(name: str, default: bool) -> bool:
    """Read an on/off setting. Anything but a recognised word keeps the default."""
    value = _get(name, "").lower()
    if value in {"1", "true", "yes", "on"}:
        return True
    if value in {"0", "false", "no", "off"}:
        return False
    return default


# =============================================================================
# WHICH CHANNEL THIS PROCESS IS
# =============================================================================
# Everything below this line is machinery, shared by every channel. What makes
# a channel itself — its sources, its voice, what it considers important, its
# pipeline — lives in channels/<name>/ and is loaded here.
#
# Two channels run as two services from this one codebase, told apart by
# CHANNEL in their .env or unit file.

CHANNEL = _get("CHANNEL", "markets")
_PREFIX = CHANNEL.upper() + "_"

try:
    _profile = __import__(f"channels.{CHANNEL}.profile", fromlist=["profile"])
except ImportError as error:  # pragma: no cover - a typo here must be loud
    raise SystemExit(
        f"CHANNEL is '{CHANNEL}' but channels/{CHANNEL}/profile.py could not be "
        f"loaded: {error}\nChannels available: "
        f"{', '.join(sorted(p.parent.name for p in HERE.glob('channels/*/profile.py')))}"
    ) from error

CHANNEL_NAME = _profile.NAME

# Each channel keeps its own database. Sharing one would let a second channel's
# items into this one's duplicate check and story layer, which both ask
# "what else has been in here recently".
DB_PATH = HERE / "data" / _profile.DB_FILENAME
LOG_PATH = HERE / "logs" / _profile.LOG_FILENAME

SOURCES = _profile.SOURCES
X_ACCOUNTS = _profile.X_ACCOUNTS
NO_MEDIA_SOURCES = _profile.NO_MEDIA_SOURCES
VALID_TOPICS = _profile.VALID_TOPICS
PERSONA_PATH = _profile.PERSONA_PATH
TOPICS = _profile.TOPICS
MARKETS = _profile.MARKETS
RUBRIC_PATH = _profile.RUBRIC_PATH
PIPELINE = _profile.PIPELINE
USE_ECONOMIC_CALENDAR = getattr(_profile, "USE_ECONOMIC_CALENDAR", False)


# =============================================================================
# SECRETS (from .env)
# =============================================================================

# Which .env keys hold them is the channel's business: a second channel posts
# somewhere else, under its own bot.
TELEGRAM_BOT_TOKEN = _get(_profile.BOT_TOKEN_KEY)
CHANNEL_ID = _get(_profile.CHANNEL_ID_KEY)   # "@name" or "-100..."
ERROR_CHAT_ID = _get("ERROR_CHAT_ID")    # your DM, for error alerts
OPENROUTER_API_KEY = _get("OPENROUTER_API_KEY")
OPENROUTER_BASE_URL = _get("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1")






# =============================================================================
# POST APPEARANCE
# =============================================================================
# At most one mark per post, at the very front, before the headline. The WRITER
# picks it, from this list or a country's flag.
#
# WHY A LIST AND NOT A FREE CHOICE: a free choice once put a 🔥 on a drone
# strike. Every mark below says WHAT KIND of news this is, never how to feel
# about it, so a wrong pick is merely unhelpful. writer.enforce_mark() removes
# anything else.
#
# No hashtags, on purpose: two tags covering the whole channel sorted posts the
# way this desk thinks rather than the way a reader does.

POST_MARKS = {
    # a number moving — the short, specific posts
    "🔺": "a price, yield or figure rising — the number is the news",
    "🔻": "a price, yield or figure falling — the number is the news",
    "📈": "a market or trend moving up over a period, or an expected rise",
    "📉": "a market or trend moving down over a period, or an expected fall",
    "📊": "a scheduled data release or official statistics",
    # institutions
    "🏛️": "a central bank, government or regulator deciding or projecting",
    "🏦": "a commercial bank, or the banking system",
    "⚖️": "a court ruling, charge, lawsuit or enforcement action",
    "📝": "a tax, a bill, a law — legislation moving",
    # money and markets
    "💵": "the dollar, dollar liquidity, or money in general; crypto too",
    "💴": "the yen or Japan's money",
    "💶": "the euro or the eurozone's money",
    "🛢️": "oil, gas, refining, pipelines",
    # risk
    "⚠️": "a hack, exploit, breach or security vulnerability",
    "🔒": "safety, custody, a freeze or a lock-up of funds or assets",
}

# The bullet for lists inside a post. The one emoji allowed in the body:
# writer.strip_emojis() protects it while removing everything else.
BULLET = "▪️"

# A country's flag is also a valid mark, when that country IS the story —
# "🇯🇵 Japan's 10-year yield tops 3%". Flags are not listed above because
# there are 250 of them; writer.enforce_mark() accepts any flag as the mark.

# "markets" was added 28 Aug 2026. Widening the topic is NOT lowering the bar:
# the market-impact test in nodes/sorter.py is unchanged and still does the
# filtering. Such a story is now allowed to be judged, not refused a hearing.

# Short sources — typically an X post of a few sentences — become BRIEF posts:
# kept much shorter, because there is nothing to pad a longer post with except
# invention.
BRIEF_SOURCE_CHARS = _get_int("BRIEF_SOURCE_CHARS", 400)


# =============================================================================
# WHAT GETS PUBLISHED
# =============================================================================

# The first AI node scores every story 1-5 for market impact. Only stories at or
# above this number are published. This is the main control on volume and on
# how trivial the channel feels.
#   5 = only the biggest events        4 = market-moving news (recommended)
#   3 = ordinary news too              1 = everything
# The scale is defined in nodes/sorter.py — change both together.
MIN_IMPORTANCE = _get_int("MIN_IMPORTANCE", _profile.MIN_IMPORTANCE)


# =============================================================================
# TIMING AND LIMITS
# =============================================================================

POLL_MINUTES = _get_int("POLL_MINUTES", 10)

# Reading tweets from the shared relay
REDIS_URL = _get("REDIS_URL", "redis://localhost:6379/0")
TWEET_STREAM_KEY = _get("TWEET_STREAM_KEY", "tweets:stream")
# Redis bookmark name. Must stay different from the trading bot ("sniper-ingest"),
# and must not change on this live channel — a new name would start a new
# bookmark and either skip tweets or re-read old ones.
TWEET_STREAM_GROUP = _get("TWEET_STREAM_GROUP", "news-channel")
# Guards that stop a restart flooding the channel with old tweets.
X_MAX_AGE_MINUTES = _get_int("X_MAX_AGE_MINUTES", 45)
X_MAX_BURST = _get_int("X_MAX_BURST", 25)

# Guards against an abandoned feed still serving old contents, and a new feed
# handing over its whole back catalogue on the first poll.
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
# Five checks, cheapest first — see nodes/dedup.py.

# Check 3: how alike two headlines must be (0-100) to count as the same story.
FUZZY_THRESHOLD = _get_int("FUZZY_THRESHOLD", 92)
FUZZY_WINDOW_HOURS = _get_int("FUZZY_WINDOW_HOURS", 24)

# Check 4: same MEANING rather than same words (0.0-1.0). The score is a
# SHORTLIST, not a verdict — check 5 decides.
#
#   >= COSINE_CERTAIN    near-verbatim; merge without paying for a judgement
#   >= COSINE_SHORTLIST  worth asking about -> check 5
#   below                different story, no further checks
#
# Measured on 19 hand-labelled pairs from this channel: real duplicates scored
# 0.738-0.993 and genuinely different ones 0.785-0.900. Overlapping ranges, so
# no single cutoff works — which is why check 5 exists. 0.72 caught 100% of the
# real duplicates in that sample and 0.75 already started missing them.
COSINE_SHORTLIST = _get_float("COSINE_SHORTLIST", 0.72)
COSINE_CERTAIN = _get_float("COSINE_CERTAIN", 0.95)
COSINE_WINDOW_HOURS = _get_int("COSINE_WINDOW_HOURS", 48)

# How many shortlisted candidates to consider. It was 1, which is why a burst of
# three tweets about one event only ever got compared in pairs.
DEDUP_TOP_K = _get_int("DEDUP_TOP_K", 3)

# THE TIME GATE. Every real duplicate in that sample landed within 10.1 hours;
# the worst false merges were 24 hours apart — two editions of the same
# recurring report. This removes them for free, before any model runs.
DUPLICATE_MAX_GAP_HOURS = _get_int("DUPLICATE_MAX_GAP_HOURS", 12)

EMBEDDING_MODEL = _get("EMBEDDING_MODEL", "openai/text-embedding-3-small")
# =============================================================================
# AI MODELS
# =============================================================================
# Prompts live at the top of each node file, not here.

# 1. Scores importance and topic. Runs on everything for about $1.50 a month,
# so choose it for JUDGEMENT rather than for being cheap — it decides what
# matters.
SORTER_MODEL = _get("SORTER_MODEL", "deepseek/deepseek-v3.2")

# 2. Writes the post in the house style. DeepSeek's output is $0.40/M against
# Gemini Flash's $2.50/M, and the writer is output-heavy, so this roughly halves
# the biggest cost in the pipeline.
WRITER_MODEL = _get("WRITER_MODEL", "deepseek/deepseek-v3.2")

# 3. Checks the finished post against its source. Keep it a DIFFERENT lab from
# the writer — a model judges its own prose badly.
#
# Chosen by testing 9 models on 7 source/post pairs. MiniMax M2.7: 7/7, 2.5s.
# mistral-medium-3.1 also 7/7 but dearer; deepseek-v3.2 missed a falsehood;
# qwen3.5-plus missed three and took 32s; glm-4.7 and claude-haiku-4.5 REJECT
# the strict schema, which is what stops the editor inventing its own rejection
# reasons — so they are disqualified however good they are.
EDITOR_MODEL = _get("EDITOR_MODEL", "minimax/minimax-m2.7")

# Used when EDITOR_MODEL cannot be reached at all. This node fails closed, so an
# unreachable editor means the post is not published — and minimax is rate-limited
# upstream often enough to matter: 15 failures in two weeks, 10 of them HTTP 429,
# which cost four real stories including a BLS payrolls print. The item burns all
# three of its attempts inside one four-minute window and is marked failed.
#
# The fallback must clear the same bar as the primary, because a rubber-stamp
# second opinion is WORSE than losing the story: it would publish unchecked
# posts instead of none. Tested on the same three cases as the primary —
# mistral-medium-3.1 caught all three at 1-2s, faster than minimax. Every qwen
# tried waved through "SEC is reportedly considering" published as "SEC
# approves", which is the exact failure this node exists to prevent.
#
# Set to "" to switch the fallback off.
EDITOR_FALLBACK_MODEL = _get("EDITOR_FALLBACK_MODEL", "mistralai/mistral-medium-3.1")

# A ceiling on the whole editor call, retries and fallback included. Without it
# the worst case is three retries against each of two models — 368 seconds on one
# post, three times the publish tick. A normal verdict takes 2-4 seconds, so this
# is fifteen times the usual and only ever fires when something is badly wrong.
EDITOR_TIMEOUT_SECONDS = _get_int("EDITOR_TIMEOUT_SECONDS", 60)

# Alerts you if the editor starts rejecting an unusual share of posts.
EDITOR_DECLINE_ALERT_RATE = _get_float("EDITOR_DECLINE_ALERT_RATE", 0.5)
EDITOR_DECLINE_WINDOW = _get_int("EDITOR_DECLINE_WINDOW", 20)

# A post rejected for a FIXABLE rule (see FIXABLE_RULES in brain/nodes.py) goes
# back to the writer with the reason instead of being dropped. Capped low: each
# loop costs another writer + editor call, and a third draft never makes it.
MAX_REWRITES = _get_int("MAX_REWRITES", 1)


# 4. Duplicate check 5 — the only step that can tell "ETF inflows" from "ETF
# outflows". Tested on 20 hand-labelled pairs from this channel:
#
#   deepseek-v3.2           80%, 0 wrong merges, order-STABLE on 6/6 pairs
#   gemini-2.5-flash-lite   75%, 0 wrong merges, order-FLIPS on 3/6 pairs
#   minimax-m2.7            13 of 20 calls failed under concurrency
#
# STABILITY BEATS RAW ACCURACY here. Swapping which story is shown first flipped
# gemini's verdict on half the hard pairs, and this node deletes content nobody
# sees again — an unstable verdict is an unreproducible bug. Before switching
# models, ask for a verdict on (A,B) and (B,A) and check they match.
#
# minimax is disqualified despite being the editor: the judge fires in bursts by
# nature, so a model that falls over under concurrency is the wrong tool.
JUDGE_MODEL = _get("JUDGE_MODEL", "deepseek/deepseek-v3.2")

# Past this we treat the item as new and post it. 25s was enough for a normal
# day but one call hit it when 20 fired at once, and a timeout means a duplicate
# is published — cheap headroom inside a 120-second publish tick.
JUDGE_TIMEOUT_SECONDS = _get_int("JUDGE_TIMEOUT_SECONDS", 40)


# =============================================================================
# BRAIN / PERSONA
# =============================================================================

# The channel's voice, prepended to the writer's system prompt. A missing file
# just means the writer's built-in generic prompt.

# Recent posts the writer sees as voice examples: enough to catch the rhythm
# without bloating the prompt.
PERSONA_RECENT_POSTS = _get_int("PERSONA_RECENT_POSTS", 15)


# =============================================================================
# STORIES
# =============================================================================
# The unit of work: items join a running story, and a story posts when it moves.

# Same model as the sorter and the judge. Both questions here are reading
# comprehension over short text, which is what this model is cheapest at.
STORY_MODEL = _get("STORY_MODEL", "deepseek/deepseek-v3.2")
STORY_TIMEOUT_SECONDS = _get_int("STORY_TIMEOUT_SECONDS", 30)

# How many open stories the placement step is shown. It answers with an index
# into this list, so a long one makes the prompt long and the numbering easy to
# get wrong; the oldest candidates are the least likely answers anyway.
STORY_MAX_OPEN = _get_int("STORY_MAX_OPEN", 12)

# How many unposted items the gate and the writer are shown from one story.
# Keeps both prompts bounded no matter how much piles up; anything outside the
# slice is still marked covered when the post goes out.
STORY_MAX_PENDING = _get_int("STORY_MAX_PENDING", 12)

# A story nobody has added to in this long is over. A new item that looks like
# it cannot reopen it — it starts a fresh story, which is what a reader coming
# back the next day would expect.
# 12 hours split a war into eight stories in a six-day replay: it went quiet
# overnight, and every morning was a "new" story whose first post always goes
# out. A running situation stays one story across a quiet night.
STORY_IDLE_HOURS = _get_int("STORY_IDLE_HOURS", 36)

# A hard end, whatever the story is doing. Without it a broad situation stays
# live indefinitely by absorbing one item every eleven hours, and slowly starts
# swallowing everything else.
STORY_MAX_HOURS = _get_int("STORY_MAX_HOURS", 168)

# A floor against two wires seconds apart becoming two posts — nothing more.
# It was 25 minutes, and a timer silenced Iran announcing its retaliation on
# 1 September. How loud a running story is allowed to be is now the gate's
# judgement, which is told how long it has been and holds a higher bar when the
# last post is recent.
STORY_MIN_GAP_MINUTES = _get_int("STORY_MIN_GAP_MINUTES", 6)  # anti-double-post only

# A runaway stop, not an editorial rule — the gate is told the count and weighs
# it. At 6 this was a rule, and it silenced the second half of a war.
STORY_MAX_POSTS = _get_int("STORY_MAX_POSTS", 12)

# THE ROUNDUP. Held items are fuel for a story's next post, but a story that
# never moves again never burns it. Once this many are waiting and this long
# has passed since the story last posted, they go out together as one roundup.
# The one place a post is released by arithmetic rather than by the editor,
# on purpose: it turns "lost" into "late".
STORY_DIGEST_ITEMS = _get_int("STORY_DIGEST_ITEMS", 3)
STORY_DIGEST_MINUTES = _get_int("STORY_DIGEST_MINUTES", 180)


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

# =============================================================================
# READING THE LINKED ARTICLE
# =============================================================================
# 59% of items arrive with a body under 120 characters and a link nobody
# followed, which is why a post's second line so often just restates its first.
# See nodes/article.py.

# One request, then extraction. Generous enough for a slow news site, short
# enough that the publish round does not stall behind one of them.
ARTICLE_TIMEOUT_SECONDS = _get_int("ARTICLE_TIMEOUT_SECONDS", 15)

# The whole browser attempt, including launch. Only sites that refuse a plain
# request get here, and a browser that hangs must not hold the round forever.
ARTICLE_BROWSER_TIMEOUT_SECONDS = _get_int("ARTICLE_BROWSER_TIMEOUT_SECONDS", 60)

# What is kept. The writer is shown the source in full; an entire long-read
# would crowd out the wire items it is meant to be summarising.
ARTICLE_MAX_CHARS = _get_int("ARTICLE_MAX_CHARS", 6000)

# =============================================================================
# THE LAST CHECK BEFORE SENDING
# =============================================================================
# See nodes/echo.py. The shortlist is deliberately wide because no cutoff
# separates a repeat from a genuine step forward — measured, they interleave —
# so the judge does the deciding and this number only decides who gets read.
ECHO_SHORTLIST = _get_float("ECHO_SHORTLIST", 0.72)

# How far back a reader remembers. Apple's repeat came three days later.
ECHO_WINDOW_HOURS = _get_int("ECHO_WINDOW_HOURS", 120)
ECHO_MAX_COMPARED = _get_int("ECHO_MAX_COMPARED", 40)
