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

**Semantic Deduplication (Cosine Similarity):**
When a new item arrives, the system:
1. Converts the title into a semantic embedding (OpenRouter /embeddings API)
2. Compares it against all recent posted items (cosine similarity threshold: 0.80)
3. If match found: mark as "duplicate" → archive it (never posts)
4. If new: continues to the next stage

*Why this works:* "Fed raises rates 25 bps" and "Fed hiking by quarter point" are the same news, different words. Cosine similarity ≥ 0.80 catches this. Threshold calibrated on real data.

---

### Phase 2: SORTING & FILTERING (Sorter Node)
The sorter judges: "Is this worth posting?"

**Sorter criteria:**
1. **Importance score** (1-10): calculated by LLM based on market impact
2. **Topic check:** crypto, markets, news (not promotions, spam, or off-topic)
3. **Redundancy test:** "Is this already being discussed in an open story?"
4. **Economy weight:**
   - US news: weight 5 (most important)
   - EUR, CNY, JPY: weight 4
   - Other: weight 3
5. **Price anchor test:** Does this news actually move markets, or is it noise?
6. **Priced-in test:** Is this using the economic calendar to check if it's stale?

**Output:** \`relevant\` (continues) or \`low_impact\`/\`irrelevant\` (archived, never posts)

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

**State ladder:**
- STARTED → RISING → PEAKED → FALLING → ENDED
- (Or STEADY if no clear direction)

**Gate logic:**
- **First post of a story:** Always post (reader needs to know this story exists)
- **Second+ post:** Only post if the STATE CHANGED
  - ✅ "Yields start rising" → post (STARTED → RISING)
  - ✅ "Yields are falling now" → post (RISING → FALLING)
  - ❌ "Another yield data point, still rising" → hold (no state change)
- **Held items:** Queued, will be included in the story's next post or a digest

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

**Editor checks (11 rules):**
- ✓ No empty body
- ✓ Not just a headline (needs substance)
- ✓ Emoji used correctly (not spam)
- ✓ No HTML markup showing (only clean text)
- ✓ No source byline in body (URL is separate)
- ✓ Response posts reply to story's first message
- ✓ Calendar data matches real scheduled releases (no drift)

**If edit fails:** item is rejected and archived

---

### Phase 7: PUBLISHING (Publish Node)
Approved post is sent to Telegram \`@market_one_news\`

**Rules:**
- Respect rate limits: 6 min gap between posts, max 12 posts per story
- Reply to first post if it's a story update (creates a thread)
- Include media if source has video/GIF (via tweet-relay)

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

2. **Semantic dedup protects against noise.** Cosine 0.80 catches rephrasing; threshold calibrated so "Fed cuts" doesn't repeat 10 times.

3. **Story state machine prevents repetition.** "Yields rising" posts once. The next "yields still rising" is held. When they fall, that's a new post.

4. **Gate = second opinion.** If the sorter misplaces an item, the gate can eject it to its own story, preventing silent loss.

5. **Fail-open design.** Sorter/gate errors → post anyway (safety). Only editor can reject (if text is broken).

6. **Everything is auditable.** Every item has a status + reason. If something didn't post, you can see why (held, expired, rejected, low_impact, etc.).

---

## 🔧 Config Tuning (if needed)

- **Dedup threshold** (0.80): raise to be stricter, lower to catch more rephrases
- **Sorter importance cutoff** (e.g., ≥ 5): raise = fewer posts, lower = more noise
- **Gate state ladder**: currently STARTED → RISING → PEAKED → FALLING → ENDED
- **Rate limits**: 6 min gap, 12 posts/story max — tune if posting too fast/slow
- **Story timeout**: 36 hours idle = story closes (can be adjusted)
`;
