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

  which story does this belong to?   ->  obvious_home() then place()
  has that story moved enough?       ->  should_post()

The first is a broader question than dedup asks. dedup.py rules on "the same
EVENT" and is right to be strict — two different events must not be merged into
one post. A story is wider: the strikes, Iran's answer, and oil spiking on it are
three events and one story. That is why this asks its own question rather than
reusing the dedup verdict.

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

import numpy as np

import config
from utils import embeddings, logger as log_setup, openrouter

log = log_setup.get("stories")


@dataclass
class Story:
    """One running story: the items in it, and what the reader has been told."""
    id: int
    headline: str
    centroid: np.ndarray
    item_ids: list[int] = field(default_factory=list)
    first_at: datetime | None = None
    last_item_at: datetime | None = None
    last_post_at: datetime | None = None
    posts: list[str] = field(default_factory=list)   # the published text, in order
    pending: list[dict] = field(default_factory=list)  # items since the last post
    posted_items: list[dict] = field(default_factory=list)  # everything already covered

    def eject(self, item: dict) -> None:
        """Take an item back out — the gate says the placement was wrong."""
        self.pending = [i for i in self.pending if i["id"] != item["id"]]
        self.item_ids = [i for i in self.item_ids if i != item["id"]]

    def absorb(self, item: dict, vector: np.ndarray, when: datetime) -> None:
        """Add an item, moving the centroid to the mean of everything in the story."""
        n = len(self.item_ids)
        self.centroid = (self.centroid * n + vector) / (n + 1)
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


def obvious_home(vector: np.ndarray, stories: list["Story"], now: datetime
                 ) -> tuple["Story | None", float]:
    """The free fast path: a story so close there is nothing to ask about.

    Everything else goes to place(). Measured on the 1 September wire, items
    inside ONE story scored 0.43-0.72 against each other while unrelated ones
    reached 0.79 — the ranges overlap, so no threshold can separate them. This
    only claims the top of the range, where it is safe.
    """
    best: Story | None = None
    best_score = 0.0

    for story in stories:
        if not story.is_live(now):
            continue
        score = float(np.dot(vector, story.centroid) /
                      ((np.linalg.norm(vector) * np.linalg.norm(story.centroid)) or 1.0))
        if score > best_score:
            best, best_score = story, score

    if best is not None and best_score >= config.STORY_JOIN_CERTAIN:
        return best, best_score
    return None, best_score


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

AN EVENT AND ITS EXPLANATION ARE ONE STORY. "Blasts reported across southern
Iran" and, nine minutes later, "US carrying out strikes on Iranian targets" are
not two stories — the second says what the first was. Whenever a new item names
the cause, the source, the confirmation or the scale of something already in a
story, it joins that story.

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
        # The opening headline alone misleads once a story has developed: a story
        # that began "TWO TANKERS HIT IN HORMUZ" and is now a war reads, from its
        # first line, as a story about shipping. Show what is actually in it.
        age = int((now - story.last_item_at).total_seconds() // 60) if story.last_item_at else 0
        contents = [f"    - {(i['title'] or '')[:100]}" for i in story.recent_titles(3)]
        listed.append(f"[{n}] last item {age} min ago\n" + "\n".join(contents))

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
        return None, f"could not be asked: {error}"

    choice = answer.get("story") or 0
    reason = str(answer.get("reason", ""))[:200]
    if not isinstance(choice, int) or not 1 <= choice <= len(live):
        return None, reason
    return live[choice - 1], reason


GATE_SYSTEM = """You are the editor of a news channel. A story you are already
covering has new wire items. You decide whether to post again, or stay quiet.

You are shown WHAT THE READER ALREADY KNOWS — every post the channel has
published on this story — and WHAT HAS COME IN SINCE.

Post again ONLY when the story has MOVED for the reader. It has moved when:
  - another party acted or answered (Iran retaliates after being struck)
  - a decision, ruling or announcement landed
  - a number crossed a level that changes the picture, not merely ticked further
  - the situation reached a new stage, or ended

It has NOT moved when:
  - a different outlet reports what we already told the reader
  - more detail arrives about the same development — extra place names, extra
    quotes, a fuller list of the same strikes
  - a trend we already reported simply continues by a little more
  - an analyst, market or commentator reacts to what we already said

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

A DAY-LONG SITUATION IS A HANDFUL OF POSTS, NOT A COMMENTARY. If a reader
scrolled your channel tomorrow, they should see the shape of what happened:
it began, it widened, the other side answered, here is where it stands. Five or
six posts tell that. Fifteen bury it.

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
        return {"verdict": "hold", "angle": "",
                "reason": f"runaway stop: {len(story.posts)} posts on one story"}

    known = "\n\n".join(f"[post {i + 1}]\n{p}" for i, p in enumerate(story.posts))
    fresh = "\n".join(
        f"- {i['source_name']}: {(i['title'] or '')[:180]}" for i in story.pending
    )

    try:
        answer = await asyncio.wait_for(
            openrouter.chat_json(
                model=config.STORY_MODEL, system=GATE_SYSTEM,
                user=(f"## What the reader already knows\n"
                      f"({len(story.posts)} posts on this story so far, the last "
                      f"one {quiet:.0f} minutes ago)\n\n{known}\n\n"
                      f"## What has come in since ({len(story.pending)} items)\n\n{fresh}"),
                schema=GATE_SCHEMA, schema_name="gate",
                temperature=0.0, max_tokens=400,
            ),
            timeout=config.STORY_TIMEOUT_SECONDS,
        )
    except Exception as error:  # noqa: BLE001
        # Fail open, matching dedup and the judge: a duplicate-feeling post is a
        # smaller failure than a story the channel silently sat on.
        log.warning("The story gate failed for story %s (%s) — posting", story.id, error)
        return {"verdict": "post", "angle": "", "reason": f"gate unavailable: {error}"}

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
    newest = story.pending[-1]
    parts = []
    for item in story.pending:
        head = (item["title"] or "").strip()
        body = (item["body"] or "").strip()
        parts.append(f"[{item['source_name']}] {head}\n{body}".strip())

    return {
        "id": newest["id"],
        "source_name": newest["source_name"],
        "origin": newest["origin"],
        "url": newest["url"],
        "title": newest["title"],
        "body": "\n\n".join(parts)[:4000],
        "image_url": newest.get("image_url") or "",
        "topic": newest.get("topic") or "",
        "topic_hint": newest.get("topic_hint") or "",
        "importance": max((i.get("importance") or 0) for i in story.pending),
    }


def brief_for_writer(story: Story, angle: str) -> str:
    """What the writer is told beyond the source: the reader's memory of this story."""
    parts = []

    if story.posts:
        earlier = "\n\n".join(f"— {p}" for p in story.posts[-3:])
        parts.append(
            "THIS READER IS ALREADY FOLLOWING THIS STORY. The channel has "
            f"published this on it:\n\n{earlier}\n\n"
            "Do not tell them any of that again. Write the next thing only."
        )

    if angle:
        parts.append(f"WHAT THIS POST IS FOR:\n{angle}")

    if len(story.pending) > 1:
        parts.append(
            f"The source below is {len(story.pending)} wire items about this one "
            "story, put together. Write ONE post covering what they add up to — "
            "not a list, and not one of them picked out."
        )

    return "\n\n".join(parts)


async def vector_for(item: dict) -> np.ndarray | None:
    """The item's meaning-vector, from storage if we already have it."""
    blob = item.get("embedding")
    if blob:
        return embeddings.from_blob(blob)

    from utils import textclean
    text = textclean.for_embedding(f"{item['title']}\n{(item['body'] or '')[:400]}")
    got = await embeddings.embed_one(text)
    return np.array(got, dtype=np.float32) if got else None


def parse_time(value: str) -> datetime:
    """Database timestamps are UTC without a marker; make them comparable."""
    try:
        return datetime.fromisoformat(value).replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return datetime.now(timezone.utc)
