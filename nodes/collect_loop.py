"""
NODE: Collect loop
PURPOSE: Keep the queue stocked — read the feeds on a timer, and the tweet
         stream continuously, storing anything genuinely new.
INPUT:   Nothing. It pulls from the feeds and the relay.
OUTPUT:  Rows in the items table with status 'queued'. Nothing is posted here.
DEPENDENCIES: nodes/fetch_rss.py, nodes/fetch_tweets.py, utils/db.py

TWO SPEEDS, ON PURPOSE
    Feeds are polled every POLL_MINUTES (10 by default). Sites don't publish
    faster than that, and each poll is nearly free thanks to caching.

    Tweets are watched CONTINUOUSLY, because that is where breaking news shows
    up first. Waiting up to ten minutes to notice a WatcherGuru alert would
    waste the main advantage of having the X feed at all.

    So the two run side by side as separate tasks rather than taking turns.

WHAT THIS FILE DELIBERATELY DOES NOT DO
    It does not write posts, call any AI, or send anything. Its only job is
    turning "something exists out there" into "a row in our database". Keeping
    it that simple means a crash here can never produce a half-posted message.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

import config
from nodes import dedup, fetch_rss, fetch_tweets
from utils import db, logger as log_setup, textclean

log = log_setup.get("collect")


# --- Is this article too old to be news? ---
def _is_stale(article: fetch_rss.Article) -> bool:
    """True if the article was published longer ago than we care about.

    Two real situations this catches:
      * A feed that has been abandoned but still serves its old contents.
        Blockworks' /feed is exactly this — it still responds perfectly, but its
        newest article is months old. Without this check those would have been
        published as today's news.
      * A newly added feed handing over its entire back catalogue at once.

    An article with NO date counts as fresh. We would rather post something
    slightly old than silently drop real news because a feed omitted a date.
    """
    if article.published is None:
        return False
    cutoff = datetime.now(timezone.utc) - timedelta(hours=config.ARTICLE_MAX_AGE_HOURS)
    return article.published < cutoff


# --- Store one article from a feed ---
def _store_article(article: fetch_rss.Article) -> bool:
    """Save a feed article if we haven't got it. True if it was genuinely new.

    Runs the cheap filters on the way in:
      0. is it recent enough to be news?
      1. the same link (the database refuses it outright)
      2. the same headline seen recently
    """
    if _is_stale(article):
        return False

    normalised_url = textclean.normalise_url(article.url)
    norm_title = textclean.normalise_headline(article.title)
    fingerprint = textclean.title_hash(article.title)

    item_id = db.insert_item(
        origin="rss",
        source_name=article.source_name,
        external_id=normalised_url,
        url=article.url,
        title=article.title,
        body=article.summary,
        published_at=(article.published.strftime("%Y-%m-%d %H:%M:%S")
                      if article.published else None),
        norm_title=norm_title,
        title_hash=fingerprint,
        topic_hint=article.topic,
    )

    # None means the link was already in the database — check 1 did its job.
    # This is the normal outcome for most articles on most polls.
    if item_id is None:
        return False

    # Check 2: the same headline arriving from a different address.
    if dedup.check_headline(item_id, article.title, fingerprint):
        db.set_item_status(item_id, "duplicate", "same headline already seen")
        db.bump_counter("deduped_headline")
        return False

    db.bump_counter("ingested")
    return True


# --- Store one tweet ---
def _store_tweet(tweet: fetch_tweets.Tweet) -> bool:
    """Save a tweet if we haven't got it. True if it was genuinely new.

    Tweets have no headline of their own, so we treat their opening line as one.
    That gives the duplicate checks something to work with, and it is what lets
    a tweet be matched against a news article about the same event.
    """
    title = textclean.tweet_to_title(tweet.text)
    norm_title = textclean.normalise_headline(title)
    fingerprint = textclean.title_hash(title)

    item_id = db.insert_item(
        origin="x",
        source_name=tweet.handle,
        external_id=tweet.tweet_id,
        url=tweet.url,
        title=title,
        body=tweet.text,
        image_url=tweet.image_url,
        published_at=None,   # X's own timestamp format differs; fetched_at is enough
        norm_title=norm_title,
        title_hash=fingerprint,
        topic_hint=config.X_ACCOUNTS.get(tweet.handle, ""),
    )

    if item_id is None:
        return False

    if dedup.check_headline(item_id, title, fingerprint):
        db.set_item_status(item_id, "duplicate", "same headline already seen")
        db.bump_counter("deduped_headline")
        return False

    db.bump_counter("ingested")
    return True


# --- One pass over all the feeds ---
async def collect_feeds_once() -> int:
    """Read every feed once and store what's new. Returns how many were new."""
    articles = await fetch_rss.execute()
    stale = sum(1 for article in articles if _is_stale(article))
    new_count = sum(1 for article in articles if _store_article(article))

    if stale:
        log.info("Feeds: %d offered, %d new, %d too old (over %dh)",
                 len(articles), new_count, stale, config.ARTICLE_MAX_AGE_HOURS)
    else:
        log.info("Feeds: %d article(s) offered, %d new", len(articles), new_count)
    return new_count


# --- One pass over whatever tweets are waiting ---
async def collect_tweets_once() -> int:
    """Take whatever tweets are waiting and store them. Returns how many were new."""
    tweets = await fetch_tweets.drain_once()
    new_count = sum(1 for tweet in tweets if _store_tweet(tweet))

    if tweets:
        log.info("Tweets: %d offered, %d new", len(tweets), new_count)
    return new_count


# --- The feed timer, running forever ---
async def _feed_task() -> None:
    """Poll the feeds every POLL_MINUTES, forever."""
    while True:
        try:
            await collect_feeds_once()
        except Exception as error:  # noqa: BLE001 - one bad cycle must not end the loop
            log.exception("Feed cycle failed: %s", error)
            from utils import telegram_error
            telegram_error.send_error(str(error), node_name="collect_feeds")

        await asyncio.sleep(config.POLL_MINUTES * 60)


# --- The tweet watcher, running forever ---
async def _tweet_task() -> None:
    """Watch the tweet stream continuously and store what arrives."""
    async for batch in fetch_tweets.stream():
        try:
            new_count = sum(1 for tweet in batch if _store_tweet(tweet))
            log.info("Tweets: %d arrived, %d new", len(batch), new_count)
        except Exception as error:  # noqa: BLE001
            log.exception("Could not store a batch of tweets: %s", error)
            from utils import telegram_error
            telegram_error.send_error(str(error), node_name="collect_tweets")


# --- The node's entry point ---
async def run(once: bool = False) -> None:
    """Run collection.

    Args:
        once: do a single pass over both sources and stop. Used for testing and
              by `python main.py collect --once`.
    """
    if once:
        log.info("Single collection pass...")
        feeds = await collect_feeds_once()
        tweets = await collect_tweets_once()
        log.info("Done: %d new from feeds, %d new from X", feeds, tweets)
        return

    log.info("Collecting: feeds every %d min, tweets continuously",
             config.POLL_MINUTES)
    await asyncio.gather(_feed_task(), _tweet_task())
