"""Runs real queued items through the brain (brain/graph.py) and prints every
decision. Publishes nothing, stores nothing, records no verdicts.

    python tools/test_brain.py [--limit 25] [--topic crypto]

dry_run.py exercises the old hand-written pipeline; this exercises the graph.
It is the place to rehearse graph-only features — continuation threading, the
rewrite loop, persona memory — that dry_run.py knows nothing about.

About $0.002 an item.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config  # noqa: E402
from brain import graph as brain  # noqa: E402
from nodes import editor, sorter, writer  # noqa: E402
from utils import db, logger as log_setup  # noqa: E402

LINE = "─" * 74


def _render(post_html: str, topic: str, url: str, brief: bool = False) -> str:
    """Show the post roughly as a reader would see it (same as dry_run)."""
    text = (post_html
            .replace("<b>", "\033[1m").replace("</b>", "\033[0m")
            .replace("<i>", "\033[3m").replace("</i>", "\033[0m")
            .replace("<code>", "`").replace("</code>", "`"))

    return f"{text}\n\n— Source: {url}"


async def _process(item, index: int, total: int, args) -> str:
    """Run one item through the brain graph and print the trace it left behind."""
    title = (item["title"] or "")[:70]
    header = f"[{index}/{total}] {item['source_name']} · {item['origin']}"

    state = await brain.run_item(item, dry_run=True)
    outcome = state.get("outcome", "?")
    verdict = state.get("sorter_verdict") or {}
    decision = state.get("editor_verdict") or {}
    post = state.get("post_html") or ""

    if outcome == "duplicate":
        if not args.declined:
            print(f"\n{header}")
            print(f"   🔁 DUPLICATE — {title}")
        return "duplicate"

    if outcome == "irrelevant":
        if not args.declined:
            print(f"\n{header}")
            print(f"   ⛔ SKIPPED ({verdict.get('topic', '?')}) — {title}")
            print(f"      reason: {verdict.get('reason', '')}")
        return "irrelevant"

    if outcome == "low_impact":
        if not args.declined:
            print(f"\n{header}")
            print(f"   📉 BELOW THE BAR ({verdict.get('topic', '?')} · market "
                  f"{verdict.get('market', '?')} · {verdict.get('importance', '?')}/5, need "
                  f"{config.MIN_IMPORTANCE}) — {title}")
            print(f"      reason: {verdict.get('reason', '')}")
        return "low_impact"

    if outcome == "write_failed":
        print(f"\n{header}")
        print(f"   ❌ WRITER FAILED — {title}")
        return "write_failed"

    if args.topic and verdict.get("topic") != args.topic:
        return "filtered"

    # A finished post exists — show it with the editor's verdict.
    image_note = "  🖼 with image" if item["image_url"] else ""
    brief_note = "  ⚡ brief" if writer.is_brief(dict(item)) else ""
    ai_note = "" if state.get("used_ai") else "  📋 verbatim"
    print(f"\n{header}")
    print(f"   {verdict.get('topic', '?')} · market {verdict.get('market', '?')} · "
          f"importance {verdict.get('importance', '?')}{image_note}{brief_note}{ai_note} · "
          f"{len(post)} chars")
    print(LINE)
    print(_render(post, verdict.get("topic", ""), item["url"],
                  brief=writer.is_brief(dict(item))))
    print(LINE)

    if outcome == "editor_error":
        print(f"   ⚠️  EDITOR UNREACHABLE — {decision.get('reason', '')}")
        print("      (in the live channel this post would wait and be retried)")
        return "editor_error"

    if outcome == "approved":
        print(f"   ✅ EDITOR APPROVED ({decision.get('confidence', 0):.2f}) — "
              f"{decision.get('reason', '')}")
        return "approved"

    print(f"   ❌ EDITOR REJECTED ({decision.get('confidence', 0):.2f}) "
          f"{decision.get('rules_broken', [])}")
    print(f"      {decision.get('reason', '')}")
    print("      ⚠️  Read this one carefully — was the editor right?")
    return "declined"


async def main() -> None:
    parser = argparse.ArgumentParser(description="Rehearse the brain graph without sending.")
    parser.add_argument("--limit", type=int, default=10, help="how many items (default 10)")
    parser.add_argument("--topic", choices=list(config.VALID_TOPICS),
                        help="only show one topic")
    parser.add_argument("--declined", action="store_true",
                        help="only show posts the editor rejects")
    parser.add_argument("--recent", type=int, metavar="HOURS",
                        help="rehearse on anything fetched in the last N hours, "
                             "regardless of status (default: only queued items). "
                             "Useful for testing continuations, which need "
                             "already-published posts to exist.")
    args = parser.parse_args()

    log_setup.setup(level="WARNING", to_file=False)   # keep the output readable
    db.init_db()

    problems = config.check(require_openrouter=True)
    if problems:
        print("\n❌ Cannot run:\n")
        for problem in problems:
            print(f"   • {problem}")
        print(f"\n   Add it to {config.HERE / '.env'} and try again.\n")
        return

    if args.recent:
        items = db.query(
            "SELECT * FROM items "
            "WHERE fetched_at > datetime('now', ?) "
            "ORDER BY id DESC LIMIT ?",
            (f"-{int(args.recent)} hours", args.limit),
        )
    else:
        items = db.next_queued_items(args.limit)
    if not items:
        print("\nNothing to rehearse on. Fetch some news first:\n")
        print("   python main.py collect --once\n")
        return

    print(f"\nRehearsing {len(items)} item(s) through THE BRAIN GRAPH. Nothing will be sent.")
    print(f"Publishing anything scored {config.MIN_IMPORTANCE}/5 or above.")
    print(f"Models — sorting: {sorter.MODEL}")
    print(f"         writer: {writer.MODEL}")
    print(f"         editor: {editor.MODEL}")
    print(f"Rough cost: about ${len(items) * 0.002:.3f}")

    outcomes: dict[str, int] = {}
    for index, item in enumerate(items, start=1):
        try:
            outcome = await _process(item, index, len(items), args)
        except Exception as error:  # noqa: BLE001 - one bad item shouldn't end the run
            print(f"\n[{index}/{len(items)}] 💥 unexpected error: {error}")
            outcome = "error"
        outcomes[outcome] = outcomes.get(outcome, 0) + 1

    print(f"\n{LINE}")
    print("\n   Summary:")
    labels = {
        "approved": "✅ would be published",
        "declined": "❌ rejected by the editor",
        "duplicate": "🔁 already covered",
        "irrelevant": "⛔ not news, or not our subject",
        "low_impact": "📉 real news, nothing to reprice",
        "write_failed": "❌ the writer failed",
        "editor_error": "⚠️  the editor was unreachable",
        "sorter_error": "⚠️  the sorter was unreachable",
        "filtered": "   (other topics, hidden)",
        "error": "💥 unexpected errors",
    }
    for key, count in sorted(outcomes.items(), key=lambda kv: -kv[1]):
        print(f"      {labels.get(key, key):<32} {count}")

    declined = outcomes.get("declined", 0)
    judged = declined + outcomes.get("approved", 0)
    if judged:
        rate = declined / judged
        print(f"\n      Rejection rate: {rate:.0%} of {judged} finished post(s)")
    print()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nStopped.\n")
