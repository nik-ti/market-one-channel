"""GET /api/v1/graph — the pipeline's node/edge shape, plus live health per
node (last invocation time, error count) read from the DB every request.

The node list is built from the requested channel's own PIPELINE (see
pipeline.py), not a fixed list — a channel can declare stations the other
does not have. long-standing display names (dedup / gate); everything else keeps its
PIPELINE name. Nodes this dashboard has no dedicated health query for report
zero errors and an unknown last invocation rather than raising.

Takes an optional ?channel=, resolved by channel_resolver (defaults to
markets, never .env). A channel with no database yet returns an
empty-but-valid response with "ready": false instead of an error — the
pipeline shape without any DB to measure health from is not worth guessing at.
"""

from __future__ import annotations

from fastapi import APIRouter, Query

import paths
import pipeline
from channel_resolver import resolve_channel
from db_connector import query, query_one

router = APIRouter()

# PIPELINE station name -> the id this graph has always shown it under.
# Anything not listed here keeps its PIPELINE name as-is.
# Stations are shown under the names the channel gives them in PIPELINE.
_DISPLAY_NAME: dict[str, str] = {}

# What each station is called and what it does, for the diagram. A station a
# channel adds of its own falls back to its id with the underscores removed.
_STATION_INFO: dict[str, tuple[str, str]] = {
    "dedup": ("Dedup", "Have we already covered this? Five checks, ending in an LLM that reads both texts."),
    "sorter": ("Sorter", "Is it worth posting at all? Scores 1-5; the bar is 4."),
    "fetch_article": ("Fetch article", "Follows the link and reads the full article, so the post is more than a headline."),
    "story_organizer": ("Story organizer", "Which running story does this join, or does it open a new one?"),
    "gatekeeper": ("Gatekeeper", "Has the story moved? Post, hold, or this is the wrong story."),
    "writer": ("Writer", "Writes the post in the channel's voice."),
    "editor": ("Editor", "Checks the finished post against its source. The one station that fails closed."),
    "publish": ("Publish", "Sends it to Telegram and books it against its story."),
}


def _graph_node_ids(channel: str) -> list[str]:
    stations = pipeline.channel_pipeline(channel)
    return [_DISPLAY_NAME.get(s, s) for s in stations]


def _sum_counter(*kinds: str, channel: str) -> int:
    if not kinds:
        return 0
    placeholders = ",".join("?" for _ in kinds)
    row = query_one(
        f"SELECT COALESCE(SUM(n), 0) AS total FROM counters WHERE kind IN ({placeholders})",
        kinds,
        channel=channel,
    )
    return row["total"] if row else 0


def _max_value(table: str, column: str, where: str = "", *, channel: str) -> str | None:
    row = query_one(f"SELECT MAX({column}) AS v FROM {table} {where}", channel=channel)
    return row["v"] if row else None


def _health(error_count: int) -> str:
    if error_count == 0:
        return "ok"
    if error_count <= 3:
        return "degraded"
    return "error"


# node id -> a function computing its health dict, for the nodes this
# dashboard knows how to measure. Anything else (a channel-specific station
# with no dedicated query) falls back to the neutral default below.
def _known_node_health(node_id: str, channel: str) -> dict | None:
    if node_id == "dedup":
        return {
            "last_invocation": _max_value("items", "fetched_at", channel=channel),
            "error_count": _sum_counter("judge_error", "meaning_check_failed", channel=channel),
        }
    if node_id == "sorter":
        return {"last_invocation": _max_value("items", "updated_at", channel=channel), "error_count": 0}
    if node_id == "fetch_article":
        return {
            "last_invocation": _max_value("items", "updated_at", "WHERE article_text != ''", channel=channel),
            "error_count": 0,
        }
    if node_id == "story_organizer":
        return {
            "last_invocation": _max_value("items", "updated_at", "WHERE story_id IS NOT NULL", channel=channel),
            "error_count": _sum_counter("story_place_failed", channel=channel),
        }
    if node_id == "gate":
        return {"last_invocation": _max_value("stories", "last_item_at", channel=channel), "error_count": 0}
    if node_id == "writer":
        failed = query_one("SELECT COUNT(*) AS n FROM items WHERE status = 'failed'", channel=channel)
        return {
            "last_invocation": _max_value("posts", "created_at", channel=channel),
            "error_count": failed["n"] if failed else 0,
        }
    if node_id == "editor":
        return {"last_invocation": _max_value("editor_decisions", "created_at", channel=channel), "error_count": 0}
    if node_id == "publish":
        send_failed = query_one("SELECT COUNT(*) AS n FROM posts WHERE status = 'send_failed'", channel=channel)
        return {
            "last_invocation": _max_value("posts", "sent_at", "WHERE status = 'sent'", channel=channel),
            "error_count": send_failed["n"] if send_failed else 0,
        }
    return None


@router.get("/graph")
def get_graph(channel: str | None = Query(default=None, description="Which channel's database to read")):
    name = resolve_channel(channel)

    if not paths.database_ready(name):
        return {"channel": name, "ready": False, "nodes": [], "edges": []}

    node_ids = _graph_node_ids(name)

    nodes = []
    for node_id in node_ids:
        health = _known_node_health(node_id, name) or {"last_invocation": None, "error_count": 0}
        nodes.append(
            {
                "id": node_id,
                "label": _STATION_INFO.get(node_id, (node_id.replace("_", " "), ""))[0],
                "description": _STATION_INFO.get(node_id, ("", ""))[1],
                "last_invocation": health["last_invocation"],
                "error_count": health["error_count"],
                "health": _health(health["error_count"]),
            }
        )
    edges = [{"source": a, "target": b} for a, b in zip(node_ids, node_ids[1:])]

    return {"channel": name, "ready": True, "nodes": nodes, "edges": edges}
