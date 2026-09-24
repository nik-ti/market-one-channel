"""GET /api/v1/posts — paginated feed of items flowing through the pipeline.

Reads the `items` table (not `posts` — an item may never reach the posts
table, e.g. if it was rejected before writing), which is what carries all the
columns the frontend table needs: source, title, story link, status. `body`
and `status_reason` are included too, so the frontend's expandable row can
show the full item text and the pipeline's reason without a second request.

Takes an optional ?channel=, resolved by channel_resolver (defaults to
markets, never .env). A channel with no database yet (ai_news, today) returns
an empty-but-valid response with "ready": false instead of a 503 or 500 — see
paths.database_ready().
"""

from __future__ import annotations

from fastapi import APIRouter, Query

import paths
from channel_resolver import resolve_channel
from db_connector import query, query_one

router = APIRouter()


@router.get("/posts")
def get_posts(
    channel: str | None = Query(default=None, description="Which channel's database to read"),
    source: str | None = Query(default=None, description="Filter by source_name"),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
):
    name = resolve_channel(channel)

    if not paths.database_ready(name):
        return {
            "channel": name,
            "ready": False,
            "items": [],
            "total": 0,
            "limit": limit,
            "offset": offset,
        }

    where = ""
    params: tuple = ()
    if source:
        where = "WHERE source_name = ?"
        params = (source,)

    total_row = query_one(f"SELECT COUNT(*) AS n FROM items {where}", params, channel=name)
    total = total_row["n"] if total_row else 0

    rows = query(
        f"""
        SELECT id, fetched_at AS time, source_name, title, body, story_id, status, status_reason
        FROM items
        {where}
        ORDER BY id DESC
        LIMIT ? OFFSET ?
        """,
        (*params, limit, offset),
        channel=name,
    )

    return {"channel": name, "ready": True, "items": rows, "total": total, "limit": limit, "offset": offset}
