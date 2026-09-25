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

Filters combine with AND: ?source= picks one source, ?status= takes a
comma-separated list of raw item statuses (published,held,...), and ?q= is
a keyword search over title and body — every word must appear somewhere,
case-insensitively, so "iran oil" finds items mentioning both. The response
also carries status_counts: how many items match source + q per status,
ignoring the status filter, so the frontend's status chips can show what
each one would give you before it is clicked.
"""

from __future__ import annotations

from fastapi import APIRouter, Query

import paths
from channel_resolver import resolve_channel
from db_connector import query

router = APIRouter()


# Longest keyword search accepted. A search box, not a query language — this
# only stops a pasted essay from becoming a hundred LIKE clauses.
_MAX_TERMS = 8


def _escape_like(term: str) -> str:
    return term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


@router.get("/posts")
def get_posts(
    channel: str | None = Query(default=None, description="Which channel's database to read"),
    source: str | None = Query(default=None, description="Filter by source_name"),
    status: str | None = Query(default=None, description="Comma-separated item statuses"),
    q: str | None = Query(default=None, max_length=200, description="Keywords, all must match"),
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
            "status_counts": {},
        }

    # Everything except the status filter — shared by the counts query, so a
    # chip's number means "what you get if you add this status".
    clauses: list[str] = []
    params: list = []
    if source:
        clauses.append("source_name = ?")
        params.append(source)
    for term in (q or "").split()[:_MAX_TERMS]:
        pattern = f"%{_escape_like(term)}%"
        clauses.append("(title LIKE ? ESCAPE '\\' OR body LIKE ? ESCAPE '\\')")
        params.extend([pattern, pattern])

    base_where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    count_rows = query(
        f"SELECT status, COUNT(*) AS n FROM items {base_where} GROUP BY status",
        tuple(params),
        channel=name,
    )
    status_counts = {row["status"]: row["n"] for row in count_rows}

    statuses = [s.strip() for s in (status or "").split(",") if s.strip()]
    if statuses:
        clauses.append(f"status IN ({','.join('?' * len(statuses))})")
        params.extend(statuses)
        total = sum(status_counts.get(s, 0) for s in set(statuses))
    else:
        total = sum(status_counts.values())

    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    rows = query(
        f"""
        SELECT id, fetched_at AS time, source_name, title, body, url, story_id,
               status, status_reason, importance, market, topic
        FROM items
        {where}
        ORDER BY id DESC
        LIMIT ? OFFSET ?
        """,
        (*params, limit, offset),
        channel=name,
    )

    return {
        "channel": name,
        "ready": True,
        "items": rows,
        "total": total,
        "limit": limit,
        "offset": offset,
        "status_counts": status_counts,
    }
