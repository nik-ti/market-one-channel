# Improving the channel

An analysis of what this channel is today, why the Wildberries post got through,
and what would have to change for someone to want to subscribe.

Written after reading the whole pipeline and sampling the live output of all four
configured feeds. Recommendations are ordered by leverage, and each says what it
costs.

---

## 1. The Wildberries post: what actually happened

> 🌍 **Russian retailer evacuates warehouses after drone attacks** 🔥
> Wildberries, Russia's largest online retailer, says it has evacuated
> warehouses in Ryazan following Ukrainian drone attacks on industrial sites.
> 🔗 aljazeera · #geopolitics

**It was not the editor's fault, and changing the editor's prompt would not have
stopped it.**

`nodes/editor.py` can only reject a post by naming one of ten rules, and none of
them is "this is boring". Check them against this post: it doesn't drift from its
source, doesn't drop a hedge, is a real event, is filed under a topic it
plausibly belongs to, has valid HTML, is complete, is short, isn't hyped by the
writer, follows no injected instruction, is safe. **The editor approved it
because it was, by its own rules, a correct post.** It was doing its job.

The decision to cover it at all was made a step earlier, in `nodes/sorter.py`.
That prompt asked one question — *"has something been DECIDED, ENFORCED, or
BROKEN?"* — and listed "fighting starting, escalating, or stopping" as a 4. A
drone strike on industrial sites fits that description exactly. The model scored
it 4, `MIN_IMPORTANCE` is 4, and it published.

So the rubric was an **event test**, and a running war generates qualifying
events every single day. Nothing anywhere in the pipeline asked the only question
that matters for this audience: *who has to reprice anything?* Nobody does. A
warehouse is not a traded asset, a retailer is not a listed equity that moves an
index, and the war was already on yesterday.

### What has been changed (already committed)

`nodes/sorter.py` now makes the model **name the market that has to reprice
before it scores** — one of `crypto`, `rates_fx`, `energy`, `commodities`,
`equities`, `risk_sentiment`, or `none` — and `none` caps the score at 3, which
is below the publishing threshold. The cap is enforced in code, not just asked
for in the prompt, on the same reasoning as stripping foreign links out of
finished posts: a model that has just written "nothing needs repricing" and then
scores the item 4 has contradicted itself, and the specific field beats the
number.

Two further tests were added because a live conflict is the main way filler gets
in:

* **The continuing-story test** — another instalment of something already running
  is a 3 unless it crosses a line: a new participant, a new class of target, or a
  changed rule. A strike on the same kind of target in the same war is more of
  the same, however large the explosion.
* **The priced-in test** — a confirmation of what everybody already expected is
  a 3.

And three worked examples, including the exact failure:

| Story | market | score |
|---|---|---|
| Russian retailer evacuates warehouses after drone attacks | `none` | 2 |
| Ukrainian drones halt loading at Novorossiysk oil terminal | `energy` | 4 |
| Russia says it reserves the right to respond | `none` | 3 |

Same war, same weapon, three different answers. That is the distinction the old
prompt could not draw.

### Why the gate is now inspectable, and why that matters more than the prompt

The prompt will be wrong again. Prompts always are. The thing that decides
whether you find out is whether the filter leaves evidence.

The editor has had an audit trail since day one, precisely because of the
nmd_consulting disaster. The importance gate had none — it throws away **far
more** than the editor, **earlier**, and **before any post exists to record**.
All you got was `below_importance: 47` in a counter. You could not have answered
"what good stories did we not run yesterday?" at all.

So dropped stories now get their own status (`low_impact`, kept separate from
`irrelevant`, which is sport and opinion columns you never want to see again),
and:

```bash
python3 tools/stats.py --dropped                 # real news the gate refused
python3 tools/stats.py --dropped --market none   # the judgement most worth checking
python3 main.py stats                            # now shows the market breakdown
```

**Also fixed:** `tools/dry_run.py` never applied `MIN_IMPORTANCE`. It was
rehearsing a different pipeline from the live one — writing posts for stories
that would never run, and showing you none of the ones the gate binned. Since the
gate is the main control on what the channel feels like, the rehearsal was hiding
the setting you most need to see. It now prints a `📉 BELOW THE BAR` line for
every dropped story, with its market and reason.

### What to watch for now

The risk has moved. The gate is now stricter, so the failure mode to watch is
**the channel going quiet**, not filler. Do this before you touch anything:

```bash
python3 main.py collect --once
python3 tools/dry_run.py --limit 30
```

Read the `📉 BELOW THE BAR` list. If good stories are in it, loosen the market
definitions or the continuing-story test in `nodes/sorter.py` — **not**
`MIN_IMPORTANCE`, which is a blunt instrument that would let everything back in.

---

## 2. The bigger problem, honestly

Fixing the filter removes bad posts. It does not create a reason to subscribe.
Those are different problems, and the second one is harder.

Here is the channel from a reader's side of the glass:

* Everything on it is **available free, earlier, elsewhere**. The X-sourced posts
  are literally verbatim reposts of accounts anyone can follow directly, arriving
  later. `passthrough()` is the right engineering decision — it makes
  hallucination impossible — but it means those posts add nothing but delay.
* The RSS posts are **shorter versions of articles**, with a link to the article.
  A reader who cares clicks the link. A reader who doesn't wasn't going to trade
  on it.
* There is **no judgement anywhere**. No context, no numbers, no read-through, no
  continuity between related stories, no view. The channel never tells you
  anything that isn't in the source.
* It covers **two unrelated beats**, reconciled by a hashtag.

That is a wire relay. Wire relays are a solved, commoditised product with
entrenched incumbents who are faster and free.

**The thing this channel could uniquely offer is the judgement its own pipeline
is already making and then throwing away.** The sorter decides which market has
to reprice and writes a sentence explaining why. That sentence — the single most
valuable output of the whole system — is currently written to a database column
and never shown to anybody.

---

## 3. Measured: your sources are the upstream cause

I pulled the live feed of all four sources and read every headline. This is the
current intake:

| Feed | Sampled | Could ever pass a market test | Notes |
|---|---|---|---|
| `guardian_world` | 21 | **~1 (5%)** | elephants, Ebola, bird flu, bats, wildfires, an earthquake covered 4 separate times |
| `aljazeera` | 21 | **~3 (14%)** | 5 of 21 were FIFA/F1 — a quarter of the feed was sport |
| `coindesk` | 21 | ~6 (29%) | much of the rest is price commentary, correctly filtered as opinion |
| `theblock` | 20 | **~9 (45%)** | clearly your best source |

Two things follow.

**First, the geopolitics side of this channel is being fed by two general-interest
firehoses.** You are paying an LLM to find one needle in twenty, and every
borderline call is a chance to be wrong. The Wildberries post is what that looks
like when it goes wrong once. Shrinking the haystack is more reliable than
improving the needle detector, and it is cheaper.

**Second — and worse — the current sources will make you miss the big ones.** In
the same sample, Al Jazeera's coverage of *"Iran hits US in Jordan, US-Saudi
strikes on Iraq"* was framed as **"Is war spreading?"** — an analysis piece. The
sorter correctly marks analysis as `relevant=false`. So the largest oil story in
the entire intake would have been dropped, while a warehouse evacuation
published. That is not a tuning problem. It is a sourcing problem: when your only
window onto an event is a comment desk, you get comment.

### Recommended source changes

Prefer **narrow official feeds over broad news feeds**. Primary sources are
higher signal, faster than the outlets reporting them, immune to hype framing,
and their headlines are already written as statements of fact — which is exactly
what the sorter is looking for. I probed these; the working ones are marked.

| Add | Topic | Status |
|---|---|---|
| `https://www.federalreserve.gov/feeds/press_all.xml` | rates | ✅ 200, 20 items |
| `https://www.sec.gov/news/pressreleases.rss` | crypto/equities | ✅ 200, 25 items |
| `https://www.eia.gov/rss/todayinenergy.xml` | energy | ✅ 200, 23 items |
| ECB press releases (via `ecb.europa.eu/home/html/rss.en.html`) | rates | needs the real feed URL — the one I tried returned a single item |
| Treasury/OFAC sanctions press releases | geopolitics | redirects; find the current URL |
| `bls.gov` release feed | rates | 403 without a browser user-agent — `fetch_rss.py` may need one |

Also worth considering: a wire with a business desk (AP has feeds; Reuters
retired its public RSS), and for crypto keep both existing feeds — The Block in
particular is carrying its weight.

And **drop or narrow `guardian_world`**. At ~5% usable it is mostly a tax. If Al
Jazeera publishes a narrower economy or Middle East sub-feed, prefer it to
`all.xml`.

On the X side: `crypto_banter` is a commentary account, and commentary is exactly
what the sorter is built to reject — it will generate cost and rejections and
almost nothing else. Check it in `--dropped` after a week and cut it if it never
lands. Also, `BLS_gov` is filed as `geopolitics` but everything it posts is
`rates_fx`; harmless today, but see the hashtag recommendation below.

---

## 4. Changes ranked by leverage

### A. Show the judgement you are already making ★★★ highest leverage

Add **one line to every post** saying what it means for the market. Not opinion —
the transmission channel:

> 🛢 **Ukrainian drones halt loading at Novorossiysk oil terminal**
>
> Exports from Russia's largest Black Sea crude terminal stopped on Tuesday after
> an overnight drone attack. The port handles about 2% of global seaborne crude.
>
> _Why it matters: a supply route for physical crude is offline, with no restart
> date given._
>
> 🔗 reuters · #energy

That italic line is the only thing on the channel a reader cannot get faster
elsewhere. It is also the entire argument for the channel existing.

**The reason this is safe here, when it would be reckless in most pipelines:**
you already compute it, in a different node, with a different model, before the
writer sees anything. `sorter.execute()` returns `market` and `reason`. Feed both
into `writer.execute()` as the *basis* for the line — the writer explains the
market the sorter named, rather than inventing a rationale at write time. Two
models agreeing on a market is a much stronger claim than one model
free-associating.

Guardrails, all cheap:

* Hard rules in the writer prompt: name the mechanism, never a direction, never a
  price, never a target, never advice. "A supply route is offline" ✅. "Oil should
  rally" ❌.
* Add one editor rule — `UNSUPPORTED_READTHROUGH`: the line claims a market
  effect the source doesn't support. This is the one addition to the editor's
  rule list I would make, because it guards a genuinely new risk you are choosing
  to take on, which is what that list is for.
* If the sorter's market was `risk_sentiment`, consider omitting the line
  entirely — that is the vaguest category and the one most likely to produce
  waffle.

**Cost:** a prompt change, one plumbing change in `publish_loop.py` to pass the
verdict through, one new editor rule. No new models, no new calls.

### B. Publish less, and publish it in tiers ★★★

`MAX_POSTS_PER_DAY` is 60 and `MAX_POSTS_PER_HOUR` is 12. Telegram growth data
consistently puts the sweet spot at **1–3 posts a day for most channels, with
news channels able to go higher, and unsubscribe rates climbing sharply past
five**. For a channel whose entire promise is "only things that move markets",
40–60 posts a day actively contradicts the pitch. Every marginal post trains the
reader that the channel isn't selective, and the muted reader is gone
permanently — Telegram gives you no way to win them back.

Two changes:

1. **Lower the caps** to something like 15/day, 4/hour. Start there and raise it
   only if `--dropped` shows you are losing real stories.
2. **Add a ceiling as well as a floor.** `MIN_IMPORTANCE` is a floor: it stops
   filler but does nothing on a busy day, when 30 legitimate 4s arrive and 30
   legitimate 4s go out. Add a rolling quota — *at most N posts per 6 hours,
   highest importance first* — in `publish_loop.publish_once()`. The queue is
   already ordered by importance, so this is a few lines. The floor prevents
   filler; the ceiling prevents floods. You need both.

Consider two visible tiers, which the ⚡/🌍 system is already 80% of the way to:
a **flash** tier for 5s that should arrive within a minute, and a **brief** tier
for 4s that can wait and be batched. Tiering is what lets a reader keep
notifications on.

### C. Hashtag by market, not by topic ★★★ nearly free

`#crypto` and `#geopolitics` are your desk's categories, not a reader's. You now
compute something far more useful on every item and store it in `items.market`.

Publish `#energy`, `#rates`, `#crypto`, `#commodities`, `#equities` instead of —
or alongside — the topic tag. Telegram hashtags are tappable inside a channel, so
this instantly turns one channel into five filtered feeds organised the way a
trader actually thinks. A rates person taps `#rates` and never sees a token
launch.

**Cost:** a dict in `config.TOPIC_STYLE`, and passing `item["market"]` into
`publisher.compose()`. The data already exists in the row.

### D. Thread developing stories instead of dropping them ★★

Right now a follow-up to a story you already covered is either a duplicate
(silently binned) or a fresh standalone post with no memory of the first. Neither
is what a reader wants.

You already have the machinery: `dedup.py` computes embeddings and cosine
similarity against everything from the last 48 hours. Items landing in the band
*just below* the duplicate threshold — say 0.70 to 0.80 — are typically **the same
story moving on**. Telegram's `sendMessage` takes `reply_to_message_id`, and
`posts.telegram_message_id` is already stored.

So: when a new item is a near-match to something published in the last 48 hours,
post it as a **reply** to that message. Telegram renders it as a quoted card, and
the reader sees the story develop. Almost nothing does this, it reads as
enormously more competent than a flat feed, and the expensive part — the
embeddings — is already paid for.

**Cost:** a similarity band in `dedup.py`, an optional argument on
`telegram_client.send_text`, one new column. Genuinely small.

### E. Put numbers in the post ★★

A market channel that never mentions a price does not feel like a market channel.
Where the sorter named a market, attach a level at the time of posting: BTC/ETH
from a free API for `crypto`, Brent for `energy`, DXY or the 10-year for
`rates_fx`.

Two rules: fetch it in **code**, never from the model (a hallucinated price is
the worst thing this channel could publish), and **timestamp it** — "BTC $64,100
at time of posting" — because it will be stale within minutes and a bare number
implies a currency it doesn't have.

**Cost:** one small fetcher with a cache and a hard fail-open (no price → no
line, never a blocked post).

### F. Anchor posts and a schedule ★★

Retention on Telegram is habit. Habit needs something that arrives when the
reader expects it. Breaking news, by definition, cannot do that.

* **Morning: "what to watch today"** — the scheduled events with dates known in
  advance: central bank meetings, CPI and jobs prints, ETF decision deadlines,
  token unlocks, major earnings. This is the single most-forwarded format on
  every market channel worth reading, and it is entirely mechanical to produce
  from a calendar. No model risk at all.
* **Evening: what actually happened**, four or five lines, linking back to the
  day's posts.
* **Weekly recap** for people who muted you during the week.

These also solve the quiet-day problem: the channel is never silent, even when
nothing passes the gate. That matters more than it sounds — a channel that
sometimes goes 30 hours without posting gets forgotten.

**Cost:** the biggest item on this list, because it needs an events source. Start
by hand-maintaining a small YAML of known dates; it will still beat nothing.

### G. Close the loop with reader reactions ★★

Every calibration decision in this pipeline is currently made blind. Nothing,
anywhere, measures whether readers valued a post. `MIN_IMPORTANCE = 4` is a
guess; the sorter's rubric is a guess informed by your taste.

Telegram will tell you. Bot API 7.0+ delivers **`message_reaction_count`**
updates for channel posts, provided the bot is an administrator **and** explicitly
lists `message_reaction_count` in `allowed_updates` (they are aggregated and can
lag by a few minutes). Log them against `posts.id` and you can finally ask:

* Which `market` gets the most reactions per post?
* Do 5s actually outperform 4s, or is the scale imaginary?
* Which source produces posts people react to, and which produces wallpaper?

After a few hundred posts that is a real answer to "what should this channel
cover", replacing several guesses at once. Nothing else on this list gives you
ground truth.

**Cost:** a small polling loop and a table. Fully independent of everything else.

### H. Smaller things, mostly free

* **Take emoji away from the writer entirely.** The 🔥 on that drone-attack post
  came from `EMOJI_RULE_NORMAL`, which invites the model to pick "1-3 emoji that
  complement the content". On a story about explosions, 🔥 reads as celebratory. A
  fixed emoji per market, chosen in code exactly as the topic emoji already is
  (🛢 energy, 🏦 rates, 🪙 crypto, ⚖️ regulation), is more consistent, more
  professional, and removes a whole class of tone failure. The code that strips
  emoji from brief posts already exists — reuse it for all posts.
* **A pinned post.** What the channel covers, what it never does (no signals, no
  advice, no referrals), how the tags work, and how fast it aims to be. Cheap,
  and it converts visitors who arrive from a forward.
* **Measure your latency.** For a channel positioning on speed, source-publish →
  channel-post is *the* number, and nothing records it. `items.published_at` and
  `posts.sent_at` are both stored — the subtraction is one query and one line in
  `stats.py`. If it turns out to be 20 minutes, speed is not your pitch and you
  should compete on judgement instead. Worth knowing which.
* **Fix the `("📰", "#news")` fallback** in `publisher.compose()`. It only fires if
  a topic is unexpected, but if it ever does, you have published an off-brand tag
  with no way to notice. Log it.
* **Enable comments** by attaching a discussion group. Discussion is retention,
  and it costs one setting.

---

## 5. Two things I would not do

**Do not add importance judgement to the editor.** It is the intuitive fix — it
is the node with "approve or decline" in it — but it is the exact mistake that
cost nmd_consulting 110 posts. That rule list is a fixed enumeration on purpose,
and "not interesting enough" is unfalsifiable in a way the other ten rules are
not: you cannot check it against the source, so you cannot tell a correct
rejection from a broken one. Keep relevance in the sorter, where it is decided
once, recorded with a market and a reason, and reviewable. One gate, well built,
with its work shown.

**Do not chase subscriber count with paid promotion yet.** Acquisition into a
channel that has no reason to be kept just buys you a higher unsubscribe rate.
Retention is the multiplier: fix the product, then promote. Cross-promotion with
one or two niche-relevant channels is the exception — it is cheap and it brings
people who already want this.

---

## 6. Order I would do it in

**Now (days, mostly prompt and plumbing)**
1. ✅ Market-transmission gate — done and committed
2. Rehearse 30 real items and read every `📉 BELOW THE BAR` line
3. Drop or narrow `guardian_world`; add Fed, SEC, EIA
4. Hashtag by market (§C) — nearly free, immediately visible to readers
5. Lower the posting caps (§B)

**Next (a week or two)**

6. The "why it matters" line + the `UNSUPPORTED_READTHROUGH` editor rule (§A)
7. Fixed emoji per market, taken away from the writer (§H)
8. Rolling quota ceiling (§B)
9. Latency measurement + pinned post (§H)

**Then (worth real effort)**

10. Threading developing stories (§D)
11. Reaction logging (§G) — start early, it needs volume to become useful
12. Price context (§E)
13. Morning "what to watch" (§F)

---

## 7. Numbers to watch afterwards

Existing four, plus:

| Number | Where | What it means |
|---|---|---|
| **share with `market = none`** | `main.py stats` | Over ~90%: your sources are general news, not market news. Under ~40%: the model is reaching for justifications — read `--dropped` |
| **good stories in `--dropped`** | `stats.py --dropped` | The only direct evidence the gate is too strict. Read it weekly |
| **source → post latency** | not built yet | Decides whether speed or judgement is your pitch |
| **reactions per post, by market** | not built yet | The only reader signal available. Everything else is your taste |

---

*The filter fix is committed and tested. Everything from §4 onward is a proposal —
none of it is implemented, and each item is independent, so they can be picked up
in any order.*
