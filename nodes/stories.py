"""Groups incoming wire items into running stories, and decides when one earns a post.

THE PROBLEM THIS REPLACES. Every station before this one judges a single item and
lets exactly one post out per item that survives. On 1 September that produced
fifteen posts in four and a half hours about one war, and seven about bond yields
in a day. Each post was correct. The sequence read like a machine, because the
thing that makes a channel read like a person — deciding NOT to post — has no
station to happen in.

So the unit of work changes. Items no longer become posts; they join a STORY, and
a story posts when it has moved. Six wires about the same strikes become one post,
and the seventh yield print becomes silence.

TWO QUESTIONS, KEPT SEPARATE:

  which story does this belong to?   ->  place()
  has that story moved enough?       ->  should_post()

The first is a broader question than dedup asks. dedup.py rules on "the same
EVENT" and is right to be strict — two different events must not be merged into
one post. A story is wider: the strikes, Iran's answer, and oil spiking on it are
three events and one story. That is why this asks its own question rather than
reusing the dedup verdict.

A story remembers itself as a SUMMARY IN WORDS, rewritten from each post as it
goes out, not as an averaged vector. Measured on the 1 September wire, items
inside one story scored 0.43-0.72 against each other while unrelated ones
reached 0.79 — the ranges overlap, so no arithmetic can separate them and a
centroid was only ever dead weight. A sentence can also be read by a person.

COST. Placing costs one call per item — the same as the node this replaces —
and shows the model every open story at once rather than asking about each in
turn. should_post() then runs at most once per story per gap window rather than
once per item, so a busy story gets CHEAPER as it gets busier. That is the
opposite of today, where the fifteenth wire about one war costs the same as the
first.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

import config
from brain import persona_loader
from utils import db, logger as log_setup, openrouter, textclean

log = log_setup.get("stories")

# Both model calls here fail OPEN — placement into a new story, the gate into
# posting. That is the right default, and it is also why a dead STORY_MODEL is
# invisible: the channel quietly reverts to one post per item and nothing looks
# broken. Same reasoning, and same alert, as dedup's meaning check.
_consecutive_failures = 0
FAILURE_ALERT_AFTER = 10


def _record_failure(what: str, story_or_item: str) -> None:
    """Count a fail-open, and shout once they start piling up."""
    global _consecutive_failures
    _consecutive_failures += 1
    db.bump_counter(f"story_{what}_failed")

    if _consecutive_failures == FAILURE_ALERT_AFTER:
        from utils import telegram_error
        telegram_error.send_error(
            f"The story layer has failed {_consecutive_failures} times in a row "
            f"(latest: {what} on {story_or_item}). It fails open, so the channel "
            f"is still posting — but it is now posting one item per post again, "
            f"with no grouping and no silence. That is exactly what this layer "
            f"exists to prevent, and nothing else will look wrong.\n\n"
            f"Check STORY_MODEL and OpenRouter.",
            node_name="stories",
        )


def _record_success() -> None:
    """Reset the run after a call that worked."""
    global _consecutive_failures
    if _consecutive_failures:
        log.info("The story layer is working again after %d failure(s)",
                 _consecutive_failures)
        _consecutive_failures = 0


def _span(then: datetime | None, now: datetime) -> str:
    """A duration in words. Ages are shown to the model, so they read like prose."""
    if then is None:
        return "unknown"
    minutes = int((now - then).total_seconds() // 60)
    if minutes < 60:
        return f"{max(minutes, 0)} min"
    if minutes < 60 * 24:
        return f"{minutes // 60}h {minutes % 60:02d}m"
    return f"{minutes // (60 * 24)}d"


@dataclass
class Story:
    """One running story: the items in it, and what the reader has been told."""
    id: int
    headline: str                                    # the first item's title, fixed
    summary: str = ""                                # what the story IS now, from the last post
    item_ids: list[int] = field(default_factory=list)
    first_at: datetime | None = None
    last_item_at: datetime | None = None
    last_post_at: datetime | None = None
    posts: list[str] = field(default_factory=list)   # the published text, in order
    first_message_id: int | None = None              # what later posts reply to
    pending: list[dict] = field(default_factory=list)  # items since the last post
    posted_items: list[dict] = field(default_factory=list)  # everything already covered

    def eject(self, item: dict) -> None:
        """Take an item back out — the gate says the placement was wrong."""
        self.pending = [i for i in self.pending if i["id"] != item["id"]]
        self.item_ids = [i for i in self.item_ids if i != item["id"]]

    def absorb(self, item: dict, when: datetime) -> None:
        """Add an item to the story, in memory."""
        self.item_ids.append(item["id"])
        self.pending.append(item)
        self.last_item_at = when
        if self.first_at is None:
            self.first_at = when

    def recent_titles(self, n: int) -> list[dict]:
        """The last few items in the story, posted or not — what it is ABOUT now."""
        return (self.posted_items + self.pending)[-n:]

    def is_live(self, now: datetime) -> bool:
        """A story nobody has added to in a while is over, and cannot adopt new items."""
        if self.last_item_at is None:
            return False
        return (now - self.last_item_at) < timedelta(hours=config.STORY_IDLE_HOURS)


PLACE_SYSTEM = """You place a new wire item into the story it belongs to.

A STORY is a situation the channel is following, not a single event. Several
different events belong to one story when a reader would see them as the same
thing developing:

  - US strikes on Iran, Iran's retaliation, and further strikes  -> ONE story.
  - Oil jumping BECAUSE of those strikes                         -> that story too.
  - Explosions reported in four Iranian towns during the strikes -> that story.
  - Bitcoin falling BECAUSE of the fighting                      -> that story.

They are separate stories when a reader would have to change subject:

  - US-Iran fighting  vs  a Fed official speaking about yields   -> TWO stories.
  - Gold falling      vs  a court ruling on a crypto exchange    -> TWO stories.
  - Two countries' bond yields rising the same day with no
    stated link between them                                     -> TWO stories.

Being about the same MARKET is not enough. Being about the same COUNTRY is not
enough. There has to be one situation a reader is following.

BUT ONE SQUEEZE IS ONE STORY, wherever its pieces come from. When a market is
under one pressure and several places feed it on the same day, those are one
situation:

  - "Saudi Arabia cancels crude cargoes to Europe" and, two minutes later,
    "Libya halts output at two oil fields", and an hour on, "loadings suspended
    at Yanbu"  -> ONE story: oil supply is being squeezed. A reader following
    oil is following all three at once.
  - "US 10-year through 5%" and "30-year at a 22-year high" the same afternoon
    -> ONE story: Treasuries selling off.

The test for these: would a trader watching that market call it one move? If
the first post on the story already NAMED the new place — "compounded by
outages in Libya" — the new item about that place belongs to it, always.

AN EVENT AND ITS EXPLANATION ARE ONE STORY. "Blasts reported across southern
Iran" and, nine minutes later, "US carrying out strikes on Iranian targets" are
not two stories — the second says what the first was. Whenever a new item names
the cause, the source, the confirmation or the scale of something already in a
story, it joins that story.

AGE IS EVIDENCE, NOT A RULE. Each story below shows how long it has been running
and how long since anything was added to it. A story nobody has touched for six
hours is an unlikely home for something breaking now, even when the words match.
A story with an item four minutes ago is where a fast-moving situation belongs.
Weigh it against the reading; do not let it decide on its own.

Answer with the number of the story it joins, or 0 if it starts a new one. Judge
it on what the story CONTAINS, listed below, not on the first line of it — a
story that opened with two tankers hit in Hormuz may since have become a war."""

PLACE_SCHEMA = {
    "type": "object",
    "properties": {
        "story": {"type": "integer", "description": "Story number, or 0 for a new story."},
        "reason": {"type": "string", "description": "One short sentence."},
    },
    "required": ["story", "reason"],
    "additionalProperties": False,
}


async def place(item: dict, stories: list["Story"], now: datetime
                ) -> tuple["Story | None", str]:
    """Ask which open story an item joins, showing all of them at once.

    ONE call per item, not one per candidate. The model is shown every live
    story and picks; that is the question we actually want answered, and it is
    the question a distance between two short headlines cannot answer.

    Fails open into a NEW story: a wrongly separated item is one extra post,
    which is what the channel does today anyway.
    """
    live = [s for s in stories if s.is_live(now)]
    if not live:
        return None, "no open stories"

    listed = []
    for n, story in enumerate(live, 1):
        # Both halves matter. The summary is what the channel has SAID, which is
        # what a reader would recognise; the unposted items are the freshest
        # evidence of where the story is going and are not in the summary yet.
        # Showing only the opening headline misleads once a story has developed.
        block = [f"[{n}] running {_span(story.first_at, now)}, "
                 f"last item {_span(story.last_item_at, now)} ago"]
        if story.summary:
            block.append(f"    so far: {story.summary[:240]}")
        fresh = [i for i in story.pending]
        if fresh:
            block.append("    just in, not yet posted:")
            block += [f"      - {(i['title'] or '')[:100]}" for i in fresh[-3:]]
        elif story.posted_items:
            block += [f"      - {(i['title'] or '')[:100]}"
                      for i in story.posted_items[-2:]]
        listed.append("\n".join(block))

    user = (
        "## Open stories\n\n" + "\n\n".join(listed) +
        f"\n\n## The new item\n{(item['title'] or '')[:200]}\n"
        f"{(item['body'] or '')[:600]}"
    )

    try:
        answer = await asyncio.wait_for(
            openrouter.chat_json(
                model=config.STORY_MODEL, system=PLACE_SYSTEM, user=user,
                schema=PLACE_SCHEMA, schema_name="place",
                temperature=0.0, max_tokens=200,
            ),
            timeout=config.STORY_TIMEOUT_SECONDS,
        )
    except Exception as error:  # noqa: BLE001
        log.warning("Could not place item %s (%s) — starting its own story",
                    item["id"], error)
        _record_failure("place", f"item {item['id']}")
        return None, f"could not be asked: {error}"

    _record_success()
    choice = answer.get("story") or 0
    reason = str(answer.get("reason", ""))[:200]
    if not isinstance(choice, int) or not 1 <= choice <= len(live):
        return None, reason
    return live[choice - 1], reason


GATE_SYSTEM = """You are the editor of a news channel. A story you are already
covering has new wire items. You decide whether to post again, or stay quiet.

You are shown WHAT THE READER ALREADY KNOWS — every post the channel has
published on this story — and WHAT HAS COME IN SINCE.

Post again ONLY when the story has CHANGED STATE for the reader. A situation has
a small number of states, and only a move between them is worth a post:

  a war:        not started → fighting → ceasefire → fighting again → widened → over
  a dispute:    talks → tariffs imposed → deal
  a case:       filed → ruled → appealed
  a price run:  below a landmark → through it (once)

It has changed state when:
  - a ceasefire, a truce, a deal, a ruling, a resumption — the situation is now
    in a different state than the last post described
  - a NEW party or a NEW front enters (Saudi Arabia joins; a second country's
    ships are hit; a second regulator opens a case)
  - a price crosses a landmark the reader will remember — a record, a multi-year
    extreme, a major round number — for the FIRST time in this story. ONCE, and
    then never again for that run. A rise that is already under way sets a fresh
    record most days, so "a record" on its own does not qualify: if you have
    already told the reader this is the highest since 2004, the next highest
    since 2004 is the same run, whatever the new figure says

It has NOT changed state when:
  - another incident happens inside the same state: another strike, another
    tanker, another drone, another explosion, more casualties, more damage.
    The war was on before; it is on now. That is the war continuing.
  - another piece of the same squeeze: supply was disrupted before, and now
    one more port, field, pipeline or cargo is disrupted. "Loadings suspended
    at Yanbu" after "Saudi cancels cargoes to Europe" is the squeeze
    continuing. Hold it; the roundup will carry it.
  - a different outlet reports what we already told the reader — INCLUDING a
    longer, better-written article that adds a detail or two ("the move
    follows a similar one by the SEC"). An action was taken; a second outlet
    describing it more fully is the same action. One detail is not a state.
  - more detail arrives about the same development — extra place names, extra
    quotes, a fuller list of the same strikes
  - a number ticks further along a trend already reported ("highest in 112
    days" after we said it was rising; $104 after we said $100). When this and
    the landmark rule above both seem to apply, THIS ONE WINS — a new extreme
    inside a run you have already described is the run continuing
  - the same move turns up on a related instrument. Rising US yields are one
    story across the 2-, 10- and 30-year: one curve, not three landmarks.
    Reporting that the 30-year did what you already said the 10-year did is one
    move told twice
  - an analyst, market or commentator reacts to what we already said
  - one side threatens, warns, or says it "will respond" — words, not a state

The test: name the state before and the state after. If you cannot name two
DIFFERENT states, hold.

THE TEST. Read the posts already published, then read what has come in. If a
reader who saw those posts would learn nothing they could act on or retell, do
not post. A quiet channel is not a broken channel. Repeating yourself is worse
than saying nothing, because it teaches the reader to stop reading.

PROPORTION. You are told how many posts this story has already had and how long
ago the last one was. Both raise the bar and neither is a rule. A story on its
fifth post in an hour needs a real turn to earn a sixth; a story that has been
quiet for hours needs less. But a war widening, a country entering it, a
decision landing — those are posted the moment they happen, whatever the count
says. A number must never be the reason the biggest thing of the day went
unreported.

A RUNNING SITUATION IS A FEW POSTS OVER ITS WHOLE LIFE, NOT A COMMENTARY. A
reader who scrolls back should see its states: it began, it widened, there was
a truce, the truce broke, it ended. Three to five posts over days or weeks tell
that. Fifteen in an afternoon bury it, and a reader who already knows the war
is on learns nothing from the next explosion.

Being newsworthy in general is not the question. Everything here is newsworthy
or it would not have reached you. The question is whether it is new TO THIS
READER, who has already read the posts above.

## Your three answers
"post"            — the story moved, tell the reader.
"hold"            — it belongs here, but the reader would learn nothing. Stay quiet.
"not_this_story"  — this does not belong in this story at all. Say so; it will be
                    taken out and handled on its own. Use this whenever the new
                    items are about a different situation, however much they share
                    a market, a country or a commodity with this one. NEVER answer
                    "hold" for something that simply does not belong here — that
                    buries a real story instead of telling it.

## angle
When you post, write one or two sentences telling the writer what this post is
FOR: what is new, and what the reader already has and must not be told again.
Be specific — name the fact, not the category."""

ROUNDUP_ANGLE = (
    "This is a ROUNDUP: several smaller developments on a story the reader is "
    "already following, none of which earned its own post. Title it plainly as "
    "an update — name the story and say 'update' or 'latest', no drama. Then "
    "a list: one ▪️ bullet per development, one line each, in the order they "
    "happened. Do not inflate any of them, and do not add a conclusion; the "
    "reader can draw one."
)

GATE_SCHEMA = {
    "type": "object",
    "properties": {
        "verdict": {"type": "string", "enum": ["post", "hold", "not_this_story"]},
        "angle": {"type": "string"},
        "reason": {"type": "string", "description": "One short sentence."},
    },
    "required": ["verdict", "angle", "reason"],
    "additionalProperties": False,
}


# Above this, what is arriving reads almost exactly like something already
# posted on this story. Measured on every follow-up post the channel has made:
# genuine repeats scored 0.80-0.89 and everything else 0.39-0.77, with nothing
# in between. It is deliberately NOT a rule — story 54 scored 0.80 on "Bitcoin
# falls below $77,000" right after "Bitcoin crosses $79,000", and that is a
# reversal, not a repeat. Embeddings cannot tell up from down. So the number is
# handed to the gate as evidence and the gate still decides, the same division
# of labour dedup already uses.
ECHO_FLOOR = 0.79


async def _closest_published(story: Story) -> str:
    """The post this story already made that the incoming items most resemble.

    Returns a block for the gate's prompt, or "" when nothing is close or the
    embeddings are unavailable — in which case the gate judges as it did before.
    """
    if not story.posts or not story.pending:
        return ""
    try:
        from utils import embeddings
        incoming = " ".join(f"{i['title'] or ''} {(i['body'] or '')[:300]}"
                            for i in story.pending[-3:])
        vector = await embeddings.embed_one(textclean.for_embedding(incoming))
        if vector is None:
            return ""
        # Only the recent ones: a story can run to twelve posts, and echoing
        # something said that long ago is both unlikely and cheap to forgive.
        recent = list(enumerate(story.posts))[-6:]
        scored = []
        for index, post in recent:
            other = await embeddings.embed_one(textclean.for_embedding(post))
            if other is not None:
                scored.append((embeddings.cosine(vector, other), index, post))
        if not scored:
            return ""
        score, index, post = max(scored)
    except Exception as error:  # noqa: BLE001 - evidence, never the decision
        log.debug("Could not measure the echo for story %s: %s", story.id, error)
        return ""

    if score < ECHO_FLOOR:
        return ""
    return (f"\n\n## Careful — this sounds like something you already said\n"
            f"What has just come in resembles [post {index + 1}] very closely "
            f"({score:.2f} out of 1.00):\n\n{post}\n\n"
            f"That score cannot tell a repeat from a reversal, so read both. If "
            f"the new item only restates that post with a different figure, hold "
            f"it. If it turns the story around or adds a state that post did not "
            f"describe, post it and say which.")


async def should_post(story: Story, now: datetime) -> dict:
    """Decide whether a story's pending items are worth a post. Fails open to posting.

    The first post of a story never reaches the model — a story nobody has heard
    of has, by definition, moved. That keeps the channel as fast as it is today
    on the thing that matters most: breaking a story.
    """
    if not story.pending:
        return {"verdict": "hold", "angle": "", "reason": "nothing new"}

    if not story.posts:
        return {"verdict": "post", "angle": "", "reason": "first post of this story"}

    quiet = ((now - story.last_post_at).total_seconds() / 60
             if story.last_post_at else 999.0)

    # A floor, not a gate. It exists to stop two wires arriving seconds apart
    # becoming two posts — nothing more. The 25-minute version of this rule
    # silenced "IRAN ANNOUNCES RETALIATORY OPERATION", the largest development
    # of 1 September, because a timer was allowed to outrank the news. How long
    # it has been is now something the editor is TOLD, not something that
    # decides for it.
    if quiet < config.STORY_MIN_GAP_MINUTES:
        return {"verdict": "hold", "angle": "",
                "reason": f"only {quiet:.0f} min since this story last posted"}

    # An emergency stop, not an editorial rule. The 6-post version of this line
    # silenced Iran announcing its retaliation, seven wires into a war — the
    # same mistake as the timer, made by a counter instead. How many posts a
    # story has had is something the editor is told below; only a runaway is
    # stopped here.
    if len(story.posts) >= config.STORY_MAX_POSTS:
        log.warning("Story %s hit the runaway stop at %d posts — it is being "
                    "silenced from here on", story.id, len(story.posts))
        return {"verdict": "hold", "angle": "",
                "reason": f"runaway stop: {len(story.posts)} posts on one story"}

    # The roundup: enough has piled up for long enough. Released without asking
    # the model, deliberately — this exists for the case where the story never
    # gives the model a reason to say yes again.
    if (len(story.pending) >= config.STORY_DIGEST_ITEMS
            and quiet >= config.STORY_DIGEST_MINUTES):
        return {"verdict": "post", "angle": ROUNDUP_ANGLE,
                "reason": f"roundup: {len(story.pending)} items waiting, "
                          f"{quiet:.0f} min since the last post"}

    known = "\n\n".join(f"[post {i + 1}]\n{p}" for i, p in enumerate(story.posts))
    echo = await _closest_published(story)
    pending = story.pending[-config.STORY_MAX_PENDING:]
    fresh = "\n".join(
        f"- {i['source_name']}: {(i['title'] or '')[:180]}" for i in pending
    )

    try:
        answer = await asyncio.wait_for(
            openrouter.chat_json(
                model=config.STORY_MODEL, system=GATE_SYSTEM,
                user=(f"## What the reader already knows\n"
                      f"({len(story.posts)} posts on this story so far, the last "
                      f"one {quiet:.0f} minutes ago)\n\n{known}\n\n"
                      f"## What has come in since ({len(pending)} items)\n\n{fresh}"
                      f"{echo}"),
                schema=GATE_SCHEMA, schema_name="gate",
                temperature=0.0, max_tokens=400,
            ),
            timeout=config.STORY_TIMEOUT_SECONDS,
        )
    except Exception as error:  # noqa: BLE001
        # Fail open, matching dedup and the judge: a duplicate-feeling post is a
        # smaller failure than a story the channel silently sat on.
        log.warning("The story gate failed for story %s (%s) — posting", story.id, error)
        _record_failure("gate", f"story {story.id}")
        return {"verdict": "post", "angle": "", "reason": f"gate unavailable: {error}"}

    _record_success()
    verdict = str(answer.get("verdict", "post"))
    if verdict not in {"post", "hold", "not_this_story"}:
        verdict = "post"

    return {
        "verdict": verdict,
        "angle": str(answer.get("angle", ""))[:600],
        "reason": str(answer.get("reason", ""))[:300],
    }


def as_source(story: Story) -> dict:
    """Fold a story's pending items into one source text for the writer.

    The writer's rules are unchanged and still bind: every fact in the post must
    appear in the text it is given. This widens what it was given from one wire
    item to all of them, which is the whole point.
    """
    pending = story.pending[-config.STORY_MAX_PENDING:]
    newest = pending[-1]
    parts = []
    for item in pending:
        head = (item["title"] or "").strip()
        # The article we read beats the wire stub it came from: that is the
        # whole point of reading it. See nodes/article.py.
        keys = item.keys() if hasattr(item, "keys") else ()
        body = ((item["article_text"] if "article_text" in keys else "") or
                (item["body"] or "")).strip()
        parts.append(f"[{item['source_name']}] {head}\n{body}".strip())

    return {
        "id": newest["id"],
        "source_name": newest["source_name"],
        "origin": newest["origin"],
        "url": newest["url"],
        "title": newest["title"],
        "body": "\n\n".join(parts)[:4000],
        "image_url": newest.get("image_url") or "",
        "video_url": newest.get("video_url") or "",
        "video_kind": newest.get("video_kind") or "",
        "calendar_title": newest.get("calendar_title") or "",
        "calendar_forecast": newest.get("calendar_forecast") or "",
        "calendar_previous": newest.get("calendar_previous") or "",
        "topic": newest.get("topic") or "",
        "topic_hint": newest.get("topic_hint") or "",
        "importance": max((i.get("importance") or 0) for i in pending),
    }


def brief_for_writer(story: Story, angle: str, *, single_item: bool = False) -> str:
    """What the writer is told beyond the source: the reader's memory of this story."""
    parts = []

    if story.posts:
        earlier = "\n\n".join(f"— {p}" for p in story.posts[-3:])
        parts.append(
            "THIS READER IS ALREADY FOLLOWING THIS STORY. The channel has "
            f"published this on it:\n\n{earlier}\n\n"
            "Do not tell them any of that again. The HEADLINE of this post is "
            "what is new since then — not the original action restated. If the "
            "new thing is that the SEC did the same earlier, the headline says "
            "that; it does not announce the CFTC's move a second time."
        )

    if angle:
        parts.append(f"WHAT THIS POST IS FOR:\n{angle}")

    # A forced post is written from one item on its own, so the source is not
    # the story folded together and must not be described as if it were.
    if len(story.pending) > 1 and not single_item:
        parts.append(
            f"The source below is {len(story.pending)} wire items about this one "
            "story, put together. Write ONE post covering what they add up to — "
            "not a list, and not one of them picked out."
        )

    return "\n\n".join(parts)


# --- Hydration: a Story is a snapshot of the database, never memory ---

def _hydrate(row, now: datetime) -> Story:
    """Build one Story from its row plus the items and posts that point at it."""
    story = Story(
        id=row["id"],
        headline=row["headline"],
        summary=row["summary"],
        first_at=parse_time(row["first_at"]),
        last_item_at=parse_time(row["last_item_at"]),
        last_post_at=parse_time(row["last_post_at"]) if row["last_post_at"] else None,
    )

    for item in db.get_story_items(story.id):
        story.item_ids.append(item["id"])
        if item["status"] in ("queued", "held"):
            story.pending.append(dict(item))
        else:
            story.posted_items.append(dict(item))

    # visible_text, not raw post_html: the tags and the source byline would both
    # waste tokens and teach the model to put markup in its answers. The replay
    # tool strips the same way, which is what keeps the backtest honest.
    sent = db.get_story_posts(story.id)
    story.posts = [persona_loader.visible_text(p["post_html"]) for p in sent]
    # A story is a thread: every later post replies to the one that opened it,
    # so the reader sees "CFTC permits…" quoted above "CFTC cancels…".
    if sent:
        story.first_message_id = sent[0]["telegram_message_id"]
    return story


def load_open(now: datetime, limit: int | None = None) -> list[Story]:
    """Every story a new item could still join, most recently active first.

    The list is CAPPED before it reaches place(), which answers with an index
    into it. A long list makes the prompt long and the numbering easy to get
    wrong, and the oldest candidates are the least likely answers anyway.
    """
    rows = db.load_live_stories(config.STORY_IDLE_HOURS,
                                limit or config.STORY_MAX_OPEN)
    return [_hydrate(row, now) for row in rows]


def load_one(story_id: int, now: datetime) -> Story | None:
    """Re-read one story. Used after attaching an item, so the gate and the
    writer see exactly the rows the database holds rather than a patched copy."""
    row = db.get_story(story_id)
    return _hydrate(row, now) if row is not None else None


def parse_time(value: str) -> datetime:
    """Database timestamps are UTC without a marker; make them comparable."""
    try:
        return datetime.fromisoformat(value).replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return datetime.now(timezone.utc)
