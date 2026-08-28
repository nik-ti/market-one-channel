"""
NODE: Dedup
PURPOSE: Decide whether a story is one we have already covered.
INPUT:   An item that has just arrived, or one waiting in the queue.
OUTPUT:  True if it is a repeat (and it gets dropped), False if it is new.
DEPENDENCIES: rapidfuzz (wording comparison), utils/embeddings.py (meaning
              comparison, which costs a fraction of a cent per item).

THE PROBLEM THIS SOLVES
    The same event reaches us several times over: CoinDesk writes it up,
    Cointelegraph writes it up, and WatcherGuru tweets it. To a reader those are
    one story. Posting all three makes the channel look automated and cheap.

THE LADDER
    Five checks, cheapest first. An item only reaches a check if everything
    above it let it through.

      1. SAME LINK OR TWEET        free      Handled by the database itself.
                                             Catches the same article being
                                             re-listed every time we poll — by
                                             far the most common case.

      2. SAME HEADLINE             free      A fingerprint of the headline text.
                                             Catches one story appearing at two
                                             different addresses.

      3. NEARLY THE SAME WORDING   ~1ms      "Fed holds rates steady" versus
                                             "Fed leaves rates unchanged".

      4. THE SAME SUBJECT          ~1 call   Turns each story into numbers that
                                             describe its meaning and finds the
                                             closest recent ones. This is the
                                             only check that can match a tweet
                                             to a news article. It draws up a
                                             SHORTLIST — it does not decide.

      5. THE SAME EVENT            ~1 call   Reads the two stories properly and
                                             rules. "BREAKING: SEC approves spot
                                             ETH ETF" and "Regulator green-lights
                                             ether ETFs" are one event; "$49M
                                             ETF outflows" and "$32M ETF
                                             inflows" are two, despite scoring
                                             higher at step 4. See nodes/judge.py.

WHY STEP 4 IS NOT ALLOWED TO DECIDE ANY MORE
    It used to be. It had a single cutoff at 0.80 and it was wrong in both
    directions at once — see the long note in nodes/judge.py for the measured
    numbers and the two live failures that prompted this. Short text is the
    problem: with only a headline to go on, the measurement tracks what a story
    is ABOUT, not what happened. Most of what this channel reads is short.

WHY BOTHER WITH THE CHEAP ONES IF STEPS 4-5 CATCH EVERYTHING?
    Honestly, not to save money — step 4 costs about $0.000002 an item and step
    5 only runs on a shortlist, roughly 16 times a day. The reasons are:
      - Steps 1 and 2 dispose of roughly 85% of everything for free. Sending all
        of that over the network would mean hundreds of pointless calls an hour.
      - Steps 1-3 take microseconds; steps 4-5 need network round trips. On
        breaking news, that delay is the difference between first and late.
      - The real money is further down the line. Anything killed here never
        reaches the writer, which costs about $0.0012 a time.

THE TIME GATE
    Before any of steps 4-5 run, candidates more than DUPLICATE_MAX_GAP_HOURS
    from the new item are discarded outright. Recurring reports — daily ETF
    flows, weekly roundups — are nearly word-for-word identical from one edition
    to the next, so yesterday's is the most dangerous thing in the pool. Every
    real duplicate measured on this channel arrived within 10.1 hours; the worst
    false merges were 24 hours apart. See db.recent_embeddings.

WHICH WAY IT FAILS
    If the meaning check is unavailable (the service is down, the key is wrong),
    we treat the item as NEW and let it through. A duplicate post is a small
    embarrassment; a channel that goes silent because an API had a bad afternoon
    is a bigger problem. Note that the editor node does the opposite — see the
    note at the top of nodes/editor.py. The asymmetry is deliberate.

FAILING OPEN IS FINE. FAILING OPEN IN SILENCE IS NOT.
    That decision above is right, and it had a hole in it: nothing anywhere
    recorded that the check had failed. A dedup_hit row is only written when two
    items MATCH, so "check 4 ran and found nothing close enough" and "check 4
    never ran because the API was down and we waved everything through" produced
    byte-identical evidence — which is to say none.

    That is not a theoretical worry. Three tweets about the same Fed decision
    published within four minutes of each other, and there was no way to tell
    from the database which of those two things had happened.

    So every failure is now counted, logged, and alerted on after a run of them,
    and tools/check_dedup.py answers the question directly. The rule the rest of
    this project already follows applies here too: a filter you cannot inspect
    is a filter you cannot fix.
"""

from __future__ import annotations

from rapidfuzz import fuzz, process

import config
from utils import db, logger as log_setup, textclean

log = log_setup.get("dedup")

# How many meaning checks have failed in a row. A single failure is a network
# blip and not worth anyone's attention; a run of them means the check is off,
# and every item since the run started went out unchecked.
_consecutive_meaning_failures = 0

# Alert after this many in a row. Deliberately small: at one item every couple
# of minutes, ten failures is roughly twenty minutes of a channel with no
# duplicate detection at all, which is long enough to publish the same story
# three times — and short enough that you hear about it the first time.
MEANING_FAILURE_ALERT_AFTER = 10

# Log a NEAR MISS — two items that looked related but scored under the threshold
# — when the score is at least this. Below it the pair is genuinely unrelated
# and recording it would just be noise.
#
# This is what turns "check 4 caught nothing" into an answerable question. If
# your channel carries the same story twice and the pair is sitting in this list
# at 0.84 against a 0.86 threshold, you do not have a broken check — you have a
# threshold two points too high, and now you can see that.
NEAR_MISS_FLOOR = 0.70


def _record_meaning_failure(item_id: int) -> None:
    """Count a failed meaning check, and shout if they are piling up."""
    global _consecutive_meaning_failures
    _consecutive_meaning_failures += 1
    db.bump_counter("meaning_check_failed")

    log.warning("Meaning check unavailable — item %s goes through UNCHECKED "
                "(%d in a row)", item_id, _consecutive_meaning_failures)

    if _consecutive_meaning_failures == MEANING_FAILURE_ALERT_AFTER:
        from utils import telegram_error
        telegram_error.send_error(
            f"The duplicate MEANING check has failed "
            f"{_consecutive_meaning_failures} times in a row. Every item since "
            f"then has been published without it, so the same story can now "
            f"appear several times from different sources. This check fails "
            f"open on purpose, which is why nothing looks broken.\n\n"
            f"Run: python3 tools/check_dedup.py",
            node_name="dedup_meaning",
        )


def _record_meaning_success() -> None:
    """Reset the run of failures after a check that worked."""
    global _consecutive_meaning_failures
    if _consecutive_meaning_failures:
        log.info("Meaning check is working again after %d failure(s)",
                 _consecutive_meaning_failures)
        _consecutive_meaning_failures = 0


# --- Check 2: have we seen this exact headline recently? ---
def check_headline(item_id: int, title: str, title_hash: str) -> bool:
    """True if an item with the same headline fingerprint already exists.

    Catches the same story published at two addresses: syndication, a site that
    changed its link format, or a feed that reissues articles.
    """
    if not title_hash:
        return False

    match = db.title_hash_seen(title_hash, config.FUZZY_WINDOW_HOURS)
    if match is None or match["id"] == item_id:
        return False

    db.log_dedup_hit(
        item_id=item_id, matched_item_id=match["id"], rung="title_hash",
        score=100.0, kept=False,
        detail=f"{title[:150]}  ==  {match['title'][:150]}",
    )
    log.info("Duplicate headline [%s]: %r already seen from %s",
             "exact", title[:80], match["source_name"])
    return True


# --- Check 3: is this a reworded version of something recent? ---
def check_wording(item_id: int, title: str, norm_title: str) -> bool:
    """True if this headline is a rewrite of one we handled in the last day.

    Uses similarity scoring: how many single-character edits separate the two
    headlines, as a percentage. Above FUZZY_THRESHOLD (92 by default) we call it
    the same story.

    The time window matters. A reworded headline shows up within hours. The same
    wording appearing three weeks later is far more likely to be a genuine new
    development, so we only look back a day.

    Never raises: if the lookup fails we say "new" rather than risk silently
    binning real news because of a database hiccup.
    """
    if not norm_title:
        return False

    try:
        candidates = db.recent_titles(config.FUZZY_WINDOW_HOURS, exclude_item_id=item_id)
        if not candidates:
            return False

        lookup = {text: row_id for row_id, text in candidates}
        hit = process.extractOne(
            norm_title, list(lookup.keys()),
            scorer=fuzz.ratio, score_cutoff=config.FUZZY_THRESHOLD,
        )
    except Exception as error:  # noqa: BLE001 - never drop news over an infra blip
        log.warning("Wording check failed (treating item as new): %s", error)
        return False

    if hit is None:
        return False

    matched_text, score = hit[0], hit[1]
    matched_id = lookup[matched_text]

    # THE NUMBERS GUARD.
    # Similarity scoring cannot tell "16 dead" becoming "17 dead" (same story,
    # updated) from "Fed cuts 25bp" versus "Fed cuts 50bp" (two completely
    # different events). Both score about 97%. Since only meaning separates
    # them, and we do not have meaning at this step, we refuse to merge either.
    if textclean.same_but_for_digits(norm_title, matched_text):
        db.log_dedup_hit(
            item_id=item_id, matched_item_id=matched_id, rung="fuzzy",
            score=score, kept=True,
            detail=f"KEPT (differs only by a number): {title[:120]} ~ {matched_text[:120]}",
        )
        log.info("Near-duplicate KEPT (only a number differs, %.0f%%): %r", score, title[:80])
        return False

    db.log_dedup_hit(
        item_id=item_id, matched_item_id=matched_id, rung="fuzzy",
        score=score, kept=False,
        detail=f"{title[:150]}  ~{score:.0f}%~  {matched_text[:150]}",
    )
    log.info("Duplicate wording (%.0f%%): %r", score, title[:80])
    return True


# --- Check 4: is this the same EVENT, a CONTINUATION, or a different story? ---
async def check_meaning(item, item_norm_title: str = "") -> tuple[str, int | None, float]:
    """Return the relationship between this item and any recent similar item.

    Returns a tuple (verdict, matched_item_id, score):
      verdict:  "duplicate"    → same event; the newer item should be dropped
                "continuation" → same developing story, new development; should
                                  thread as a reply to matched_item_id
                "different"    → unrelated or different happening
                "error"        → could not get a verdict (fail open)
      matched_item_id: the older item it relates to, when verdict is duplicate
                       or continuation; otherwise None.
      score: the cosine similarity that put the candidate on the shortlist.

    This step no longer decides anything on its own for duplicates — the judge
    reads the pair. But it now ALSO distinguishes genuine continuations, which
    the old pipeline had no language for.
    """
    from utils import embeddings
    from nodes import judge

    item_id = item["id"]
    title = item["title"] or ""
    body = item["body"] or ""

    # Strip the source's house decoration first — sirens, ALL CAPS, cashtags.
    # A tweet and a wire story about the same event are written in completely
    # different registers, and that difference alone was enough to push a real
    # duplicate below the shortlist floor. See textclean.for_embedding().
    text = textclean.for_embedding(f"{title}\n{body[:400]}")
    if not text:
        return "different", None, 0.0

    vector = await embeddings.embed_one(text)
    if vector is None:
        _record_meaning_failure(item_id)
        return "error", None, 0.0

    _record_meaning_success()

    blob = embeddings.to_blob(vector)
    db.set_item_embedding(item_id, blob)

    candidates = db.recent_embeddings(
        config.COSINE_WINDOW_HOURS,
        exclude_item_id=item_id,
        near_time=item["fetched_at"],
        max_gap_hours=config.DUPLICATE_MAX_GAP_HOURS,
    )
    if not candidates:
        return "different", None, 0.0

    best = embeddings.most_similar(
        vector, candidates, top_k=config.DEDUP_TOP_K, threshold=0.0
    )
    matches = [m for m in best if m[1] >= config.COSINE_SHORTLIST]

    if not matches:
        if best:
            near_id, near_score = best[0]
            if near_score >= NEAR_MISS_FLOOR:
                near = db.get_item(near_id)
                db.log_dedup_hit(
                    item_id=item_id, matched_item_id=near_id, rung="embedding",
                    score=near_score, kept=True,
                    detail=(f"KEPT (below the {config.COSINE_SHORTLIST} shortlist floor): "
                            f"{title[:120]} ~ {(near['title'] if near else '')[:120]}"),
                )
                log.info("Near miss on meaning (%.3f, floor %.2f): %r ≈ %r",
                         near_score, config.COSINE_SHORTLIST, title[:60],
                         (near["title"] if near else "")[:60])
        return "different", None, 0.0

    for matched_id, score in matches:
        matched = db.get_item(matched_id)
        if matched is None:
            continue
        matched_title = matched["title"] or ""

        if textclean.same_but_for_digits(item_norm_title, matched["norm_title"] or ""):
            db.log_dedup_hit(
                item_id=item_id, matched_item_id=matched_id, rung="embedding",
                score=score, kept=True,
                detail=f"KEPT (differs only by a number): {title[:120]} ~ {matched_title[:120]}",
            )
            log.info("Same-meaning KEPT (only a number differs, %.3f): %r", score, title[:70])
            continue

        if score >= config.COSINE_CERTAIN:
            db.log_dedup_hit(
                item_id=item_id, matched_item_id=matched_id, rung="embedding",
                score=score, kept=False,
                detail=f"{title[:150]}  ~{score:.3f}~  {matched_title[:150]}",
            )
            log.info("Duplicate meaning (%.3f, certain): %r ≈ %r",
                     score, title[:70], matched_title[:70])
            db.bump_counter("deduped_meaning")
            return "duplicate", matched_id, score

        # Ask the three-way judge instead of the old binary one.
        verdict, reason = await judge.execute_three_way(item, matched)

        if verdict is None:
            db.log_dedup_hit(
                item_id=item_id, matched_item_id=matched_id, rung="judge",
                score=score, kept=True,
                detail=f"KEPT (judge unavailable: {reason}): "
                       f"{title[:110]} ~ {matched_title[:110]}",
            )
            continue

        if verdict == "different":
            db.log_dedup_hit(
                item_id=item_id, matched_item_id=matched_id, rung="judge",
                score=score, kept=True,
                detail=f"KEPT (different event: {reason[:120]}): "
                       f"{title[:110]} ~{score:.3f}~ {matched_title[:110]}",
            )
            log.info("Looked alike (%.3f) but judged a DIFFERENT event: %r vs %r — %s",
                     score, title[:60], matched_title[:60], reason[:100])
            continue

        if verdict == "continuation":
            db.log_dedup_hit(
                item_id=item_id, matched_item_id=matched_id, rung="continuation",
                score=score, kept=True,
                detail=f"CONTINUATION ({reason[:120]}): "
                       f"{title[:110]} ~{score:.3f}~ {matched_title[:110]}",
            )
            log.info("Continuation (%.3f): %r → %r — %s",
                     score, title[:60], matched_title[:60], reason[:100])
            return "continuation", matched_id, score

        # verdict == "same_event"
        db.log_dedup_hit(
            item_id=item_id, matched_item_id=matched_id, rung="judge",
            score=score, kept=False,
            detail=f"{title[:130]}  ~{score:.3f}~  {matched_title[:130]}  | {reason[:120]}",
        )
        log.info("Duplicate event (%.3f, judged): %r ≈ %r — %s",
                 score, title[:60], matched_title[:60], reason[:100])
        db.bump_counter("deduped_judge")
        return "duplicate", matched_id, score

    return "different", None, 0.0


# --- Internal classifier used by the brain ---
async def classify(item, *, with_meaning: bool = True) -> tuple[str, int | None, float]:
    """Run checks 3 and 4 and return the full verdict, without setting statuses.

    This is what the brain's relationship_check node calls. It needs the raw
    verdict (duplicate / continuation / different) so it can route correctly.
    """
    if check_wording(item["id"], item["title"] or "", item.get("norm_title") or ""):
        # Wording duplicates don't carry a matched id for downstream use; they
        # are always dropped. Return a synthetic score of 100.
        return "duplicate", None, 100.0
    if not with_meaning:
        return "different", None, 0.0
    return await check_meaning(item, item_norm_title=item.get("norm_title") or "")


# --- The node's entry point: run the checks that need doing now ---
async def execute(item, *, with_meaning: bool = True) -> bool:
    """Run the duplicate ladder on one queued item. True means "this is a repeat".

    Checks 1 and 2 already ran when the item was stored (check 1 is enforced by
    the database, check 2 runs in the collect loop). This function runs checks 3
    and 4, which are deliberately deferred: they are the slower ones, and doing
    them at posting time rather than collection time means we only pay for items
    that are actually candidates for publication.

    This function is the OLD binary API. It treats continuations as NOT
    duplicates, because the old pipeline has no threading. The brain uses
    classify() instead.

    Args:
        item:         a row from the items table.
        with_meaning: set False to skip the paid check (used by the feed checker).
    """
    item_id = item["id"]
    title = item["title"] or ""

    if check_wording(item_id, title, item["norm_title"] or ""):
        db.set_item_status(item_id, "duplicate", "same wording as a recent story")
        db.bump_counter("deduped_fuzzy")
        return True

    if with_meaning:
        verdict, _, _ = await check_meaning(item, item_norm_title=item["norm_title"] or "")
        if verdict == "duplicate":
            db.set_item_status(item_id, "duplicate", "same event as a recent story")
            return True

    return False
