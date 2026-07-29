"""
TOOL: Dry run
PURPOSE: Run real items through the whole pipeline and print the results here,
         in your terminal. Nothing is sent to Telegram. Nothing is published.
USAGE:   python tools/dry_run.py                 (10 items)
         python tools/dry_run.py --limit 25      (more)
         python tools/dry_run.py --topic crypto  (only one topic)
         python tools/dry_run.py --declined      (only show what the editor rejects)

WHY THIS TOOL MATTERS MORE THAN THE OTHERS
    Posts on this channel go straight out live — there is no admin channel and
    no approve button. So this is your ONE chance to see what the AI writes, and
    what the AI editor rejects, before either of them is doing it in public.

    Use it properly:
      1. Run it over 20-30 real items.
      2. Read every post. Is that the voice you want for the channel?
      3. Read every 📉 BELOW THE BAR line. Those are real stories the channel
         chose not to run, with the market the sorter said would have to
         reprice. This is where you find out whether the channel is about to be
         full of things nobody trades on — or about to be silent. Tune it in
         PROMPT at the top of nodes/sorter.py.
      4. Read every REJECTION especially carefully. Was the editor right? A
         project on this machine lost 15% of its finished posts to an editor
         nobody checked.
      5. Adjust the prompts — PROMPT at the top of nodes/sorter.py,
         nodes/writer.py and nodes/editor.py — and run it again. Iteration here
         is free and instant.

WHAT IT COSTS
    About $0.002 per item, so a 25-item run is roughly five cents.

WHAT IT CHANGES
    It does store the meaning-vectors it works out (that is useful, and they
    would be computed anyway). It does NOT write posts, record editor verdicts,
    change any item's status, or send anything. You can run it repeatedly on the
    same items.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config  # noqa: E402
from nodes import dedup, editor, sorter, writer  # noqa: E402
from utils import db, logger as log_setup  # noqa: E402

LINE = "─" * 74


def _render(post_html: str, topic: str, url: str, brief: bool = False) -> str:
    """Show the post roughly as a reader would see it in Telegram.

    Telegram renders <b>bold</b>; a terminal doesn't, so we swap the tags for
    the terminal's own bold codes to give a fair impression of the result.
    """
    emoji, hashtag = config.TOPIC_STYLE.get(topic, ("📰", "#news"))
    if brief:
        emoji = config.BRIEF_EMOJI

    text = (post_html
            .replace("<b>", "\033[1m").replace("</b>", "\033[0m")
            .replace("<i>", "\033[3m").replace("</i>", "\033[0m")
            .replace("<code>", "`").replace("</code>", "`"))

    return f"{emoji} {text}\n\n🔗 Source: {url}\n{hashtag}"


async def _process(item, index: int, total: int, args) -> str:
    """Run one item through the full pipeline and print what happened.

    Returns a one-word outcome so the summary at the end can count them up.
    """
    title = (item["title"] or "")[:70]
    header = f"[{index}/{total}] {item['source_name']} · {item['origin']}"

    # --- Duplicate checks 3 and 4 ---
    is_duplicate = await dedup.execute(item, with_meaning=True)
    if is_duplicate:
        if not args.declined:
            print(f"\n{header}")
            print(f"   🔁 DUPLICATE — {title}")
        return "duplicate"

    # --- Is it worth covering, and what is it? ---
    verdict = await sorter.execute(item)
    if not verdict["relevant"]:
        if not args.declined:
            print(f"\n{header}")
            print(f"   ⛔ SKIPPED ({verdict['topic']}) — {title}")
            print(f"      reason: {verdict['reason']}")
        return "irrelevant"

    # The importance gate, exactly as the live loop applies it.
    #
    # This rehearsal used to skip this check and write every relevant item,
    # which quietly made it a rehearsal of a different pipeline: it showed
    # finished posts for stories the channel would never have run, and — worse
    # for tuning — showed none of the stories the gate had thrown away. Since
    # the gate is the main control on what the channel feels like, a rehearsal
    # that ignored it was hiding the setting you most need to see.
    if verdict["importance"] < config.MIN_IMPORTANCE:
        if not args.declined:
            print(f"\n{header}")
            print(f"   📉 BELOW THE BAR ({verdict['topic']} · market "
                  f"{verdict['market']} · {verdict['importance']}/5, need "
                  f"{config.MIN_IMPORTANCE}) — {title}")
            print(f"      reason: {verdict['reason']}")
        return "low_impact"

    if args.topic and verdict["topic"] != args.topic:
        return "filtered"

    # --- Write it ---
    has_image = bool(item["image_url"])
    post = await writer.execute(item, has_image=has_image)
    if not post:
        print(f"\n{header}")
        print(f"   ❌ WRITER FAILED — {title}")
        return "write_failed"

    # --- Judge it ---
    # record=False so rehearsing here doesn't distort the real statistics.
    decision = await editor.execute(item, post, post_id=0, record=False)

    if args.declined and decision["approved"]:
        return "approved"

    # --- Show it ---
    image_note = "  🖼 with image" if has_image else ""
    brief_note = "  ⚡ brief" if writer.is_brief(item) else ""
    print(f"\n{header}")
    print(f"   {verdict['topic']} · market {verdict['market']} · "
          f"importance {verdict['importance']}{image_note} · {len(post)} chars")
    print(LINE)
    print(_render(post, verdict["topic"], item["url"], brief=writer.is_brief(item)))
    print(LINE)

    if decision["error"]:
        print(f"   ⚠️  EDITOR UNREACHABLE — {decision['reason']}")
        print("      (in the live channel this post would wait and be retried)")
        return "editor_error"

    if decision["approved"]:
        print(f"   ✅ EDITOR APPROVED ({decision['confidence']:.2f}) — {decision['reason']}")
        return "approved"

    print(f"   ❌ EDITOR REJECTED ({decision['confidence']:.2f}) "
          f"{decision['rules_broken']}")
    print(f"      {decision['reason']}")
    print("      ⚠️  Read this one carefully — was the editor right?")
    return "declined"


async def main() -> None:
    """Run the pipeline over some queued items and print everything."""
    parser = argparse.ArgumentParser(description="Rehearse the pipeline without sending.")
    parser.add_argument("--limit", type=int, default=10, help="how many items (default 10)")
    parser.add_argument("--topic", choices=list(config.VALID_TOPICS),
                        help="only show one topic")
    parser.add_argument("--declined", action="store_true",
                        help="only show posts the editor rejects")
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

    items = db.next_queued_items(args.limit)
    if not items:
        print("\nNothing is queued. Fetch some news first:\n")
        print("   python main.py collect --once\n")
        return

    print(f"\nRehearsing {len(items)} item(s). Nothing will be sent.")
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
        if rate > 0.5:
            print("      ⚠️  That is high. Before blaming the news, check whether the")
            print("          editor is being too strict — read its reasons above, and")
            print("          consider loosening PROMPT in nodes/editor.py.")
    print()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nStopped.\n")
