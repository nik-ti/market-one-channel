"""Reads what the channel just published and tells the writer how to sit next to it.

WHY THIS EXISTS. The writer was already shown the last posts, but only as voice
samples with "DO NOT repeat their facts" attached, so it copied their SHAPE and
swapped the number. On 31 August the channel posted four government bond yields
in one day, each rebuilt from the same skeleton, each re-explaining what a yield
is. Nothing was wrong with any single post; the sequence read like a machine.

This node is the only place that sees the incoming story and the published ones
TOGETHER. It answers two questions a person would answer without thinking:

  1. did the reader just read the neighbour of this story?   -> sibling
  2. what have we already explained to them today?           -> already_covered

The dedup check upstream answers a different question — is this the SAME event —
and correctly said no ("differs only by a number"). Different event, same
conversation, is this node's job.

IT FAILS OPEN. No brief means the writer works exactly as it did before, which
is a duller post, not a wrong one. So an unreachable model never holds a story.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone

import config
from utils import logger as log_setup, openrouter

log = log_setup.get("continuity")

MODEL = config.CONTINUITY_MODEL
TEMPERATURE = 0.0
MAX_TOKENS = 500

SYSTEM = """You are the editor of a news channel, briefing the writer before they
write the next post.

You see the incoming story and the posts the channel has ALREADY published, with
how long ago each went out. Your job is not to write anything. It is to tell the
writer how this post should sit next to the ones the reader just read.

## sibling
Mark the story a SIBLING when the reader would recognise it as the next item in
something they just read: the same measurement on a different instrument, the
same move in a different market, the second company reporting the same quarter,
the second country doing the same thing.

  - "US 5-year yield rises to 4.5%" one hour after "US 10-year yield rises to
    4.75%" IS a sibling. Same move, next instrument along.
  - "Gold falls 1%" one hour after "S&P 500 hits a record" is NOT. Two markets,
    two stories, no shared thread.

A sibling is NOT the same event — if it were, the story would have been dropped
as a duplicate before it reached you. Being a sibling does not make it less
worth posting. It changes how it is written: short, leaning on what the reader
already has, not rebuilt from nothing.

When you mark a sibling, put the number of the post it belongs with in
`sibling_of`. Use only numbers from the list you were given.

## already_covered
List anything in the recent posts the writer must NOT explain, define or state
again, in plain words. Definitions are the main one — a term explained four
hours ago does not get explained again. So are figures and framings the reader
has already seen ("highest since January 2025", "up 4.5 basis points").

Only list things THIS story would otherwise repeat. A definition the writer
has no reason to reach for does not belong on the list.

Leave the list empty when the story shares nothing with what came before.

## instruction
Two or three sentences, addressed to the writer, in the imperative. Say what to
lead with, what to leave out because the reader has it, and what NOT to repeat
in shape or phrasing. Be specific: name the phrase, not the category.

If the story stands entirely on its own, say so in one sentence and stop.
Do not invent a connection to fill the field."""

SCHEMA = {
    "type": "object",
    "properties": {
        "relation": {"type": "string", "enum": ["standalone", "sibling"]},
        "sibling_of": {
            "type": ["integer", "null"],
            "description": "Number of the recent post this belongs with, or null.",
        },
        "already_covered": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Things the reader already read that must not be repeated.",
        },
        "instruction": {"type": "string"},
    },
    "required": ["relation", "sibling_of", "already_covered", "instruction"],
    "additionalProperties": False,
}

EMPTY = {"relation": "standalone", "sibling_of": None,
         "already_covered": [], "instruction": "", "error": False}


def _age(sent_at: str) -> str:
    """How long ago a post went out, in words. This is what makes a sibling obvious."""
    try:
        sent = datetime.fromisoformat(sent_at).replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return "earlier"

    minutes = int((datetime.now(timezone.utc) - sent).total_seconds() // 60)
    if minutes < 60:
        return f"{max(minutes, 1)} minutes ago"
    hours = minutes // 60
    if hours < 24:
        return f"{hours} hours ago"
    return f"{hours // 24} days ago"


def _render(recent: list) -> str:
    """The published posts as a numbered list the model can point back at.

    The number IS the Telegram message id, so `sibling_of` comes back as
    something the publisher can reply to without a second lookup.
    """
    from brain import persona_loader

    lines = []
    for row in recent:
        text = persona_loader.visible_text(row["post_html"])
        if text:
            lines.append(f"[{row['telegram_message_id']}] ({_age(row['sent_at'])})\n{text}")
    return "\n\n".join(lines)


async def execute(item, recent: list) -> dict:
    """Brief the writer on one story. Never raises; an empty brief is a valid answer."""
    if not recent:
        return dict(EMPTY)

    rendered = _render(recent)
    if not rendered:
        return dict(EMPTY)

    user = (
        f"ALREADY PUBLISHED (newest first):\n\n{rendered}\n\n"
        f"---\n\nINCOMING STORY\n"
        f"Source: {item['source_name']}\n"
        f"Headline: {item['title'] or ''}\n\n"
        f"Text:\n{(item['body'] or '')[:1500]}"
    )

    try:
        answer = await asyncio.wait_for(
            openrouter.chat_json(
                model=MODEL, system=SYSTEM, user=user, schema=SCHEMA,
                schema_name="continuity", temperature=TEMPERATURE, max_tokens=MAX_TOKENS,
            ),
            timeout=config.CONTINUITY_TIMEOUT_SECONDS,
        )
    except Exception as error:  # noqa: BLE001
        log.warning("No brief for item %s (%s) — the writer works without one",
                    item["id"], error)
        return {**EMPTY, "error": True}

    known_ids = {row["telegram_message_id"] for row in recent}
    sibling_of = answer.get("sibling_of")
    if sibling_of not in known_ids:
        # A message id we never showed it cannot be replied to.
        sibling_of = None

    relation = answer.get("relation") or "standalone"
    if relation == "sibling" and sibling_of is None:
        relation = "standalone"

    covered = [str(c) for c in (answer.get("already_covered") or []) if str(c).strip()]

    return {
        "relation": relation,
        "sibling_of": sibling_of,
        "already_covered": covered[:6],
        "instruction": (answer.get("instruction") or "").strip(),
        "error": False,
    }


def as_text(brief: dict) -> str:
    """The brief as the block that goes into the writer's prompt. Empty if there is nothing to say."""
    parts = []

    if brief.get("relation") == "sibling":
        parts.append(
            "This story is a SIBLING of the post above it — the reader has just "
            "read its neighbour. Write it as the next line of that conversation, "
            "not as a story starting from nothing. It goes out as a reply to that "
            "post, so it can be shorter than usual."
        )

    covered = brief.get("already_covered") or []
    if covered:
        listed = "\n".join(f"  - {c}" for c in covered)
        parts.append(
            "THE READER ALREADY HAS THIS. Do not explain, define or state it "
            f"again, and do not reuse the phrasing it was given in:\n{listed}"
        )

    if brief.get("instruction"):
        parts.append(f"EDITOR'S INSTRUCTION FOR THIS POST:\n{brief['instruction']}")

    return "\n\n".join(parts)
