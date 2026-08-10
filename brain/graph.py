"""
BRAIN GRAPH — the editorial workflow as an explicit state machine.

PURPOSE: This is the map of the whole pipeline. Each station is a node in
         brain/nodes.py; this file wires them together and says where an item
         can go next. Reading this file should tell you the entire life of a
         queued item without opening anything else.

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

    publish sends continuations with reply_to_message_id set, so Telegram
    threads them under the original post.

WHY THE STATE IS A PLAIN DICT SHAPE
    LangGraph threads one state object through every node. We keep the item as
    a plain dict (converted from the sqlite row) rather than the row itself so
    the state stays serialisable — that keeps the door open to a checkpointer
    later, and avoids every node depending on sqlite internals.

ENTRY POINT
    run_item(item, dry_run=...) — build the initial state, invoke the compiled
    graph, return the final state. nodes/publish_loop.py and tools/test_brain.py
    both call this; the only difference is dry_run.
"""

from __future__ import annotations

from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph

from brain import nodes


class BrainState(TypedDict, total=False):
    """Everything the graph knows about the item it is processing.

    total=False means a node only has to return the fields it changes;
    LangGraph merges the rest. `outcome` is the terminal word the publish loop
    counts (published | duplicate | irrelevant | low_impact | declined |
    retry | failed) — an empty outcome means "still moving through the line".
    """
    # --- set once, at the start ---
    item: dict                  # the queued item, as a plain dict
    dry_run: bool               # rehearsal mode: decide everything, send nothing

    # --- filled in as the item moves down the line ---
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
    parent_post_html: str       # the original post a continuation replies to

    # --- the final word ---
    outcome: str


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


# Compiled once and shared — compiling is wiring, not work, so doing it at
# import time is fine and means every caller uses the identical machine.
graph = build_graph()


async def run_item(item_row, *, dry_run: bool = False) -> dict[str, Any]:
    """Run one queued item through the whole editorial graph.

    Args:
        item_row: a row from the items table (sqlite3.Row or dict).
        dry_run:  when True, every decision is made for real but nothing is
                  written to the posts table, no verdicts are recorded, and
                  nothing is sent. This is how tools/test_brain.py rehearses.

    Returns the final state; state["outcome"] is the one-word result the
    publish loop counts.
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
