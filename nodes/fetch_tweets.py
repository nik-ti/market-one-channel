"""Reads new posts from the shared tweet relay and hands them on as items.

We do NOT connect to X ourselves — X allows one connection and the trading bot's
relay holds it, copying every tweet into a Redis stream. Each program watching
that stream has its own bookmark (a consumer group), so both see every tweet and
neither can take one from the other.

UNLIKE THE TRADING BOT, we keep our place across a restart instead of jumping to
the end. Acting on a ten-minute-old "breaking" tweet is a bug when trading; for
a news channel, skipping what arrived during a restart leaves a hole. Three
guards stop a long outage dumping hundreds of old tweets at once: X_MAX_AGE_
MINUTES bins anything stale unread, X_MAX_BURST caps one batch, and the
publisher's hourly limit is the backstop.
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
