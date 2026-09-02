"""Replays real history through story clustering: what the channel WOULD have posted.

Nothing here touches the live channel or the live tables. It reads items that
were actually published, regroups them into stories, asks the gate whether each
story had moved, and prints the difference.

    python3 tools/replay_stories.py --days 14
    python3 tools/replay_stories.py --day 2026-09-01 --write

--write also calls the writer, so you can read the posts that would have gone
out instead of counting them. That costs real model calls; without it the replay
uses each item's real published text as the stand-in for "what the reader was
told", which is enough to count merges and silences.
"""

from __future__ import annotations

import argparse
import asyncio
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config
from brain import persona_loader
from nodes import stories, writer
from utils import db

BOLD, DIM, RED, GREEN, YELLOW, OFF = (
    "\033[1m", "\033[2m", "\033[31m", "\033[32m", "\033[33m", "\033[0m"
)


def plain(html: str) -> str:
    """The post as a reader sees it, minus the markup and the source credit."""
    text = re.sub(r"\n?(?:🔗|—) .*$", "", html or "", flags=re.MULTILINE)
    return re.sub(r"<[^>]+>", "", text).strip()


def load(since: str, until: str) -> list[dict]:
    """Every item that was actually published in the window, oldest first."""
    rows = db.query(
        """SELECT i.*, p.post_html, p.sent_at, p.telegram_message_id
           FROM items i JOIN posts p ON p.item_id = i.id
           WHERE p.telegram_message_id IS NOT NULL
             AND p.sent_at >= ? AND p.sent_at < ?
           ORDER BY p.sent_at ASC""",
        (since, until),
    )
    return [dict(r) for r in rows]


async def replay(items: list[dict], write: bool) -> tuple[list[stories.Story], list[dict]]:
    """Walk the items in real order, building stories and running the gate."""
    live: list[stories.Story] = []
    timeline: list[dict] = []
    next_id = 1

    persona = persona_loader.load_persona() if write else ""

    for item in items:
        now = stories.parse_time(item["sent_at"])
        vector = await stories.vector_for(item)
        if vector is None:
            continue

        home, score = stories.obvious_home(vector, live, now)
        if home is None:
            home, _why = await stories.place(item, live, now)

        if home is None:
            home = stories.Story(
                id=next_id, headline=(item["title"] or "")[:90], centroid=vector,
            )
            next_id += 1
            live.append(home)
            joined = False
        else:
            joined = True

        home.absorb(item, vector, now)
        verdict = await stories.should_post(home, now)

        # The gate is the second opinion on the placement. When it says the item
        # does not belong, the item leaves rather than being silenced inside the
        # wrong story — otherwise one bad placement quietly kills real news.
        if verdict["verdict"] == "not_this_story":
            home.eject(item)
            home = stories.Story(
                id=next_id, headline=(item["title"] or "")[:90], centroid=vector,
            )
            next_id += 1
            live.append(home)
            joined = False
            home.absorb(item, vector, now)
            verdict = await stories.should_post(home, now)

        entry = {
            "at": now, "item": item, "story": home, "joined": joined,
            "score": score, "posted": verdict["verdict"] == "post",
            "reason": verdict["reason"], "angle": verdict["angle"],
            "merged": len(home.pending), "text": "",
        }

        if verdict["verdict"] == "post":
            if write:
                source = stories.as_source(home)
                text = await writer.execute(
                    source,
                    persona=persona,
                    continuity_text=stories.brief_for_writer(home, verdict["angle"]),
                )
                entry["text"] = plain(text) if text else "(the writer returned nothing)"
            else:
                # Stand-in: what the channel actually told the reader for the
                # item that triggered this post.
                entry["text"] = plain(item["post_html"])

            home.posts.append(entry["text"])
            home.last_post_at = now
            home.posted_items.extend(home.pending)
            home.pending = []

        timeline.append(entry)

    return live, timeline


def report(items: list[dict], live: list[stories.Story], timeline: list[dict],
           show_text: bool) -> None:
    would = sum(1 for e in timeline if e["posted"])
    silent = len(timeline) - would

    print(f"\n{BOLD}РЕЗУЛЬТАТ{OFF}")
    print(f"  было опубликовано на самом деле : {len(items)}")
    print(f"  вышло бы при сюжетах            : {would}")
    print(f"  промолчал бы                    : {silent}  "
          f"({silent / max(len(items), 1) * 100:.0f}%)")
    print(f"  сюжетов                         : {len(live)}")

    multi = [s for s in live if len(s.item_ids) > 1]
    if multi:
        biggest = sorted(multi, key=lambda s: len(s.item_ids), reverse=True)[:8]
        print(f"\n{BOLD}САМЫЕ КРУПНЫЕ СЮЖЕТЫ{OFF}   (сообщений → постов)")
        for s in biggest:
            print(f"  {len(s.item_ids):>3} → {len(s.posts):<2}  {s.headline[:78]}")

    print(f"\n{BOLD}ЛЕНТА{OFF}   {GREEN}зелёное{OFF} = вышло, {DIM}серое{OFF} = промолчал")
    last_day = ""
    for e in timeline:
        day = e["at"].strftime("%d.%m")
        if day != last_day:
            print(f"\n{BOLD}── {day} ──{OFF}")
            last_day = day
        stamp = e["at"].strftime("%H:%M")
        title = (e["item"]["title"] or "")[:74].replace("\n", " ")
        tag = f"с{e['story'].id}"

        if e["posted"]:
            merged = f" {YELLOW}+{e['merged'] - 1}{OFF}" if e["merged"] > 1 else ""
            print(f"  {GREEN}▌{OFF} {stamp} {DIM}{tag:>5}{OFF} {title}{merged}")
            if show_text and e["text"]:
                for line in e["text"].split("\n"):
                    if line.strip():
                        print(f"        {line}")
                print()
        else:
            print(f"  {DIM}·{OFF} {DIM}{stamp} {tag:>5} {title}{OFF}")
            print(f"    {DIM}    ↳ {e['reason'][:96]}{OFF}")


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--days", type=int, default=7, help="how far back to replay")
    parser.add_argument("--day", type=str, default="", help="one day, YYYY-MM-DD")
    parser.add_argument("--write", action="store_true",
                        help="actually write the posts (costs model calls)")
    args = parser.parse_args()

    if args.day:
        since, until = args.day, (
            datetime.fromisoformat(args.day) + timedelta(days=1)
        ).strftime("%Y-%m-%d")
    else:
        until = (datetime.now(timezone.utc) + timedelta(days=1)).strftime("%Y-%m-%d")
        since = (datetime.now(timezone.utc) - timedelta(days=args.days)).strftime("%Y-%m-%d")

    items = load(since, until)
    print(f"{BOLD}Прогон {since} → {until}{OFF}  ·  {len(items)} опубликованных материалов"
          f"{'  ·  с написанием постов' if args.write else ''}")
    if not items:
        print("Нечего прогонять.")
        return

    live, timeline = await replay(items, args.write)
    report(items, live, timeline, show_text=args.write)


if __name__ == "__main__":
    asyncio.run(main())
