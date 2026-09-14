"""Every SQL query in the project. No other file writes SQL.

This file is deliberately NOT async, unlike the rest of the project. The
database is a local file and every query finishes well under a millisecond, so
there is nothing to wait on and async would add complexity for nothing. A
choice, not an oversight — please do not "fix" it.
"""

from __future__ import annotations

import json
import logging
import sqlite3
from datetime import datetime, timezone
from typing import Any, Iterable

import config

logger = logging.getLogger("news-channel.db")

# The one shared connection. Opened on first use and kept for the life of the
# program, because opening a database file is slower than any query we run.
_conn: sqlite3.Connection | None = None


def conn() -> sqlite3.Connection:
    """Return the open database connection, creating it on first call.

    The settings applied here (WAL, busy_timeout) must be set on the connection
    itself, not just in schema.sql, which is why they are repeated.
    """
    global _conn
    if _conn is None:
        config.DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        _conn = sqlite3.connect(config.DB_PATH, timeout=10.0)

        # Return rows that behave like dictionaries (row["title"]) instead of
        # bare tuples (row[3]) — far easier to read and impossible to misorder.
        _conn.row_factory = sqlite3.Row

        # Let readers and the writer work at the same time without blocking.
        _conn.execute("PRAGMA journal_mode = WAL")
        _conn.execute("PRAGMA busy_timeout = 5000")
        _conn.execute("PRAGMA foreign_keys = ON")
    return _conn


def init_db() -> None:
    """Create any missing tables and indexes by running schema.sql.

    Every statement in that file uses "IF NOT EXISTS", so running this against a
    database full of data is harmless and changes nothing.
    """
    schema = config.SCHEMA_PATH.read_text()
    conn().executescript(schema)
    conn().commit()
    _apply_migrations()
    logger.info("Database ready at %s", config.DB_PATH)


# --- Add columns invented after the database was first created ---
# When a new feature needs a new column, add it to this dictionary rather than
# editing schema.sql alone. Existing databases then gain the column on next
# start, so you never have to delete data to pick up a change.
#
# Format:  "table name": [("column name", "the ALTER TABLE statement"), ...]
_MIGRATIONS: dict[str, list[tuple[str, str]]] = {
    # Which market the sorter said has to reprice because of this item — see
    # nodes/sorter.py. Added after the "market impact" score turned out to be
    # unreviewable without it: a bare 4 tells you nothing about WHY the model
    # thought a trader would care.
    "items": [
        ("market", "ALTER TABLE items ADD COLUMN market TEXT DEFAULT ''"),
        # Dead since stories took over: a follow-up is now just the next post
        # of the same story. Kept so a fresh database matches every existing one.
        ("continuation_of", "ALTER TABLE items ADD COLUMN continuation_of INTEGER DEFAULT NULL"),
        # Which running story this item belongs to. Not a REFERENCES column on
        # purpose: SQLite cannot add a foreign key by ALTER without rebuilding
        # the table, and a cascade from stories would take real items with it.
        ("story_id", "ALTER TABLE items ADD COLUMN story_id INTEGER DEFAULT NULL"),
        # The MP4 behind a video or GIF on X. image_url keeps the thumbnail, so
        # a clip that cannot be sent still has a picture to fall back to.
        ("video_url", "ALTER TABLE items ADD COLUMN video_url TEXT DEFAULT ''"),
        ("video_kind", "ALTER TABLE items ADD COLUMN video_kind TEXT DEFAULT ''"),
    ],
}

# Indexes over migrated columns. They cannot live in schema.sql, which runs
# BEFORE the migrations and would hit a column that does not exist yet — on an
# existing database that is a crash on startup, not a warning.
_MIGRATION_INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_items_story ON items(story_id, status)",
]


def _apply_migrations() -> None:
    """Add any columns listed in _MIGRATIONS that this database doesn't have yet."""
    for table, changes in _MIGRATIONS.items():
        existing = {row["name"] for row in conn().execute(f"PRAGMA table_info({table})")}
        for column, statement in changes:
            if column not in existing:
                logger.info("Migrating: adding %s.%s", table, column)
                conn().execute(statement)

    for statement in _MIGRATION_INDEXES:
        conn().execute(statement)
    conn().commit()


def now_iso() -> str:
    """Current UTC time as 'YYYY-MM-DD HH:MM:SS'.

    Everything in this database is UTC. Mixing time zones in stored data is a
    reliable way to produce bugs that only appear twice a year.
    """
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def today_utc() -> str:
    """Today's date as 'YYYY-MM-DD' in UTC, used as the key for daily counters."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


# =============================================================================
# SOURCES
# =============================================================================

def sync_sources(sources: Iterable[dict]) -> None:
    """Copy the feed list from config.SOURCES into the database.

    config.py is the list you edit; this table is only where each feed's runtime
    state lives (caching tokens, failure counts). So on every startup we push the
    config list in: new feeds get a row, existing ones get their url/topic
    refreshed, and their accumulated state is left untouched.

    Feeds you DELETE from config are disabled rather than removed, so their
    history and the items they produced stay intact.
    """
    wanted = {s["name"] for s in sources}

    for source in sources:
        conn().execute(
            """
            INSERT INTO sources (name, url, kind, topic, enabled)
            VALUES (?, ?, 'rss', ?, 1)
            ON CONFLICT(name) DO UPDATE SET
                url = excluded.url,
                topic = excluded.topic,
                enabled = 1
            """,
            (source["name"], source["url"], source["topic"]),
        )

    # Switch off anything no longer in config.
    for row in conn().execute("SELECT name FROM sources WHERE enabled = 1"):
        if row["name"] not in wanted:
            logger.info("Source '%s' removed from config — disabling", row["name"])
            conn().execute("UPDATE sources SET enabled = 0 WHERE name = ?", (row["name"],))

    # Then throw away any unpublished item from a source that is not in the
    # current config — regardless of when it was disabled.
    #
    # This sweep is deliberately unconditional rather than tied to the moment a
    # source is switched off. That distinction is not academic: the first version
    # only fired on the enabled→disabled transition, so a source disabled in an
    # EARLIER run kept its queued articles, and they carried on publishing. That
    # is exactly how a BBC article reached the channel hours after the feed had
    # been deleted. Written as a sweep, it is idempotent and self-correcting.
    if wanted:
        placeholders = ",".join("?" for _ in wanted)
        dropped = conn().execute(
            f"""
            UPDATE items
               SET status = 'expired',
                   status_reason = 'source no longer in config',
                   updated_at = ?
             WHERE origin = 'rss'
               AND status IN ('queued', 'written')
               AND source_name NOT IN ({placeholders})
            """,
            (now_iso(), *sorted(wanted)),
        ).rowcount
        if dropped:
            logger.info("Dropped %d unpublished item(s) from source(s) no longer "
                        "in config", dropped)

    conn().commit()


def drop_unfollowed_x_items(handles: Iterable[str]) -> int:
    """Throw away queued tweets from X accounts we no longer follow.

    Removing a handle stops new tweets being stored, but anything already queued
    would carry on posting hours later.
    """
    followed = set(handles)
    rows = conn().execute(
        "SELECT DISTINCT source_name FROM items "
        "WHERE origin = 'x' AND status IN ('queued', 'written')"
    )
    stale = [r["source_name"] for r in rows if r["source_name"] not in followed]
    if not stale:
        return 0

    placeholders = ",".join("?" for _ in stale)
    dropped = conn().execute(
        f"""
        UPDATE items
           SET status = 'expired',
               status_reason = 'X account no longer followed',
               updated_at = ?
         WHERE origin = 'x'
           AND status IN ('queued', 'written')
           AND source_name IN ({placeholders})
        """,
        (now_iso(), *stale),
    ).rowcount
    conn().commit()

    if dropped:
        logger.info("Dropped %d queued tweet(s) from unfollowed account(s): %s",
                    dropped, ", ".join(sorted(stale)))
    return dropped


def get_enabled_sources() -> list[sqlite3.Row]:
    """Return every feed we should currently be polling."""
    return list(conn().execute("SELECT * FROM sources WHERE enabled = 1 ORDER BY name"))


def record_source_success(name: str, etag: str, last_modified: str) -> None:
    """Mark a feed as polled successfully, saving its caching tokens for next time."""
    conn().execute(
        """
        UPDATE sources
           SET etag = ?, last_modified = ?, last_checked_at = ?, last_ok_at = ?,
               fail_count = 0, last_error = ''
         WHERE name = ?
        """,
        (etag, last_modified, now_iso(), now_iso(), name),
    )
    conn().commit()


def record_source_failure(name: str, error: str) -> int:
    """Record a failed poll and return how many times it has now failed in a row."""
    conn().execute(
        """
        UPDATE sources
           SET last_checked_at = ?, fail_count = fail_count + 1, last_error = ?
         WHERE name = ?
        """,
        (now_iso(), error[:500], name),
    )
    conn().commit()
    row = conn().execute("SELECT fail_count FROM sources WHERE name = ?", (name,)).fetchone()
    return row["fail_count"] if row else 0


# =============================================================================
# ITEMS
# =============================================================================

def insert_item(
    *,
    origin: str,
    source_name: str,
    external_id: str,
    url: str = "",
    title: str = "",
    body: str = "",
    image_url: str = "",
    video_url: str = "",
    video_kind: str = "",
    published_at: str | None = None,
    norm_title: str = "",
    title_hash: str = "",
    topic_hint: str = "",
    status: str = "queued",
    status_reason: str = "",
) -> int | None:
    """Store a new item. Returns its id, or None if we already had it.

    None is duplicate check 1 and by far the most common outcome — re-reading a
    feed hands back the same articles. Normal, not an error.
    """
    try:
        cursor = conn().execute(
            """
            INSERT INTO items (
                origin, source_name, external_id, url, title, body, image_url,
                video_url, video_kind,
                published_at, norm_title, title_hash, topic_hint,
                status, status_reason, fetched_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                origin, source_name, external_id, url, title,
                body[: config.MAX_BODY_CHARS], image_url, video_url, video_kind,
                published_at,
                norm_title, title_hash, topic_hint,
                status, status_reason, now_iso(), now_iso(),
            ),
        )
        conn().commit()
        return cursor.lastrowid
    except sqlite3.IntegrityError:
        # The UNIQUE(origin, external_id) rule fired: we have seen this before.
        return None


def title_hash_seen(title_hash: str, hours: int) -> sqlite3.Row | None:
    """Check 2: an earlier item with the same headline fingerprint.

    Catches one story arriving at two addresses — syndication, a changed link.
    """
    if not title_hash:
        return None
    return conn().execute(
        f"""
        SELECT id, title, source_name FROM items
         WHERE title_hash = ?
           AND fetched_at > datetime('now', '-{int(hours)} hours')
         ORDER BY id LIMIT 1
        """,
        (title_hash,),
    ).fetchone()


def recent_titles(hours: int, exclude_item_id: int | None = None) -> list[tuple[int, str]]:
    """Check 3: (id, normalised headline) for items seen recently."""
    rows = conn().execute(
        f"""
        SELECT id, norm_title FROM items
         WHERE norm_title != ''
           AND fetched_at > datetime('now', '-{int(hours)} hours')
           AND id != ?
        """,
        (exclude_item_id or -1,),
    )
    return [(row["id"], row["norm_title"]) for row in rows]


def recent_embeddings(
    hours: int,
    exclude_item_id: int | None = None,
    *,
    near_time: str | None = None,
    max_gap_hours: int | None = None,
) -> list[tuple[int, bytes]]:
    """Return (id, meaning-vector) for items already made sense of. Feeds check 4.

    THE TIME GATE (near_time + max_gap_hours) is an accuracy fix, not a
    performance trick. Recurring reports — daily ETF flows, weekly roundups —
    are near-identical day to day and score extremely highly, so yesterday's
    edition is the most dangerous thing in the pool. The cheapest answer is to
    refuse to look at it.
    """
    where = [
        "embedding IS NOT NULL",
        f"fetched_at > datetime('now', '-{int(hours)} hours')",
        "id != ?",
    ]
    params: list = [exclude_item_id or -1]

    if near_time and max_gap_hours is not None:
        where.append(
            "abs(julianday(fetched_at) - julianday(?)) * 24.0 <= ?"
        )
        params += [near_time, float(max_gap_hours)]

    rows = conn().execute(
        f"SELECT id, embedding FROM items WHERE {' AND '.join(where)}",
        tuple(params),
    )
    return [(row["id"], row["embedding"]) for row in rows]


def set_item_embedding(item_id: int, blob: bytes) -> None:
    """Save an item's meaning-vector so future items can be compared against it."""
    conn().execute("UPDATE items SET embedding = ? WHERE id = ?", (blob, item_id))
    conn().commit()


def set_item_status(item_id: int, status: str, reason: str = "") -> None:
    """Move an item to a new stage, always recording why.

    An item that vanished with no explanation is the failure this project is
    built to avoid, so the reason is not optional in spirit.
    """
    conn().execute(
        "UPDATE items SET status = ?, status_reason = ?, updated_at = ? WHERE id = ?",
        (status, reason[:500], now_iso(), item_id),
    )
    conn().commit()


def set_item_sorting(item_id: int, topic: str, importance: int,
                     market: str = "") -> None:
    """Record what the sorter decided. `market` is stored even for dropped items,
    which is what lets tools/stats.py --dropped show why the gate binned them.
    """
    conn().execute(
        "UPDATE items SET topic = ?, importance = ?, market = ?, updated_at = ? "
        "WHERE id = ?",
        (topic, importance, market, now_iso(), item_id),
    )
    conn().commit()


def bump_attempts(item_id: int) -> int:
    """Count one more failed try on an item and return the new total."""
    conn().execute(
        "UPDATE items SET attempts = attempts + 1, updated_at = ? WHERE id = ?",
        (now_iso(), item_id),
    )
    conn().commit()
    row = conn().execute("SELECT attempts FROM items WHERE id = ?", (item_id,)).fetchone()
    return row["attempts"] if row else 0


def get_item(item_id: int) -> sqlite3.Row | None:
    """Fetch one item by id."""
    return conn().execute("SELECT * FROM items WHERE id = ?", (item_id,)).fetchone()


def next_queued_items(limit: int) -> list[sqlite3.Row]:
    """The items most deserving of being posted next: importance, then newest.

    When more news arrives than we can post, the best and freshest wins and the
    rest go stale. An old "breaking" story is worse than no story.
    """
    return list(conn().execute(
        """
        SELECT * FROM items
         WHERE status = 'queued' AND attempts < ?
         ORDER BY importance DESC, id DESC
         LIMIT ?
        """,
        (config.MAX_ATTEMPTS, limit),
    ))


def expire_stale_items(ttl_minutes: int) -> int:
    """Bin queued items that have waited too long. Returns how many were binned."""
    cursor = conn().execute(
        f"""
        UPDATE items
           SET status = 'expired',
               status_reason = 'sat in the queue longer than {int(ttl_minutes)} minutes',
               updated_at = ?
         WHERE status = 'queued'
           AND fetched_at < datetime('now', '-{int(ttl_minutes)} minutes')
        """,
        (now_iso(),),
    )
    conn().commit()
    return cursor.rowcount


def trim_queue(max_size: int) -> int:
    """If the queue is over its size limit, bin the least important, oldest items.

    A second safety net beyond the time limit above: if news suddenly floods in,
    the queue stops growing rather than building a backlog nobody will ever read.
    """
    total = conn().execute(
        "SELECT COUNT(*) AS n FROM items WHERE status = 'queued'"
    ).fetchone()["n"]
    if total <= max_size:
        return 0

    cursor = conn().execute(
        """
        UPDATE items
           SET status = 'expired', status_reason = 'queue over size limit', updated_at = ?
         WHERE id IN (
             SELECT id FROM items
              WHERE status = 'queued'
              ORDER BY importance ASC, id ASC
              LIMIT ?
         )
        """,
        (now_iso(), total - max_size),
    )
    conn().commit()
    return cursor.rowcount


def recent_low_impact(limit: int, market: str = "") -> list[sqlite3.Row]:
    """Return real news the importance gate refused to publish, newest first.

    The counterpart to recent editor rejections. The editor has been auditable
    since day one; the importance gate was not, even though it throws away far
    more — and unlike the editor it drops items BEFORE any post exists, so
    nothing else records that they were ever considered.

    Pass a market name to see only what was dropped in one category. Asking for
    'none' is the useful one: it shows every story the model said had no
    consequence for any market, which is the judgement most worth checking.
    """
    sql = """
        SELECT id, source_name, title, topic, market, importance,
               status_reason, updated_at
          FROM items
         WHERE status = 'low_impact'
    """
    params: list[Any] = []
    if market:
        sql += " AND market = ?"
        params.append(market)
    sql += " ORDER BY id DESC LIMIT ?"
    params.append(limit)
    return list(conn().execute(sql, tuple(params)))


def market_breakdown(days: int) -> list[sqlite3.Row]:
    """Count scored items by market and outcome, for the stats report.

    Shows where the gate is spending its 'none' verdicts. If a market you care
    about never appears, either no source covers it or the prompt's definition
    of it is too narrow.
    """
    return list(conn().execute(
        f"""
        SELECT market,
               COUNT(*) AS seen,
               SUM(CASE WHEN status = 'published' THEN 1 ELSE 0 END) AS published
          FROM items
         WHERE market != ''
           AND updated_at > datetime('now', '-{int(days)} days')
         GROUP BY market
         ORDER BY seen DESC
        """
    ))


# =============================================================================
# DUPLICATE AUDIT TRAIL
# =============================================================================

def log_dedup_hit(
    *, item_id: int, matched_item_id: int | None, rung: str,
    score: float, kept: bool, detail: str,
) -> None:
    """Record that two items looked like the same story, and what we did about it.

    Called for drops AND for near-misses that a guard rescued, so the filter can
    be reviewed later with tools/stats.py.
    """
    conn().execute(
        """
        INSERT INTO dedup_hits (item_id, matched_item_id, rung, score, kept, detail, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (item_id, matched_item_id, rung, score, 1 if kept else 0, detail[:1000], now_iso()),
    )
    conn().commit()


# =============================================================================
# POSTS
# =============================================================================

def create_post(
    *, item_id: int, topic: str, post_html: str, image_url: str,
    writer_model: str,
) -> int | None:
    """Store a finished post. Returns its id, or None if this item already has one.

    The "already has one" guard is what stops a crash mid-send turning into two
    identical messages in the channel.
    """
    try:
        cursor = conn().execute(
            """
            INSERT INTO posts (
                item_id, topic, post_html, image_url, has_image,
                char_count, writer_model, status, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, 'draft', ?)
            """,
            (
                item_id, topic, post_html, image_url, 1 if image_url else 0,
                len(post_html), writer_model, now_iso(),
            ),
        )
        conn().commit()
        return cursor.lastrowid
    except sqlite3.IntegrityError:
        return None


def get_post_by_item(item_id: int) -> sqlite3.Row | None:
    """Fetch the post belonging to an item, if one has been written."""
    return conn().execute("SELECT * FROM posts WHERE item_id = ?", (item_id,)).fetchone()


def set_post_status(post_id: int, status: str) -> None:
    """Move a post to a new stage: draft → approved/declined → sent."""
    conn().execute("UPDATE posts SET status = ? WHERE id = ?", (status, post_id))
    conn().commit()


def update_post_text(post_id: int, post_html: str) -> None:
    """Replace the text of an existing post. Used by the brain's rewrite loop.

    The rewrite loop sends a rejected draft back to the writer and gets a fixed
    version. The post row already exists (one per item, enforced by the
    database), so the new text REPLACES the old — the rejected draft is not
    lost, though: the editor's decision log keeps a frozen copy of exactly what
    it judged, which is where rejected text is meant to live.
    """
    conn().execute(
        "UPDATE posts SET post_html = ?, char_count = ?, status = 'draft' WHERE id = ?",
        (post_html, len(post_html), post_id),
    )
    conn().commit()


def mark_post_sent(post_id: int, message_id: int, post_url: str) -> None:
    """Record that a post reached the channel, and where it landed."""
    conn().execute(
        """
        UPDATE posts
           SET status = 'sent', telegram_message_id = ?, post_url = ?, sent_at = ?
         WHERE id = ?
        """,
        (message_id, post_url, now_iso(), post_id),
    )
    conn().commit()


def get_recent_sent_posts(limit: int) -> list[sqlite3.Row]:
    """Return the most recently published posts, newest first.

    Used by the brain's persona memory (step 5): the writer can see the last few
    posts so its tone stays consistent. Only visible text is returned.
    """
    return list(conn().execute(
        "SELECT id, post_html, sent_at FROM posts "
        "WHERE status = 'sent' AND post_html != '' "
        "ORDER BY sent_at DESC, id DESC LIMIT ?",
        (limit,),
    ))


def bump_send_attempts(post_id: int) -> int:
    """Count one more failed send attempt and return the new total."""
    conn().execute("UPDATE posts SET send_attempts = send_attempts + 1 WHERE id = ?", (post_id,))
    conn().commit()
    row = conn().execute("SELECT send_attempts FROM posts WHERE id = ?", (post_id,)).fetchone()
    return row["send_attempts"] if row else 0


def posts_sent_since(minutes: int) -> int:
    """How many posts we have sent in the last N minutes — powers the rate caps."""
    return conn().execute(
        f"""
        SELECT COUNT(*) AS n FROM posts
         WHERE status = 'sent' AND sent_at > datetime('now', '-{int(minutes)} minutes')
        """
    ).fetchone()["n"]


def seconds_since_last_post() -> float:
    """How long since the last message went out. Large number if we never have."""
    row = conn().execute(
        "SELECT MAX(sent_at) AS last FROM posts WHERE status = 'sent'"
    ).fetchone()
    if not row or not row["last"]:
        return 1e9
    last = datetime.strptime(row["last"], "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - last).total_seconds()


# =============================================================================
# STORIES
# =============================================================================
# A story is the unit of work: items join one, and it posts when it has moved.
# Nothing about a story is held in memory between items — every field of a
# Story object is rebuilt from these queries, which is why a restart in the
# middle of a developing story costs nothing.

def create_story(*, headline: str, summary: str, item_id: int, at: str) -> int:
    """Open a story and put its first item in it. Returns the new story id.

    Both writes share ONE transaction. Done as two commits, a crash in between
    leaves a story with no items in it, which then shows up in the placement
    prompt as an empty candidate for the rest of the day.
    """
    cursor = conn().execute(
        "INSERT INTO stories (headline, summary, first_at, last_item_at) "
        "VALUES (?, ?, ?, ?)",
        (headline[:200], summary[:300], at, at),
    )
    story_id = int(cursor.lastrowid)
    conn().execute(
        "UPDATE items SET story_id = ?, updated_at = ? WHERE id = ?",
        (story_id, at, item_id),
    )
    conn().commit()
    return story_id


def attach_item_to_story(item_id: int, story_id: int) -> None:
    """Put an existing item into an existing story."""
    at = now_iso()
    conn().execute(
        "UPDATE items SET story_id = ?, updated_at = ? WHERE id = ?",
        (story_id, at, item_id),
    )
    conn().execute(
        "UPDATE stories SET last_item_at = ? WHERE id = ?", (at, story_id),
    )
    conn().commit()


def detach_item_from_story(item_id: int) -> None:
    """Take an item back out. Only the gate's "this does not belong here" uses it."""
    conn().execute(
        "UPDATE items SET story_id = NULL, updated_at = ? WHERE id = ?",
        (now_iso(), item_id),
    )
    conn().commit()


def get_story(story_id: int) -> sqlite3.Row | None:
    """One story row, or None."""
    return conn().execute("SELECT * FROM stories WHERE id = ?", (story_id,)).fetchone()


def load_live_stories(idle_hours: int, limit: int) -> list[sqlite3.Row]:
    """Open stories a new item could still join, most recently active first."""
    return list(conn().execute(
        f"""
        SELECT * FROM stories
         WHERE status = 'live'
           AND last_item_at > datetime('now', '-{int(idle_hours)} hours')
         ORDER BY last_item_at DESC
         LIMIT ?
        """,
        (limit,),
    ))


def get_story_items(story_id: int) -> list[sqlite3.Row]:
    """Every item in a story, oldest first. The caller splits them on status."""
    return list(conn().execute(
        "SELECT * FROM items WHERE story_id = ? ORDER BY id ASC", (story_id,),
    ))


def get_story_posts(story_id: int) -> list[sqlite3.Row]:
    """What the channel has already published on a story, oldest first."""
    return list(conn().execute(
        """
        SELECT p.id, p.post_html, p.sent_at, p.telegram_message_id, p.item_id
          FROM posts p JOIN items i ON i.id = p.item_id
         WHERE i.story_id = ? AND p.status = 'sent'
         ORDER BY p.sent_at ASC, p.id ASC
        """,
        (story_id,),
    ))


def record_story_post(story_id: int, published_item_id: int, summary: str) -> None:
    """Book a story post that has actually gone out.

    One post covers several items, so every other item still waiting on this
    story is now covered too and must stop being a candidate for anything.

    ONLY call this after the send succeeded — it marks items as covered, and
    doing that for a message that never arrived silently buries their content.
    Idempotent, because it only touches items still queued or held, and two
    call sites reach it (the graph, and publish_loop's resend fast path).
    """
    at = now_iso()
    conn().execute(
        "UPDATE stories SET last_post_at = ?, summary = ? WHERE id = ?",
        (at, summary[:300], story_id),
    )
    conn().execute(
        """
        UPDATE items
           SET status = 'merged',
               status_reason = ?,
               updated_at = ?
         WHERE story_id = ? AND id != ? AND status IN ('queued', 'held')
        """,
        (f"covered by the story {story_id} post written from item {published_item_id}",
         at, story_id, published_item_id),
    )
    conn().commit()


def close_stale_stories(idle_hours: int, max_hours: int) -> list[sqlite3.Row]:
    """End stories nothing has added to, and stories that have run too long.

    Returns the rows that were closed WITH how many items they still had
    waiting, because a story closing on unposted content is the one way this
    design can quietly drop something, and it should be visible in the log.
    """
    doomed = list(conn().execute(
        f"""
        SELECT s.id, s.headline,
               (SELECT COUNT(*) FROM items i
                 WHERE i.story_id = s.id AND i.status IN ('queued', 'held')) AS waiting
          FROM stories s
         WHERE s.status = 'live'
           AND (s.last_item_at < datetime('now', '-{int(idle_hours)} hours')
                OR s.first_at  < datetime('now', '-{int(max_hours)} hours'))
        """
    ))
    if doomed:
        conn().executemany(
            "UPDATE stories SET status = 'closed' WHERE id = ?",
            [(row["id"],) for row in doomed],
        )
        conn().commit()
    return doomed


def recent_held(limit: int) -> list[sqlite3.Row]:
    """Items the story gate decided not to post, newest first.

    The counterpart to recent_low_impact(). This is the new pile where a real
    story can go quiet — the gate saying "the reader already has this" when it
    was actually something else — so it has to be readable. See stats.py --held.
    """
    return list(conn().execute(
        """
        SELECT i.id, i.source_name, i.title, i.story_id, i.status_reason,
               i.updated_at, s.headline AS story_headline
          FROM items i LEFT JOIN stories s ON s.id = i.story_id
         WHERE i.status = 'held'
         ORDER BY i.id DESC LIMIT ?
        """,
        (limit,),
    ))


# =============================================================================
# EDITOR AUDIT TRAIL
# =============================================================================

def log_editor_decision(
    *, post_id: int, item_id: int, verdict: str, rules_broken: list[str],
    reason: str, confidence: float, post_html: str, model: str,
    latency_ms: int, attempt: int = 1,
) -> None:
    """Record an editor verdict — approvals as well as rejections.

    Approvals are logged too, not just rejections, because a decline rate is only
    meaningful if you also counted the approvals.
    """
    conn().execute(
        """
        INSERT INTO editor_decisions (
            post_id, item_id, verdict, rules_broken, reason, confidence,
            post_html, model, latency_ms, attempt, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            post_id, item_id, verdict, json.dumps(rules_broken), reason[:1000],
            confidence, post_html, model, latency_ms, attempt, now_iso(),
        ),
    )
    conn().commit()


def recent_decline_rate(window: int) -> tuple[float, int]:
    """Return (share declined, how many decisions we looked at) for recent verdicts.

    Used to raise the alarm if the editor starts rejecting nearly everything —
    which usually means the editor is broken, not the posts.
    """
    rows = list(conn().execute(
        "SELECT verdict FROM editor_decisions ORDER BY id DESC LIMIT ?", (window,)
    ))
    if not rows:
        return 0.0, 0
    declines = sum(1 for r in rows if r["verdict"] == "decline")
    return declines / len(rows), len(rows)


# =============================================================================
# COUNTERS AND NOTES
# =============================================================================

def bump_counter(kind: str, n: int = 1) -> None:
    """Add to today's tally for one kind of event (ingested, published, ...)."""
    conn().execute(
        """
        INSERT INTO counters (day, kind, n) VALUES (?, ?, ?)
        ON CONFLICT(day, kind) DO UPDATE SET n = n + excluded.n
        """,
        (today_utc(), kind, n),
    )
    conn().commit()


def get_counters(day: str | None = None) -> dict[str, int]:
    """Return all of a day's tallies as a plain dictionary. Defaults to today."""
    rows = conn().execute(
        "SELECT kind, n FROM counters WHERE day = ?", (day or today_utc(),)
    )
    return {row["kind"]: row["n"] for row in rows}


def meta_get(key: str, default: str = "") -> str:
    """Read a one-off saved fact."""
    row = conn().execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else default


def meta_set(key: str, value: str) -> None:
    """Save a one-off fact."""
    conn().execute(
        "INSERT INTO meta (key, value) VALUES (?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (key, str(value)),
    )
    conn().commit()


def query(sql: str, params: tuple[Any, ...] = ()) -> list[sqlite3.Row]:
    """Run a SELECT and return the rows. Used by tools/stats.py for reporting."""
    return list(conn().execute(sql, params))
