"""
TOOL: Check the duplicate detector
PURPOSE: Answer the one question the database could not: is the MEANING check
         actually working, and if it is, is its shortlist floor set right?
USAGE:   python3 tools/check_dedup.py                  the health report
         python3 tools/check_dedup.py --pairs 30       score recent posts against each other
         python3 tools/check_dedup.py --compare "a" "b"  score two pieces of text

WHY THIS EXISTS
    Check 4 fails OPEN: if the embedding service cannot be reached, the item is
    treated as new and published. That is the right call — a duplicate is a
    small embarrassment and a silent channel is worse — but it used to happen in
    complete silence.

    A dedup_hit row is only written when two items MATCH. So "the check ran and
    found nothing close enough" and "the check never ran at all and everything
    sailed through" left exactly the same evidence behind: none. When three
    tweets about one Fed decision went out four minutes apart, there was no way
    to tell which of those had happened.

    This tool distinguishes them, and it takes about a second.

WHAT IT COSTS
    A few embedding calls, so a fraction of a cent. It publishes nothing and
    changes nothing.
"""

from __future__ import annotations

import argparse
import asyncio
import itertools
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config  # noqa: E402
from nodes import dedup  # noqa: E402
from utils import db, embeddings, logger as log_setup  # noqa: E402

LINE = "─" * 74

# Statuses an item can only have reached by going through the dedup ladder. An
# item still queued, or expired before it was looked at, was never a candidate
# for the meaning check and must not count against its coverage.
PROCESSED = ("published", "duplicate", "irrelevant", "low_impact", "written", "failed")


async def health() -> bool:
    """Is the embedding service reachable right now? Prints and returns."""
    print("\n1. IS THE MEANING CHECK ALIVE?")
    print(LINE)
    print(f"   model:     {config.EMBEDDING_MODEL}")
    print(f"   endpoint:  {config.OPENROUTER_BASE_URL}/embeddings")

    if not config.OPENROUTER_API_KEY:
        print("\n   ❌ OPENROUTER_API_KEY is not set. The meaning check cannot run,")
        print("      and because it fails open, EVERY item is being published")
        print("      without it. That alone explains duplicates getting through.")
        return False

    # Shaped the way nodes/dedup.py shapes it — "{title}\n{body}" — because
    # that is what the live check measures. Scoring two BARE headlines here
    # understates it badly: these same two stories score 0.68 as headlines
    # alone and 0.75 once their tweet text is included, which is the
    # difference between "raise the alarm" and "working as intended".
    pair = [
        "Fed holds rates steady at 3.50%-3.75%\n"
        "The Federal Reserve left the federal funds target range unchanged "
        "for a fifth consecutive meeting.",
        "Federal Reserve leaves interest rates unchanged\n"
        "BREAKING: Federal Reserve leaves interest rates unchanged, "
        "remains at 3.50% - 3.75%.",
    ]
    vectors = await embeddings.embed(pair)

    if vectors is None:
        print("\n   ❌ THE CALL FAILED. The meaning check is not working, so every")
        print("      item is going out with only checks 1-3 — which cannot match a")
        print("      tweet to an article, or two differently-worded headlines.")
        print("      The log line above says why. Check the key and the model name.")
        return False

    score = embeddings.most_similar(vectors[0], [(1, embeddings.to_blob(vectors[1]))],
                                    top_k=1, threshold=0.0)[0][1]
    print(f"\n   ✅ Working. {len(vectors[0])} dimensions.")
    print(f"      Two real reports of one Fed decision score {score:.3f} against "
          f"a shortlist floor of {config.COSINE_SHORTLIST}.")
    if score < config.COSINE_SHORTLIST:
        print("      ⚠️  They mean the same thing and would never even be shown")
        print("          to the judge. The floor is too high — see section 3.")
    else:
        print("      They would be handed to the judge, which is the intended")
        print("      behaviour: the floor only decides what gets READ, not what")
        print("      gets merged.")
    return True


def coverage(days: int) -> None:
    """How many processed items actually have a stored meaning-vector?

    This is the number that would have answered the question outright. Every
    item that reaches the meaning check gets its vector stored, whether or not
    it turned out to be a duplicate. So if items have been processed and have no
    vector, the check was failing — silently, and for exactly that long.
    """
    print(f"\n2. DID THE CHECK ACTUALLY RUN? (last {days} day(s))")
    print(LINE)

    placeholders = ",".join("?" for _ in PROCESSED)
    row = db.query(
        f"""
        SELECT COUNT(*) AS total,
               SUM(CASE WHEN embedding IS NOT NULL THEN 1 ELSE 0 END) AS stored
          FROM items
         WHERE status IN ({placeholders})
           AND fetched_at > datetime('now', '-{int(days)} days')
        """,
        tuple(PROCESSED),
    )[0]

    total, stored = row["total"] or 0, row["stored"] or 0
    if not total:
        print("   No items have been processed yet.")
        return

    share = stored / total
    print(f"   items that reached the dedup stage:  {total}")
    print(f"   of those, with a meaning-vector:     {stored}  ({share:.0%})")

    failures = db.get_counters().get("meaning_check_failed", 0)
    if failures:
        print(f"   meaning checks that FAILED today:    {failures}")

    if share < 0.5:
        print("\n   ❌ MOST ITEMS WERE NEVER CHECKED FOR MEANING.")
        print("      They were published with only checks 1-3, which compare")
        print("      WORDING. Two accounts phrasing one story differently will")
        print("      always get through that. This is your answer.")
    elif share < 0.95:
        print("\n   ⚠️  Some items skipped the meaning check — an intermittent")
        print("       failure. Every one of those went out unchecked.")
    else:
        print("\n   ✅ The check ran on effectively everything. If duplicates are")
        print("      still getting through, the threshold is the suspect — "
              "see section 3.")


def near_misses(count: int) -> None:
    """Show pairs that looked related but scored under the threshold."""
    print(f"\n3. NEAR MISSES — pairs the check saw and let through")
    print(LINE)

    rows = db.query(
        "SELECT * FROM dedup_hits WHERE rung = 'embedding' AND kept = 1 "
        "ORDER BY id DESC LIMIT ?", (count,)
    )
    if not rows:
        print("   None recorded. Note that near-miss logging is new — if the")
        print("   service has not run since, there is nothing here yet.")
        return

    print(f"   The shortlist floor is {config.COSINE_SHORTLIST}. Anything listed "
          f"below it never reached the judge and was PUBLISHED separately.\n")
    for row in rows:
        print(f"   {row['score']:.3f}   {row['detail'][:150]}")

    below = [r for r in rows if r["score"] < config.COSINE_SHORTLIST
             and "below the" in (r["detail"] or "")]
    if below:
        highest = max(r["score"] for r in below)
        print(f"\n   The closest miss scored {highest:.3f} against your "
              f"{config.COSINE_SHORTLIST} shortlist floor.")
        print(f"   If those really are the same story, set "
              f"COSINE_SHORTLIST={highest - 0.02:.2f} in .env and re-check.")
        print("   Lowering it is cheap now: it only means the judge gets asked")
        print("   about a few more pairs, not that they are merged blindly.")


async def pairwise(count: int) -> None:
    """Score the most recent published posts against each other.

    The direct test: if two things on the channel are the same story, this says
    what the detector thought of them, and therefore whether the threshold or
    the service is at fault.
    """
    print(f"\n4. THE LAST {count} PUBLISHED ITEMS, SCORED AGAINST EACH OTHER")
    print(LINE)

    rows = db.query(
        "SELECT id, source_name, title, body FROM items "
        "WHERE status = 'published' ORDER BY id DESC LIMIT ?", (count,)
    )
    if len(rows) < 2:
        print("   Not enough published items to compare.")
        return

    texts = [f"{r['title']}\n{(r['body'] or '')[:400]}".strip() for r in rows]
    vectors = await embeddings.embed(texts)
    if vectors is None:
        print("   Could not embed — see section 1.")
        return

    scored = []
    for i, j in itertools.combinations(range(len(rows)), 2):
        score = embeddings.most_similar(
            vectors[i], [(j, embeddings.to_blob(vectors[j]))], top_k=1, threshold=0.0
        )[0][1]
        if score >= dedup.NEAR_MISS_FLOOR:
            scored.append((score, rows[i], rows[j]))

    if not scored:
        print(f"   No two of them scored above {dedup.NEAR_MISS_FLOOR}. Nothing")
        print("   published recently was a near-duplicate of anything else.")
        return

    print(f"   Shortlist floor {config.COSINE_SHORTLIST} — anything under it never")
    print(f"   reached the judge and went out as a separate post.\n")
    for score, a, b in sorted(scored, reverse=True, key=lambda s: s[0]):
        if score >= config.COSINE_CERTAIN:
            verdict = "merged outright (near-verbatim)"
        elif score >= config.COSINE_SHORTLIST:
            verdict = "judge decides"
        else:
            verdict = "❌ NEVER JUDGED — both published"
        print(f"   {score:.3f}  {verdict}")
        print(f"          {a['source_name']:<14} {(a['title'] or '')[:70]}")
        print(f"          {b['source_name']:<14} {(b['title'] or '')[:70]}")

    missed = [s for s, _, _ in scored if s < config.COSINE_SHORTLIST]
    if missed:
        print(f"\n   {len(missed)} pair(s) look related but never reached the judge.")
        print(f"   If they ARE the same story, lower COSINE_SHORTLIST to about "
              f"{max(missed) - 0.02:.2f} in .env.")
    judged = [s for s, _, _ in scored if s >= config.COSINE_SHORTLIST]
    if judged:
        print(f"\n   {len(judged)} pair(s) DID reach the judge and it said they were")
        print("   different events. If you disagree, the fix is the prompt in")
        print("   nodes/judge.py — run `main.py stats` to read its stated reason.")


async def compare(a: str, b: str) -> None:
    """Score two pieces of text the way the detector would."""
    vectors = await embeddings.embed([a, b])
    if vectors is None:
        print("\n   ❌ Could not embed — the meaning check is not working.\n")
        return
    score = embeddings.most_similar(
        vectors[0], [(1, embeddings.to_blob(vectors[1]))], top_k=1, threshold=0.0
    )[0][1]
    print(f"\n   {a[:70]}\n   {b[:70]}")
    if score >= config.COSINE_CERTAIN:
        outcome = "MERGED outright, no judge needed"
    elif score >= config.COSINE_SHORTLIST:
        outcome = "handed to the judge, which has the final say"
    else:
        outcome = "PUBLISHED as two — never shown to the judge"
    print(f"\n   score {score:.4f}   shortlist {config.COSINE_SHORTLIST}   "
          f"certain {config.COSINE_CERTAIN}")
    print(f"   -> {outcome}\n")


async def main() -> None:
    parser = argparse.ArgumentParser(description="Diagnose the duplicate detector.")
    parser.add_argument("--days", type=int, default=3)
    parser.add_argument("--pairs", type=int, default=0,
                        help="score the last N published items against each other")
    parser.add_argument("--compare", nargs=2, metavar=("A", "B"),
                        help="score two pieces of text")
    parser.add_argument("--count", type=int, default=15)
    args = parser.parse_args()

    log_setup.setup(level="WARNING", to_file=False)
    db.init_db()

    if args.compare:
        await compare(*args.compare)
        return

    print(f"\n{'=' * 74}")
    print("  DUPLICATE DETECTOR — health check")
    print(f"{'=' * 74}")

    alive = await health()
    coverage(args.days)
    near_misses(args.count)
    if args.pairs:
        await pairwise(args.pairs)
    else:
        print(f"\n   To score what actually went out against itself:")
        print(f"      python3 tools/check_dedup.py --pairs 30")

    print(f"\n{LINE}")
    if not alive:
        print("   Start with section 1 — nothing else matters until the check runs.\n")
    else:
        print("   Checks 1-3 compare WORDING and cannot match two accounts")
        print("   phrasing one story differently. Only check 4 can, so if it is")
        print("   off or set too high, cross-source duplicates are inevitable.\n")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nStopped.\n")
