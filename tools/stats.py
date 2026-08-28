"""What the channel has been doing, and what its filters threw away.

    python main.py stats [--days 7]
    python tools/stats.py --declines   the editor's rejections, in full
    python tools/stats.py --dropped    real news the importance gate binned

FOUR NUMBERS TO WATCH
  1. Rejection rate. Over about a third means the editor is more likely too
     strict than the news that bad — and nothing human sits between it and your
     readers.
  2. Duplicates by check. A "meaning" check that never fires means the threshold
     is too high; one that fires constantly may be merging different stories.
  3. Expired vs published. More expiring than publishing means you gather far
     more than you allow yourself to post.
  4. Posts per day. Does the pace feel right as a reader?
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config  # noqa: E402
from nodes.sorter import NO_MARKET_CAP  # noqa: E402
from utils import db  # noqa: E402

LINE = "─" * 74


def _section(title: str) -> None:
    print(f"\n{title}")
    print(LINE)


def report(days: int = 3) -> None:
    """Print the full summary."""
    print(f"\n{'=' * 74}")
    print(f"  NEWS CHANNEL — the last {days} day(s)")
    print(f"{'=' * 74}")

    _section("Where everything stands")
    rows = db.query("SELECT status, COUNT(*) n FROM items GROUP BY status ORDER BY n DESC")
    if not rows:
        print("   Nothing collected yet. Run: python main.py collect --once")
        return
    for row in rows:
        print(f"   {row['status']:<18} {row['n']:>6}")

    _section("Published")
    rows = db.query(
        f"""
        SELECT date(sent_at) day, topic, COUNT(*) n
          FROM posts
         WHERE status = 'sent' AND sent_at > datetime('now', '-{int(days)} days')
         GROUP BY day, topic ORDER BY day DESC, n DESC
        """
    )
    if rows:
        current_day = None
        for row in rows:
            if row["day"] != current_day:
                current_day = row["day"]
                total = sum(r["n"] for r in rows if r["day"] == current_day)
                print(f"\n   {current_day}   ({total} post(s))")
            print(f"      {row['topic']:<14} {row['n']:>4}")
    else:
        print("   Nothing published yet.")

    _section("The editor")
    rows = db.query(
        f"""
        SELECT verdict, COUNT(*) n FROM editor_decisions
         WHERE created_at > datetime('now', '-{int(days)} days')
         GROUP BY verdict
        """
    )
    counts = {row["verdict"]: row["n"] for row in rows}
    approved, declined = counts.get("approve", 0), counts.get("decline", 0)
    total = approved + declined

    if total:
        rate = declined / total
        print(f"   approved  {approved:>5}")
        print(f"   rejected  {declined:>5}")
        print(f"   rejection rate: {rate:.0%} of {total} finished post(s)")

        if rate > 0.5:
            print("\n   ⚠️  MORE THAN HALF REJECTED. That is very unlikely to be the")
            print("       news being bad. Read the reasons below — if the editor is")
            print("       wrong, loosen PROMPT in nodes/editor.py.")
        elif rate > 0.33:
            print("\n   ⚠️  Rejection rate is on the high side. Worth reading the")
            print("       reasons below to check the editor is being fair.")

        # Which rules is it actually using?
        rule_counts: dict[str, int] = {}
        for row in db.query(
            f"""SELECT rules_broken FROM editor_decisions
                 WHERE verdict = 'decline'
                   AND created_at > datetime('now', '-{int(days)} days')"""
        ):
            try:
                for rule in json.loads(row["rules_broken"]):
                    rule_counts[rule] = rule_counts.get(rule, 0) + 1
            except (json.JSONDecodeError, TypeError):
                pass

        if rule_counts:
            print("\n   Reasons given:")
            for rule, count in sorted(rule_counts.items(), key=lambda kv: -kv[1]):
                print(f"      {rule:<16} {count:>4}")
    else:
        print("   The editor hasn't judged anything yet.")

    _section("The importance gate")
    rows = db.market_breakdown(days)
    if rows:
        print("   Every scored item, by the market the sorter said must reprice.")
        print(f"   'none' caps an item at {NO_MARKET_CAP}/5, so it never reaches "
              f"the channel.\n")
        print(f"   {'market':<16}{'seen':>7}{'published':>12}")
        for row in rows:
            print(f"   {row['market']:<16}{row['seen']:>7}{row['published'] or 0:>12}")

        none_seen = next((r["seen"] for r in rows if r["market"] == "none"), 0)
        total_seen = sum(r["seen"] for r in rows)
        if total_seen:
            share = none_seen / total_seen
            print(f"\n   {share:.0%} of scored items had no market to reprice.")
            if share > 0.9:
                print("   ⚠️  That is very high. Either your sources are mostly general")
                print("       news rather than market news, or the market definitions in")
                print("       nodes/sorter.py are too narrow. Check with --dropped before")
                print("       loosening anything.")
            elif share < 0.4:
                print("   ⚠️  That is low, and the usual cause is a model reaching for a")
                print("       market to justify a story. Read --dropped and the published")
                print("       posts together: are the 4s really things you would trade on?")
    else:
        print("   Nothing scored yet.")

    _section("Duplicates caught")
    rows = db.query(
        f"""
        SELECT rung, kept, COUNT(*) n FROM dedup_hits
         WHERE created_at > datetime('now', '-{int(days)} days')
         GROUP BY rung, kept ORDER BY n DESC
        """
    )
    if rows:
        names = {
            "title_hash": "2. same headline",
            "fuzzy": "3. same wording",
            "embedding": "4. same subject",
            "judge": "5. same event",
        }
        for row in rows:
            label = names.get(row["rung"], row["rung"])
            note = "  (kept — a guard overruled it)" if row["kept"] else ""
            print(f"   {label:<22} {row['n']:>5}{note}")
        print("\n   (check 1, the same link twice, is handled by the database and not counted)")
    else:
        print("   None caught yet.")

    # The meaning check fails OPEN, so a broken one looks exactly like a quiet
    # one from up here. This is the line that tells them apart.
    failures = db.get_counters().get("meaning_check_failed", 0)
    if failures:
        print(f"\n   ⚠️  The MEANING check failed {failures} time(s) today. Every one")
        print("       of those items was published without it — checks 1-3 compare")
        print("       wording only and cannot match a tweet to an article.")
        print("       Run: python3 tools/check_dedup.py")
    elif not any(r["rung"] == "embedding" for r in rows):
        print("\n   Check 4 (meaning) has not fired. That is either fine — your")
        print("   sources don't overlap — or it is not running at all. Those look")
        print("   identical from here, so confirm with: python3 tools/check_dedup.py")

    _section("Intake versus output")
    today = db.get_counters()
    if today:
        for kind in ("ingested", "irrelevant", "below_importance", "written",
                     "published", "expired",
                     "deduped_headline", "deduped_fuzzy", "deduped_meaning",
                     # Check 5. judge_different is the one to watch: it counts
                     # stories that LOOKED like repeats and were saved from
                     # being binned. judge_error means we failed open and may
                     # have let a real duplicate through.
                     "deduped_judge", "judge_same", "judge_different",
                     "judge_error"):
            if kind in today:
                print(f"   {kind:<20} {today[kind]:>6}")

        expired, published = today.get("expired", 0), today.get("published", 0)
        if expired > published and expired > 5:
            print(f"\n   ⚠️  {expired} item(s) went stale today but only {published} were")
            print("       published. You are collecting more than you allow yourself to")
            print("       post. Either raise MAX_POSTS_PER_HOUR in .env, or drop a few")
            print("       of the noisier sources.")
    else:
        print("   Nothing recorded today yet.")

    # --- Feeds that are failing ---
    # Only feeds still in use — a feed removed from config keeps its old failure
    # count, and reporting that would send you chasing a problem you already fixed.
    rows = db.query("SELECT name, fail_count, last_error FROM sources "
                    "WHERE fail_count > 0 AND enabled = 1 ORDER BY fail_count DESC")
    if rows:
        _section("Feeds having trouble")
        for row in rows:
            print(f"   {row['name']:<18} {row['fail_count']} failure(s) in a row")
            print(f"      {(row['last_error'] or '')[:100]}")

    print(f"\n{LINE}")
    print("   To see exactly what the editor rejected, and the full text of each:")
    print("      python tools/stats.py --declines")
    print("   To see the real news the importance gate decided not to run:")
    print("      python tools/stats.py --dropped\n")


def show_declines(count: int = 10) -> None:
    """Print recent rejections in full, so you can judge whether they were right."""
    rows = db.query(
        "SELECT * FROM editor_decisions WHERE verdict = 'decline' "
        "ORDER BY id DESC LIMIT ?", (count,)
    )
    if not rows:
        print("\n   The editor hasn't rejected anything yet.\n")
        return

    print(f"\n{'=' * 74}")
    print(f"  THE LAST {len(rows)} REJECTION(S) — was the editor right?")
    print(f"{'=' * 74}")

    for row in rows:
        try:
            rules = json.loads(row["rules_broken"])
        except (json.JSONDecodeError, TypeError):
            rules = []

        print(f"\n   {row['created_at']}   confidence {row['confidence']:.2f}")
        print(f"   Rules: {', '.join(rules) or '(none — this would have been overruled)'}")
        print(f"   Reason: {row['reason']}")
        print(LINE)
        print(row["post_html"])
        print(LINE)

    print("\n   If these look like good posts, the editor is too strict.")
    print("   Loosen PROMPT in nodes/editor.py and rehearse with tools/dry_run.py.\n")


def show_dropped(count: int = 25, market: str = "") -> None:
    """Print the real news the importance gate refused to publish.

    Read this the way you read --declines, but with the opposite worry. The
    danger with the editor is that it rejects too much. The danger here runs
    both ways:

      * Too strict, and the channel goes quiet and misses things it should have
        run. You will see stories here you would obviously have posted.
      * Too loose, and it publishes filler. You will see almost nothing here,
        while the channel fills up with items nobody would trade on.

    The 'market' column is the one to look at. An item dropped with market
    'none' is the model saying, in plain words, that nothing has to be repriced.
    If you disagree with that on a story, THAT is the judgement to go and fix in
    nodes/sorter.py — not the number.
    """
    rows = db.recent_low_impact(count, market=market)
    if not rows:
        where = f" with market '{market}'" if market else ""
        print(f"\n   Nothing has been dropped for low impact{where} yet.\n")
        return

    heading = f"  THE LAST {len(rows)} STORIES THE GATE DID NOT RUN"
    if market:
        heading += f" — market '{market}'"
    print(f"\n{'=' * 74}")
    print(heading)
    print(f"  Would you have posted these? That is the whole question.")
    print(f"{'=' * 74}")

    for row in rows:
        print(f"\n   {row['updated_at']}   {row['source_name']}")
        print(f"   {(row['title'] or '')[:100]}")
        print(f"   topic {row['topic'] or '?':<12} market {row['market'] or '?':<14} "
              f"impact {row['importance']}/5")
        print(f"   → {row['status_reason']}")

    print(f"\n{LINE}")
    print("   If good stories are in this list, the gate is too strict: loosen the")
    print("   market definitions or the continuing-story test in nodes/sorter.py.")
    print("   If this list looks correctly boring, the gate is doing its job.\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Show how the channel is doing.")
    parser.add_argument("--days", type=int, default=3)
    parser.add_argument("--declines", action="store_true",
                        help="show recent editor rejections in full")
    parser.add_argument("--dropped", action="store_true",
                        help="show real news the importance gate did not run")
    parser.add_argument("--market", default="",
                        help="with --dropped, show only one market (e.g. none)")
    parser.add_argument("--count", type=int, default=10)
    args = parser.parse_args()

    db.init_db()
    if args.declines:
        show_declines(args.count)
    elif args.dropped:
        show_dropped(max(args.count, 25), market=args.market)
    else:
        report(days=args.days)
