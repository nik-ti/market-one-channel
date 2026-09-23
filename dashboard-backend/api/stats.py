"""GET /api/v1/stats — three views used by the Stats tab: posts-by-source,
the published/rejected/held/expired split, and posts-per-hour for 24h.

`status` on an item can be one of many fine-grained values (irrelevant,
low_impact, duplicate, merged, failed, skipped_*...). The Stats tab only
shows four buckets, so everything that isn't published/held/expired is
folded into "rejected" here — it is, from the reader's point of view, a
post that did not happen.
"""

from __future__ import annotations

from fastapi import APIRouter

from db_connector import query

router = APIRouter()

_HELD_OR_TERMINAL = {"published", "held", "expired"}


@router.get("/stats")
def get_stats():
    source_rows = query(
        """
        SELECT source_name, COUNT(*) AS count
        FROM items
        GROUP BY source_name
        ORDER BY count DESC
        """
    )

    status_rows = query("SELECT status, COUNT(*) AS count FROM items GROUP BY status")
    gate_outcomes = {"published": 0, "rejected": 0, "held": 0, "expired": 0}
    for row in status_rows:
        status = row["status"]
        bucket = status if status in _HELD_OR_TERMINAL else "rejected"
        gate_outcomes[bucket] += row["count"]

    # One row per hour, last 24h, oldest first — what a line chart expects.
    trend_rows = query(
        """
        SELECT strftime('%Y-%m-%d %H:00', fetched_at) AS hour, COUNT(*) AS count
        FROM items
        WHERE fetched_at >= datetime('now', '-24 hours')
        GROUP BY hour
        ORDER BY hour ASC
        """
    )

    return {
        "sources_count": source_rows,
        "gate_outcomes": gate_outcomes,
        "trends": trend_rows,
    }
