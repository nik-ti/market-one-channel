"""The editorial workflow as an explicit state machine.

Each station is a node in brain/nodes.py; this file wires them together and
says where an item can go next.

THE SHAPE

    START → dedup_check
               ├─ duplicate ─────────────────────────────────────────► END
               └─ new ──► sorter
                            ├─ irrelevant / low_impact / retry ──────► END
                            └─ passes ──► place_story ──► story_gate
                                                            ├─ hold ─► END
                                                            └─ post ─► writer
                                                                         │
    ┌─── rewrite (once) ───────────────────────────────────────────┐     ▼
    └──────────────────────────────────────────────────────────► editor
                                                    ├─ declined ──────► END
                                                    └─ approved ─► publish ─► END

Placement runs on every round, even when the pacing limits forbid posting: an
item that expires before it is filed takes its content out of its story with it.
Those rounds stop at place_story and leave the item queued, and the next round
resumes its story without paying for the placement again.

The unit of work is the story, not the item. An item that reaches the gate and
is held does not become a post; it stays attached to its story as fuel for that
story's next one. Because the gate always posts a story's FIRST post, a hold
only ever means "the reader already has this", never "this went unreported".

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
    item: dict                  # after the gate approves, the FOLDED story source
    dry_run: bool               # rehearsal: decide everything, write and send nothing
    place_only: bool            # file into a story, but stop before the gate

    # The live Story object, rebuilt from the database by place_story. It MUST
    # be declared here: LangGraph silently drops any state key the schema does
    # not name, and an undeclared story reaches the gate as a KeyError.
    story: Any
    story_id: int               # 0 in a dry run
    story_angle: str            # the gate's instruction for this post
    story_brief: str            # what the writer is told beyond the source
    gate_reason: str            # why a held item was held
    trigger_item_id: int        # the item that arrived, kept for logging

    sorter_verdict: dict
    post_html: str
    used_ai: bool
    post_id: int
    editor_verdict: dict
    editor_feedback: str
    rewrite_count: int
    outcome: str                # published | duplicate | irrelevant | low_impact |
                                # held | placed | declined | retry | failed


def build_graph():
    """Wire the stations together and compile the graph."""
    builder = StateGraph(BrainState)

    builder.add_node("dedup_check", nodes.dedup_check)
    builder.add_node("sorter", nodes.sorter_node)
    builder.add_node("place_story", nodes.place_story_node)
    builder.add_node("story_gate", nodes.story_gate_node)
    builder.add_node("writer", nodes.writer_node)
    builder.add_node("editor", nodes.editor_node)
    builder.add_node("publish", nodes.publish_node)

    builder.add_edge(START, "dedup_check")
    builder.add_conditional_edges(
        "dedup_check", nodes.route_after_dedup, {"drop": END, "sort": "sorter"},
    )
    builder.add_conditional_edges(
        "sorter", nodes.route_after_sorter, {"place": "place_story", "end": END},
    )
    builder.add_conditional_edges(
        "place_story", nodes.route_after_place,
        {"gate": "story_gate", "end": END},
    )
    builder.add_conditional_edges(
        "story_gate", nodes.route_after_gate, {"write": "writer", "end": END},
    )
    builder.add_conditional_edges(
        "writer", nodes.route_after_writer, {"edit": "editor", "end": END},
    )
    builder.add_conditional_edges(
        "editor", nodes.route_after_editor,
        {"publish": "publish", "rewrite": "writer", "end": END},
    )
    builder.add_edge("publish", END)

    return builder.compile()


# Compiling is wiring, not work, so doing it at import time is fine.
graph = build_graph()


async def run_item(item_row, *, dry_run: bool = False,
                   place_only: bool = False) -> dict[str, Any]:
    """Run one queued item through the editorial graph.

    dry_run makes every decision for real but writes nothing and sends nothing.
    place_only files the item into its story and stops there, for rounds where
    the pacing limits mean nothing can go out anyway.
    Returns the final state; state["outcome"] is the one-word result.
    """
    initial: BrainState = {
        "item": dict(item_row),
        "dry_run": dry_run,
        "place_only": place_only,
        "rewrite_count": 0,
        "editor_feedback": "",
        "outcome": "",
    }
    return await graph.ainvoke(initial)
