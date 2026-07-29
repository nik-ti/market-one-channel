"""
TOOL: Check tweets
PURPOSE: Watch the shared tweet relay live and print what arrives.
USAGE:   python tools/check_tweets.py           (watch until you press Ctrl-C)
         python tools/check_tweets.py --drain   (show what's waiting, then exit)

WHY THIS IS SAFE TO RUN
    It uses a THROWAWAY bookmark, named after the current time, instead of the
    real "news-channel" one. So it cannot skip tweets the live channel hasn't
    processed yet, and it cannot disturb the trading bot either. Run it whenever
    you like, including while everything else is running.

WHAT TO LOOK FOR
    - Tweets appearing at all (if nothing appears for several minutes, the
      accounts are simply quiet — check tweet-relay is running with
      `systemctl status tweet-relay`)
    - The [IMAGE] marker, which is what the channel will attach to a post
    - "not followed" lines: accounts the relay carries for the trading bot that
      this channel deliberately ignores
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config  # noqa: E402
from nodes import fetch_tweets  # noqa: E402
from utils import db, logger as log_setup  # noqa: E402


def _show(tweet) -> None:
    """Print one tweet in a readable form."""
    marker = "  🖼 [IMAGE]" if tweet.image_url else ""
    topic = config.X_ACCOUNTS.get(tweet.handle, "?")
    print(f"\n@{tweet.handle}  [{topic}]{marker}")
    print(f"   {tweet.text[:400]}")
    print(f"   → {tweet.url}")
    if tweet.image_url:
        print(f"   🖼 {tweet.image_url}")


async def main() -> None:
    """Watch or drain the tweet stream, printing whatever turns up."""
    parser = argparse.ArgumentParser(description="Watch the shared tweet relay.")
    parser.add_argument("--drain", action="store_true",
                        help="print what is waiting right now, then exit")
    args = parser.parse_args()

    log_setup.setup(level="INFO", to_file=False)
    db.init_db()

    # Use a one-off bookmark so the real channel's place is never touched.
    throwaway = f"check-{int(time.time())}"
    config.TWEET_STREAM_GROUP = throwaway

    # A throwaway group must start from the END of the belt, not replay history.
    # Forcing the "first run" flag off achieves exactly that.
    db.meta_set("x_stream_initialised", "no")

    print(f"\nStream:    {config.TWEET_STREAM_KEY}  on  {config.REDIS_URL}")
    print(f"Bookmark:  {throwaway}  (throwaway — the live channel is unaffected)")
    print(f"Following: {', '.join('@' + h for h in config.X_ACCOUNTS)}")
    print("\nNote: this starts from NOW, so you will only see tweets posted from")
    print("this moment on. Quiet accounts can take a while.\n")
    print("=" * 78)

    if args.drain:
        tweets = await fetch_tweets.drain_once(max_wait_ms=3000)
        for tweet in tweets:
            _show(tweet)
        print(f"\n\n   {len(tweets)} tweet(s) were waiting.\n")
        return

    print("Watching... press Ctrl-C to stop.\n")
    count = 0
    async for batch in fetch_tweets.stream():
        for tweet in batch:
            count += 1
            _show(tweet)
        print(f"\n   ({count} so far)")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n\nStopped.\n")
