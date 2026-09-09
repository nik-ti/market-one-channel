"""Turns queued items into published posts, at a sensible pace.

Each tick expires stale items, then runs the rest through brain.graph.

EXPIRY IS NOT OPTIONAL. The feeds supply far more than the hourly posting limit,
so without it the queue grows forever and the channel ends up posting this
morning's news at midnight. Another project on this machine has 2,540 items
stuck in exactly that state. Anything older than QUEUE_TTL_MINUTES is dropped:
when more news arrives than we can carry, the best and freshest wins.

Every step is a recorded state change in the database, so a crash resumes where
it left off and nothing is ever half-posted.
"""

from __future__ import annotations

import asyncio

import config
from brain import graph as brain
from nodes import publisher
from utils import db, logger as log_setup

log = log_setup.get("publish")


def _expire_stale() -> None:
    """Drop queued items that have waited too long, and end stories that are over."""
    expired = db.expire_stale_items(config.QUEUE_TTL_MINUTES)
    trimmed = db.trim_queue(config.MAX_QUEUE_SIZE)

    for story in db.close_stale_stories(config.STORY_IDLE_HOURS, config.STORY_MAX_HOURS):
        if story["waiting"]:
            # The one way this design can quietly drop something: a story that
            # went silent while still holding unposted items. Visible on purpose.
            log.info("Closed story %s with %d item(s) never covered: %s",
                     story["id"], story["waiting"], story["headline"][:70])
        else:
            log.info("Closed story %s: %s", story["id"], story["headline"][:70])

    if expired:
        log.info("Dropped %d item(s) that waited longer than %d minutes",
                 expired, config.QUEUE_TTL_MINUTES)
        db.bump_counter("expired", expired)
    if trimmed:
        log.info("Dropped %d item(s) because the queue was over %d",
                 trimmed, config.MAX_QUEUE_SIZE)
        db.bump_counter("expired", trimmed)


async def process_item(item, place_only: bool = False) -> str:
    """Run one item through the brain graph.

    Returns one word: published | duplicate | irrelevant | low_impact | held |
    placed | declined | retry | failed.
    """
    item_id = item["id"]

    # A queued item with an already-approved post means a previous send failed.
    # That work is paid for, so go straight to sending.
    existing = db.get_post_by_item(item_id)
    if existing is not None and existing["status"] == "approved" and not place_only:
        log.info("Item %s already has an approved post — retrying the send only", item_id)
        sent = await publisher.execute(item, existing["post_html"], existing["id"])
        if sent and item["story_id"]:
            # Same booking the graph does. Without it a crash-then-resume sends
            # the post and leaves the story's other items held forever.
            from brain import persona_loader
            db.record_story_post(item["story_id"], item_id,
                                 persona_loader.visible_text(existing["post_html"]))
        return "published" if sent else "retry"

    state = await brain.run_item(item, dry_run=False, place_only=place_only)
    return state.get("outcome", "failed")


async def publish_once(limit: int | None = None) -> dict[str, int]:
    """Do one round: expire stale items, then process a few. Returns the tally."""
    _expire_stale()

    # Not being allowed to POST is not a reason to skip the round. Items still
    # have to be filed into their stories, or they expire and the story never
    # learns of them. Those rounds stop at placement and leave items queued.
    allowed, reason = publisher.check_limits()
    if not allowed:
        log.info("Not posting this round (%s) — filing into stories only", reason)

    batch_size = limit or config.MAX_POSTS_PER_TICK
    outcomes: dict[str, int] = {}
    published = 0
    placed = 0

    # Look at more than we intend to publish; most get filtered out.
    # Selection is by importance; PROCESSING is chronological, because a story
    # built newest-first posts its ending and folds the beginning in afterwards.
    candidates = sorted(db.next_queued_items(batch_size * 8), key=lambda r: r["id"])
    if not candidates:
        return {}

    # One item at a time, deliberately: two placed concurrently would each see
    # the same open stories and each open one for the same news.
    for item in candidates:
        if allowed and published >= batch_size:
            break
        if not allowed and placed >= batch_size * 2:
            break

        try:
            outcome = await process_item(item, place_only=not allowed)
        except Exception as error:  # noqa: BLE001 - one bad item must not stop the rest
            log.exception("Item %s blew up: %s", item["id"], error)
            db.bump_attempts(item["id"])
            outcome = "error"

        outcomes[outcome] = outcomes.get(outcome, 0) + 1
        if outcome == "placed":
            placed += 1

        if outcome == "published":
            published += 1
            if published < batch_size:
                await publisher.pause_between_sends()

            allowed, reason = publisher.check_limits()
            if not allowed:
                log.info("Stopping this round: %s", reason)
                break

    if outcomes:
        summary = ", ".join(f"{count} {name}" for name, count in sorted(outcomes.items()))
        log.info("Round finished: %s", summary)
    return outcomes


async def run(once: bool = False, limit: int | None = None) -> None:
    """Run the publishing loop. `once` does a single round and stops."""
    if once:
        log.info("Single publishing round...")
        await publish_once(limit=limit)
        return

    log.info("Publishing: checking every %ds, up to %d/hour",
             config.PUBLISH_TICK_SECONDS, config.MAX_POSTS_PER_HOUR)

    while True:
        try:
            await publish_once(limit=limit)
        except Exception as error:  # noqa: BLE001
            log.exception("Publishing round failed: %s", error)
            from utils import telegram_error
            telegram_error.send_error(str(error), node_name="publish_loop")

        await asyncio.sleep(config.PUBLISH_TICK_SECONDS)
