# --- The orchestrator: everything starts here ---
#
# WHAT THIS FILE DOES
#   Runs the news channel. It takes a subcommand so one file can do several jobs:
#
#     python main.py initdb     create the database (safe to re-run any time)
#     python main.py check      check your settings without doing anything
#     python main.py collect    read the feeds and the tweet stream, once or forever
#     python main.py publish    turn queued items into posts and send them
#     python main.py run        do both, forever — this is what the service runs
#     python main.py stats      print a summary of how things are going
#
#   The useful flags:
#     --once       do one cycle and stop, instead of looping forever
#     --limit N    only handle N items (handy while testing)
#
# WHERE IT FITS
#   The top of the tree. This file decides WHAT happens WHEN; the actual work
#   lives in nodes/. Read the flow diagram in README.md alongside this.
#
# WHY ONE FILE WITH SUBCOMMANDS
#   "collect" and "publish" are two separate loops that run at different speeds,
#   but they share all their settings and their database. Keeping them in one
#   program means one thing to install, one log, one thing to restart. If web
#   crawling is ever added, that is the moment to split them into two services —
#   and because they are already separate subcommands, that split needs no code
#   changes.
#
# DEPENDENCIES
#   Everything else in this project. Settings from config.py.

from __future__ import annotations

import argparse
import asyncio
import sys

import config
from utils import db, logger as log_setup

log = log_setup.get("main")


# --- Stop early if the settings are wrong, instead of failing confusingly later ---
def _verify_settings(*, need_telegram: bool = False, need_openrouter: bool = False) -> None:
    """Check config and exit with a clear message if something essential is missing."""
    problems = config.check(require_telegram=need_telegram, require_openrouter=need_openrouter)
    if problems:
        print("\n❌ Cannot start — please fix these first:\n")
        for problem in problems:
            print(f"   • {problem}")
        print(f"\n   Settings are read from: {config.HERE / '.env'}")
        print("   Copy .env.example to .env if you haven't yet.\n")
        sys.exit(1)


# --- Set up the database and load the feed list into it ---
def _prepare_database() -> None:
    """Create tables, sync the source list, and purge anything no longer wanted.

    The purge matters: deleting a feed or an X handle from config.py has to take
    effect immediately, not just for future items. Without it, everything
    already collected from a removed source keeps publishing as though nothing
    had changed.
    """
    db.init_db()
    db.sync_sources(config.SOURCES)
    db.drop_unfollowed_x_items(config.X_ACCOUNTS.keys())


# =============================================================================
# THE SUBCOMMANDS
# =============================================================================

def cmd_initdb(_args: argparse.Namespace) -> None:
    """Create the database file and its tables."""
    _prepare_database()
    tables = db.query("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
    print(f"\n✅ Database ready: {config.DB_PATH}\n")
    print("   Tables created:")
    for row in tables:
        if not row["name"].startswith("sqlite_"):
            count = db.query(f"SELECT COUNT(*) AS n FROM {row['name']}")[0]["n"]
            print(f"      {row['name']:<20} {count} row(s)")
    print(f"\n   Feeds loaded from config: {len(config.SOURCES)}")
    print(f"   X accounts followed:      {len(config.X_ACCOUNTS)}\n")


def cmd_check(_args: argparse.Namespace) -> None:
    """Report whether the settings look complete, without touching anything."""
    print("\n--- Settings check ---\n")

    def show(label: str, value: str, secret: bool = False) -> None:
        if not value:
            print(f"   ❌ {label:<22} (not set)")
        elif secret:
            print(f"   ✅ {label:<22} {value[:6]}…{value[-4:]} ({len(value)} chars)")
        else:
            print(f"   ✅ {label:<22} {value}")

    show("TELEGRAM_BOT_TOKEN", config.TELEGRAM_BOT_TOKEN, secret=True)
    show("CHANNEL_ID", config.CHANNEL_ID)
    show("ERROR_CHAT_ID", config.ERROR_CHAT_ID)
    show("OPENROUTER_API_KEY", config.OPENROUTER_API_KEY, secret=True)

    print(f"\n   Feeds in config:     {len(config.SOURCES)}")
    print(f"   X accounts followed: {len(config.X_ACCOUNTS)}")
    print(f"   Posting limits:      {config.MAX_POSTS_PER_HOUR}/hour, "
          f"{config.MAX_POSTS_PER_DAY}/day")
    print(f"   Database:            {config.DB_PATH}")
    print(f"   Database exists:     {'yes' if config.DB_PATH.exists() else 'no — run: python main.py initdb'}")

    problems = config.check()
    if problems:
        print("\n   Problems found:")
        for problem in problems:
            print(f"      • {problem}")
    else:
        print("\n   No problems found in the source and topic lists.")
    print()


def cmd_collect(args: argparse.Namespace) -> None:
    """Read the feeds and the tweet stream, storing anything new."""
    from nodes import collect_loop

    _verify_settings()
    _prepare_database()
    asyncio.run(collect_loop.run(once=args.once))


def cmd_publish(args: argparse.Namespace) -> None:
    """Turn queued items into finished posts and send them to the channel."""
    from nodes import publish_loop

    _verify_settings(need_telegram=True, need_openrouter=True)
    _prepare_database()
    asyncio.run(publish_loop.run(once=args.once, limit=args.limit))


def cmd_run(_args: argparse.Namespace) -> None:
    """Run both loops together, forever. This is what the systemd service starts."""
    from nodes import collect_loop, publish_loop

    _verify_settings(need_telegram=True, need_openrouter=True)
    _prepare_database()

    async def _both() -> None:
        """Start both loops side by side and keep them both alive.

        Each loop is supervised separately: if one crashes it is restarted on its
        own, rather than taking the other one down with it.
        """
        log.info("Starting news channel — collect + publish")
        await asyncio.gather(
            _supervise("collect", collect_loop.run),
            _supervise("publish", publish_loop.run),
        )

    asyncio.run(_both())


async def _supervise(name: str, coro_factory) -> None:
    """Keep one loop running: if it crashes, alert, wait, and start it again.

    Without this, one unhandled error anywhere would stop the whole channel
    silently. With it, a broken feed parser stops collection for 60 seconds and
    publishing carries on untouched.
    """
    from utils import telegram_error

    backoff = 15
    while True:
        try:
            await coro_factory()
            return  # a loop that returns normally (e.g. --once) is done
        except asyncio.CancelledError:
            raise
        except Exception as error:  # noqa: BLE001 - a loop must never die quietly
            log.exception("Loop '%s' crashed — restarting in %ss", name, backoff)
            telegram_error.send_error(str(error), node_name=f"{name}_loop")
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 300)  # back off up to 5 minutes


def cmd_stats(args: argparse.Namespace) -> None:
    """Print a summary of what the channel has been doing."""
    from tools import stats

    _prepare_database()
    stats.report(days=args.days)


# =============================================================================
# COMMAND LINE
# =============================================================================

def main() -> None:
    """Read the command line and run the requested subcommand."""
    parser = argparse.ArgumentParser(
        prog="news-channel",
        description="Automated crypto / geopolitics news channel.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("initdb", help="create the database (safe to re-run)")
    subparsers.add_parser("check", help="check settings without doing anything")

    collect = subparsers.add_parser("collect", help="read feeds and tweets")
    collect.add_argument("--once", action="store_true", help="one cycle, then stop")

    publish = subparsers.add_parser("publish", help="write and send posts")
    publish.add_argument("--once", action="store_true", help="one cycle, then stop")
    publish.add_argument("--limit", type=int, default=None, help="handle at most N items")

    subparsers.add_parser("run", help="collect and publish together, forever")

    stats_cmd = subparsers.add_parser("stats", help="print a summary")
    stats_cmd.add_argument("--days", type=int, default=3, help="how many days to show")

    args = parser.parse_args()

    # Tools print to your terminal; the long-running loops also write to the log
    # file, so there is a record after the fact.
    to_file = args.command in ("collect", "publish", "run")
    log_setup.setup(to_file=to_file)

    handlers = {
        "initdb": cmd_initdb,
        "check": cmd_check,
        "collect": cmd_collect,
        "publish": cmd_publish,
        "run": cmd_run,
        "stats": cmd_stats,
    }
    handlers[args.command](args)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nStopped.")
