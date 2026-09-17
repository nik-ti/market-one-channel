"""One function per station of the editorial graph.

Thin wrappers: each calls a module in nodes/ and records the result into the
graph state. The intelligence lives there; this file is the assembly line.

DRY RUN takes an item through the same decisions but publishes nothing, writes
no posts and changes no statuses — that is how tools/test_brain.py rehearses the
live pipeline. Duplicate checks DO still mark items: a confirmed duplicate is a
fact, not a rehearsal side effect.

A model that cannot be reached bumps the item's attempt counter and leaves it
queued. After config.MAX_ATTEMPTS it is marked failed.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import config
from brain import persona_loader
from nodes import dedup, editor, publisher, sorter, stories, writer
from utils import db, logger as log_setup

log = log_setup.get("brain")

# Problems of EXECUTION, which one more draft can fix. The other five rules
# (NO_NEWS, WRONG_TOPIC, HYPE, INJECTION, UNSAFE) are problems of CONTENT — no
# rewrite of the same source fixes those, so they are dropped on the spot.
FIXABLE_RULES = frozenset({
    "FACTUAL_DRIFT", "OVERCLAIM", "INCOMPLETE", "BROKEN_HTML", "TOO_LONG",
    "EMPTY_BODY",
})


# Conditional edges in brain/graph.py call these. A node sets state["outcome"]
# when the item's journey is over; an empty outcome means "carry on".

def route_after_dedup(state: dict) -> str:
    return "drop" if state.get("outcome") else "sort"


def route_after_sorter(state: dict) -> str:
    return "end" if state.get("outcome") else "place"


def route_after_place(state: dict) -> str:
    # Placement still runs when the pacing limits forbid posting. An item that
    # is not filed into its story before QUEUE_TTL_MINUTES expires takes its
    # content out of that story with it, and the story never learns of it.
    return "end" if state.get("place_only") else "gate"


def route_after_gate(state: dict) -> str:
    return "end" if state.get("outcome") else "write"


def route_after_writer(state: dict) -> str:
    return "end" if state.get("outcome") else "edit"


def route_after_editor(state: dict) -> str:
    if state.get("outcome"):
        return "end"
    return "rewrite" if state.get("rewrite_requested") else "publish"


async def dedup_check(state: dict) -> dict[str, Any]:
    """Drop an item the channel has already covered.

    Only two answers now. "This continues something" used to be a third, handled
    by a parallel branch of the graph; the story layer answers that question
    better, because it sees every open story rather than one candidate pair.
    """
    item = state["item"]
    if state.get("sweep"):
        return {}          # judged when it first arrived
    verdict, _matched_id, score = await dedup.classify(item, with_meaning=True)

    if verdict != "duplicate":
        return {}

    if not state.get("dry_run", False):
        db.set_item_status(item["id"], "duplicate", "same event as a recent story")
        db.bump_counter(
            "deduped_fuzzy" if score >= 100
            else "deduped_meaning" if score >= config.COSINE_CERTAIN
            else "deduped_judge"
        )
    return {"outcome": "duplicate"}


async def sorter_node(state: dict) -> dict[str, Any]:
    """Score the item and apply the importance gate."""
    item = state["item"]
    item_id = item["id"]
    dry = state.get("dry_run", False)

    if state.get("sweep"):
        return {"sorter_verdict": {"topic": item.get("topic") or "", "importance": item.get("importance") or 0,
                                   "market": item.get("market") or "", "relevant": True,
                                   "reason": "roundup", "fallback": False}}

    verdict = await sorter.execute(item)

    # Unreachable scorer: we do not know if it matters, so requeue rather than
    # publish or drop.
    if verdict["fallback"]:
        if dry:
            return {"sorter_verdict": verdict, "outcome": "sorter_error"}
        attempts = db.bump_attempts(item_id)
        if attempts >= config.MAX_ATTEMPTS:
            db.set_item_status(item_id, "failed", "could not be scored")
            return {"sorter_verdict": verdict, "outcome": "failed"}
        return {"sorter_verdict": verdict, "outcome": "retry"}

    if not dry:
        db.set_item_sorting(item_id, verdict["topic"], verdict["importance"],
                            verdict["market"])

    if not verdict["relevant"]:
        if not dry:
            db.set_item_status(item_id, "irrelevant", verdict["reason"])
            db.bump_counter("irrelevant")
        return {"sorter_verdict": verdict, "outcome": "irrelevant"}

    # The importance gate — the main control on how trivial the channel feels.
    min_importance = config.MIN_IMPORTANCE

    if verdict["importance"] < min_importance:
        if not dry:
            db.set_item_status(
                item_id, "low_impact",
                f"impact {verdict['importance']}/5 (market: {verdict['market']}), below "
                f"the {min_importance} threshold: {verdict['reason']}",
            )
            db.bump_counter("below_importance")
        return {"sorter_verdict": verdict, "outcome": "low_impact"}

    # Carry the verdict on the item so the writer and editor see the topic the
    # sorter decided.
    updated_item = dict(item)
    updated_item["topic"] = verdict["topic"]
    updated_item["importance"] = verdict["importance"]
    updated_item["market"] = verdict["market"]

    return {"sorter_verdict": verdict, "item": updated_item}


# =============================================================================
# STATION 3: which story is this, and has it moved?
# =============================================================================

async def place_story_node(state: dict) -> dict[str, Any]:
    """Put the item into a running story, or open one for it.

    The only station that sees the incoming item and the channel's own output
    together. Fails open into a NEW story: a wrongly separated item is one extra
    post, which is what the channel did for every item before this existed.
    """
    item = state["item"]
    now = datetime.now(timezone.utc)
    dry = state.get("dry_run", False)

    # Resume first, before paying for a model call. A writer or editor retry
    # leaves the item queued with its story_id already set, and re-placing it
    # would either burn a call or file it somewhere else than the first time.
    existing_id = item.get("story_id")
    if existing_id:
        row = db.get_story(existing_id)
        if row is not None and row["status"] == "live":
            story = stories.load_one(existing_id, now)
            if story is not None:
                log.info("Item %s resumes story %s", item["id"], existing_id)
                return {"story": story, "story_id": story.id}

    open_stories = stories.load_open(now)
    home, why = await stories.place(item, open_stories, now)

    if home is None:
        headline = (item["title"] or "")[:200]
        if dry:
            story = stories.Story(id=0, headline=headline, summary=headline)
            story.absorb(dict(item), now)
        else:
            story_id = db.create_story(headline=headline, summary=headline,
                                       item_id=item["id"], at=db.now_iso())
            story = stories.load_one(story_id, now)
            db.bump_counter("story_opened")
        log.info("Item %s opens story %s: %s", item["id"], story.id, why[:120])
    else:
        if dry:
            home.absorb(dict(item), now)
            story = home
        else:
            db.attach_item_to_story(item["id"], home.id)
            # Re-read rather than patch in memory, so the gate and the writer
            # see exactly the rows the database holds — including the topic and
            # importance the sorter wrote onto older pending items.
            story = stories.load_one(home.id, now)
            db.bump_counter("story_joined")
        log.info("Item %s joins story %s: %s", item["id"], story.id, why[:120])

    if state.get("place_only"):
        # Left queued on purpose: next round resumes this story for free.
        return {"story": story, "story_id": story.id, "outcome": "placed"}

    return {"story": story, "story_id": story.id}


async def story_gate_node(state: dict) -> dict[str, Any]:
    """Decide whether the story has moved enough to be worth a post.

    Three answers. "post" folds the whole story into the writer's source;
    "hold" leaves the item as fuel for the story's next post; "not this story"
    undoes a bad placement instead of silencing what it misfiled — that last one
    is why a mis-placed "Fed rate hike odds 66%" is not lost any more.
    """
    item = state["item"]
    story = state["story"]
    now = datetime.now(timezone.utc)
    dry = state.get("dry_run", False)

    verdict = await stories.should_post(story, now)

    if verdict["verdict"] == "not_this_story":
        log.info("Item %s does not belong in story %s (%s) — giving it its own",
                 item["id"], story.id, verdict["reason"][:100])
        headline = (item["title"] or "")[:200]
        if dry:
            story.eject(dict(item))
            story = stories.Story(id=0, headline=headline, summary=headline)
            story.absorb(dict(item), now)
        else:
            db.detach_item_from_story(item["id"])
            story_id = db.create_story(headline=headline, summary=headline,
                                       item_id=item["id"], at=db.now_iso())
            story = stories.load_one(story_id, now)
            db.bump_counter("story_ejected")
        # A story with no posts always speaks, so this cannot end in silence.
        verdict = await stories.should_post(story, now)

    if verdict["verdict"] != "post":
        if not dry:
            db.set_item_status(item["id"], "held",
                               f"story {story.id}: {verdict['reason']}")
            db.bump_counter("story_held")
        log.info("Holding item %s on story %s: %s",
                 item["id"], story.id, verdict["reason"][:120])
        return {"outcome": "held", "story": story, "story_id": story.id,
                "gate_reason": verdict["reason"]}

    # Folding the story into state["item"] is the one seam: every station after
    # this keeps working on "an item" and needs to know nothing about stories.
    return {
        "item": stories.as_source(story),
        "trigger_item_id": item["id"],
        "story": story,
        "story_id": story.id,
        "story_angle": verdict["angle"],
        "story_brief": stories.brief_for_writer(story, verdict["angle"]),
    }


async def writer_node(state: dict) -> dict[str, Any]:
    """Produce the post text from the story the gate approved.

    state["item"] is no longer one wire item: the gate replaced it with the
    story's pending items folded into one source. The writer's rules are
    unchanged and still bind — every fact must appear in the text it is given —
    but that text is now the whole story rather than one wire.

    On a rewrite pass state["editor_feedback"] carries the rejection reason and
    state["post_id"] points at the existing draft, whose text is REPLACED rather
    than a second post row being created.
    """
    item = state["item"]
    item_id = item["id"]
    dry = state.get("dry_run", False)
    has_image = bool(item.get("image_url") or item.get("video_url"))
    feedback = state.get("editor_feedback") or ""
    existing_post_id = state.get("post_id")

    # Voice context for the writer: the persona file plus recent published posts.
    # These are loaded inside the node rather than in the graph state because
    # they do not need to survive across items and are not part of routing.
    persona = persona_loader.load_persona()
    recent_posts = persona_loader.get_recent_posts()

    used_ai = True
    writer_kwargs = {
        "has_image": has_image,
        "editor_feedback": feedback,
        "persona": persona,
        "recent_posts": recent_posts,
        "brief": state.get("story_brief", ""),
    }

    post_html = await writer.execute(item, **writer_kwargs)

    if not post_html:
        # Writing can fail for reasons that pass on their own. Leave it queued.
        if dry:
            return {"outcome": "write_failed", "editor_feedback": "",
                    "rewrite_requested": False}
        attempts = db.bump_attempts(item_id)
        if attempts >= config.MAX_ATTEMPTS:
            db.set_item_status(item_id, "failed",
                               f"the writer failed {attempts} times")
            db.bump_counter("write_failed")
            return {"outcome": "failed"}
        return {"outcome": "retry"}

    if dry:
        # No post row in a rehearsal — the editor runs with record=False.
        return {"post_html": post_html, "used_ai": used_ai, "post_id": 0,
                "editor_feedback": "", "rewrite_requested": False}

    if existing_post_id:
        # Rewrite pass: the draft row already exists.
        db.update_post_text(existing_post_id, post_html)
        post_id = existing_post_id
    else:
        post_id = db.create_post(
            item_id=item_id,
            topic=item.get("topic") or state["sorter_verdict"]["topic"],
            post_html=post_html, image_url=item.get("image_url") or "",
            writer_model=writer.MODEL,
        )
        if post_id is None:
            # A previous run got this far before stopping. Reuse its post.
            existing = db.get_post_by_item(item_id)
            if existing is None:
                db.set_item_status(item_id, "failed", "post row went missing")
                return {"outcome": "failed"}
            post_id, post_html = existing["id"], existing["post_html"]

        db.set_item_status(item_id, "written", "waiting on the editor")
        db.bump_counter("written")

    return {"post_html": post_html, "used_ai": used_ai, "post_id": post_id,
            "editor_feedback": "", "rewrite_requested": False}


# =============================================================================
# STATION 5: the editor decides
# =============================================================================

async def editor_node(state: dict) -> dict[str, Any]:
    """Judge the finished post against its source. Fails closed.

    A rejection naming ONLY fixable rules goes back to the writer once with the
    reason. The post is not marked declined until a rejection is final, so a
    rejected-then-fixed draft never shows up in the decline statistics.
    """
    item = state["item"]
    dry = state.get("dry_run", False)
    rewrite_count = state.get("rewrite_count", 0)

    # A story's fourth post pointing back at what the channel already said is
    # not the writer inventing facts — but without the earlier post in front of
    # it, that is exactly what the editor sees.
    story = state.get("story")
    parent_post = story.posts[-1] if story and story.posts else ""

    decision = await editor.execute(
        item, state["post_html"], state["post_id"],
        record=not dry,
        attempt=rewrite_count + 1,
        parent_post=parent_post,
    )

    if decision["error"]:
        # No verdict means nothing is published.
        if dry:
            return {"editor_verdict": decision, "outcome": "editor_error",
                    "rewrite_requested": False}
        attempts = db.bump_attempts(item["id"])
        if attempts >= config.MAX_ATTEMPTS:
            db.set_item_status(item["id"], "failed", "could not reach the editor")
            return {"editor_verdict": decision, "outcome": "failed",
                    "rewrite_requested": False}
        db.set_item_status(item["id"], "queued", "waiting to be re-judged")
        return {"editor_verdict": decision, "outcome": "retry",
                "rewrite_requested": False}

    if not decision["approved"]:
        rules = set(decision["rules_broken"])

        # Fixable and not yet rewritten: back to the writer with the reason.
        if rules and rules <= FIXABLE_RULES and rewrite_count < config.MAX_REWRITES:
            feedback = f"{', '.join(decision['rules_broken'])}: {decision['reason']}"
            log.info("Editor asked for a rewrite of item %s: %s", item["id"], feedback[:150])
            if not dry:
                db.set_item_status(item["id"], "written",
                                   f"editor asked for a rewrite: {feedback[:200]}")
            return {"editor_verdict": decision, "editor_feedback": feedback,
                    "rewrite_count": rewrite_count + 1, "rewrite_requested": True}

        # Final rejection.
        if not dry:
            db.set_post_status(state["post_id"], "declined")
            db.set_item_status(
                item["id"], "irrelevant",
                f"editor rejected it {decision['rules_broken']}: {decision['reason']}",
            )
        return {"editor_verdict": decision, "outcome": "declined",
                "rewrite_requested": False}

    if not dry:
        db.set_post_status(state["post_id"], "approved")

    return {"editor_verdict": decision, "rewrite_requested": False}


# =============================================================================
# STATION 6: send it
# =============================================================================

async def publish_node(state: dict) -> dict[str, Any]:
    """Send the approved post, then book it against its story. Dry-run stops here."""
    if state.get("dry_run"):
        return {"outcome": "approved"}

    item = state["item"]
    story = state.get("story")
    reply_to = story.first_message_id if story and story.posts else None
    sent = await publisher.execute(item, state["post_html"], state["post_id"],
                                   reply_to_message_id=reply_to)

    if sent and state.get("story_id"):
        # Only after the send. Booking marks every other item of the story as
        # covered, and doing that for a message that never arrived would bury
        # their content with nothing published in its place.
        db.record_story_post(
            state["story_id"], item["id"],
            persona_loader.visible_text(state["post_html"]),
        )

    return {"outcome": "published" if sent else "retry"}
