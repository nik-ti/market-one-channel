"""The only endpoints that change anything. Everything else here reads.

Two of them, and they are two halves of the same record. One overrules a
rejection and puts the item back in the queue; the other marks a post that
should not have gone out. Neither is only a button: each writes a row saying
what the pipeline decided and what a human decided instead, which is the
channel's only labelled data and the only honest way to tell whether a later
change to a prompt helped.

Forcing does not publish anything by itself. It returns the item to the queue
with a flag, and the live pipeline picks it up on its next round — through
story placement, so the story knows it was covered, but past the two stations
that judge whether it was worth covering.
"""

from __future__ import annotations

import sqlite3

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

import paths

router = APIRouter()


class OverrideRequest(BaseModel):
    item_id: int
    note: str = ""


def _writable_connection(channel: str | None) -> sqlite3.Connection:
    """The one place this service opens a database for writing.

    Everything else uses db_connector's read-only connection on purpose. The
    timeout matters: the channel itself is usually holding the database, and
    the right answer to that is to wait a moment, not to fail the click.
    """
    path = paths.database_path(channel)
    if not path.exists():
        raise HTTPException(status_code=404, detail=f"no database for channel '{channel}'")
    connection = sqlite3.connect(path, timeout=15.0)
    connection.row_factory = sqlite3.Row
    return connection


@router.post("/actions/force")
def force_publish(request: OverrideRequest, channel: str | None = None):
    """Overrule a rejection: back to the queue, to go out on the next round."""
    connection = _writable_connection(channel)
    try:
        row = connection.execute(
            "SELECT id, status, status_reason FROM items WHERE id = ?", (request.item_id,)
        ).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail=f"no item {request.item_id}")
        if row["status"] == "published":
            raise HTTPException(status_code=409, detail="that item is already published")

        with connection:
            connection.execute(
                """INSERT INTO overrides (item_id, was_status, was_reason, decision, note, created_at)
                   VALUES (?, ?, ?, 'publish', ?, datetime('now'))""",
                (request.item_id, row["status"], row["status_reason"] or "", request.note),
            )
            connection.execute(
                """UPDATE items SET status = 'queued', status_reason = 'forced by a human',
                                    forced = 1, attempts = 0, story_id = NULL
                    WHERE id = ?""",
                (request.item_id,),
            )
    except sqlite3.OperationalError as error:
        raise HTTPException(status_code=503, detail=f"database busy: {error}") from error
    finally:
        connection.close()

    return {"ok": True, "item_id": request.item_id, "was": row["status"],
            "message": "queued; the pipeline will pick it up within a couple of minutes"}


@router.post("/actions/should-not-have-posted")
def mark_regret(request: OverrideRequest, channel: str | None = None):
    """Label a post as one that should not have gone out.

    Deliberately does not delete anything from Telegram. It is the other half
    of the dataset — without it every label points the same way, and a filter
    tuned only on things it wrongly rejected learns to reject nothing.
    """
    connection = _writable_connection(channel)
    try:
        row = connection.execute(
            "SELECT id, status, status_reason FROM items WHERE id = ?", (request.item_id,)
        ).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail=f"no item {request.item_id}")
        with connection:
            connection.execute(
                """INSERT INTO overrides (item_id, was_status, was_reason, decision, note, created_at)
                   VALUES (?, ?, ?, 'should_not_have_posted', ?, datetime('now'))""",
                (request.item_id, row["status"], row["status_reason"] or "", request.note),
            )
    except sqlite3.OperationalError as error:
        raise HTTPException(status_code=503, detail=f"database busy: {error}") from error
    finally:
        connection.close()

    return {"ok": True, "item_id": request.item_id}


@router.get("/overrides")
def list_overrides(channel: str | None = None, limit: int = 100):
    """Every disagreement, newest first — the list worth reading weekly."""
    connection = _writable_connection(channel)
    try:
        rows = connection.execute(
            """SELECT o.id, o.item_id, o.was_status, o.was_reason, o.decision,
                      o.note, o.created_at,
                      i.title, i.source_name, i.topic, i.market, i.importance, i.status
                 FROM overrides o JOIN items i ON i.id = o.item_id
                ORDER BY o.id DESC LIMIT ?""",
            (min(limit, 500),),
        ).fetchall()
    finally:
        connection.close()
    return {"overrides": [dict(r) for r in rows], "total": len(rows)}
