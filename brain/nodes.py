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

from typing import Any

import config
from brain import persona_loader
from nodes import dedup, editor, publisher, sorter, writer
from utils import db, logger as log_setup

log = log_setup.get("brain")

# Problems of EXECUTION, which one more draft can fix. The other five rules
# (NO_NEWS, WRONG_TOPIC, HYPE, INJECTION, UNSAFE) are problems of CONTENT — no
# rewrite of the same source fixes those, so they are dropped on the spot.
FIXABLE_RULES = frozenset({
    "FACTUAL_DRIFT", "OVERCLAIM", "INCOMPLETE", "BROKEN_HTML", "TOO_LONG",
})


# Conditional edges in brain/graph.py call these. A node sets state["outcome"]
# when the item's journey is over; an empty outcome means "carry on".

def route_after_relationship(state: dict) -> str:
    if state.get("outcome"):
        return "drop"
    if state.get("relationship") == "continuation":
        return "continuation"
    return "sort"


def route_after_sorter(state: dict) -> str:
    if state.get("outcome"):
        return "end"
    if state.get("relationship") == "continuation":
        return "continuation"
    return "write"


def route_after_writer(state: dict) -> str:
    return "end" if state.get("outcome") else "edit"


def route_after_editor(state: dict) -> str:
    if state.get("outcome"):
        return "end"
    if state.get("rewrite_requested"):
        # A continuation that needs fixing should keep its parent context.
        if state.get("relationship") == "continuation":
            return "rewrite_continuation"
        return "rewrite"
    return "publish"


async def relationship_check(state: dict) -> dict[str, Any]:
    """Classify the item as duplicate, continuation, or new.

    A continuation is threaded as a reply to the story it continues.
    """
    item = state["item"]
    item_id = item["id"]
    dry = state.get("dry_run", False)

    if dry:
        verdict, matched_id, score = await dedup.classify(item, with_meaning=True)
    else:
        # Same classify, but statuses and counters are recorded here.
        verdict, matched_id, score = await dedup.classify(item, with_meaning=True)
        if verdict == "duplicate":
            db.set_item_status(item_id, "duplicate", "same event as a recent story")
            db.bump_counter("deduped_fuzzy" if score >= 100 else "deduped_meaning" if score >= config.COSINE_CERTAIN else "deduped_judge")
        elif verdict == "continuation":
            db.set_item_status(item_id, "continuation",
                               f"continues item {matched_id} (score {score:.3f})")
            db.bump_counter("continuation_detected")
            if matched_id:
                db.set_item_continuation(item_id, matched_id)

    if verdict == "duplicate":
        return {"relationship": "duplicate", "outcome": "duplicate"}
    if verdict == "continuation":
        return {"relationship": "continuation", "matched_item_id": matched_id, "similarity_score": score}

    return {"relationship": "new"}


async def fetch_parent(state: dict) -> dict[str, Any]:
    """Look up the Telegram message a continuation will reply to.

    If the older item was never published a reply is impossible, so it falls
    back to a new standalone story.
    """
    matched_id = state.get("matched_item_id")
    if not matched_id:
        return {"relationship": "new"}

    parent = db.get_published_post_for_item(matched_id)
    if parent is None:
        log.info("Continuation item %s points to unpublished item %s — "
                 "falling back to new story", state["item"]["id"], matched_id)
        return {"relationship": "new"}

    return {
        "reply_to_message_id": parent["telegram_message_id"],
        "parent_post_html": parent["post_html"],
    }


async def sorter_node(state: dict) -> dict[str, Any]:
    """Score the item and apply the importance gate."""
    item = state["item"]
    item_id = item["id"]
    dry = state.get("dry_run", False)

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
    # Continuations get a lower bar: the parent story was already vetted, so a
    # genuine new development of it can post at importance 3.
    is_continuation = state.get("relationship") == "continuation"
    min_importance = (config.CONTINUATION_MIN_IMPORTANCE
                      if is_continuation else config.MIN_IMPORTANCE)

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
# STATION 3: write the post
# =============================================================================

async def writer_node(state: dict) -> dict[str, Any]:
    """Produce the post text. Everything goes through the writer, tweets included.

    Tweets used to bypass it via writer.passthrough(), which was safe but sent a
    third of the channel out in the source account's voice. What keeps the
    rewrite honest is LENGTH_RULE_BRIEF, which forbids adding any fact not in
    the source, plus the editor checking the result against it.

    On a rewrite pass state["editor_feedback"] carries the rejection reason and
    state["post_id"] points at the existing draft, whose text is REPLACED rather
    than a second post row being created.
    """
    item = state["item"]
    item_id = item["id"]
    dry = state.get("dry_run", False)
    has_image = bool(item.get("image_url"))
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
            item_id=item_id, topic=state["sorter_verdict"]["topic"],
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


async def continuation_writer_node(state: dict) -> dict[str, Any]:
    """Write a continuation as a follow-up to the original post.

    The writer sees the parent post and is told to add only what is new.
    """
    item = state["item"]
    item_id = item["id"]
    dry = state.get("dry_run", False)
    has_image = bool(item.get("image_url"))
    feedback = state.get("editor_feedback") or ""
    existing_post_id = state.get("post_id")

    persona = persona_loader.load_persona()
    recent_posts = persona_loader.get_recent_posts()

    post_html = await writer.execute_continuation(
        item,
        parent_post_html=state.get("parent_post_html", ""),
        has_image=has_image,
        editor_feedback=feedback,
        persona=persona,
        recent_posts=recent_posts,
    )

    if not post_html:
        if dry:
            return {"outcome": "write_failed", "editor_feedback": "",
                    "rewrite_requested": False}
        attempts = db.bump_attempts(item_id)
        if attempts >= config.MAX_ATTEMPTS:
            db.set_item_status(item_id, "failed",
                               f"the continuation writer failed {attempts} times")
            db.bump_counter("write_failed")
            return {"outcome": "failed"}
        return {"outcome": "retry"}

    if dry:
        return {"post_html": post_html, "used_ai": True, "post_id": 0,
                "editor_feedback": "", "rewrite_requested": False}

    if existing_post_id:
        db.update_post_text(existing_post_id, post_html)
        post_id = existing_post_id
    else:
        post_id = db.create_post(
            item_id=item_id, topic=state["sorter_verdict"]["topic"],
            post_html=post_html, image_url=item.get("image_url") or "",
            writer_model=writer.MODEL,
        )
        if post_id is None:
            existing = db.get_post_by_item(item_id)
            if existing is None:
                db.set_item_status(item_id, "failed", "post row went missing")
                return {"outcome": "failed"}
            post_id, post_html = existing["id"], existing["post_html"]

        db.set_item_status(item_id, "written", "waiting on the editor (continuation)")
        db.bump_counter("written")

    return {"post_html": post_html, "used_ai": True, "post_id": post_id,
            "editor_feedback": "", "rewrite_requested": False}


# =============================================================================
# STATION 4: the editor decides
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

    decision = await editor.execute(
        item, state["post_html"], state["post_id"],
        record=not dry, verbatim=not state["used_ai"],
        attempt=rewrite_count + 1,
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
# STATION 5: send it
# =============================================================================

async def publish_node(state: dict) -> dict[str, Any]:
    """Send the approved post. Dry-run stops here."""
    if state.get("dry_run"):
        return {"outcome": "approved"}

    item = state["item"]
    reply_to = state.get("reply_to_message_id")
    sent = await publisher.execute(item, state["post_html"], state["post_id"],
                                   reply_to_message_id=reply_to)
    return {"outcome": "published" if sent else "retry"}
