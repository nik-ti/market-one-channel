"""GET /api/v1/graph — the pipeline's node/edge shape, plus live health per
node (last invocation time, error count) read from the DB every request.

The node order matches nodes/collect_loop.py and nodes/publish_loop.py:
dedup -> sorter -> place_story -> gate -> writer -> editor -> publish.
This list is the only "static" part; everything else (times, error counts,
status) is computed fresh on each call, so a poll always reflects the
current DB state (see SPEC FAILURE #13).

Error counts come from the `counters` table where a node has a dedicated
counter kind; nodes without one report 0 rather than a guess.
"""

from __future__ import annotations

from fastapi import APIRouter

from db_connector import query, query_one

router = APIRouter()

NODES = ["dedup", "sorter", "place_story", "gate", "writer", "editor", "publish"]
EDGES = list(zip(NODES, NODES[1:]))


def _sum_counter(*kinds: str) -> int:
    if not kinds:
        return 0
    placeholders = ",".join("?" for _ in kinds)
    row = query_one(
        f"SELECT COALESCE(SUM(n), 0) AS total FROM counters WHERE kind IN ({placeholders})",
        kinds,
    )
    return row["total"] if row else 0


def _max_value(table: str, column: str, where: str = "") -> str | None:
    row = query_one(f"SELECT MAX({column}) AS v FROM {table} {where}")
    return row["v"] if row else None


def _health(error_count: int) -> str:
    if error_count == 0:
        return "ok"
    if error_count <= 3:
        return "degraded"
    return "error"


@router.get("/graph")
def get_graph():
    node_health = {
        "dedup": {
            "last_invocation": _max_value("items", "fetched_at"),
            "error_count": _sum_counter("judge_error", "meaning_check_failed"),
        },
        "sorter": {
            "last_invocation": _max_value("items", "updated_at"),
            "error_count": 0,
        },
        "place_story": {
            "last_invocation": _max_value("items", "updated_at", "WHERE story_id IS NOT NULL"),
            "error_count": _sum_counter("story_place_failed"),
        },
        "gate": {
            "last_invocation": _max_value("stories", "last_item_at"),
            "error_count": 0,
        },
        "writer": {
            "last_invocation": _max_value("posts", "created_at"),
            "error_count": _sum_counter_from_items_failed(),
        },
        "editor": {
            "last_invocation": _max_value("editor_decisions", "created_at"),
            "error_count": 0,
        },
        "publish": {
            "last_invocation": _max_value("posts", "sent_at", "WHERE status = 'sent'"),
            "error_count": _sum_counter_from_send_failed(),
        },
    }

    nodes = [
        {
            "id": name,
            "label": name.replace("_", " "),
            "last_invocation": node_health[name]["last_invocation"],
            "error_count": node_health[name]["error_count"],
            "health": _health(node_health[name]["error_count"]),
        }
        for name in NODES
    ]
    edges = [{"source": a, "target": b} for a, b in EDGES]

    return {"nodes": nodes, "edges": edges}


def _sum_counter_from_items_failed() -> int:
    row = query_one("SELECT COUNT(*) AS n FROM items WHERE status = 'failed'")
    return row["n"] if row else 0


def _sum_counter_from_send_failed() -> int:
    row = query_one("SELECT COUNT(*) AS n FROM posts WHERE status = 'send_failed'")
    return row["n"] if row else 0
