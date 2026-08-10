"""
AI NODE: Judge
PURPOSE: Given two stories that LOOK alike, decide if they are the same event.
INPUT:   The new item, and one candidate it might be a repeat of.
OUTPUT:  True = same event (drop the new one), False = genuinely different.
DEPENDENCIES: utils/openrouter.py, utils/db.py (for the audit log)

WHY THIS NODE HAD TO EXIST
    Duplicate check 4 turns each story into a list of numbers describing its
    meaning, then measures how closely two lists point the same way. That works
    beautifully on long articles and badly on short ones, and most of what this
    channel reads is short.

    The reason is simple once you see it. A 40-word tweet gives the measurement
    almost nothing to work with except its SUBJECT. "Fed" plus "rates" plus a
    percentage lands in the same place whether the Fed raised, cut, or held.
    So the score answers "are these about the same thing?" when the question we
    actually need answered is "are these the same happening?"

    Measured on 19 hand-labelled pairs from this channel's own history:

        real duplicates      scored 0.738 - 0.993
        genuinely different  scored 0.785 - 0.900

    The ranges overlap, which means no cutoff anywhere can separate them. That
    is not a tuning problem to be solved with a better number — it is the wrong
    instrument. Two real failures on the live channel:

      MISSED   Three posts about one Fed rate decision, minutes apart, from
               TreeNews, WatcherGuru and crypto_banter. They scored 0.738,
               0.745 and 0.797 against a 0.80 cutoff.

      WRONGLY  "$49.75M ETF OUTflows" was merged into "$32.11M ETF INflows"
      MERGED   at 0.900. Opposite events, a day apart. The story never posted.

    This node reads both stories properly and answers the real question. On that
    same sample it got every one of those cases right.

WHERE IT SITS
    It does not replace check 4, it is fed by it. The numbers do the cheap work
    of finding a handful of plausible candidates out of everything from the last
    two days; this node does the expensive work of ruling on them. Roughly 16
    calls a day at current volume — a few cents a month.

WHY NOT JUST LOWER THE CUTOFF
    Measured, not assumed. Dropping it from 0.80 to 0.72 caught every real
    duplicate and DOUBLED the wrong merges, from three to six. There is no
    setting of that one number that makes this better.

WHICH WAY IT FAILS
    OPEN. If the model cannot be reached, times out, or answers with nonsense,
    we return "not a duplicate" and the story posts. This matches
    nodes/dedup.py and is the opposite of nodes/editor.py. An accidental repeat
    is an embarrassment; a channel that goes quiet because a provider had a bad
    afternoon is a failure. Every such fallback is logged as a `judge_error`
    counter so you can see it in tools/stats.py rather than guessing.
"""

from __future__ import annotations

import asyncio

import config
from utils import db, logger as log_setup, openrouter

log = log_setup.get("judge")


# ============================================================================
# THE BINARY PROMPT (kept for backwards compatibility with tools/check_dedup.py)
# ============================================================================
# Written against the cases that actually went wrong. The "NOT the same event"
# list is not padding — every line is a real pair this channel got wrong or
# nearly got wrong, and removing one will bring that failure back.

SYSTEM = """You decide whether two news items report THE SAME SPECIFIC EVENT.

Same event = the same occurrence, on the same occasion. One is a re-report of
the other, possibly by a different outlet, in different words, at a different
length. A short tweet and a long article about one happening ARE the same event.

NOT the same event, even when the subject and the wording look nearly identical:
  - opposite outcomes (inflows vs outflows, rose vs fell, approved vs rejected)
  - different figures for a recurring measurement (daily flows, weekly totals)
  - a recurring column or roundup published on a different date
  - a reaction, analysis or consequence rather than the occurrence itself
  - the same person or body doing a different thing on a different occasion

Judge the STORY, not the headline. Outlets hook the same event on different
angles — "X worked for a disgraced trader" and "X's fund is raising cash" were
one story, and only the body said so. Read both bodies before deciding.

An update that revises the SAME occurrence — a death toll rising, a figure being
refined as the story develops — IS the same event.

Judge only what the two texts say. Do not use outside knowledge to connect them.
"""

SCHEMA = {
    "type": "object",
    "properties": {
        "same_event": {"type": "boolean"},
        "reason": {"type": "string", "description": "One short sentence."},
    },
    "required": ["same_event", "reason"],
    "additionalProperties": False,
}


# ============================================================================
# THE THREE-WAY PROMPT (used by the brain to detect continuations)
# ============================================================================
# Same judge, but now it also distinguishes "this is a new development of a
# story we are already covering" from "this is a completely different story".
# That distinction is what lets the channel thread developing events instead of
# dropping every related item as a duplicate.

SYSTEM_THREE_WAY = """You classify the relationship between two news items.

The OLDER item is one the channel has already published. The NEWER item just
arrived. Decide whether the newer item is:

1. SAME EVENT — it reports the same specific happening as the older item, just
   in different words, from a different outlet, or at a different length.
   Examples: "Fed holds rates steady" and "Fed leaves rates unchanged" about the
   same meeting; a short tweet and a long article about one company being hacked.

2. CONTINUATION — it is a genuine NEW development in the same ongoing story, not
   a re-report. It adds a material fact that changes what a reader knows:
   - a number updated (death toll, losses, inflows/outflows)
   - a new actor or participant has entered the story
   - a proposal moved to a decision (Senate passes the bill that was proposed)
   - a consequence or official reaction that is itself a new event
   - a confirmation of an earlier report from a new authoritative source
   Examples: "The port operator confirms loading has stopped" after "Drones halt
   loading at port"; "Death toll rises to 120" after "Blast kills dozens".

3. DIFFERENT — the two items are about the same broad subject but report
   different happenings, or they are unrelated.

NOT same event, even when the wording looks nearly identical:
  - opposite outcomes (inflows vs outflows, rose vs fell, approved vs rejected)
  - different figures for a recurring measurement (daily ETF flows, weekly totals)
  - a recurring column or roundup published on a different date
  - a reaction, analysis or opinion rather than an occurrence itself
  - the same person or body doing a different thing on a different occasion

An update that revises the SAME occurrence — a death toll rising for the same
blast, a figure being refined — is SAME EVENT, not CONTINUATION. The difference
is whether the newer item adds a NEW development or just re-states/revises the
old one.

Judge only what the two texts say. Do not use outside knowledge.
"""

SCHEMA_THREE_WAY = {
    "type": "object",
    "properties": {
        "verdict": {
            "type": "string",
            "enum": ["same_event", "continuation", "different"],
        },
        "reason": {"type": "string", "description": "One short sentence."},
    },
    "required": ["verdict", "reason"],
    "additionalProperties": False,
}


def _describe(item, when: str) -> str:
    """Render one item for the prompt.

    The timestamp is included deliberately. Several of the hardest pairs are
    recurring reports that differ ONLY by date, and the model cannot see that
    unless we tell it.
    """
    title = (item["title"] or "").strip()
    body = (item["body"] or "").strip()[:300]
    source = item["source_name"]
    return f"[{source}, {when}]\n{title}\n{body}".strip()


# --- Internal: ask the judge once with one of the two prompts ---
async def _judge(item, candidate, *, system: str, schema: dict,
                 schema_name: str) -> dict | None:
    """Call the judge model once and return the parsed reply, or None on failure.

    None means "could not get a verdict" — callers fail open.
    """
    user = (
        f"ITEM A (newer):\n{_describe(item, item['fetched_at'])}\n\n"
        f"---\n\n"
        f"ITEM B (older):\n{_describe(candidate, candidate['fetched_at'])}\n\n"
    )

    try:
        answer = await asyncio.wait_for(
            openrouter.chat_json(
                model=config.JUDGE_MODEL,
                system=system,
                user=user,
                schema=schema,
                schema_name=schema_name,
                temperature=0.0,
                max_tokens=200,
            ),
            timeout=config.JUDGE_TIMEOUT_SECONDS,
        )
    except asyncio.TimeoutError:
        db.bump_counter("judge_error")
        log.warning("Judge timed out after %ss on items %s/%s — treating as new",
                    config.JUDGE_TIMEOUT_SECONDS, item["id"], candidate["id"])
        return None
    except Exception as error:  # noqa: BLE001 - never drop news over an infra blip
        db.bump_counter("judge_error")
        log.warning("Judge failed on items %s/%s (treating as new): %s",
                    item["id"], candidate["id"], error)
        return None

    return answer


# --- The node's entry point: binary verdict (kept for dedup.py compatibility) ---
async def execute(item, candidate) -> tuple[bool | None, str]:
    """Rule on one pair. Returns (same_event, reason).

    same_event is None when we could not get an answer — the caller treats that
    as "not a duplicate" and posts. See "WHICH WAY IT FAILS" above.

    A verdict of "continuation" is treated as NOT a duplicate here, because
    this function answers the dedup question. Continuations are picked up by
    the brain in brain/nodes.py via execute_three_way.
    """
    answer = await _judge(item, candidate, system=SYSTEM, schema=SCHEMA,
                          schema_name="same_event")

    if answer is None:
        return None, "judge unavailable"

    if "same_event" not in answer:
        db.bump_counter("judge_error")
        log.warning("Judge returned no verdict for items %s/%s: %r",
                    item["id"], candidate["id"], answer)
        return None, "no verdict in reply"

    same = bool(answer["same_event"])
    reason = str(answer.get("reason", ""))[:300]
    db.bump_counter("judge_same" if same else "judge_different")
    return same, reason


# --- Three-way verdict: same_event / continuation / different ---
async def execute_three_way(item, candidate) -> tuple[str | None, str]:
    """Classify a pair's relationship for the brain.

    Returns (verdict, reason). Verdict is one of:
        "same_event"   → drop as a duplicate
        "continuation" → a genuine new development; reply to the older post
        "different"    → unrelated or different happening; treat as a new story

    Returns (None, reason) when the judge cannot be reached — callers must fail
    open (treat as "different" so the story at least gets considered).
    """
    answer = await _judge(item, candidate, system=SYSTEM_THREE_WAY,
                          schema=SCHEMA_THREE_WAY, schema_name="relationship")

    if answer is None:
        return None, "judge unavailable"

    verdict = str(answer.get("verdict", "")).lower()
    reason = str(answer.get("reason", ""))[:300]

    if verdict not in {"same_event", "continuation", "different"}:
        db.bump_counter("judge_error")
        log.warning("Judge returned unexpected verdict %r for items %s/%s",
                    verdict, item["id"], candidate["id"])
        return None, "unexpected verdict"

    db.bump_counter(f"judge_{verdict}")
    return verdict, reason


# --- Sanity check: is the judge stable when the items are shown in reverse order?
# This matters because a judge that flips its answer depending on argument order
# will produce unreproducible bugs: the same two stories merge on Monday and not
# on Tuesday. The original config.py comment caught this on a real model.
async def test_order_stability(pairs: list[tuple[dict, dict, str]],
                               description: str = "") -> dict:
    """Run execute_three_way on each pair both ways and report any flips.

    Args:
        pairs: list of (newer_item, older_item, expected_verdict).
        description: optional label for the report.

    Returns a dict with mismatches and the raw verdicts. Safe to call with real
    pairs from the channel's history before trusting the new prompt live.
    """
    results = {
        "description": description,
        "total": len(pairs),
        "mismatches": [],
        "details": [],
    }

    for newer, older, expected in pairs:
        v_ab, r_ab = await execute_three_way(newer, older)
        v_ba, r_ba = await execute_three_way(older, newer)

        results["details"].append({
            "newer": newer.get("title", "")[:80],
            "older": older.get("title", "")[:80],
            "expected": expected,
            "a_then_b": v_ab,
            "b_then_a": v_ba,
            "reason_ab": r_ab,
            "reason_ba": r_ba,
        })

        # A reversal is when the verdict changes between the two orders.
        # For expected="same_event", the model might say "different" both ways
        # (a miss) — that is a quality problem, not an order-stability problem.
        # A flip is specifically (same ↔ different/continuation).
        if v_ab != v_ba:
            results["mismatches"].append({
                "newer": newer.get("title", "")[:80],
                "older": older.get("title", "")[:80],
                "expected": expected,
                "a_then_b": v_ab,
                "b_then_a": v_ba,
            })

    return results
