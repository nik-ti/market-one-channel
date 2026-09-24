"""GET /api/v1/nodes — for each LLM node in the pipeline: what it does, which
model runs it, and its full system prompt (the embeddings step has a model
but no prompt).

Everything here is extracted by reading nodes/*.py, config.py and .env as
TEXT with regexes, rather than by importing the pipeline. Importing would run
module-level code that needs API keys and config this read-only dashboard has
no business depending on — same reasoning as the old prompts-only endpoint
this replaces.

WHY .env IS READ TOO: config.py's MODEL constants are only DEFAULTS —
`SORTER_MODEL = _get("SORTER_MODEL", "deepseek/deepseek-v3.2")` — and .env can
override any of them at runtime, the same way it already overrides
MAX_POSTS_PER_HOUR today. A dashboard that shows config.py's default while the
running channel actually reads a `.env` override would be lying about what is
live. So model resolution mirrors config.py's own `_get()`: an override in
.env (or the real process environment) wins, otherwise fall back to the
default written in config.py's source. Models are this shared machinery's
config, not a channel's, so this part stays global regardless of ?channel=.

Takes an optional ?channel=, resolved by channel_resolver (defaults to
markets, never .env): the node LIST and the sorter's rubric now come from
that channel's own PIPELINE and rubric.md (see pipeline.py), since two
channels can wire up different stations. This does not depend on the
channel's database — a channel with no DB yet (ai_news, today) still has a
profile and a rubric, so its pipeline shows up; "ready" is included purely
for the frontend to note that there's no data behind it yet.
"""

from __future__ import annotations

import os
import re
import traceback
from pathlib import Path

from dotenv import dotenv_values
from fastapi import APIRouter, Query

import paths
import pipeline
from channel_resolver import resolve_channel

router = APIRouter()

ROOT_DIR = paths.ROOT_DIR
NODES_DIR = ROOT_DIR / "nodes"
CONFIG_PATH = ROOT_DIR / "config.py"
ENV_PATH = paths.ENV_PATH

# PIPELINE station name -> the LLM node id it shows up as here. Stations with
# no entry (fetch_article, publish, or anything a channel adds of its own)
# have no dedicated model/prompt in this dashboard and are left out of the
# node list, same as before this became channel-aware.
STATION_TO_NODE: dict[str, str] = {
    "dedup": "dedup_judge",
    "sorter": "sorter",
    "story_organizer": "story_organizer",
    "gatekeeper": "gatekeeper",
    "writer": "writer",
    "editor": "editor",
}

# node_name -> (source file, constant name) for nodes that have a prompt.
# The judge's THREE-WAY prompt is the one actually used by dedup.py's check 5
# (nodes/dedup.py calls judge.execute_three_way) — the binary SYSTEM prompt
# is legacy, kept only for tools/check_dedup.py.
PROMPT_SOURCES: dict[str, tuple[str, str]] = {
    "dedup_judge": ("judge.py", "SYSTEM_THREE_WAY"),
    "story_organizer": ("stories.py", "PLACE_SYSTEM"),
    "gatekeeper": ("stories.py", "GATE_SYSTEM"),
    "writer": ("writer.py", "PROMPT"),
    "editor": ("editor.py", "PROMPT"),
}

# node_name -> config.py variable name that holds the model it runs on.
MODEL_VARS: dict[str, str] = {
    "dedup_judge": "JUDGE_MODEL",
    "sorter": "SORTER_MODEL",
    "story_organizer": "STORY_MODEL",
    "gatekeeper": "STORY_MODEL",
    "writer": "WRITER_MODEL",
    "editor": "EDITOR_MODEL",
    "embeddings": "EMBEDDING_MODEL",
}

# node_name -> config.py variable name for its fallback model, where one exists.
FALLBACK_MODEL_VARS: dict[str, str] = {
    "editor": "EDITOR_FALLBACK_MODEL",
}

LABELS: dict[str, str] = {
    "dedup_judge": "Dedup judge",
    "sorter": "Sorter",
    "story_organizer": "Story organizer",
    "gatekeeper": "Gatekeeper",
    "writer": "Writer",
    "editor": "Editor",
    "embeddings": "Embeddings",
}

DESCRIPTIONS: dict[str, str] = {
    "dedup_judge": "Rules on two look-alike stories — same event, a continuation, or "
             "different — the deciding step behind duplicate check 5.",
    "sorter": "Scores every incoming item 1-5 for market impact and picks its "
              "topic — the only node that decides if something is worth "
              "covering at all.",
    "story_organizer": "Decides which running story a new item joins, or starts a "
                   "new one.",
    "gatekeeper": "Decides whether a story has moved enough since its last post to "
            "publish again.",
    "writer": "Rewrites the story into the channel's one house style.",
    "editor": "Reads the finished post against its source and approves or "
              "rejects it before it can be published.",
    "embeddings": "Turns text into meaning-vectors so near-duplicate stories "
                  "can be shortlisted before the judge rules on them "
                  "(dedup check 4). No prompt — it is not an LLM call.",
}

# Fallback display order, used only if a profile's PIPELINE can't be parsed.
NODE_ORDER = ["dedup_judge", "sorter", "story_organizer", "gatekeeper",
              "writer", "editor", "embeddings"]


def _node_order_for(channel: str) -> list[str]:
    """This channel's PIPELINE, translated into the LLM node ids above, in
    the order items actually flow through them. Embeddings isn't a PIPELINE
    station — it's the sub-step check 4 of dedup runs before the judge ever
    sees a pair — so it's appended whenever dedup (-> judge) is present,
    same placement the dashboard has always shown it at."""
    stations = pipeline.channel_pipeline(channel)
    order: list[str] = []
    for station in stations:
        node_id = STATION_TO_NODE.get(station)
        if node_id and node_id not in order:
            order.append(node_id)
    if not order:
        return list(NODE_ORDER)
    if "dedup_judge" in order:
        order.append("embeddings")
    return order


def _channel_rubric(channel: str) -> str | None:
    """`channel`'s rubric — see channels/<name>/rubric.md."""
    path = paths.channel_dir(channel) / "rubric.md"
    try:
        return path.read_text()
    except OSError:
        return None


def _extract_prompt(file_path: Path, const_name: str) -> str | None:
    """Pull a triple-quoted constant out of a node source file as plain text."""
    try:
        text = file_path.read_text(encoding="utf-8")
    except OSError as exc:
        print(f"[nodes] could not read {file_path}: {exc}")
        traceback.print_exc()
        return None

    pattern = re.compile(
        rf'^{re.escape(const_name)}\s*=\s*"""(.*?)"""', re.DOTALL | re.MULTILINE
    )
    match = pattern.search(text)
    if not match:
        return None
    return match.group(1).strip()


def _extract_default_model(config_text: str, var_name: str) -> str | None:
    """Pull the DEFAULT out of `VAR = _get("VAR", "default/value")` in config.py."""
    pattern = re.compile(
        rf'^{re.escape(var_name)}\s*=\s*_get\(\s*"{re.escape(var_name)}"\s*,\s*"([^"]*)"\s*\)',
        re.MULTILINE,
    )
    match = pattern.search(config_text)
    return match.group(1) if match else None


def _resolve_model(var_name: str, config_text: str, env_overrides: dict[str, str | None]) -> str:
    """The value the running channel actually uses for `var_name`.

    Mirrors config.py's `_get()`: an override in .env or the process
    environment wins over the default written in config.py's source.
    """
    value = env_overrides.get(var_name) or os.environ.get(var_name)
    default = _extract_default_model(config_text, var_name)
    resolved = value if value else default
    return (resolved or "(unknown — not found in config.py)").strip()


@router.get("/nodes")
def get_nodes(channel: str | None = Query(default=None, description="Which channel's pipeline/rubric to read")):
    name = resolve_channel(channel)

    try:
        config_text = CONFIG_PATH.read_text(encoding="utf-8")
    except OSError as exc:
        print(f"[nodes] could not read {CONFIG_PATH}: {exc}")
        traceback.print_exc()
        config_text = ""

    env_overrides: dict[str, str | None] = dotenv_values(ENV_PATH) if ENV_PATH.exists() else {}

    nodes = []
    for node_id in _node_order_for(name):
        prompt: str | None = None
        if node_id in PROMPT_SOURCES:
            filename, const_name = PROMPT_SOURCES[node_id]
            prompt = _extract_prompt(NODES_DIR / filename, const_name)
        elif node_id == "sorter":
            # The sorter's rubric is the one prompt that belongs to the channel
            # rather than to the machinery, so it lives beside its profile.
            prompt = _channel_rubric(name)

        model = _resolve_model(MODEL_VARS[node_id], config_text, env_overrides)

        fallback_model: str | None = None
        if node_id in FALLBACK_MODEL_VARS:
            fallback_model = _resolve_model(FALLBACK_MODEL_VARS[node_id], config_text, env_overrides)
            if not fallback_model or fallback_model.startswith("(unknown"):
                fallback_model = None

        nodes.append(
            {
                "id": node_id,
                "label": LABELS.get(node_id, node_id.replace("_", " ").title()),
                "description": DESCRIPTIONS.get(node_id, ""),
                "model": model,
                "fallback_model": fallback_model,
                "prompt": prompt,
            }
        )

    return {"channel": name, "ready": paths.database_ready(name), "nodes": nodes}
