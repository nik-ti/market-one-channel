"""Fetches every feed in config.SOURCES once and reports which ones work.

    python tools/check_sources.py

Prints whether each feed worked, how many articles came back, and three sample
headlines. Writes nothing and sends nothing, so it is safe on a live system.

Run it twice in a row: the second run should show several feeds "unchanged",
which proves the free-polling trick is working.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

# Allow running this file directly (python tools/check_sources.py) by making the
# project folder importable.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import httpx  # noqa: E402

import config  # noqa: E402
from nodes import fetch_rss  # noqa: E402
from utils import db, logger as log_setup  # noqa: E402


async def main() -> None:
    """Fetch every configured feed once and print a readable report."""
    log_setup.setup(level="WARNING", to_file=False)   # keep the output clean
    db.init_db()
    db.sync_sources(config.SOURCES)

    sources = db.get_enabled_sources()
    print(f"\nChecking {len(sources)} feed(s)...\n")
    print("=" * 78)

    working, cached, broken = 0, 0, 0

    async with httpx.AsyncClient(follow_redirects=True) as client:
        for source in sources:
            row = dict(source)
            articles, status = await fetch_rss.fetch_one(client, row)

            label = f"{row['name']}  [{row['topic']}]"

            if status == "cached":
                cached += 1
                print(f"\n💤 {label}")
                print(f"   {row['url']}")
                print("   Unchanged since last check (this is good — it costs nothing).")

            elif status.startswith("error"):
                broken += 1
                print(f"\n❌ {label}")
                print(f"   {row['url']}")
                print(f"   FAILED: {status[6:]}")

            elif not articles:
                # Loaded fine but contained nothing we could read. This is the
                # sneakiest failure of all — no error anywhere, just silence —
                # so it is counted as BROKEN, not as working.
                broken += 1
                print(f"\n⚠️  {label}")
                print(f"   {row['url']}")
                print("   LOADED BUT EMPTY: the page came back fine but no articles")
                print("   could be read from it. Usually that means it is a web page")
                print("   rather than a feed, or it uses a format we don't handle.")

            else:
                working += 1
                print(f"\n✅ {label}")
                print(f"   {row['url']}")
                print(f"   {len(articles)} article(s). Most recent:")
                for article in articles[:3]:
                    when = (article.published.strftime("%d %b %H:%M")
                            if article.published else "no date")
                    print(f"      • [{when}] {article.title[:88]}")

    print("\n" + "=" * 78)
    print(f"\n   ✅ working: {working}    💤 unchanged: {cached}    ❌ broken: {broken}\n")

    if broken:
        print("   Fix or delete the broken ones in config.SOURCES.")
        print("   Common causes: the site moved its feed, or that address was")
        print("   never a feed to begin with. Try opening it in a browser —")
        print("   a real feed looks like a wall of XML, not a normal web page.\n")

    if cached and not working:
        print("   Everything came back 'unchanged', which means you have run this")
        print("   recently. That is the caching working as intended. To force a")
        print("   full re-read:  sqlite3 data/news.db \"UPDATE sources SET etag='',")
        print("   last_modified=''\"\n")


if __name__ == "__main__":
    asyncio.run(main())
