"""GET /api/v1/posts — paginated feed of items flowing through the pipeline.

Reads the `items` table (not `posts` — an item may never reach the posts
table, e.g. if it was rejected before writing), which is what carries all the
columns the frontend table needs: source, title, story link, status. `body`
and `status_reason` are included too, so the frontend's expandable row can
show the full item text and the pipeline's reason without a second request.
"""

from __future__ import annotations

from fastapi import APIRouter, Query

from db_connector import query, query_one

router = APIRouter()


@router.get("/posts")
def get_posts(
    source: str | None = Query(default=None, description="Filter by source_name"),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
):
    where = ""
    params: tuple = ()
    if source:
        where = "WHERE source_name = ?"
        params = (source,)

    total_row = query_one(f"SELECT COUNT(*) AS n FROM items {where}", params)
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
    )

    return {"items": rows, "total": total, "limit": limit, "offset": offset}
