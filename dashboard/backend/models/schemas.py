"""Pydantic response models. These only describe API SHAPES; they are not
used to validate DB rows, since the DB is treated as already-trusted data.
"""

from __future__ import annotations

from pydantic import BaseModel


class PostItem(BaseModel):
    id: int
    time: str | None = None
    source_name: str
    title: str
    body: str
    url: str = ""
    story_id: int | None = None
    status: str
    status_reason: str
    importance: int = 0
    market: str = ""
    topic: str = ""


class PostsResponse(BaseModel):
    channel: str
    ready: bool
    items: list[PostItem]
    total: int
    limit: int
    offset: int
    status_counts: dict[str, int] = {}


class StoryPost(BaseModel):
    id: int
    item_id: int
    title: str
    body: str
    status: str
    status_reason: str


class Story(BaseModel):
    id: int
    headline: str
    summary: str
    status: str
    item_count: int
    post_count: int
    state: str
    last_post_at: str | None = None
    posts: list[StoryPost] = []


class StoriesResponse(BaseModel):
    channel: str
    ready: bool
    stories: list[Story]


class SourceCount(BaseModel):
    source_name: str
    count: int


class GateOutcomes(BaseModel):
    published: int
    rejected: int
    held: int
    expired: int


class TrendPoint(BaseModel):
    hour: str
    count: int


class WindowTotals(BaseModel):
    ingested: int
    published: int


class StatsSummary(WindowTotals):
    previous: WindowTotals | None = None
    publish_rate: float | None = None
    median_minutes_to_publish: float | None = None
    queued_now: int
    held_now: int
    live_stories: int
    last_published_at: str | None = None


class ActivityPoint(BaseModel):
    bucket: str
    ingested: int
    published: int


class StatusCount(BaseModel):
    status: str
    count: int


class SourcePerformance(BaseModel):
    source_name: str
    total: int
    published: int
    folded: int
    duplicate: int
    filtered: int
    avg_importance: float | None = None


class ImportanceCount(BaseModel):
    importance: int
    count: int
    published: int


class MarketCount(BaseModel):
    market: str
    count: int
    published: int


class RuleCount(BaseModel):
    rule: str
    count: int


class EditorStats(BaseModel):
    approve: int
    decline: int
    top_rules: list[RuleCount]


class DedupRung(BaseModel):
    rung: str
    dropped: int
    kept: int


class HourCount(BaseModel):
    hour: int
    count: int


class StatsResponse(BaseModel):
    channel: str
    ready: bool
    range: str
    sources_count: list[SourceCount]
    gate_outcomes: GateOutcomes
    trends: list[TrendPoint]
    summary: StatsSummary | None = None
    activity: list[ActivityPoint]
    status_breakdown: list[StatusCount]
    sources: list[SourcePerformance]
    importance: list[ImportanceCount]
    markets: list[MarketCount]
    editor: EditorStats
    dedup: list[DedupRung]
    hour_of_day: list[HourCount]


class GraphNode(BaseModel):
    id: str
    label: str
    last_invocation: str | None = None
    error_count: int = 0
    health: str = "ok"


class GraphEdge(BaseModel):
    source: str
    target: str


class GraphResponse(BaseModel):
    channel: str
    ready: bool
    nodes: list[GraphNode]
    edges: list[GraphEdge]


class NodeInfo(BaseModel):
    id: str
    label: str
    description: str
    model: str
    fallback_model: str | None = None
    prompt: str | None = None


class NodesResponse(BaseModel):
    channel: str
    ready: bool
    nodes: list[NodeInfo]


class ChannelInfo(BaseModel):
    id: str
    name: str
    ready: bool


class ChannelsResponse(BaseModel):
    channels: list[ChannelInfo]


class ErrorResponse(BaseModel):
    error: str
