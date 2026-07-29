"""
NODE: Publish loop
PURPOSE: Take queued items and turn them into published posts, at a sensible pace.
INPUT:   Rows in the items table with status 'queued'.
OUTPUT:  Messages in the Telegram channel.
DEPENDENCIES: every other node — dedup, sorting, writer, editor, publisher.

THE ORDER OF WORK, AND WHY
    Every tick:
      0. Throw away anything that has gone stale.
      1. Duplicate checks 3 and 4  — cheapest filtering first
      2. Sorting                    — is it worth covering? about 7 hundredths of a cent
      3. Writer                    — the expensive step, about a tenth of a cent
      4. Editor                    — the safety check
      5. Publisher                 — pacing, then send

    Filtering comes before writing so we never pay to write something we then
    throw away. Each step narrows the field for the next.

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
from nodes import dedup, editor, publisher, sorter, writer
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
    """Run one item through the whole pipeline.

    Returns a word describing what happened, for the log and the counters:
        published | duplicate | irrelevant | low_impact | declined | retry | failed
    """
    item_id = item["id"]

    # --- Have we already done all the work for this one? ---
    # An item back in the queue with a post already approved means a previous
    # send failed. Everything up to that point is done and paid for, so go
    # straight to sending rather than repeating the duplicate checks, the
    # sorting, the writing and the editor — which would cost real money and
    # could even reach a different verdict on text that was already approved.
    existing = db.get_post_by_item(item_id)
    if existing is not None and existing["status"] == "approved":
        log.info("Item %s already has an approved post — retrying the send only", item_id)
        sent = await publisher.execute(item, existing["post_html"], existing["id"])
        return "published" if sent else "retry"

    # --- Steps 1: the remaining duplicate checks ---
    if await dedup.execute(item, with_meaning=True):
        return "duplicate"

    # --- Step 2: is this worth covering? ---
    verdict = await sorter.execute(item)
    db.set_item_sorting(item_id, verdict["topic"], verdict["importance"],
                        verdict["market"])

    # The scorer could not be reached. We do not know whether this matters, so
    # we neither publish it nor throw it away — it goes back in the queue.
    if verdict["fallback"]:
        attempts = db.bump_attempts(item_id)
        if attempts >= config.MAX_ATTEMPTS:
            db.set_item_status(item_id, "failed", "could not be scored")
            return "failed"
        return "retry"

    if not verdict["relevant"]:
        db.set_item_status(item_id, "irrelevant", verdict["reason"])
        db.bump_counter("irrelevant")
        return "irrelevant"

    # Not important enough. This is the main control on how trivial the channel
    # feels — see MIN_IMPORTANCE in config.py.
    #
    # Recorded under its own status rather than lumped in with 'irrelevant',
    # because these two piles need reading for opposite reasons: 'irrelevant' is
    # sport and opinion columns and you never want to see it again, while this
    # one is real news the channel chose not to run. If the gate is
    # mis-calibrated — in either direction — the evidence is here.
    # See: python tools/stats.py --dropped
    if verdict["importance"] < config.MIN_IMPORTANCE:
        db.set_item_status(
            item_id, "low_impact",
            f"impact {verdict['importance']}/5 (market: {verdict['market']}), below "
            f"the {config.MIN_IMPORTANCE} threshold: {verdict['reason']}",
        )
        db.bump_counter("below_importance")
        return "low_impact"

    # Re-read the row so later steps see the topic sorting just decided.
    item = db.get_item(item_id)

    # --- Step 3: produce the post text ---
    has_image = bool(item["image_url"])

    # Tweets are NOT rewritten. These accounts already write short, factual
    # posts aimed at exactly this audience, so rewriting them cost money and
    # added risk without adding anything — the rewrite step was where the model
    # invented casualty figures during testing. Passing the text through means
    # the post cannot say anything the source did not, because it IS the source.
    used_ai = True
    if item["origin"] == "x":
        post_html = writer.passthrough(item, strip_emoji=writer.is_brief(item))
        used_ai = False

        # If X truncated the tweet so badly that nothing complete survives,
        # fall back to the AI writer rather than dropping a real story.
        if len(post_html) < 40:
            log.info("Tweet %s left too little after trimming — using the writer", item_id)
            post_html = await writer.execute(item, has_image=has_image)
            used_ai = True
    else:
        post_html = await writer.execute(item, has_image=has_image)

    if not post_html:
        # Leave it queued and try again next tick. Writing can fail for reasons
        # that pass on their own — a busy model, a network blip.
        attempts = db.bump_attempts(item_id)
        if attempts >= config.MAX_ATTEMPTS:
            db.set_item_status(item_id, "failed",
                               f"the writer failed {attempts} times")
            db.bump_counter("write_failed")
            return "failed"
        return "retry"

    post_id = db.create_post(
        item_id=item_id, topic=verdict["topic"], post_html=post_html,
        image_url=item["image_url"] or "",
        writer_model=writer.MODEL if used_ai else "passthrough (no AI)",
    )
    if post_id is None:
        # A post already exists for this item, which means a previous run got
        # this far before stopping. Pick it up rather than writing a second one.
        existing = db.get_post_by_item(item_id)
        if existing is None:
            db.set_item_status(item_id, "failed", "post row went missing")
            return "failed"
        post_id, post_html = existing["id"], existing["post_html"]

    db.set_item_status(item_id, "written", "waiting on the editor")
    db.bump_counter("written")

    # --- Step 4: the editor decides ---
    decision = await editor.execute(item, post_html, post_id, verbatim=not used_ai)

    if decision["error"]:
        # We could not get a verdict. Nothing goes out without one, so put the
        # item back and try again shortly.
        attempts = db.bump_attempts(item_id)
        if attempts >= config.MAX_ATTEMPTS:
            db.set_item_status(item_id, "failed", "could not reach the editor")
            return "failed"
        db.set_item_status(item_id, "queued", "waiting to be re-judged")
        return "retry"

    if not decision["approved"]:
        db.set_post_status(post_id, "declined")
        db.set_item_status(
            item_id, "irrelevant",
            f"editor rejected it {decision['rules_broken']}: {decision['reason']}",
        )
        return "declined"

    db.set_post_status(post_id, "approved")

    # --- Step 5: send it ---
    sent = await publisher.execute(item, post_html, post_id)
    return "published" if sent else "retry"


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
