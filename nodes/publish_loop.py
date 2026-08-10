"""
NODE: Publish loop
PURPOSE: Take queued items and turn them into published posts, at a sensible pace.
INPUT:   Rows in the items table with status 'queued'.
OUTPUT:  Messages in the Telegram channel.
DEPENDENCIES: brain.graph (runs dedup/sorter/writer/editor/publisher), publisher.

THE ORDER OF WORK, AND WHY
    Every tick:
      0. Throw away anything that has gone stale.
      1. Run the item through brain.graph.run_item(), which handles
         relationship detection, sorting, writing, editing, and sending.

    The graph narrows the field the same way the old hand-written pipeline did,
    but the state machine is explicit and reusable (tools/test_brain.py uses the
    same graph in dry-run mode).

WHY STEP 0 IS NOT OPTIONAL
    The posting limits mean we can publish at most 12 items an hour. The feeds
    supply far more than that at busy times. So the queue would grow without
    end, and eventually the channel would be posting this morning's news at
    midnight.

    Another project on this machine has 2,540 items stuck in exactly this state,
    growing by about 400 a day, because it has no expiry. Here, news simply goes
    off: anything not posted within QUEUE_TTL_MINUTES is dropped. Combined with
    posting the most important first, that means when more news arrives than we
    can carry, the best and freshest wins and the rest quietly dies. For news
    that is the correct behaviour.

CRASH SAFETY
    Every step is a recorded change of state in the database. If the program
    stops halfway through, the next start picks up exactly where it left off.
    Nothing is ever half-posted, because a post can only be created once per
    item — the database enforces that.
"""

from __future__ import annotations

import asyncio

import config
from brain import graph as brain
from nodes import publisher
from utils import db, logger as log_setup

log = log_setup.get("publish")


# --- Step 0: throw out anything past its sell-by date ---
def _expire_stale() -> None:
    """Drop queued items that have waited too long, and trim an oversized queue."""
    expired = db.expire_stale_items(config.QUEUE_TTL_MINUTES)
    trimmed = db.trim_queue(config.MAX_QUEUE_SIZE)

    if expired:
        log.info("Dropped %d item(s) that waited longer than %d minutes",
                 expired, config.QUEUE_TTL_MINUTES)
        db.bump_counter("expired", expired)
    if trimmed:
        log.info("Dropped %d item(s) because the queue was over %d",
                 trimmed, config.MAX_QUEUE_SIZE)
        db.bump_counter("expired", trimmed)


# --- Take one item all the way from queued to published ---
async def process_item(item) -> str:
    """Run one item through the brain graph.

    The graph (brain/graph.py) replaces the old hand-written pipeline with an
    explicit state machine: relationship check, sorter, writer/editor loop, and
    publisher. It records the same statuses and counters the old loop did.

    Returns a word describing what happened, for the log and the counters:
        published | duplicate | irrelevant | low_impact | declined | retry | failed
    """
    item_id = item["id"]

    # --- Have we already done all the work for this one? ---
    # An item back in the queue with a post already approved means a previous
    # send failed. Everything up to that point is done and paid for, so go
    # straight to sending rather than repeating the entire graph.
    existing = db.get_post_by_item(item_id)
    if existing is not None and existing["status"] == "approved":
        log.info("Item %s already has an approved post — retrying the send only", item_id)
        sent = await publisher.execute(item, existing["post_html"], existing["id"])
        return "published" if sent else "retry"

    # --- Run the editorial brain ---
    state = await brain.run_item(item, dry_run=False)
    return state.get("outcome", "failed")


# --- One pass of the publishing cycle ---
async def publish_once(limit: int | None = None) -> dict[str, int]:
    """Do one round: expire stale items, then process a few. Returns the tally."""
    _expire_stale()

    allowed, reason = publisher.check_limits()
    if not allowed:
        log.info("Not posting this round: %s", reason)
        return {}

    batch_size = limit or config.MAX_POSTS_PER_TICK
    outcomes: dict[str, int] = {}
    published = 0

    # Look at more items than we intend to publish, because most will be
    # filtered out along the way — duplicates, off-topic, rejected.
    candidates = db.next_queued_items(batch_size * 8)
    if not candidates:
        return {}

    for item in candidates:
        if published >= batch_size:
            break

        try:
            outcome = await process_item(item)
        except Exception as error:  # noqa: BLE001 - one bad item must not stop the rest
            log.exception("Item %s blew up: %s", item["id"], error)
            db.bump_attempts(item["id"])
            outcome = "error"

        outcomes[outcome] = outcomes.get(outcome, 0) + 1

        if outcome == "published":
            published += 1
            if published < batch_size:
                await publisher.pause_between_sends()

            # Re-check the limits after each send — the hourly cap may now be
            # reached even though we started the round under it.
            allowed, reason = publisher.check_limits()
            if not allowed:
                log.info("Stopping this round: %s", reason)
                break

    if outcomes:
        summary = ", ".join(f"{count} {name}" for name, count in sorted(outcomes.items()))
        log.info("Round finished: %s", summary)
    return outcomes


# --- The node's entry point ---
async def run(once: bool = False, limit: int | None = None) -> None:
    """Run the publishing loop.

    Args:
        once:  do a single round and stop.
        limit: publish at most this many in the round (testing).
    """
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
