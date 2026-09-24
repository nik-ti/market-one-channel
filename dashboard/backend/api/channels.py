"""GET /api/v1/channels — every channel this project defines, plus whether
each has a database yet, so the dashboard can build a switcher without
guessing (or 500ing on the one — ai_news, today — that doesn't have one).
"""

from __future__ import annotations

from fastapi import APIRouter

from channel_resolver import list_channels

router = APIRouter()


@router.get("/channels")
def get_channels():
    return {"channels": list_channels()}
