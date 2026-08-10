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
# Five checks, cheapest first — see nodes/dedup.py.

# Check 3: how alike two headlines must be (0-100) to count as the same story.
FUZZY_THRESHOLD = _get_int("FUZZY_THRESHOLD", 92)
FUZZY_WINDOW_HOURS = _get_int("FUZZY_WINDOW_HOURS", 24)

# Check 4: same MEANING rather than same words (0.0-1.0).
#
# This USED to be a single cutoff at 0.80, and it did not work. Measured on 19
# hand-labelled pairs from this channel's own history:
#
#     real duplicates      scored 0.738 - 0.993
#     genuinely different  scored 0.785 - 0.900
#
# Those ranges overlap, so NO single cutoff can separate them. The 0.80 line let
# three separate posts about one Fed rate decision go out (they scored 0.738,
# 0.745 and 0.797) while merging "$49.75M ETF OUTflows" into "$32.11M ETF
# INflows" at 0.900 — opposite events, a day apart. Lowering the line to 0.72
# was measured too: it doubled the wrong merges without fixing anything.
#
# So the score is no longer a verdict. It is a SHORTLIST, and check 5 decides.
#
#   >= COSINE_CERTAIN    near-verbatim; merge without paying for a judgement
#   >= COSINE_SHORTLIST  close enough to be worth asking about -> check 5
#   below                different story, no further checks
#
# 0.72 was chosen because it caught 100% of the real duplicates in that sample;
# 0.75 already started missing them.
COSINE_SHORTLIST = _get_float("COSINE_SHORTLIST", 0.72)
COSINE_CERTAIN = _get_float("COSINE_CERTAIN", 0.95)
COSINE_WINDOW_HOURS = _get_int("COSINE_WINDOW_HOURS", 48)

# How many shortlisted candidates to consider. It was 1, which is why a burst of
# three tweets about one event only ever got compared in pairs.
DEDUP_TOP_K = _get_int("DEDUP_TOP_K", 3)

# THE TIME GATE. Two items can only be the same event if they arrived close
# together. In that same sample every real duplicate landed within 10.1 hours,
# while the worst false merges were 24 hours apart — two days of the same
# recurring report. This one line removes them for free, before any model runs.
DUPLICATE_MAX_GAP_HOURS = _get_int("DUPLICATE_MAX_GAP_HOURS", 12)

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

# The brain's rewrite loop: when the editor rejects a post for a FIXABLE rule
# (see FIXABLE_RULES in brain/nodes.py), the draft goes back to the writer once
# with the rejection reason, instead of the post being dropped on the spot.
# Capped low on purpose — each loop costs another writer + editor call, and a
# post that needs a third draft was never going to make it.
MAX_REWRITES = _get_int("MAX_REWRITES", 1)

# Continuations of already-published stories may publish with a lower importance
# bar than new stories, because the parent story was already vetted. New
# stories still need MIN_IMPORTANCE.
CONTINUATION_MIN_IMPORTANCE = _get_int("CONTINUATION_MIN_IMPORTANCE", 3)

# 4. Duplicate check number 5: given two stories that LOOK alike, decide whether
# they are the same event. This is the only step that can tell "ETF inflows"
# from "ETF outflows", so it is worth choosing carefully.
#
# Chosen by testing three models on 20 hand-labelled pairs from this channel's
# own history, using the exact prompt in nodes/judge.py:
#
#   deepseek-v3.2           80%, 0 wrong merges, order-STABLE on 6/6 pairs
#   gemini-2.5-flash-lite   75%, 0 wrong merges, order-FLIPS on 3/6 pairs
#   minimax-m2.7            unusable — see below
#
# Both got every case that has actually broken the live channel right: the Fed
# trio AND the ETF inflow/outflow pair. deepseek wins on STABILITY, which is
# what actually matters here.
#
# WHY STABILITY BEATS RAW ACCURACY
# Gemini was faster and looked no worse on paper, but swapping which story is
# shown first flipped its verdict on half the hard pairs. A judge whose answer
# depends on argument order is not really judging — and this node deletes
# content that nobody ever sees again. An unstable verdict means an unreproducible
# bug: the same two stories merge on Monday and not on Tuesday, and you can never
# work out why. deepseek gave the same answer both ways on all six.
#
# Test it yourself before switching models — ask for a verdict on (A,B) and on
# (B,A) and check they match. A model that fails that is disqualified regardless
# of its score.
#
# minimax-m2.7 is DISQUALIFIED here even though it is the editor: 13 of 20 calls
# failed on rate limits and unreadable JSON when run concurrently. The judge
# fires in bursts by its nature — breaking news arrives all at once — so a model
# that falls over under concurrency is the wrong tool no matter how accurate.
JUDGE_MODEL = _get("JUDGE_MODEL", "deepseek/deepseek-v3.2")

# How long to wait for a verdict. Past this we treat the item as new and post it
# — see "WHICH WAY IT FAILS" at the top of nodes/judge.py.
#
# 25s was enough for a normal day (zero timeouts replaying 188 real items) but
# one call did hit it when 20 were fired at once. Timing out means a duplicate
# gets published, so the extra headroom is worth more than the wait: this runs
# inside the 120-second publish tick, which posts at most 2 items anyway.
JUDGE_TIMEOUT_SECONDS = _get_int("JUDGE_TIMEOUT_SECONDS", 40)


# =============================================================================
# BRAIN / PERSONA
# =============================================================================

# Where the channel's voice lives. The file is plain markdown; the writer prepends
# it to its system prompt. If the file is missing, the writer falls back to its
# built-in generic prompt so nothing breaks before the persona is ready.
PERSONA_PATH = HERE / "brain" / "persona.md"

# How many recent published posts the writer sees as voice examples. More
# context gives more consistency but costs slightly more per call. 15 is a
# sweet spot: enough to catch rhythm, not enough to bloat the prompt.
PERSONA_RECENT_POSTS = _get_int("PERSONA_RECENT_POSTS", 15)


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
