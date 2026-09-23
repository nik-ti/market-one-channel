"""GET /api/v1/stories — every story, with item/post counts computed live
from the items and posts tables (nothing is duplicated/cached on the story
row itself, matching how nodes/stories.py treats the story as derived data),
plus the full list of posts (one entry per item attached to the story) so
the dashboard can show exactly what happened to each piece of news without
a second round-trip.
"""

from __future__ import annotations

import re

from fastapi import APIRouter

from db_connector import query

router = APIRouter()

# The only statuses the dashboard distinguishes with their own color. Every
# other items.status value (irrelevant, low_impact, duplicate, failed,
# expired, ...) reads as "rejected" from a reader's point of view: it did
# not become a post.
_DISPLAY_STATUSES = {"published", "merged", "held"}

_TAG_RE = re.compile(r"<[^>]+>")


def _strip_html(text: str) -> str:
    """post_html only ever uses <b> for Telegram formatting — drop tags so
    the dashboard shows plain text, matching how it shows item bodies."""
    return _TAG_RE.sub("", text).strip()


def _display_status(item_status: str) -> str:
    return item_status if item_status in _DISPLAY_STATUSES else "rejected"


@router.get("/stories")
def get_stories():
    story_rows = query(
        """
        SELECT
            s.id,
            s.headline,
            s.summary,
            s.status,
            s.last_post_at,
            (SELECT COUNT(*) FROM items i WHERE i.story_id = s.id) AS item_count,
            (SELECT COUNT(*) FROM posts p
                JOIN items i2 ON i2.id = p.item_id
                WHERE i2.story_id = s.id) AS post_count
        FROM stories s
        ORDER BY s.last_item_at DESC
        """
    )

    # One query for every item belonging to any story, LEFT JOINed to its
    # post (if the item ever made it that far) — avoids an N+1 query per
    # story. post_html/post_url/telegram_message_id come along so `body`
    # and `status_reason` can prefer the actually-published text.
    item_rows = query(
        """
        SELECT
            i.id AS item_id,
            i.story_id,
            i.title,
            i.body AS item_body,
            i.status AS item_status,
            i.status_reason AS item_status_reason,
            p.post_html,
            p.status AS post_status,
            p.telegram_message_id
        FROM items i
        LEFT JOIN posts p ON p.item_id = i.id
        WHERE i.story_id IS NOT NULL
        ORDER BY i.id ASC
        """
    )

    posts_by_story: dict[int, list[dict]] = {}
    for row in item_rows:
        body = _strip_html(row["post_html"]) if row["post_html"] else (row["item_body"] or "")
        entry = {
            "id": row["item_id"],
            "item_id": row["item_id"],
            "title": row["title"],
            "body": body,
            "status": _display_status(row["item_status"]),
            "status_reason": row["item_status_reason"] or "",
        }
        posts_by_story.setdefault(row["story_id"], []).append(entry)

    stories = []
    for row in story_rows:
        stories.append(
            {
                "id": row["id"],
                "headline": row["headline"],
                "summary": row["summary"],
                # "status" and "state" are the same underlying value
                # (live/closed) — the API spec asks for both field names.
                "status": row["status"],
                "state": row["status"],
                "item_count": row["item_count"],
                "post_count": row["post_count"],
                "last_post_at": row["last_post_at"],
                "posts": posts_by_story.get(row["id"], []),
            }
        )

    return {"stories": stories}
