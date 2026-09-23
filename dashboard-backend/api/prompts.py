"""GET /api/v1/prompts — the raw system prompt text for each LLM node.

Prompts are extracted by reading the node source files as TEXT and pulling
out the triple-quoted constant, rather than importing the modules. Importing
would run module-level code that needs API keys and config this dashboard
has no business depending on — a read-only dashboard should not be able to
break if the pipeline's config changes.
"""

from __future__ import annotations

import re
import traceback
from pathlib import Path

from fastapi import APIRouter

router = APIRouter()

NODES_DIR = Path(__file__).resolve().parent.parent.parent / "nodes"

# node_name -> (source file, constant name)
PROMPT_SOURCES = {
    "sorter": ("sorter.py", "PROMPT"),
    "place_story": ("stories.py", "PLACE_SYSTEM"),
    "gate": ("stories.py", "GATE_SYSTEM"),
    "writer": ("writer.py", "PROMPT"),
    "editor": ("editor.py", "PROMPT"),
}


def _extract(file_path: Path, const_name: str) -> str | None:
    try:
        text = file_path.read_text(encoding="utf-8")
    except OSError as exc:
        print(f"[prompts] could not read {file_path}: {exc}")
        traceback.print_exc()
        return None

    # Matches: CONST_NAME = """....""" (non-greedy, across lines)
    pattern = re.compile(
        rf'^{re.escape(const_name)}\s*=\s*"""(.*?)"""', re.DOTALL | re.MULTILINE
    )
    match = pattern.search(text)
    if not match:
        return None
    return match.group(1).strip()


@router.get("/prompts")
def get_prompts():
    prompts: dict[str, str] = {}
    for node_name, (filename, const_name) in PROMPT_SOURCES.items():
        text = _extract(NODES_DIR / filename, const_name)
        prompts[node_name] = text if text is not None else ""
    return {"prompts": prompts}
