# News Channel — crypto / geopolitics

**What it does:** reads news from RSS feeds and from X accounts, throws away
anything it has already covered or that isn't market-moving, rewrites what's left
into one consistent voice, has a second AI check the result, and posts it to your
Telegram channel.

**What starts it:** a systemd service running `main.py run`, which never stops.
Feeds are checked every 10 minutes; tweets arrive continuously.

---

## The flow

```
   RSS feeds  ──────┐
   (4 sources,      │
    every 10 min)   │
                    ├──►  fetch_rss.py   ─┐
                    │                     │
   X accounts  ─────┤                     ├──►  CHECK 1: same link?      (free, database)
   (via the shared  │                     │     CHECK 2: same headline?  (free, database)
    tweet relay,    ├──►  fetch_tweets.py ┘             │
    continuously)   │                                   ▼
                    │                        items table (status = queued)
                    └───────────────────────────────────┬───────────────────
                                                        │
              ┌─────────────────────────────────────────┘
              ▼
    ┌──  every 120 seconds  ─────────────────────────────────────────────┐
    │                                                                    │
    │   0.  drop anything that has gone stale (older than 90 min)        │
    │            │                                                       │
    │            ▼                                                       │
    │   1.  dedup.py      CHECK 3: same wording?   (rapidfuzz, ~1ms)     │
    │                     CHECK 4: same subject?   (embeddings — builds   │
    │                              a shortlist, decides nothing)          │
    │                     CHECK 5: same event?     (judge.py, ~25×/day)   │
    │            │                                                       │
    │            ▼                                                       │
    │   2.  sorter.py     AI · is it news? which market reprices?        │
    │            │        market impact 1-5 · "none" is capped at 3      │
    │            │        drops anything below MIN_IMPORTANCE (4)         │
    │            ▼                                                       │
    │   3.  writer.py     RSS: AI rewrite · X: passed through as-is      │
    │            │        deepseek-v3.2 · brief posts get ⚡              │
    │            ▼                                                       │
    │   4.  editor.py     AI · approve or reject, naming a rule          │
    │            │        minimax-m2.7 · different lab from the writer   │
    │            ▼                                                       │
    │   5.  publisher.py  add the one emoji + source link, then send    │
    │            │        max 2 per round, 12 per hour, 60 per day       │
    │            ▼                                                       │
    │      YOUR TELEGRAM CHANNEL                                         │
    └────────────────────────────────────────────────────────────────────┘
```

Roughly **$3 a month** at 40 posts a day.

---

## Setup

**1. Settings and keys**

```bash
cp .env.example .env
nano .env
```

You need four things:

| Setting | Where to get it |
|---|---|
| `TELEGRAM_BOT_TOKEN` | @BotFather → /mybots → your bot → API Token |
| `CHANNEL_ID` | `@yourchannel`, or the `-100…` number from @userinfobot |
| `ERROR_CHAT_ID` | your own user id, from @userinfobot |
| `OPENROUTER_API_KEY` | openrouter.ai/keys |

The bot must be an **administrator** of the channel with **Post Messages** on.

**2. Check it**

```bash
python3 main.py check          # are the settings complete?
python3 main.py initdb         # create the database
python3 tools/check_sources.py # do all the feeds work?
```

**3. Rehearse before going live** — see the next section. Do not skip this.

**4. Install as a service**

```bash
sudo bash deploy/install-service.sh
```

Everything it needs is already installed on this machine. On a fresh one:
`python3 -m pip install --user --break-system-packages -r requirements.txt`

---

## ⚠️ Rehearse before going live

Posts go **straight to the live channel**. There is no approval button and no
preview channel. So `tools/dry_run.py` is your one chance to see what the AI
writes, and what the AI editor rejects, before either is doing it in public.

```bash
python3 main.py collect --once      # get some real news in
python3 tools/dry_run.py --limit 25 # run it all through, printed here
```

That costs about five cents and sends nothing. Read every post — is that the
voice you want? Then read every **rejection** especially carefully, and every
**📉 BELOW THE BAR** line: those are real stories the channel chose not to run,
shown with the market that would have had to reprice. That list is where you
find out whether the channel is about to be full of things nobody trades on, or
about to be silent.

This matters because of a real failure on this machine: a sibling project
(`systems/nmd_consulting`) has an editor node that quietly destroyed **110 of its
712 finished posts** — its reject list contained an ordinary common word. Nobody
noticed for months, because nothing recorded why. Everything in `nodes/editor.py`
is built to stop that happening here, but the rehearsal is still the step that
catches a badly-tuned prompt before your readers do.

Tune by editing `PROMPT` at the top of `nodes/writer.py` and `nodes/editor.py`,
then run the dry run again. Iteration is free and instant.

Once live, start cautious: put `MAX_POSTS_PER_HOUR=4` in `.env` and raise it
after a day or two.

---

## Nodes

### `fetch_rss.py`
- **What:** reads the RSS/Atom feeds listed in `config.SOURCES`.
- **In:** nothing · **Out:** `Article` objects
- **Why it's cheap:** it sends back the caching tokens each site gave it last
  time, so a site with no new articles replies "nothing changed" with no content
  at all. Most polls cost almost nothing.
- **Freshness gate:** articles older than `ARTICLE_MAX_AGE_HOURS` (24) are ignored.
  Blockworks is why — its feed still responds perfectly but its newest article is
  months old, and without this those would have been posted as today's news.
- **Watch out:** handles three feed formats (RSS 2.0, Atom, RSS 1.0/RDF). Getting
  that wrong fails *silently* — a valid page with no articles found in it. That
  happened with the DW feed on day one, so `check_sources.py` now reports any
  feed returning zero articles as broken.

### `fetch_tweets.py`
- **What:** reads X posts from the shared relay over Redis.
- **In:** nothing · **Out:** `Tweet` objects, with image addresses
- **Why a relay:** X allows one connection, and `trading/infra/tweet-relay`
  already holds it. It copies every tweet onto a shared conveyor belt; we watch
  it with our own bookmark (`news-channel`), completely separate from the
  trading bot's (`sniper-ingest`). Both see everything; neither disturbs the other.
- **The important difference:** unlike the trading bot, we **catch up** after a
  restart rather than skipping what we missed — a news channel with a hole in it
  is a broken news channel. Three guards stop that flooding you: tweets older
  than 45 minutes are binned, at most 25 are kept per batch, and the hourly
  posting limit backs it all up. Tested: feeding all 95 tweets then on the belt
  through the filters produced **1** item.

### `dedup.py`
- **What:** decides whether we have already covered this story.
- **In:** an item · **Out:** true/false
- **The five checks,** cheapest first — see the table below.
- **Fails open:** if the meaning check or the judge is unavailable, the item is
  treated as new. A duplicate is a small embarrassment; a silent channel is worse.

| # | Check | Cost | Catches |
|---|---|---|---|
| 1 | same link or tweet id | free | the same article re-listed every poll — most traffic |
| 2 | same headline | free | one story at two different addresses |
| 3 | same wording | ~1 ms | "Fed holds rates steady" vs "Fed leaves rates unchanged" |
| 4 | **same subject** | ~$0.000002 | draws up a **shortlist** of plausible repeats — the only check that can match a tweet to an article |
| 5 | **same event** | ~$0.00006 | **rules on that shortlist by actually reading both stories** — see `judge.py` |

#### Why check 4 is not allowed to decide on its own

It used to be, with one cutoff at 0.80, and it was wrong in **both directions at
once**. Measured on 20 hand-labelled pairs from this channel's own history:

```
real duplicates       scored 0.738 - 0.993
genuinely different   scored 0.785 - 0.900
```

Those ranges overlap, so no cutoff anywhere separates them. Two live failures:

- **Missed:** three posts about one Fed rate decision went out minutes apart
  (0.738, 0.745, 0.797 against a 0.80 line).
- **Wrongly merged:** *"$49.75M ETF **out**flows"* absorbed *"$32.11M ETF
  **in**flows"* at 0.900 — opposite events, a day apart. The second never posted.

Lowering the cutoff was measured too: at 0.72 it caught every real duplicate and
**doubled** the wrong merges. The cause is short text — with only a headline to
go on, the score tracks what a story is *about*, not what *happened*, and most
of what this channel reads is tweets.

Replaying a real day (188 items, 29 July) through the new ladder dropped 18
duplicates, **12 of which scored below the old 0.80 line** and would have been
posted twice.

#### The time gate

Before checks 4-5 run, candidates more than `DUPLICATE_MAX_GAP_HOURS` (12) away
are discarded outright. Recurring reports — daily ETF flows, weekly roundups —
are nearly word-for-word identical from one edition to the next, so yesterday's
is the single most dangerous thing in the pool. Every real duplicate measured
here arrived within 10.1 hours; the worst false merges were 24 hours apart.

#### The numbers guard

Checks 3 and 4 both refuse to merge two headlines differing only by a number,
because "Fed cuts 25bp" and "Fed cuts 50bp" score 97% alike and are completely
different events. It has already earned its keep: it rescued two different
weekly columns titled *New Ecommerce Tools: July 15* and *July 22*.

### `judge.py` (AI)
- **What:** given two stories that look alike, are they the same event?
- **In:** the new item + one candidate · **Out:** true/false + a written reason
- **Runs ~25 times a day** — only on the shortlist, not on everything.
- **Model:** `google/gemini-2.5-flash-lite`. Picked over deepseek-v3.2 because
  it made **no wrong merges** on the test set (85% vs 80%) and is 7× faster.
  A wrong merge silently deletes a real story; a missed duplicate just means one
  extra post. Prefer the model that errs towards publishing.
- **`minimax-m2.7` is disqualified** here despite being the editor: 13 of 20
  concurrent calls failed on rate limits and unreadable JSON. The judge fires in
  bursts by nature — breaking news arrives all at once.
- **Every verdict is logged** to `dedup_hits` with the reason in plain English,
  so you can see *why* a post vanished instead of guessing.

### `sorter.py` (AI)
- **What:** is this news at all, what topic, **which market has to reprice**, and
  how market-moving (1-5)?
- **The volume control.** Anything scoring below `MIN_IMPORTANCE` (4) is dropped.
  A ban that has *happened* is a 4; one being *considered* is a 3. Roughly 85% of
  general news is filtered out here.
- **This is the only node that asks "should we cover this?"** The editor
  downstream checks a finished post against its source — accuracy, hedging,
  format, safety — and its rule list contains nothing about a story being dull,
  deliberately. So when something uninteresting reaches the channel, this prompt
  is what needs changing, not the editor's.
- **It names the market before it scores.** The rubric used to ask only whether
  something had been "decided, enforced, or broken", which is an event test, and
  a war produces qualifying events daily. It let through *"Russian retailer
  evacuates warehouses after drone attacks"* — a real event, correctly filed as
  geopolitics, genuinely different from yesterday, and useless to anyone holding
  a position. The model now picks one of `crypto`, `rates_fx`, `energy`,
  `commodities`, `equities`, `risk_sentiment` or **`none`**, and `none` caps the
  score at 3 **in code**, not just in the prompt. A named transmission channel is
  a concrete claim you can disagree with later; "feels important" is not.
- **Two tests that stop a running conflict flooding the channel:** the
  *continuing-story* test (another instalment of something already underway is a
  3 unless it crosses a line — a new participant, a new class of target, a
  changed rule) and the *priced-in* test (a confirmation of what everyone
  expected is a 3).
- **Now inspectable.** Dropped stories get their own status (`low_impact`) rather
  than being mixed in with sport and opinion, and `tools/stats.py --dropped`
  prints them with the market and the reason. Same principle as the editor's
  audit log: a filter you cannot inspect is a filter you cannot fix.
- **Model:** `deepseek/deepseek-v3.2`, temperature 0.0. Chosen for judgement,
  not price — this node only costs ~$1.50/month, and it decides what matters.
- **Why:** the main cost control. Rejecting a story here saves the writer's
  cost, which is seventeen times higher.
- **Fails soft:** if it breaks, we fall back to the source's own topic and carry
  on. It is an optimiser, not a safety gate.

### `writer.py` (AI — for RSS only)
- **What:** rewrites RSS articles into one consistent house voice.
- **X posts are NOT rewritten.** These accounts already write short, factual
  posts for exactly this audience, so rewriting cost money and added risk with
  nothing gained — the rewrite step was where the model invented casualty
  figures during testing. `passthrough()` publishes the tweet's own words, so
  the post cannot say anything the source didn't, because it *is* the source.
  It strips the t.co links, drops a redundant "JUST IN:", removes the sirens and
  rockets, and bolds the first line.
- **Watch out — X truncates its own long posts** at ~280 characters and appends
  a link to the rest, so stored tweets routinely end mid-sentence
  (`"...that a statute https://t.co/..."`). `passthrough()` trims back to the
  last complete sentence; if too little survives, it falls back to the AI writer
  rather than dropping a real story.
- **That trimming only runs on tweets long enough to have been truncated**
  (`X_TRUNCATION_CHARS`, 250), and this matters more than it looks. Trimming
  assumes text not ending in punctuation was cut off. For a long tweet that is a
  good assumption; for a short one it is simply wrong, because these accounts
  write **headlines**, and a headline has no full stop:

  > 🚨 JUST IN: Bitcoin falls below $60,000

  That is a whole tweet. Ungated, the trimmer walked back looking for a sentence
  ending, found the colon in "JUST IN:" — a colon used to count — and returned
  `"🚨 JUST IN:"`, which the prefix stripper then removed entirely. **Every
  headline-shaped tweet was destroyed this way, which is most of what
  WatcherGuru and TreeNewsFeed publish.**

  Nothing looked broken, because the caller fell back to the AI writer and the
  posts still appeared. They were just being *rewritten* rather than passed
  through — quietly turning off the one guarantee this node exists to provide,
  on the majority of tweets, for months. A colon is no longer a sentence ending,
  and the trim is gated on length.
- **"Is anything usable left?" is `passthrough()`'s question to answer**, and it
  returns an empty string to say no. The publish loop used to decide this itself
  with `len(post_html) < 40`, which counted the HTML — the `<b></b>` around a
  headline is 7 characters of tag that say nothing to a reader — and so rejected
  valid one-line tweets for being short.
- **Model:** `deepseek/deepseek-v3.2`, temperature 0.2. Its output is $0.40/M
  against Gemini Flash's $2.50/M, and the writer is output-heavy.
- **Prompt:** at the top of the file. Most of its odd-looking sections are scar
  tissue: *Factual Accuracy* exists because models silently upgrade "proposed"
  to "approved"; *Untrusted input* exists because half our input is arbitrary
  tweets that anyone can write instructions into.
- **Three lengths, set by how much source material exists:** 70-100 words
  normally; 45-65 with an image (Telegram caps captions at 1024 chars); **25-45
  words when the source is under 400 characters**, because you cannot honestly
  write 90 words from a 150-character summary — the model pads, and padding a
  news post means inventing. That was a measured failure, not a theory: a short
  Cointelegraph summary produced a sentence about "organized crime rings" that
  appeared nowhere in the source.
- **The ⚡ badge is separate, and X-only.** A short *X post* leads with ⚡ instead
  of the topic emoji and carries **no other emoji at all** — one mark is its whole
  visual signature. A short *RSS summary* gets the shorter length but keeps its
  normal topic emoji, because it is still a proper article and badging it as a
  flash alert would tell the reader something untrue about where it came from.
  Emoji the model adds are stripped in code on every post, not just discouraged
  in the prompt.
- **Guards:** a post that stops mid-sentence is discarded and retried, never sent.

### `editor.py` (AI)
- **What:** the final check. Reads the finished post *against its source*.
- **Model:** `minimax/minimax-m2.7`, temperature 0.0. Picked by testing 9 models
  on 7 source/post pairs: 7/7 with nothing missed, at 2.5s per post — same
  accuracy as gpt-5-mini, three times faster, half the output price.
  (GLM-4.7 and Claude Haiku 4.5 are unusable here — both reject the strict schema.)
- **Deliberately a different model family from the writer** — a model judges its
  own writing badly, approving prose that sounds like its own habits.
- **Four safeguards against it becoming a black box:**
  1. It can only reject by naming a rule from a **fixed list of ten**. A
     rejection with no rule named is **treated as an approval** and logged. It
     cannot kill a post for a feeling.
  2. Every decision is recorded — approvals too — with the exact text judged.
  3. Rejecting more than half of the last 20 posts sends you a Telegram alert.
  4. `tools/stats.py --declines` prints rejections in full so you can judge them.
- **Fails closed:** no verdict means nothing is published; the post waits.
  (Note this is the *opposite* of dedup, on purpose.)

### `publisher.py`
- **What:** adds the one emoji and the source link, then sends.
- **One emoji per post, added by code and never by the AI:** `crypto → 🪙`,
  `geopolitics → 🌍`, and `⚡` in place of the topic emoji for short X posts.
  That single mark is the channel's whole visual signature.
  - The writer is told to use no emoji at all, and `strip_emojis()` removes any
    it produced anyway — for **every** post, not just the brief ones. Asking
    nicely in a prompt is not enough on its own.
  - It used to be allowed "1-3 that complement the content", which sounds
    reasonable and was not. Choosing an emoji is a judgement about tone, and the
    model made it badly on exactly the stories where tone matters: it put a 🔥
    on a drone strike.
  - Republished tweets are stripped too. These accounts write in sirens and
    rockets; the facts are reproduced exactly, only the decoration goes, so a
    tweet looks like everything else on the channel rather than announcing which
    account it came from.
- **No hashtags.** Dropped deliberately. Two tags covering the whole channel
  sorted posts the way this desk thinks rather than the way a reader does, and
  every post carried a line of tag that said almost nothing a reader couldn't
  already see from the emoji. `docs/IMPROVING-THE-CHANNEL.md` proposes bringing
  them back per **market** (`#energy`, `#rates`) rather than per topic, which is
  the version that would actually earn its line.
- **Pacing:** max 2 per round, 90 seconds minimum between posts, 12/hour, 60/day.
  Telegram would allow roughly 20 a *minute*; the limit here is about readers,
  not the API.
- **Images:** one message — photo with the text as its caption — never a photo
  then a separate message. If the text is too long to caption, the picture is
  dropped and the full text sent. If the image fails to send, it falls back to
  text. **A post is never lost over a picture.**
- **Injection defence:** any link in the post that doesn't point at our own
  source is stripped, keeping the words. This is plain code, not a model being
  asked nicely.

### `collect_loop.py`
- **What:** keeps the queue stocked. Two tasks at different speeds: feeds polled
  every `POLL_MINUTES`, tweets watched continuously — breaking news shows up on X
  first, and waiting ten minutes for it would waste the point of having the feed.
- **Deliberately does nothing else.** No AI, no writing, no sending. Its only job
  is turning "this exists out there" into "a row in our database", which is why a
  crash here can never produce a half-posted message.

### `publish_loop.py`
- **What:** drives steps 0-5 every 120 seconds.
- **Step 0 is not optional:** anything queued longer than 90 minutes is dropped.
  The posting limits mean intake exceeds output at busy times, so without expiry
  the queue grows forever and the channel ends up posting this morning's news at
  midnight. (A sibling project has 2,540 items stuck exactly like that.) Since
  the queue is ordered by importance, under pressure the best and freshest wins
  and the rest quietly dies — which is correct for news.

---

## Everyday use

```bash
python3 main.py stats               # how is it doing?
python3 tools/stats.py --declines   # what did the editor reject, and was it right?
python3 tools/stats.py --dropped    # what real news did the gate refuse to run?
python3 tools/check_sources.py      # are all the feeds still alive?
python3 tools/check_tweets.py       # watch the X stream live
tail -f logs/news-channel.log       # what is it doing right now?
```

### Adding a feed
Edit `SOURCES` in `config.py`, then `python3 tools/check_sources.py` to confirm
it works, then `sudo systemctl restart news-channel`.

### Removing a feed or an X account
Delete it from `SOURCES` or `X_ACCOUNTS`, then restart. The restart disables the
feed **and throws away everything already collected from it**.

That second half matters. Disabling a source only stops it being *polled* —
articles already sitting in the queue publish quite happily. A BBC article
reached the channel hours after that feed had been deleted, for exactly this
reason. `sync_sources()` now sweeps the queue on every startup, so removal takes
effect at once rather than eventually.

### Adding an X account
Two places, because the relay is shared with the trading bot:
1. `/home/nikita/trading/infra/tweet-relay/accounts.txt` — decides what X sends
   to the relay at all. Then `sudo systemctl restart tweet-relay`. This briefly
   interrupts the trading bot's tweet feed too; it reconnects by itself.
2. `X_ACCOUNTS` in `config.py` — decides which of those *this* channel wants.

### Changing the writing style
`PROMPT` at the top of `nodes/writer.py`. Rehearse with `tools/dry_run.py` before
restarting the service.

---

## Settings worth knowing about

| Setting | Default | What it does |
|---|---|---|
| `MIN_IMPORTANCE` | 4 | Only stories scored this high get published. **The main control on volume and triviality.** |
| `BRIEF_SOURCE_CHARS` | 400 | Sources shorter than this get the short-post treatment. X posts under it also get the ⚡. |
| `ARTICLE_MAX_AGE_HOURS` | 24 | Articles older than this are ignored — guards against an abandoned feed serving old content as news. |
| `X_MAX_AGE_MINUTES` | 45 | Same idea for tweets, and what stops a restart flooding the channel. |
| `MAX_POSTS_PER_HOUR` | 12 | Start at 4 while tuning, raise once happy. |
| `COSINE_SHORTLIST` | 0.72 | How alike two stories must look to be *considered* a repeat. This no longer decides anything — it only picks who gets read by the judge. Lower it if real duplicates are slipping past unexamined. |
| `COSINE_CERTAIN` | 0.95 | Above this, merge without paying for a judgement. Rarely fires (0 times in a 188-item day) — it only catches near-verbatim reposts. |
| `DUPLICATE_MAX_GAP_HOURS` | 12 | Two items further apart than this are never compared. **The cheapest accuracy setting in the project** — it is what stops yesterday's daily report absorbing today's. |
| `DEDUP_TOP_K` | 3 | How many shortlisted candidates get considered. Was effectively 1, which meant a rejected front-runner ended the search. |
| `JUDGE_MODEL` | `google/gemini-2.5-flash-lite` | Decides "same event or not". Prefer a model that errs towards publishing. |

## The four numbers worth watching

| Number | Where | What it means if it looks wrong |
|---|---|---|
| **Rejection rate** | `main.py stats` | Over ~33%, read the reasons — the editor is more likely too strict than the news that bad |
| **Share with market `none`** | `main.py stats` | Over ~90% means your sources are general news, not market news. Under ~40% means the model is inventing reasons to care — read `--dropped` |
| **Duplicates by check** | `main.py stats` | If check 4 never fires, your sources don't overlap or the threshold is too high |
| **Expired vs published** | `main.py stats` | More expiring than publishing means you collect more than you allow yourself to post |
| **Posts per day** | the channel | Does the pace feel right as a reader? |

---

## Troubleshooting

| Problem | Likely cause |
|---|---|
| Nothing is being posted | `main.py stats` — is anything queued? Is the editor rejecting everything? |
| "Chat not found" | `CHANNEL_ID` is wrong, or the bot was never added to the channel |
| "Not enough rights" | The bot is in the channel but isn't an admin with Post Messages |
| A feed stopped working | `tools/check_sources.py`. Sites move their feeds; you get an alert after 5 straight failures |
| No tweets ever arrive | `systemctl status tweet-relay`. Also check the handle is in **both** `accounts.txt` and `X_ACCOUNTS` |
| Duplicates getting through | First check *why* with `stats.py`. If the pair never reached the judge, lower `COSINE_SHORTLIST` (0.72 → 0.68). If the judge saw it and said "different", the prompt in `nodes/judge.py` needs the case adding. If `judge_error` is climbing, the model is unreachable and everything is failing open. |
| Real stories being merged | Look at the `judge` rows in `stats.py` — each one records the reason in plain English. Fix the prompt in `nodes/judge.py` rather than the threshold; raising `COSINE_SHORTLIST` only hides the pair from the one thing that can judge it. |
| Posts sound wrong | Edit `PROMPT` in `nodes/writer.py`, rehearse with `dry_run.py` |
| Posts are real news but nobody would trade on them | `nodes/sorter.py`, not the editor. Check `stats.py --dropped --market none` to see what it *is* catching, then tighten the market definitions |
| The channel has gone quiet | `stats.py --dropped` — if good stories are in that list, the gate is too strict. Loosen the continuing-story test before touching `MIN_IMPORTANCE` |

---

## Layout

```
main.py            the orchestrator: run | collect | publish | initdb | check | stats
config.py          every setting, the feed list, the X account list  ← edit this
schema.sql         the database tables
nodes/             one file per step of the pipeline (the diagram above)
utils/             shared helpers: database, Telegram, OpenRouter, text cleaning
tools/             things you run by hand — none of them post anything
deploy/            the systemd service and log rotation
docs/              analysis and proposals, not code
data/news.db       the database
logs/              the log
```

---

## Where this is going

`docs/IMPROVING-THE-CHANNEL.md` is an analysis of what the channel offers a
reader today and what would have to change for someone to want to subscribe. It
covers the sourcing measurements behind the feed list, and a ranked set of
proposals — showing the sorter's market judgement in the post itself, hashtagging
by market rather than topic, threading developing stories onto the message that
broke them, and logging Telegram reactions so calibration stops being guesswork.

None of it is implemented. Read it before making editorial changes.
