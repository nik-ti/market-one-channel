"""Decides whether a story is worth covering, what it is, and how much it matters.

THIS IS THE ONLY NODE THAT ASKS "SHOULD WE COVER THIS?" It is easy to assume the
editor shares the job; it does not — the editor only checks a finished post
against its source. So if something dull reaches the channel, this prompt is
what has to change. (An editor allowed to reject on judgement is the editor that
ate 110 posts in nmd_consulting.)

It fails OPEN: if the model cannot be reached we fall back to the source's own
topic guess and a middling importance rather than dropping real news.

The rubric asks which MARKET must be repriced before it scores anything. An
event test alone let through "Russian retailer evacuates warehouses after drone
attacks" at a 4 — real, settled, and of no use to anyone holding a position.
Naming a transmission channel is a claim that can be wrong and reviewed later;
"feels important" is not.
"""

from __future__ import annotations

import config
from nodes import calendar
from utils import logger as log_setup, openrouter

log = log_setup.get("sorting")

# --- AI configuration: the block to edit when tuning ---
MODEL = config.SORTER_MODEL
TEMPERATURE = 0.0          # we want consistent judgements, not creative ones

# 250 was too small and answers came back cut off mid-value. A reasoning model
# thinks privately before answering and that counts against this budget too.
MAX_TOKENS = 800

# This channel's rubric — what it covers and what counts as important — lives
# in channels/<name>/rubric.md. Everything else in this file is machinery that
# any channel uses: the schema, the no-market cap, the failure counters.
PROMPT = config.RUBRIC_PATH.read_text()

# The markets a story can force somebody to look at again. "none" is a real
# answer and by far the most common one — see NO_MARKET_CAP below.

# What an item scores at most when no market has to reprice. Set to one below
# the publishing threshold on purpose: naming no market is not a small penalty,
# it is the answer that keeps the item off the channel.
#
# Enforced HERE, in code, and not only asked for in the prompt — same reasoning
# as stripping emoji from brief posts and foreign links from finished ones. A
# model that has just told us nothing needs repricing and then scores the item
# 4 has contradicted itself, and we believe the concrete field over the number.
NO_MARKET_CAP = 3

# The shape of the answer. "strict" mode means the provider enforces this, so
# the model cannot invent a topic name or return importance as words.
SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["relevant", "topic", "market", "importance", "reason"],
    "properties": {
        "relevant": {"type": "boolean"},
        # "markets" MUST be here. The prompt defines it, config.VALID_TOPICS
        # expects it, and it is this channel's main subject — but it was missing
        # from this enum, and strict mode means the provider enforces the enum.
        # So every bond yield, central bank and currency story had two ways out:
        # call itself "crypto" and be killed by the editor for WRONG_TOPIC, or
        # answer "other" and be marked irrelevant on sight by the rule below.
        "topic": {"type": "string", "enum": list(config.TOPICS)},
        "market": {"type": "string", "enum": list(config.MARKETS)},
        "importance": {"type": "integer", "minimum": 1, "maximum": 5},
        "reason": {"type": "string"},
    },
}


async def execute(item) -> dict:
    """Judge one item. Always returns a usable answer, even when the model fails.

    Returns a dictionary with:
        relevant   (bool)  should we cover this at all
        topic      (str)   crypto | geopolitics | other
        market     (str)   which market has to reprice; see the channel profile
        importance (int)   1-5, used to decide what gets posted first
        reason     (str)   why, in one sentence
        fallback   (bool)  True if the model failed and we guessed
    """
    title = item["title"] or ""
    body = (item["body"] or "")[:600]
    hint = item["topic_hint"] or "unknown"
    origin = "a post on X" if item["origin"] == "x" else "a news article"

    scheduled = calendar.describe(item)
    user_message = (
        f"Source: {item['source_name']} ({origin})\n"
        f"The source files this under: {hint}\n"
        + (f"{scheduled}\n" if scheduled else "")
        + f"\nHeadline: {title}\n\n"
        f"Text: {body}"
    )

    try:
        result = await openrouter.chat_json(
            model=MODEL, system=PROMPT, user=user_message,
            schema=SCHEMA, schema_name="sorting",
            temperature=TEMPERATURE, max_tokens=MAX_TOKENS,
        )

        topic = result.get("topic", "other")
        relevant = bool(result.get("relevant", False))
        market = str(result.get("market", "none"))
        if market not in config.MARKETS:
            market = "none"
        importance = int(result.get("importance", 2))
        reason = str(result.get("reason", ""))[:300]

        # A story we do not cover is the same as a story we do not want, so we
        # collapse the two. This makes topic sorting double as the relevance
        # filter, at no extra cost.
        if topic == "other":
            relevant = False

        # No market, no 4. The model has already told us in plain words that
        # nothing needs repricing; a high score alongside that is a contradiction,
        # and the specific answer beats the vague one.
        if market == "none" and importance > NO_MARKET_CAP:
            log.info("Capping item %s from %d to %d — the model named no market "
                     "that has to reprice (%s)",
                     item["id"], importance, NO_MARKET_CAP, title[:60])
            importance = NO_MARKET_CAP
            reason = f"no market has to reprice; {reason}"[:300]

        return {
            "relevant": relevant,
            "topic": topic,
            "market": market,
            "importance": importance,
            "reason": reason,
            "fallback": False,
        }

    except Exception as error:  # noqa: BLE001
        log.warning("Scoring failed for item %s (%s): %s", item["id"], title[:60], error)

        # We could not score it, so we do not know whether it matters. There is
        # no safe guess here: publishing it unscored would defeat the whole
        # point of this node, and dropping it could lose a genuine story.
        # So we do neither — the caller sees fallback=True and puts the item
        # back in the queue for another go. It is only given up on after
        # MAX_ATTEMPTS tries.
        usable_hint = hint if hint in config.VALID_TOPICS else "other"
        return {
            "relevant": False,
            "topic": usable_hint,
            "market": "none",
            "importance": 0,
            "reason": "could not be scored; will try again",
            "fallback": True,
        }
