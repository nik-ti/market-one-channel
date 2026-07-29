"""
TOOL: Stats
PURPOSE: Show what the channel has been doing, and — most importantly — what its
         filters have been throwing away and why.
USAGE:   python main.py stats            (last 3 days)
         python main.py stats --days 7
         python tools/stats.py --declines (just the editor's rejections, in full)

THE FOUR NUMBERS TO WATCH
    1. REJECTION RATE. If the editor is turning down more than about a third of
       finished posts, read its reasons below. It is more likely to be too
       strict than the news to be that bad. This is the single most important
       number here, because there is no human check between the editor and your
       readers.

    2. DUPLICATES BY CHECK. If the "meaning" check never fires, either your
       sources don't overlap or the threshold is too high. If it fires
       constantly, it may be merging stories that are genuinely different — the
       detail lines show you exactly what it merged.

    3. EXPIRED vs PUBLISHED. If more items expire than get published, you are
       gathering far more news than you allow yourself to post. Either raise
       MAX_POSTS_PER_HOUR or remove some noisy sources.

    4. POSTS PER DAY. Does the pace feel right as a reader?
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config  # noqa: E402
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

    # --- What is sitting in the database right now ---
    _section("Where everything stands")
    rows = db.query("SELECT status, COUNT(*) n FROM items GROUP BY status ORDER BY n DESC")
    if not rows:
        print("   Nothing collected yet. Run: python main.py collect --once")
        return
    for row in rows:
        print(f"   {row['status']:<18} {row['n']:>6}")

    # --- Published posts per day ---
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

    # --- The editor: the number that matters most ---
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

    # --- Duplicates, broken down by which check caught them ---
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
            "embedding": "4. same meaning",
        }
        for row in rows:
            label = names.get(row["rung"], row["rung"])
            note = "  (kept — a guard overruled it)" if row["kept"] else ""
            print(f"   {label:<22} {row['n']:>5}{note}")
        print("\n   (check 1, the same link twice, is handled by the database and not counted)")
    else:
        print("   None caught yet.")

    # --- Are we collecting more than we can post? ---
    _section("Intake versus output")
    today = db.get_counters()
    if today:
        for kind in ("ingested", "irrelevant", "written", "published", "expired",
                     "deduped_headline", "deduped_fuzzy", "deduped_meaning"):
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
    print("      python tools/stats.py --declines\n")


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


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Show how the channel is doing.")
    parser.add_argument("--days", type=int, default=3)
    parser.add_argument("--declines", action="store_true",
                        help="show recent rejections in full")
    parser.add_argument("--count", type=int, default=10)
    args = parser.parse_args()

    db.init_db()
    if args.declines:
        show_declines(args.count)
    else:
        report(days=args.days)
