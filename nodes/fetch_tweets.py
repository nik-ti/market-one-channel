"""
NODE: Fetch tweets
PURPOSE: Read new posts from the shared tweet relay and hand them on as items.
INPUT:   Nothing — it subscribes to a Redis stream that the relay service fills.
OUTPUT:  Tweet objects (handle, text, link, image), already filtered by age,
         burst size, and whether we actually follow that account.
DEPENDENCIES: redis (the async part). Settings from config.

HOW THIS WORKS, IN PLAIN TERMS
    We do NOT connect to X ourselves. X only allows one connection, and the
    trading bot's relay service already holds it. That relay copies every tweet
    into a Redis "stream" — think of it as a shared conveyor belt that any
    number of programs can watch.

    Each program watching the belt has its own bookmark, called a consumer
    group. Ours is called "news-channel"; the trading bot's is called
    "sniper-ingest". Because the bookmarks are separate, both programs see every
    single tweet and neither can take one away from the other.

THE ONE IMPORTANT DIFFERENCE FROM THE TRADING BOT
    When the trading bot restarts, it deliberately jumps its bookmark to the end
    of the belt and ignores everything it missed. That is right for trading —
    acting on a ten-minute-old "breaking" tweet is a bug.

    A news channel wants the opposite. If we restart, the tweets that arrived
    meanwhile are still news, and skipping them leaves a hole. So we keep our
    place and catch up.

    The danger with catching up is obvious: restart after a long outage and you
    could dump hundreds of old tweets into the channel at once. Three guards
    prevent that:
        1. AGE   — anything older than X_MAX_AGE_MINUTES is binned unread.
        2. BURST — at most X_MAX_BURST tweets are kept from any one batch.
        3. The publisher's own hourly limit, as a final backstop.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

import redis.asyncio as aioredis
from redis.exceptions import ResponseError
from redis.exceptions import TimeoutError as RedisTimeoutError

import config
from utils import db, logger as log_setup

log = log_setup.get("tweets")

# Our name as an individual worker within the group. The group is the shared
# bookmark; this identifies which process is holding it.
_CONSUMER_NAME = "news-channel-1"

# How long a single read waits for new tweets before giving up and looping.
# 20 seconds means an idle channel wakes up three times a minute to check.
_BLOCK_MS = 20_000

# How long we let the network connection sit idle before declaring it dead.
# THIS MUST BE LONGER THAN THE BLOCK TIME ABOVE. If it isn't, the connection
# gets killed in the middle of a perfectly normal quiet period — a subtle bug
# the trading bot hit and documented, so we inherit the fix rather than the bug.
_SOCKET_TIMEOUT = _BLOCK_MS / 1000 + 15

# If Redis goes away, wait these many seconds between reconnection attempts.
_RECONNECT_BACKOFF = [1, 2, 5, 15, 30]

# Remembers whether we have ever connected before, which is what decides between
# "start fresh" and "catch up".
_FIRST_RUN_FLAG = "x_stream_initialised"


# --- One tweet ---
@dataclass(frozen=True)
class Tweet:
    """A single post from X, exactly as the relay publishes it."""

    tweet_id: str
    handle: str          # no leading "@"
    text: str
    created_at: str      # as X gave it; may be empty
    url: str
    media: tuple[str, ...] = field(default_factory=tuple)   # image addresses

    @property
    def image_url(self) -> str:
        """The first image, if the tweet had one. Empty string otherwise."""
        return self.media[0] if self.media else ""


# --- Turn one belt entry into a Tweet ---
def _parse_entry(fields: dict) -> Tweet | None:
    """Decode a stream entry into a Tweet, or None if it is unreadable.

    Returning None rather than raising matters: one corrupted message must never
    be able to stop the whole feed.
    """
    raw = fields.get("data")
    if not raw:
        return None
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        log.warning("Skipping unreadable stream entry: %s", str(raw)[:200])
        return None

    return Tweet(
        tweet_id=data.get("tweet_id", ""),
        handle=data.get("handle", "unknown"),
        text=data.get("text", ""),
        created_at=data.get("created_at", ""),
        url=data.get("url", ""),
        media=tuple(data.get("media", []) or ()),
    )


# --- Is this tweet too old to be news? ---
def _is_too_old(tweet: Tweet) -> bool:
    """True if the tweet is older than our freshness limit.

    A tweet with no timestamp, or an unreadable one, counts as FRESH. We would
    rather post one slightly stale item than silently drop real breaking news
    because of a date-parsing quirk.
    """
    if not tweet.created_at:
        return False
    try:
        posted = datetime.fromisoformat(tweet.created_at.replace("Z", "+00:00"))
    except ValueError:
        return False

    if posted.tzinfo is None:
        posted = posted.replace(tzinfo=timezone.utc)

    cutoff = datetime.now(timezone.utc) - timedelta(minutes=config.X_MAX_AGE_MINUTES)
    return posted < cutoff


# --- Apply all three filters to a batch ---
def _filter_batch(tweets: list[Tweet]) -> list[Tweet]:
    """Narrow a batch down to the tweets this channel actually wants.

    Three filters, in order:
      1. Is it from an account we follow? (config.X_ACCOUNTS)
      2. Is it recent enough?
      3. Is the batch too big? Keep only the newest few.
    """
    kept: list[Tweet] = []
    dropped_handle = dropped_old = 0

    for tweet in tweets:
        # The relay carries every account the TRADING BOT follows too. This
        # dictionary is our own, narrower list.
        if tweet.handle not in config.X_ACCOUNTS:
            dropped_handle += 1
            continue
        if _is_too_old(tweet):
            dropped_old += 1
            continue
        kept.append(tweet)

    # The burst guard. If a restart hands us a big backlog, keep only the newest
    # few — the rest is old news by definition.
    dropped_burst = 0
    if len(kept) > config.X_MAX_BURST:
        dropped_burst = len(kept) - config.X_MAX_BURST
        kept = kept[-config.X_MAX_BURST:]

    if dropped_handle or dropped_old or dropped_burst:
        log.info(
            "Tweets filtered: %d kept, %d not followed, %d too old, %d over burst limit",
            len(kept), dropped_handle, dropped_old, dropped_burst,
        )
    return kept


# --- Make sure our bookmark exists ---
async def _prepare_group(redis: aioredis.Redis) -> None:
    """Create our consumer group, positioned correctly for first run vs restart.

    First run ever:  start at the END of the belt. A brand-new install should not
                     replay the several thousand old tweets sitting in the buffer.
    Every run after: leave the bookmark exactly where it was, so we catch up on
                     whatever arrived while we were stopped. This is the one line
                     that differs from the trading bot, and it is deliberate.
    """
    key, group = config.TWEET_STREAM_KEY, config.TWEET_STREAM_GROUP
    first_run = db.meta_get(_FIRST_RUN_FLAG) != "yes"

    try:
        # "$" means "start from now, ignore everything already on the belt".
        await redis.xgroup_create(key, group, id="$", mkstream=True)
        log.info("Created consumer group '%s' on '%s', starting from now", group, key)
    except ResponseError as error:
        if "BUSYGROUP" not in str(error):
            raise
        # The group already exists, which is the normal case on any restart.
        # We deliberately do NOT move the bookmark here — that is what lets us
        # catch up on tweets from while we were down.
        if first_run:
            log.info("Consumer group '%s' already existed — resuming from its bookmark", group)
        else:
            log.info("Resuming consumer group '%s' from its bookmark", group)

    db.meta_set(_FIRST_RUN_FLAG, "yes")


# --- Read whatever is waiting, right now ---
async def drain_once(max_wait_ms: int = 2000) -> list[Tweet]:
    """Collect the tweets currently waiting, then return. Does not loop.

    Used by `main.py collect --once` and by the check tool. Returns an empty list
    if nothing is waiting, which is the normal case on a quiet minute.
    """
    key, group = config.TWEET_STREAM_KEY, config.TWEET_STREAM_GROUP
    redis = aioredis.from_url(
        config.REDIS_URL, decode_responses=True, socket_timeout=max_wait_ms / 1000 + 10
    )

    collected: list[Tweet] = []
    entry_ids: list[str] = []

    try:
        await _prepare_group(redis)

        # ">" means "messages not yet handed to this group".
        response = await redis.xreadgroup(
            group, _CONSUMER_NAME, {key: ">"}, count=100, block=max_wait_ms
        )
        for _stream, entries in response or []:
            for entry_id, fields in entries:
                entry_ids.append(entry_id)
                tweet = _parse_entry(fields)
                if tweet is not None:
                    collected.append(tweet)

        # Tell Redis we have these. Safe to do here because the caller stores
        # them immediately, and storing the same tweet twice is impossible
        # thanks to the UNIQUE rule in the database.
        if entry_ids:
            await redis.xack(key, group, *entry_ids)

    except Exception as error:  # noqa: BLE001 - a Redis blip must not stop collection
        log.warning("Could not read the tweet stream: %s", error)
    finally:
        try:
            await redis.aclose()
        except Exception:  # noqa: BLE001
            pass

    return _filter_batch(collected)


# --- Watch the belt continuously ---
async def stream():
    """Yield batches of tweets as they arrive, reconnecting forever.

    This is what runs in the live service. It gives us tweets within seconds of
    them being posted, rather than waiting for the next feed-polling cycle.

    Yields LISTS of tweets rather than one at a time, so the age and burst
    filters can look at a whole batch together.
    """
    key, group = config.TWEET_STREAM_KEY, config.TWEET_STREAM_GROUP
    backoff_index = 0
    prepared = False

    while True:
        redis = aioredis.from_url(
            config.REDIS_URL, decode_responses=True, socket_timeout=_SOCKET_TIMEOUT
        )
        try:
            # Set the bookmark up only on the first successful connection. On a
            # later reconnection after a brief outage we must not touch it —
            # ">" already resumes from the right place, and re-preparing could
            # lose the very messages the reconnection was meant to recover.
            if not prepared:
                await _prepare_group(redis)
                prepared = True
                log.info("Watching tweet stream '%s' as group '%s'", key, group)
            else:
                log.info("Reconnected to the tweet stream — resuming from our bookmark")
            backoff_index = 0

            while True:
                try:
                    response = await redis.xreadgroup(
                        group, _CONSUMER_NAME, {key: ">"}, count=50, block=_BLOCK_MS
                    )
                except RedisTimeoutError:
                    continue    # a quiet 20 seconds; completely normal
                except ResponseError as error:
                    # If Redis was restarted or flushed, our group can vanish.
                    if "NOGROUP" in str(error):
                        log.warning("Consumer group '%s' disappeared — recreating", group)
                        await _prepare_group(redis)
                        continue
                    raise

                if not response:
                    continue

                batch: list[Tweet] = []
                entry_ids: list[str] = []
                for _stream, entries in response:
                    for entry_id, fields in entries:
                        entry_ids.append(entry_id)
                        tweet = _parse_entry(fields)
                        if tweet is not None:
                            batch.append(tweet)

                wanted = _filter_batch(batch)
                if wanted:
                    yield wanted

                # Acknowledge only after the caller has finished storing them.
                # If we crash before this point, Redis re-delivers on restart and
                # the database's UNIQUE rule turns the retry into a harmless no-op.
                if entry_ids:
                    await redis.xack(key, group, *entry_ids)

        except asyncio.CancelledError:
            raise
        except Exception as error:  # noqa: BLE001 - reconnect rather than give up
            delay = _RECONNECT_BACKOFF[min(backoff_index, len(_RECONNECT_BACKOFF) - 1)]
            log.warning("Tweet stream error: %s — reconnecting in %ss", error, delay)
            backoff_index += 1
            await asyncio.sleep(delay)
        finally:
            try:
                await redis.aclose()
            except Exception:  # noqa: BLE001
                pass
