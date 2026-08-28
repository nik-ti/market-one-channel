"""The editorial workflow as an explicit state machine.

Each station is a node in brain/nodes.py; this file wires them together and
says where an item can go next.

THE SHAPE

    START → relationship_check
                  ├─ duplicate ───────────────────────────────► END
                  ├─ continuation → fetch_parent ─┬─ no parent → sorter
                  │                               └─ parent ───┘
                  └─ new ────────────────────────────────► sorter

    sorter ── irrelevant / low_impact / retry / failed ──► END
       │
       passes
       │
       ├─ new story ───────► writer ──► editor ──┬─ rewrite ─► writer (once)
       │                                        ├─ declined ──────────► END
       │                                        └─ approved ──► publish ─► END
       │
       └─ continuation ──► continuation_writer ──► editor ──┬─ rewrite (once)
                                                            ├─ declined
                                                            └─ approved ──► publish

The item is kept as a plain dict rather than the sqlite row so the state stays
serialisable, which keeps the door open to a checkpointer later.
"""

from __future__ import annotations

from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph

from brain import nodes


class BrainState(TypedDict, total=False):
    """Everything the graph knows about the item it is processing.

    total=False means a node only returns the fields it changes. An empty
    `outcome` means "still moving down the line".
    """
    item: dict
    dry_run: bool               # rehearsal: decide everything, send nothing

    relationship: str           # "new" | "duplicate" | "continuation"
    matched_item_id: int | None
    sorter_verdict: dict
    post_html: str
    used_ai: bool
    post_id: int
    editor_verdict: dict
    editor_feedback: str
    rewrite_count: int
    recent_posts: list[str]
    reply_to_message_id: int | None
    parent_post_html: str       # the post a continuation replies to
    outcome: str                # published | duplicate | irrelevant |
                                # low_impact | declined | retry | failed


def build_graph():
    """Wire the stations together and compile the graph."""
    builder = StateGraph(BrainState)

    builder.add_node("relationship_check", nodes.relationship_check)
    builder.add_node("fetch_parent", nodes.fetch_parent)
    builder.add_node("sorter", nodes.sorter_node)
    builder.add_node("writer", nodes.writer_node)
    builder.add_node("continuation_writer", nodes.continuation_writer_node)
    builder.add_node("editor", nodes.editor_node)
    builder.add_node("publish", nodes.publish_node)

    builder.add_edge(START, "relationship_check")
    builder.add_conditional_edges(
        "relationship_check", nodes.route_after_relationship,
        {"drop": END, "sort": "sorter", "continuation": "fetch_parent"},
    )
    builder.add_edge("fetch_parent", "sorter")
    builder.add_conditional_edges(
        "sorter", nodes.route_after_sorter,
        {"write": "writer", "continuation": "continuation_writer", "end": END},
    )
    builder.add_conditional_edges(
        "writer", nodes.route_after_writer,
        {"edit": "editor", "end": END},
    )
    builder.add_conditional_edges(
        "continuation_writer", nodes.route_after_writer,
        {"edit": "editor", "end": END},
    )
    builder.add_conditional_edges(
        "editor", nodes.route_after_editor,
        {
            "publish": "publish",
            "rewrite": "writer",
            "rewrite_continuation": "continuation_writer",
            "end": END,
        },
    )
    builder.add_edge("publish", END)

    return builder.compile()


# Compiling is wiring, not work, so doing it at import time is fine.
graph = build_graph()


async def run_item(item_row, *, dry_run: bool = False) -> dict[str, Any]:
    """Run one queued item through the editorial graph.

    dry_run makes every decision for real but writes nothing and sends nothing.
    Returns the final state; state["outcome"] is the one-word result.
    """
    initial: BrainState = {
        "item": dict(item_row),
        "dry_run": dry_run,
        "rewrite_count": 0,
        "editor_feedback": "",
        "recent_posts": [],
        "outcome": "",
    }
    return await graph.ainvoke(initial)
