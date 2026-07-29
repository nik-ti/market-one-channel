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
    Four checks, cheapest first. An item only reaches a check if everything
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

      4. THE SAME MEANING          ~1 call   The important one. "BREAKING: SEC
                                             approves spot ETH ETF" and
                                             "Regulator green-lights ether
                                             exchange-traded funds" share almost
                                             no words at all. Only this check can
                                             see they are one story — and it is
                                             the only check that can match a
                                             tweet to a news article.

WHY BOTHER WITH THE CHEAP ONES IF STEP 4 CATCHES EVERYTHING?
    Honestly, not to save money on step 4 — that costs about $0.000002 an item.
    The reasons are:
      - Steps 1 and 2 dispose of roughly 85% of everything for free. Sending all
        of that over the network would mean hundreds of pointless calls an hour.
      - Steps 1-3 take microseconds; step 4 needs a network round trip. On
        breaking news, that delay is the difference between first and late.
      - The real money is further down the line. Anything killed here never
        reaches the writer, which costs about $0.0012 a time.

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


# --- Check 4: is this the same EVENT, in different words? ---
async def check_meaning(item_id: int, title: str, body: str, item_norm_title: str = "") -> bool:
    """True if this story means the same as something we already covered.

    This is the check that does the real work, and the only one that can spot
    that a tweet and a news article are about the same event.

    How it works, briefly: the text is sent away and comes back as a long list
    of numbers that represents its meaning. Two texts about the same thing come
    back as similar lists, even with no words in common. We then measure how
    closely two lists point in the same direction — 1.0 is identical meaning,
    0.0 is unrelated. Above COSINE_THRESHOLD (0.86) we call it the same story.

    Returns False on any failure — see the note at the top of this file about
    which way each check fails.
    """
    from utils import embeddings

    # Always describe an item the same way before measuring it. A 40-word tweet
    # and a 900-word article about one event would otherwise look different
    # simply because of their length — which would undermine the exact
    # cross-source case this check exists for.
    text = f"{title}\n{body[:400]}".strip()
    if not text:
        return False

    vector = await embeddings.embed_one(text)
    if vector is None:
        _record_meaning_failure(item_id)
        return False

    _record_meaning_success()

    # Remember this item's meaning so future items can be compared against it,
    # whether or not it turns out to be a duplicate itself.
    blob = embeddings.to_blob(vector)
    db.set_item_embedding(item_id, blob)

    candidates = db.recent_embeddings(config.COSINE_WINDOW_HOURS, exclude_item_id=item_id)
    if not candidates:
        return False

    # Ask for the best match REGARDLESS of the threshold, so that a near miss
    # leaves a trace. Without this, an item scoring 0.84 against a threshold of
    # 0.86 was indistinguishable from one scoring 0.11 — and it is precisely the
    # near misses that tell you the threshold is set wrong.
    best = embeddings.most_similar(vector, candidates, top_k=1, threshold=0.0)
    matches = [m for m in best if m[1] >= config.COSINE_THRESHOLD]

    if not matches:
        if best:
            near_id, near_score = best[0]
            if near_score >= NEAR_MISS_FLOOR:
                near = db.get_item(near_id)
                db.log_dedup_hit(
                    item_id=item_id, matched_item_id=near_id, rung="embedding",
                    score=near_score, kept=True,
                    detail=(f"KEPT (below the {config.COSINE_THRESHOLD} threshold): "
                            f"{title[:120]} ~ {(near['title'] if near else '')[:120]}"),
                )
                log.info("Near miss on meaning (%.3f, threshold %.2f): %r ≈ %r",
                         near_score, config.COSINE_THRESHOLD, title[:60],
                         (near["title"] if near else "")[:60])
        return False

    matched_id, score = matches[0]
    matched = db.get_item(matched_id)
    matched_title = matched["title"] if matched else "(gone)"

    # THE NUMBERS GUARD AGAIN — it is needed here just as much as at check 3.
    # Recurring columns are the problem case: "New Ecommerce Tools: July 15" and
    # "New Ecommerce Tools: July 22" are two completely different articles, but
    # they mean almost exactly the same thing and scored 0.806 on real data —
    # high enough to be merged. Blanking the numbers makes them identical, which
    # is the signal that the only difference is a date or a figure. When that is
    # true we refuse to merge, exactly as at check 3.
    if matched is not None and textclean.same_but_for_digits(
        item_norm_title, matched["norm_title"] or ""
    ):
        db.log_dedup_hit(
            item_id=item_id, matched_item_id=matched_id, rung="embedding",
            score=score, kept=True,
            detail=f"KEPT (differs only by a number): {title[:120]} ~ {matched_title[:120]}",
        )
        log.info("Same-meaning KEPT (only a number differs, %.3f): %r", score, title[:70])
        return False

    db.log_dedup_hit(
        item_id=item_id, matched_item_id=matched_id, rung="embedding",
        score=score, kept=False,
        detail=f"{title[:150]}  ~{score:.3f}~  {matched_title[:150]}",
    )
    log.info("Duplicate meaning (%.3f): %r ≈ %r", score, title[:70], matched_title[:70])
    return True


# --- The node's entry point: run the checks that need doing now ---
async def execute(item, *, with_meaning: bool = True) -> bool:
    """Run the duplicate ladder on one queued item. True means "this is a repeat".

    Checks 1 and 2 already ran when the item was stored (check 1 is enforced by
    the database, check 2 runs in the collect loop). This function runs checks 3
    and 4, which are deliberately deferred: they are the slower ones, and doing
    them at posting time rather than collection time means we only pay for items
    that are actually candidates for publication.

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
        if await check_meaning(item_id, title, item["body"] or "",
                               item_norm_title=item["norm_title"] or ""):
            db.set_item_status(item_id, "duplicate", "same meaning as a recent story")
            db.bump_counter("deduped_meaning")
            return True

    return False
