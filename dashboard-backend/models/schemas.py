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
    story_id: int | None = None
    status: str
    status_reason: str


class PostsResponse(BaseModel):
    items: list[PostItem]
    total: int
    limit: int
    offset: int


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


class StatsResponse(BaseModel):
    sources_count: list[SourceCount]
    gate_outcomes: GateOutcomes
    trends: list[TrendPoint]


class GraphNode(BaseModel):
    id: str
    label: str


class GraphEdge(BaseModel):
    source: str
    target: str


class GraphResponse(BaseModel):
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
    nodes: list[NodeInfo]


class ErrorResponse(BaseModel):
    error: str
