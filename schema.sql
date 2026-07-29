-- --- The database: everything the news channel remembers ---
--
-- WHAT THIS FILE DOES
--   Defines the tables. It is run automatically on startup (see utils/db.py),
--   and every statement uses "IF NOT EXISTS", so running it again on a database
--   that already exists changes nothing. It is safe to re-run any time.
--
-- WHERE IT FITS
--   SQLite is a database that lives in a single file (data/news.db). There is no
--   server to install or run — the program opens the file directly. You can look
--   inside it at any time with:  sqlite3 data/news.db
--
-- WHY THE AUDIT TABLES EXIST
--   Two tables here (editor_decisions, dedup_hits) store WHY something was
--   thrown away. That is not bookkeeping for its own sake. A similar project on
--   this machine has a filter that silently destroyed 110 of its 712 finished
--   posts, and nobody noticed for months, because nothing recorded the reason.
--   A filter you cannot inspect is a filter you cannot fix.


-- Write-Ahead Logging. Lets you read the database (e.g. tools/stats.py) while
-- the service is writing to it, without either one blocking the other.
PRAGMA journal_mode = WAL;

-- If two writes ever collide, wait up to 5 seconds instead of failing instantly.
PRAGMA busy_timeout = 5000;

-- Make "REFERENCES" actually enforced, so deleting an item also deletes its post.
PRAGMA foreign_keys = ON;


-- ── sources ──────────────────────────────────────────────────────────────────
-- One row per feed. The real list lives in config.SOURCES, which is what you
-- edit; this table only remembers RUNTIME facts about each feed — the caching
-- tokens that let us poll for free, and whether it has been failing.
CREATE TABLE IF NOT EXISTS sources (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    name            TEXT    NOT NULL UNIQUE,     -- short nickname, e.g. 'coindesk'
    url             TEXT    NOT NULL,
    kind            TEXT    NOT NULL DEFAULT 'rss',  -- room for other methods later
    topic           TEXT    NOT NULL,            -- crypto | geopolitics | ecom
    enabled         INTEGER NOT NULL DEFAULT 1,

    -- Polite polling. A feed hands us these two values; we hand them back next
    -- time, and it replies "304 Not Modified" with no content if nothing changed.
    -- That is what makes checking 20 feeds every 10 minutes essentially free.
    etag            TEXT    DEFAULT '',
    last_modified   TEXT    DEFAULT '',

    last_checked_at TEXT,
    last_ok_at      TEXT,
    fail_count      INTEGER NOT NULL DEFAULT 0,  -- consecutive failures
    last_error      TEXT    DEFAULT '',
    created_at      TEXT    NOT NULL DEFAULT (datetime('now'))
);


-- ── items ────────────────────────────────────────────────────────────────────
-- Every raw thing we have taken in, from a feed or from X.
--
-- This one table does three jobs at once, on purpose:
--   1. the to-do list of things waiting to be turned into posts,
--   2. the memory that lets us recognise a story we have already seen,
--   3. the record of what arrived and what happened to it.
-- Splitting those apart would mean joining them back together on every query.
CREATE TABLE IF NOT EXISTS items (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    origin        TEXT    NOT NULL,              -- 'rss' | 'x'
    source_name   TEXT    NOT NULL,              -- feed nickname, or the X handle
    external_id   TEXT    NOT NULL,              -- tidied-up article URL, or the tweet id
    url           TEXT    DEFAULT '',            -- the link we put in the post
    title         TEXT    DEFAULT '',
    body          TEXT    DEFAULT '',            -- article summary or full tweet text
    image_url     TEXT    DEFAULT '',            -- only ever set for X items
    published_at  TEXT,                          -- when the source published it (UTC)
    fetched_at    TEXT    NOT NULL DEFAULT (datetime('now')),

    -- Duplicate-detection working data (see nodes/dedup.py for what each is for)
    norm_title    TEXT    NOT NULL DEFAULT '',   -- headline reduced to a plain form
    title_hash    TEXT    NOT NULL DEFAULT '',   -- fingerprint of the above
    embedding     BLOB,                          -- the headline's "meaning", as numbers

    -- Pipeline progress
    topic_hint    TEXT    DEFAULT '',            -- what the source claims it is about
    topic         TEXT    DEFAULT '',            -- what the AI decided it is about
    importance    INTEGER NOT NULL DEFAULT 0,    -- 1-5; the queue posts the best first
    status        TEXT    NOT NULL DEFAULT 'queued',
        -- queued          waiting to be processed
        -- duplicate       we already covered this story
        -- irrelevant      not about our topics, or not really news
        -- written         a post exists, waiting on the editor or on room to send
        -- published       it went out
        -- expired         it sat in the queue too long and went stale
        -- failed          something broke repeatedly; see status_reason
        -- skipped_stale   a tweet that was already too old when we read it
        -- skipped_backlog dropped because too many arrived at once
        -- skipped_handle  from an X account this channel does not follow
    status_reason TEXT    DEFAULT '',            -- ALWAYS filled in when status isn't 'queued'
    attempts      INTEGER NOT NULL DEFAULT 0,
    updated_at    TEXT    NOT NULL DEFAULT (datetime('now')),

    -- Duplicate check number 1, enforced by the database itself: we physically
    -- cannot store the same article URL or the same tweet twice. This is what
    -- makes re-reading a feed every 10 minutes harmless.
    UNIQUE(origin, external_id)
);

-- Matches how the publisher picks what to post next: best and freshest first.
CREATE INDEX IF NOT EXISTS idx_items_queue   ON items(status, importance DESC, id DESC);
CREATE INDEX IF NOT EXISTS idx_items_hash    ON items(title_hash);
CREATE INDEX IF NOT EXISTS idx_items_fetched ON items(fetched_at);
CREATE INDEX IF NOT EXISTS idx_items_source  ON items(source_name);


-- ── posts ────────────────────────────────────────────────────────────────────
-- The finished, written post. Kept separate from the item it came from so we
-- always have the exact text that was judged and sent, word for word.
CREATE TABLE IF NOT EXISTS posts (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    item_id             INTEGER NOT NULL REFERENCES items(id) ON DELETE CASCADE,
    topic               TEXT    NOT NULL,
    post_html           TEXT    NOT NULL,        -- Telegram-flavoured HTML
    image_url           TEXT    DEFAULT '',
    has_image           INTEGER NOT NULL DEFAULT 0,
    char_count          INTEGER NOT NULL DEFAULT 0,
    writer_model        TEXT    DEFAULT '',
    status              TEXT    NOT NULL DEFAULT 'draft',
        -- draft | approved | declined | sent | send_failed
    telegram_message_id INTEGER,
    post_url            TEXT    DEFAULT '',      -- public link to the message
    send_attempts       INTEGER NOT NULL DEFAULT 0,
    created_at          TEXT    NOT NULL DEFAULT (datetime('now')),
    sent_at             TEXT,

    -- One post per item. This is what makes a retry safe: if we crash after
    -- sending but before recording it, the retry cannot create a second post.
    UNIQUE(item_id)
);

CREATE INDEX IF NOT EXISTS idx_posts_status ON posts(status, created_at);
CREATE INDEX IF NOT EXISTS idx_posts_sent   ON posts(sent_at);   -- for the hourly cap


-- ── editor_decisions ─────────────────────────────────────────────────────────
-- Every verdict the AI editor gives, approvals as well as rejections, with the
-- exact text it was looking at. This is the table that makes the editor
-- accountable: if it starts eating good posts, this is where you see it.
CREATE TABLE IF NOT EXISTS editor_decisions (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    post_id      INTEGER NOT NULL REFERENCES posts(id) ON DELETE CASCADE,
    item_id      INTEGER NOT NULL,
    verdict      TEXT    NOT NULL,               -- approve | decline
    rules_broken TEXT    NOT NULL DEFAULT '[]',  -- which rules, as a JSON list
    reason       TEXT    NOT NULL DEFAULT '',    -- one plain sentence, always required
    confidence   REAL    NOT NULL DEFAULT 0,
    post_html    TEXT    NOT NULL,               -- frozen copy of what was judged
    model        TEXT    DEFAULT '',
    latency_ms   INTEGER NOT NULL DEFAULT 0,
    attempt      INTEGER NOT NULL DEFAULT 1,
    created_at   TEXT    NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_editor_verdict ON editor_decisions(verdict, created_at);


-- ── dedup_hits ───────────────────────────────────────────────────────────────
-- Why each item was judged a repeat. Same reasoning as the table above: a
-- filter nobody can inspect is a filter nobody can fix.
-- A row with kept=1 is a NEAR miss — one of the safety guards stepped in and
-- refused to merge two stories that looked alike but weren't.
CREATE TABLE IF NOT EXISTS dedup_hits (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    item_id         INTEGER NOT NULL,
    matched_item_id INTEGER,                     -- the older item it collided with
    rung            TEXT    NOT NULL,            -- url | title_hash | fuzzy | embedding
    score           REAL    NOT NULL DEFAULT 0,  -- 0-100 for fuzzy, 0-1 for meaning
    kept            INTEGER NOT NULL DEFAULT 0,  -- 1 = a guard overruled the drop
    detail          TEXT    DEFAULT '',          -- both headlines, for eyeballing
    created_at      TEXT    NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_dedup_item ON dedup_hits(item_id);


-- ── counters ─────────────────────────────────────────────────────────────────
-- Simple daily tallies, so the stats tool can answer "how did today go?"
-- instantly instead of counting through every row in the big tables.
CREATE TABLE IF NOT EXISTS counters (
    day  TEXT    NOT NULL,                       -- YYYY-MM-DD, UTC
    kind TEXT    NOT NULL,                       -- ingested | deduped | published | ...
    n    INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (day, kind)
);


-- ── meta ─────────────────────────────────────────────────────────────────────
-- A tiny notepad for one-off facts. Currently holds the flag that says whether
-- we have ever connected to the tweet stream before, which decides whether a
-- restart catches up on missed tweets or starts fresh.
CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
