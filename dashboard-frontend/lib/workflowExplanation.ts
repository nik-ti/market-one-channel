// The full pipeline explanation shown under the Graph tab's Mermaid diagram.
// Written by Claude Haiku (the session owner) as A-to-Z context for how the
// system works. Kept verbatim — this is content, not something to summarize.
export const WORKFLOW_EXPLANATION = `
# Market One News Channel — Complete Workflow (A to Z)

## 🔄 The Complete Pipeline

### Phase 1: COLLECTION & DEDUPLICATION
News arrives from multiple sources:
- RSS feeds (BullTheoryio, Barchart, etc.)
- Twitter/X posts (via tweet-relay service)
- Reuters, Bloomberg, and other wire services

**Deduplication — five checks, cheapest first.** An item only reaches a check if
everything above let it through. Embeddings alone never decide; an LLM does.

1. **Same link or tweet** — free, enforced by the database.
2. **Same headline** — free, a fingerprint of the headline text.
3. **Nearly the same wording** — ~1ms, rapidfuzz ratio ≥ 92 within 24h.
   "Fed holds rates" vs "Fed leaves rates". If the two differ *only by a number*
   ("16 dead" → "17 dead"), it is **kept**, not merged — scoring cannot tell that
   apart from "Fed cuts 25bp" → "50bp", and both score ~97%.
4. **Same subject** — one embeddings call. This produces a **shortlist, not a
   verdict**:
   - **≥ 0.95** — near-verbatim, merged without paying for a judgement
   - **≥ 0.72** — worth asking about, goes to check 5 (top 3 candidates, 48h window)
   - **below 0.72** — a different story, stop here
5. **Same event** — one LLM call. The judge reads *both* texts and rules three ways:
   duplicate, different, or **continuation**. A continuation is not a duplicate and
   is not dropped — where it belongs is the story layer's question, and it has every
   open story to go on rather than one pair.

*Why check 5 exists:* on 19 hand-labelled pairs from this channel, real duplicates
scored 0.738–0.993 and genuinely different ones 0.785–0.900. **The ranges overlap**,
so no single cosine cutoff can work. The embedding tracks what a story is *about*;
only the judge reads what actually *happened*.

*A time gate runs before checks 4 and 5:* candidates far apart in time are discarded
first. Daily ETF flows are near-identical edition to edition, so yesterday's is the
most dangerous thing in the pool. Every real duplicate measured here arrived within
10.1 hours; the worst false merges were 24 hours apart.

*It fails open:* if the embeddings or the judge are unavailable, the item goes through
unchecked. A duplicate is a small embarrassment; a silent channel is worse. Every such
failure is counted, and a run of them raises an alert.

---

### Phase 2: SORTING & FILTERING (Sorter Node)
The sorter judges: "Is this worth posting?"

It returns four fields: **relevant** (true/false), **topic** (\`crypto\`,
\`geopolitics\` or \`other\`), **market** — which market has to reprice — and
**importance, 1 to 5**. The bar to publish is **4**.

**The question it actually asks is "who has to look again?"** Not "is this
interesting". An item scores on *how much of the world reprices*:

- **US data — CPI, payrolls, the Fed** — reprices everything: a **5 or 4**.
- **Eurozone, China, Japan** headline releases reprice a large region: a **4**.
- **Any other single economy's** inflation, jobs, GDP or rate decision reprices its
  own currency and little else: a **3**, *even when the number surprises*. Canada's
  inflation is a 3. Australia's rate decision is a 3.

**The market field is a hard gate, enforced in code, not just asked for.** If the
model answers that **no** market has to reprice, the item is capped at **3** —
deliberately one below the bar. A model that says nothing needs repricing and then
scores the item 4 has contradicted itself, and the concrete field is believed over
the number. This is why most items die here with \`market none\`.

**It also holds the economic calendar:** the sorter is shown the scheduled release
this item matches, with its forecast and previous value, so it can tell a number that
landed on consensus (already priced in) from one that did not.

**Output:** relevant → continues; otherwise \`low_impact\` or \`irrelevant\`, archived
with a written reason you can read on the Posts tab.

---

### Phase 3: STORY PLACEMENT (Place_Story Node)
News doesn't post as isolated items. Instead, it joins a **story** — a grouping of related developments.

**How placement works:**
1. Look at all currently open stories
2. Use an LLM to decide: "Does this item belong to story #82 (Treasury yields), or is it new?"
3. Output: either attach to an existing story OR create a new one

**Story definition:**
- A story is a single ongoing event or theme
- Example: "Treasury yields rising across the curve" (story ID 82)
  - Item 1: "2-year yield hits 4.794%"
  - Item 2: "30-year yield reaches 5.367%"
  - Item 3: "10-year note hits 5.081%"
  - These are 3 separate news items but ONE story

*Why:* Without this, the channel posts 15 updates about one war, or 7 about bond yields, overwhelming readers. Stories keep related news grouped.

---

### Phase 4: STORY GATE (Should_Post Node)
The gate asks: "Has the story state changed, or is this just noise within the same story?"

**There is no single ladder.** Each kind of situation has its own short list of
states, and only a move between them earns a post:

| situation | its states |
|---|---|
| a war | not started → fighting → ceasefire → fighting again → widened → over |
| a dispute | talks → tariffs imposed → deal |
| a case | filed → ruled → appealed |
| a price run | below a landmark → through it (once) |

**It HAS changed state when:** a ceasefire, truce, deal, ruling or resumption puts
the situation somewhere else than the last post described; a **new** party or front
enters (a second country's ships are hit, a second regulator opens a case); or a price
crosses a landmark the reader will remember — a record, a multi-year extreme, a major
round number — for the **first** time in this story.

**It has NOT changed state when:** another incident happens inside the same state
(another strike, another tanker, more casualties — the war was on before and is on
now); another piece of the same squeeze is disrupted; or a different outlet reports
what the reader was already told, *including* a fuller write-up of it.

**Gate logic:**
- **First post of a story:** always posts. Every story speaks at least once, so a
  "hold" always means "the reader already knows about this", never "this was never covered".
- **Second post onward:** only on a state change, by the test above.
- **Held items:** kept, and carried by the story's next post or by a roundup.

*Note:* the **State** shown on the Stories tab is not one of these — it is the story's
own lifecycle, \`live\` or \`closed\`. The states above live inside the gate's judgement,
not in the database.

---

### Phase 5: WRITING (Writer Node)
For items that pass the gate, the writer composes the Telegram post.

**Writer rules:**
- **Headline:** One fact = one line (no fluff)
- **Body:** Answers questions the headline leaves open, sourced only from the wire (never invented)
- **Length:** Tight (often just headline, sometimes 2-3 lines of detail)
- **Emoji:** Semantic (🟢 = growth, 🔴 = decline, 🏦 = banking, etc.) — 15-item whitelist + country flags
- **Bullets:** Use ▪️ whenever a list fits
- **Data print format:** \`📊 <b>US CPI 3.4% y/y (forecast 3.4%, previous 3.4%)</b>\`

---

### Phase 6: EDITING (Editor Node)
The editor validates the post before sending.

**It can only reject by naming one of 11 rules** — the list is enforced by the
schema, so it cannot invent a reason:

\`FACTUAL_DRIFT\` · \`OVERCLAIM\` · \`HYPE\` · \`NO_NEWS\` · \`WRONG_TOPIC\` ·
\`EMPTY_BODY\` · \`INCOMPLETE\` · \`TOO_LONG\` · \`BROKEN_HTML\` · \`INJECTION\` · \`UNSAFE\`

The rule that fired is written into the item's reason, which is what you read on the
Posts tab — e.g. \`editor rejected it ['WRONG_TOPIC']: ...\`.

**Some rules are fixable, some are fatal.** A fixable one (a broken tag, an empty
body) sends the post back to the writer for another attempt, up to a limit. The rest
end it.

**This is the one station that fails CLOSED.** Everywhere else — dedup, the sorter,
placement, the gate — a failure lets the item through, because a silent channel is
worse than a duplicate. Here the reasoning inverts: if the editor cannot judge the
text, nothing is sent. A wrong post cannot be recalled.

---

### Phase 7: PUBLISHING (Publish Node)
Approved post is sent to Telegram \`@market_one_news\`

**Rules:**
- **Channel-wide: 4 posts an hour** (\`.env\` overrides the \`config.py\` default of 12).
  When that ceiling is hit, items are still sorted and placed into stories — only the
  sending waits. Otherwise a busy hour would expire the queue and lose the content.
- **Per story: a 6-minute minimum gap and 12 posts maximum.** Both are context for the
  gate rather than hard walls — a 25-minute gap once silenced a real escalation.
- A later post in a story **replies to that story's first post**, so it reads as a thread.
- Video and GIFs from a tweet are sent as real media, not a link — except from
  \`crypto_banter\`, whose media is dropped on purpose.
- An item that waits in the queue longer than **90 minutes expires**. Late breaking news
  is worse than none.

---

## 🎯 Dashboard Summary

The dashboard shows you **this entire pipeline in real time:**

| Stage | Shows | Status Colors |
|-------|-------|---|
| **Collection** | Raw items arriving | All incoming news |
| **Dedup** | Caught duplicates | 🔵 duplicate |
| **Sorter** | What passed/failed filters | 🟢 passed, 🔴 low_impact/irrelevant |
| **Stories** | Grouped items, state machine | All open stories + their items |
| **Gate** | What posts vs. holds | 🟢 published, 🟡 held, 🔴 rejected |
| **Writer** | Composed post text | Full text + metadata |
| **Editor** | Validation results | ✓ approved, ✗ rejected (reason shown) |
| **Publish** | Sent to Telegram | Timestamp + message ID |

---

## 💡 Key Concepts to Remember

1. **One item ≠ one post.** An item is a news wire. A post is what readers see. 3 wire items → 1 post if they're the same story state.

2. **Embeddings shortlist, an LLM decides.** Cosine similarity says what two items are *about*; it cannot say whether the same thing *happened*. Real duplicates and genuinely different stories overlap on that score, so anything between 0.72 and 0.95 is handed to a judge that reads both texts.

3. **A story speaks once per state, not once per wire item.** "Yields through a multi-year high" posts once; the next yield print inside the same move is held. A ceasefire, a ruling, a new party entering, or a price through a landmark it has not crossed before starts the next post.

4. **Gate = second opinion.** If the sorter misplaces an item, the gate can eject it to its own story, preventing silent loss.

5. **Fail-open design.** Sorter/gate errors → post anyway (safety). Only editor can reject (if text is broken).

6. **Everything is auditable.** Every item has a status + reason. If something didn't post, you can see why (held, expired, rejected, low_impact, etc.).

---

## 🔧 Config Tuning (if needed)

Defaults live in \`config.py\`, but \`.env\` overrides them — read \`.env\` first, or you
will tune a number the running channel never sees.

- **\`COSINE_SHORTLIST\`** (0.72): what reaches the judge. Lower catches more rephrases at the cost of more LLM calls; 0.75 already started missing real duplicates.
- **\`COSINE_CERTAIN\`** (0.95): merged outright, no judge. Raise if you see wrong merges.
- **\`FUZZY_THRESHOLD\`** (92): how alike two headlines must be to count as the same wording.
- **\`MIN_IMPORTANCE\`** (4): the publishing bar. Raise = fewer posts, lower = more noise.
- **\`MAX_POSTS_PER_HOUR\`** (**4** — overridden in \`.env\`; the default in \`config.py\` is 12).
- **\`STORY_MIN_GAP_MINUTES\`** (6) and **\`STORY_MAX_POSTS\`** (12): per story, anti-double-post.
- **\`STORY_IDLE_HOURS\`** (36) / **\`STORY_MAX_HOURS\`** (168): when a story goes quiet, and its hard end.
`;
