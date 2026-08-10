# Brain upgrade — LangGraph editorial layer for news-channel

> What changed, why, and what is still left to do.

## 1. The big picture

Before this upgrade, `news-channel` was a straight pipeline: collect → deduplicate → sort → write → edit → publish. Each step was written by hand in `nodes/publish_loop.py`. It worked, but it was hard to extend, hard to test, and impossible to reuse for rehearsals.

The **brain** is the same pipeline rebuilt as an explicit **state machine** using [LangGraph](https://langchain-ai.github.io/langgraph/). The graph lives in `brain/graph.py`, and the steps that make it up live in `brain/nodes.py`. The live publisher now just calls `brain.graph.run_item(item)` and reports the outcome.

### Why this is worth the extra files

- **Every decision is visible.** You can print the graph and see exactly how an item can move through the system.
- **Rehearsals are the same code as production.** `tools/test_brain.py` runs the exact same graph on real items, but in `dry_run` mode so nothing is sent.
- **New behaviours are easy to add.** A continuation, a rewrite loop, or a human-style tone are just extra nodes and edges, not nested `if/else` blocks.

## 2. The new graph

```text
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
```

Each rounded box is a **node**. The arrows are **edges**. The diamond-shaped choices (`duplicate | continuation | new`) are **conditional edges** — the graph decides where to go next based on the result of the previous node.

### The state object

LangGraph carries one dictionary through every node. Think of it as the item’s clipboard. Important fields:

| Field | Meaning |
|---|---|
| `item` | The queued item from the database, converted to a plain dict. |
| `relationship` | `new`, `duplicate`, or `continuation`. |
| `matched_item_id` | If it is a continuation, which older item it continues. |
| `sorter_verdict` | Topic, importance, market, and the sorter’s reasoning. |
| `post_html` | The post text produced by the writer. |
| `editor_verdict` | The editor’s decision: approved/declined, confidence, broken rules. |
| `editor_feedback` | If the editor asked for a rewrite, what to fix. |
| `rewrite_count` | How many times we have rewritten this item (max 1). |
| `reply_to_message_id` | The Telegram message ID of the parent post, for continuations. |
| `parent_post_html` | The original post text, so the continuation writer knows what not to repeat. |
| `outcome` | The final word: `published`, `duplicate`, `irrelevant`, `low_impact`, `declined`, `retry`, `failed`. |

## 3. What each new feature does

### 3.1 Continuation threading

**Problem:** the old system only knew “duplicate” or “not duplicate”. If a story developed — for example, first a defence pact is announced, then the US responds to it — the second item would be dropped as a duplicate even though it was genuinely new information about the same story.

**Solution:** the deduplication step is now a **three-way classifier** (`nodes/judge.py`):

- `duplicate` — same event, drop it.
- `continuation` — same story, new development. Publish it as a **reply** to the original Telegram post.
- `new` — unrelated or different story, publish normally.

The judge uses a separate prompt that compares the new item against the most similar recent published post. It returns a label and a confidence score.

**Lower bar for continuations:** a brand-new story must score importance `≥ 4/5`. A continuation only needs `≥ 3/5`, because the parent story was already vetted and the new development is allowed to be smaller. This is set in `config.py` as `CONTINUATION_MIN_IMPORTANCE`.

**How the reply is sent:** `nodes/publisher.py` and `utils/telegram_client.py` now accept a `reply_to_message_id`. When the graph detects a continuation, `brain/nodes.py::fetch_parent` looks up the Telegram message ID of the older published post and passes it through to the publisher.

### 3.2 Human-style tone and memory

**Problem:** the channel can read like a news aggregator because every post is written in the same flat, factual voice, with no memory of what the channel sounded like an hour ago.

**Solution:** a persona file plus recent-post memory.

- `brain/persona.md` is loaded into the writer’s system prompt. This is where you describe the channel’s voice, rhythm, and attitude. It is **not** a place for opinions or for asking for emojis — the publisher adds those later. Right now it is a placeholder; you can edit it directly.
- `brain/persona_loader.py` also fetches the most recent published posts and feeds them to the writer. This lets the writer match the channel’s recent style and avoid repeating phrases.

The writer calls are:

- `writer.execute(...)` for new stories.
- `writer.execute_continuation(...)` for continuations — it is told the parent post and must only add what is new.

Both functions now accept `persona` and `recent_posts`.

### 3.3 Post validation and the rewrite loop

**Problem:** the editor used to reject a post in one shot. Sometimes the rejection was for a fixable execution problem — a broken HTML tag, a sentence that trailed off, or a mild overclaim. Those posts were dropped even though the source was good.

**Solution:** the editor prompt (`nodes/editor.py`) now returns a list of `rules_broken`. If **all** broken rules are fixable and the post has not already been rewritten once, it goes back to the writer with the editor’s feedback instead of being dropped.

Fixable rules:

- `FACTUAL_DRIFT` — the post drifts from the source.
- `OVERCLAIM` — a hedge was dropped or a number was overstated.
- `INCOMPLETE` — a sentence is cut off or the main point is missing.
- `BROKEN_HTML` — Telegram HTML tags are malformed.
- `TOO_LONG` — the post exceeds the allowed length.

Unfixable rules (still drop immediately):

- `NO_NEWS`, `WRONG_TOPIC`, `HYPE`, `INJECTION`, `UNSAFE`.

The rewrite loop is limited to **one rewrite** per item (`config.MAX_REWRITES`). If the rewrite is still rejected, the item is declined.

## 4. Model choices and costs

The models are kept cheap and the wiring is kept simple. Current assignments in `nodes/*.py`:

| Node | Model | Why |
|---|---|---|
| Sorter | `deepseek/deepseek-v3.2` | Cheap, fast, good enough for classification. |
| Writer | `deepseek/deepseek-v3.2` | Strong enough for short posts, very cheap. |
| Editor | `minimax/minimax-m2.7` | A separate model family to avoid self-grading bias. |
| Judge / continuation | `deepseek/deepseek-v3.2` | Same as sorter, used through the dedup node. |

Rehearsing 12 items costs about **$0.024**. A single live item that passes the sorter and goes to writer + editor costs about **$0.002–0.003**.

You can change the models in the relevant `nodes/*.py` files. The graph does not hard-code them.

## 5. New files and changed files

### New

- `brain/__init__.py` — makes `brain` a Python package.
- `brain/graph.py` — the state graph definition and `run_item()` entry point.
- `brain/nodes.py` — the thin wrappers that call the existing `nodes/*.py` modules and record state.
- `brain/persona.md` — the placeholder voice document.
- `brain/persona_loader.py` — loads persona + recent posts from the database.
- `tools/test_brain.py` — rehearsal harness for the graph.

### Changed

- `config.py` — added `MAX_REWRITES`, `CONTINUATION_MIN_IMPORTANCE`, `PERSONA_PATH`, `PERSONA_RECENT_POSTS`.
- `schema.sql` — added `continuation_of` column to the `items` table.
- `utils/db.py` — added helpers for continuations, recent posts, and updating post text.
- `utils/telegram_client.py` — added `reply_to_message_id` support.
- `nodes/publisher.py` — passes `reply_to_message_id` through to Telegram.
- `nodes/dedup.py` — `classify()` now returns `(verdict, matched_id, score)`.
- `nodes/judge.py` — three-way classifier prompt.
- `nodes/writer.py` — accepts `persona`, `recent_posts`, `editor_feedback`; added `execute_continuation()`.
- `nodes/editor.py` — returns `rules_broken` and accepts an `attempt` number for the audit log.
- `nodes/publish_loop.py` — the live loop now delegates to `brain.graph.run_item()`.
- `requirements.txt` — added `langgraph>=1.0`.

## 6. How to test and rehearse

### Rehearse the graph on real items without sending anything

```bash
# Stop the live service first — two writers on the same SQLite database will lock each other.
sudo systemctl stop news-channel

# Run the brain on the last 12 hours of items.
cd /home/nikita/systems/news-channel
python tools/test_brain.py --recent 12 --limit 12

# Start the service again.
sudo systemctl start news-channel
```

`tools/test_brain.py` is the new tool. The old `tools/dry_run.py` still exercises the old hand-written pipeline, but that pipeline is no longer used in production. You can use `dry_run.py` only for historical comparison.

### Run a single live publishing round manually

```bash
sudo systemctl stop news-channel
cd /home/nikita/systems/news-channel
python main.py collect --once
python main.py publish --once
sudo systemctl start news-channel
```

### Watch the live channel

```bash
sudo journalctl -u news-channel -f
# or
tail -f /home/nikita/systems/news-channel/logs/news-channel.log
```

## 7. What is still left to do

The brain is live, but a few things remain open:

1. **Write the real persona.**  
   `brain/persona.md` is still a placeholder. The channel will keep using its default factual voice until you replace it. Keep the guardrails in the file in mind: tone, rhythm, attitude — not opinions, emojis, or source links.

2. **Verify the live writer/editor path.**  
   The recent queue has had no items scored `≥ 4/5`, so the full live path (writer → editor → publisher) has not been exercised on a real item yet. The next time a high-importance story arrives, watch the log and confirm the post looks right.

3. **Verify a live continuation.**  
   A continuation needs a published parent post and a new related item within the time window. This has been tested synthetically but not yet in production. When it happens, check that the new post is sent as a reply to the old one.

4. **Verify the rewrite loop on a real item.**  
   We limited rewrites to one pass. It is worth watching the first item that gets a fixable rejection and confirming the second draft succeeds.

5. **Tune the editor rules.**  
   The `FIXABLE_RULES` set in `brain/nodes.py` is a first guess. If the editor starts rejecting good posts for fixable reasons and the rewrite is not fixing them, adjust the rules or the prompt.

6. **Consider a state checkpointer.**  
   Right now the graph is stateless across ticks — it relies on the database for persistence. If you later want to pause and resume long-running multi-step reasoning, LangGraph supports checkpoints, but that is overkill for this channel today.

7. **Decide on commit style.**  
   This was a large change. You may want to split future feature work (persona tuning, model changes, new rules) into smaller commits so it is easier to roll back one piece at a time.

## 8. Key concepts for future work

- **LangGraph** — a library that turns a Python program into a graph of nodes and edges. The graph is compiled once and then invoked for each item.
- **State** — the single dictionary that every node reads and writes. Nodes only return the fields they change.
- **Conditional edges** — a function that decides the next node based on the state.
- **Dry run** — the same graph runs, but nothing is written to the database or sent to Telegram. The `dry_run` flag is checked inside `brain/nodes.py`.
- **Human-in-the-loop** — intentionally not used here. The graph runs fully automatically from start to finish.
