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

import config
from brain import nodes

# A channel may ship stations of its own. Most do not, so this is optional.
try:
    channel_nodes = __import__(f"channels.{config.CHANNEL}.nodes",
                               fromlist=["nodes"])
except ImportError:
    channel_nodes = None


class BrainState(TypedDict, total=False):
    """Everything the graph knows about the item it is processing.

    total=False means a node only returns the fields it changes. An empty
    `outcome` means "still moving down the line".
    """
    item: dict                  # after the gate approves, the FOLDED story source
    dry_run: bool               # rehearsal: decide everything, write and send nothing
    place_only: bool            # file into a story, but stop before the gate
    forced: bool                # a human overrode a rejection: the sorter and the
                                # gate step aside, the writer and the editor do not
    sweep: bool                 # a roundup: the item was judged once already,
                                # skip straight to its story and the gate

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
    """Wire this channel's stations together and compile the graph.

    The order comes from the channel's PIPELINE, so a channel can add a station
    of its own — one that reads the images a post carries, say — without the
    shared machinery growing a flag for it.
    """
    builder = StateGraph(BrainState)
    pipeline = list(config.PIPELINE)
    stages = dict(nodes.STAGES)
    stages.update(getattr(channel_nodes, "STAGES", {}))

    unknown = [s for s in pipeline if s not in stages]
    if unknown:
        raise SystemExit(f"{config.CHANNEL}'s PIPELINE names stations that do not "
                         f"exist: {unknown}. Known: {sorted(stages)}")

    for name in pipeline:
        builder.add_node(name, stages[name][0])
    builder.add_edge(START, pipeline[0])

    for position, name in enumerate(pipeline):
        _, router, routes = stages[name]
        following = pipeline[position + 1] if position + 1 < len(pipeline) else None

        if router is None:
            builder.add_edge(name, following or END)
            continue

        targets = {}
        for answer, destination in routes.items():
            if destination == "end":
                targets[answer] = END
            elif destination == "next":
                targets[answer] = following or END
            else:
                targets[answer] = destination
        builder.add_conditional_edges(name, router, targets)

    return builder.compile()


# Compiling is wiring, not work, so doing it at import time is fine.
graph = build_graph()


async def run_item(item_row, *, dry_run: bool = False,
                   place_only: bool = False, sweep: bool = False,
                   forced: bool = False) -> dict[str, Any]:
    """Run one queued item through the editorial graph.

    dry_run makes every decision for real but writes nothing and sends nothing.
    place_only files the item into its story and stops there, for rounds where
    the pacing limits mean nothing can go out anyway. sweep re-enters with a
    held item to release a roundup: it was judged once already, so dedup and
    the sorter step aside and placement resumes its story.

    forced is a human disagreeing with a rejection from the dashboard. It skips
    the two stations that judge whether an item is worth posting, and keeps the
    two that judge whether the post is any good. Placement still runs, because
    the story needs to know this went out — otherwise the next item on the same
    story has no idea it was already covered.
    Returns the final state; state["outcome"] is the one-word result.
    """
    initial: BrainState = {
        "item": dict(item_row),
        "dry_run": dry_run,
        "place_only": place_only,
        "sweep": sweep,
        "forced": forced,
        "rewrite_count": 0,
        "editor_feedback": "",
        "outcome": "",
    }
    return await graph.ainvoke(initial)
