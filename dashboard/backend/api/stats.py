"""GET /api/v1/stats — everything the Stats tab draws, from one call.

Takes ?range= (24h | 7d | 30d | all, default 7d). Every breakdown below is
scoped to that window, measured on items.fetched_at for things that happened
to an item and on posts.sent_at for things that went out. Three fields keep
their old, window-free meaning because other tabs lean on them:

  sources_count   every source ever seen (the Posts tab's source dropdown)
  gate_outcomes   the four-bucket published/rejected/held/expired split
  trends          items per hour over the last 24h

`status` on an item can be one of many fine-grained values (irrelevant,
low_impact, duplicate, merged, failed, skipped_*...). gate_outcomes folds
everything that isn't published/held/expired into "rejected"; the newer
status_breakdown keeps them apart, because "why didn't it post" is the
question the Stats tab is mostly for.

Takes an optional ?channel=, resolved by channel_resolver (defaults to
markets, never .env). A channel with no database yet returns an
empty-but-valid response with "ready": false instead of an error.
"""

from __future__ import annotations

import json
from collections import Counter
from statistics import median
from typing import Literal

from fastapi import APIRouter, Query

import paths
from channel_resolver import resolve_channel
from db_connector import query, query_one

router = APIRouter()

_HELD_OR_TERMINAL = {"published", "held", "expired"}

_EMPTY_GATE_OUTCOMES = {"published": 0, "rejected": 0, "held": 0, "expired": 0}

# SQLite datetime() modifiers per range; "all" has no lower bound.
_RANGE_MODIFIER = {"24h": "-24 hours", "7d": "-7 days", "30d": "-30 days", "all": None}
# The window before this one, for the "vs previous" deltas on the KPI tiles.
_PREVIOUS_MODIFIER = {"24h": "-48 hours", "7d": "-14 days", "30d": "-60 days"}

Range = Literal["24h", "7d", "30d", "all"]


def _since(column: str, modifier: str | None) -> tuple[str, tuple]:
    """A WHERE fragment bounding `column` to the window, and its params."""
    if modifier is None:
        return "1 = 1", ()
    return f"{column} >= datetime('now', ?)", (modifier,)


def _between(column: str, start: str, end: str) -> tuple[str, tuple]:
    return f"{column} >= datetime('now', ?) AND {column} < datetime('now', ?)", (start, end)


def _empty(name: str, window: str) -> dict:
    return {
        "channel": name,
        "ready": False,
        "range": window,
        "sources_count": [],
        "gate_outcomes": dict(_EMPTY_GATE_OUTCOMES),
        "trends": [],
        "summary": None,
        "activity": [],
        "status_breakdown": [],
        "sources": [],
        "importance": [],
        "markets": [],
        "editor": {"approve": 0, "decline": 0, "top_rules": []},
        "dedup": [],
        "hour_of_day": [],
    }


def _count(sql: str, params: tuple, channel: str) -> int:
    row = query_one(sql, params, channel=channel)
    return int(row["n"] or 0) if row else 0


def _window_totals(item_where: str, item_params: tuple,
                   post_where: str, post_params: tuple, channel: str) -> dict:
    return {
        "ingested": _count(f"SELECT COUNT(*) AS n FROM items WHERE {item_where}", item_params, channel),
        "published": _count(
            f"SELECT COUNT(*) AS n FROM posts WHERE status = 'sent' AND {post_where}",
            post_params, channel,
        ),
    }


@router.get("/stats")
def get_stats(
    channel: str | None = Query(default=None, description="Which channel's database to read"),
    window: Range = Query(default="7d", alias="range", description="Time window for the breakdowns"),
):
    name = resolve_channel(channel)

    if not paths.database_ready(name):
        return _empty(name, window)

    modifier = _RANGE_MODIFIER[window]
    item_where, item_params = _since("fetched_at", modifier)
    post_where, post_params = _since("sent_at", modifier)

    # ── the three original views ────────────────────────────────────────────
    source_rows = query(
        "SELECT source_name, COUNT(*) AS count FROM items GROUP BY source_name ORDER BY count DESC",
        channel=name,
    )

    all_status_rows = query("SELECT status, COUNT(*) AS count FROM items GROUP BY status", channel=name)
    gate_outcomes = dict(_EMPTY_GATE_OUTCOMES)
    for row in all_status_rows:
        bucket = row["status"] if row["status"] in _HELD_OR_TERMINAL else "rejected"
        gate_outcomes[bucket] += row["count"]

    trend_rows = query(
        """
        SELECT strftime('%Y-%m-%d %H:00', fetched_at) AS hour, COUNT(*) AS count
        FROM items
        WHERE fetched_at >= datetime('now', '-24 hours')
        GROUP BY hour
        ORDER BY hour ASC
        """,
        channel=name,
    )

    # ── headline numbers ────────────────────────────────────────────────────
    current = _window_totals(item_where, item_params, post_where, post_params, name)
    previous = None
    if window in _PREVIOUS_MODIFIER:
        prev_items = _between("fetched_at", _PREVIOUS_MODIFIER[window], modifier)
        prev_posts = _between("sent_at", _PREVIOUS_MODIFIER[window], modifier)
        previous = _window_totals(*prev_items, *prev_posts, name)

    # Minutes from an item arriving to its post going out. Median, not mean:
    # one post forced through a day later should not move the headline.
    latency_rows = query(
        f"""
        SELECT (julianday(p.sent_at) - julianday(i.fetched_at)) * 1440 AS minutes
        FROM posts p JOIN items i ON i.id = p.item_id
        WHERE p.status = 'sent' AND p.sent_at IS NOT NULL AND {post_where.replace('sent_at', 'p.sent_at')}
        """,
        post_params,
        channel=name,
    )
    latencies = [r["minutes"] for r in latency_rows if r["minutes"] is not None and r["minutes"] >= 0]

    summary = {
        **current,
        "previous": previous,
        "publish_rate": (current["published"] / current["ingested"]) if current["ingested"] else None,
        "median_minutes_to_publish": median(latencies) if latencies else None,
        "queued_now": _count(
            "SELECT COUNT(*) AS n FROM items WHERE status IN ('queued', 'written')", (), name
        ),
        "held_now": _count("SELECT COUNT(*) AS n FROM items WHERE status = 'held'", (), name),
        "live_stories": _count("SELECT COUNT(*) AS n FROM stories WHERE status = 'live'", (), name),
        "last_published_at": (query_one(
            "SELECT MAX(sent_at) AS t FROM posts WHERE status = 'sent'", channel=name
        ) or {}).get("t"),
    }

    # ── activity over time: hourly for a day, daily otherwise ───────────────
    bucket_fmt = "%Y-%m-%d %H:00" if window == "24h" else "%Y-%m-%d"
    ingested_by_bucket = query(
        f"SELECT strftime('{bucket_fmt}', fetched_at) AS bucket, COUNT(*) AS n "
        f"FROM items WHERE {item_where} GROUP BY bucket",
        item_params, channel=name,
    )
    published_by_bucket = query(
        f"SELECT strftime('{bucket_fmt}', sent_at) AS bucket, COUNT(*) AS n "
        f"FROM posts WHERE status = 'sent' AND {post_where} GROUP BY bucket",
        post_params, channel=name,
    )
    activity: dict[str, dict] = {}
    for row in ingested_by_bucket:
        if row["bucket"]:
            activity.setdefault(row["bucket"], {"bucket": row["bucket"], "ingested": 0, "published": 0})
            activity[row["bucket"]]["ingested"] = row["n"]
    for row in published_by_bucket:
        if row["bucket"]:
            activity.setdefault(row["bucket"], {"bucket": row["bucket"], "ingested": 0, "published": 0})
            activity[row["bucket"]]["published"] = row["n"]

    # ── what happened to everything that arrived ────────────────────────────
    status_breakdown = query(
        f"SELECT status, COUNT(*) AS count FROM items WHERE {item_where} "
        "GROUP BY status ORDER BY count DESC",
        item_params, channel=name,
    )

    sources = query(
        f"""
        SELECT source_name,
               COUNT(*) AS total,
               SUM(status = 'published') AS published,
               SUM(status IN ('merged', 'held')) AS folded,
               SUM(status = 'duplicate') AS duplicate,
               SUM(status IN ('low_impact', 'irrelevant')) AS filtered,
               ROUND(AVG(NULLIF(importance, 0)), 2) AS avg_importance
        FROM items
        WHERE {item_where}
        GROUP BY source_name
        ORDER BY total DESC
        """,
        item_params, channel=name,
    )

    importance = query(
        f"""
        SELECT importance, COUNT(*) AS count, SUM(status = 'published') AS published
        FROM items
        WHERE importance BETWEEN 1 AND 5 AND {item_where}
        GROUP BY importance
        ORDER BY importance ASC
        """,
        item_params, channel=name,
    )

    markets = query(
        f"""
        SELECT market, COUNT(*) AS count, SUM(status = 'published') AS published
        FROM items
        WHERE market NOT IN ('', 'none') AND market IS NOT NULL AND {item_where}
        GROUP BY market
        ORDER BY published DESC, count DESC
        LIMIT 10
        """,
        item_params, channel=name,
    )

    # ── the editor and the dedup ladder: the two filters worth auditing ─────
    editor_where, editor_params = _since("created_at", modifier)
    verdict_rows = query(
        f"SELECT verdict, COUNT(*) AS n FROM editor_decisions WHERE {editor_where} GROUP BY verdict",
        editor_params, channel=name,
    )
    verdicts = {row["verdict"]: row["n"] for row in verdict_rows}
    rules = Counter()
    for row in query(
        f"SELECT rules_broken FROM editor_decisions WHERE verdict = 'decline' AND {editor_where}",
        editor_params, channel=name,
    ):
        try:
            broken = json.loads(row["rules_broken"] or "[]")
        except (TypeError, ValueError):
            continue
        for rule in broken if isinstance(broken, list) else []:
            rules[str(rule)] += 1

    dedup_where, dedup_params = _since("created_at", modifier)
    dedup = query(
        f"""
        SELECT rung, SUM(kept = 0) AS dropped, SUM(kept = 1) AS kept
        FROM dedup_hits
        WHERE {dedup_where}
        GROUP BY rung
        ORDER BY dropped DESC
        """,
        dedup_params, channel=name,
    )

    hour_rows = query(
        f"SELECT CAST(strftime('%H', sent_at) AS INTEGER) AS hour, COUNT(*) AS count "
        f"FROM posts WHERE status = 'sent' AND {post_where} GROUP BY hour",
        post_params, channel=name,
    )
    by_hour = {row["hour"]: row["count"] for row in hour_rows if row["hour"] is not None}

    return {
        "channel": name,
        "ready": True,
        "range": window,
        "sources_count": source_rows,
        "gate_outcomes": gate_outcomes,
        "trends": trend_rows,
        "summary": summary,
        "activity": sorted(activity.values(), key=lambda r: r["bucket"]),
        "status_breakdown": status_breakdown,
        "sources": sources,
        "importance": importance,
        "markets": markets,
        "editor": {
            "approve": verdicts.get("approve", 0),
            "decline": verdicts.get("decline", 0),
            "top_rules": [{"rule": r, "count": n} for r, n in rules.most_common(8)],
        },
        "dedup": dedup,
        "hour_of_day": [{"hour": h, "count": by_hour.get(h, 0)} for h in range(24)],
    }

