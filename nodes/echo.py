"""The last question before a post is sent: have we already told the reader this?

Every other guard in the pipeline asks a narrower version of it. Dedup compares
the INCOMING WIRE ITEMS, so it never sees the post that gets written from them.
The gatekeeper compares against ONE STORY's posts, and only on the path where
it reasons — a story's first post and a roundup both skip that reasoning by
design. Three real repeats walked through those gaps in four days: Apple's $5
trillion cap came back as a brand-new story after the old one had closed, the
Treasury roundup fired twice on a count and a timer, and a Japanese yield was
reported twice from different stories without anyone noticing.

This one stands at the exit, so nothing routes around it — not a new story, not
a roundup, not a forced post. And it compares THE FINISHED POST against every
post the channel recently published, which is the only comparison that matches
what the reader actually experiences.

WHY IT CANNOT BE A THRESHOLD. Measured on 60 consecutive posts: real repeats
scored 0.741 to 0.924 and legitimate posts scored 0.734 and 0.776, interleaved.
"US House passes crypto tax bill" reads 0.776 against "US House unveils new
crypto tax legislation" and is a genuine step forward; Apple's repeat reads
0.741 and is not. No cutoff separates them, so the number only draws up a
shortlist and the judge that dedup already uses reads both posts and rules.

IT FAILS OPEN. No embeddings, no judge, no answer — the post goes out. A
duplicate is an embarrassment; a channel that stops publishing is worse.
"""

from __future__ import annotations

import config
from brain import persona_loader
from utils import db, embeddings, logger as log_setup, textclean

log = log_setup.get("echo")


async def repeats_something_published(post_html: str) -> tuple[bool, str]:
    """(True, why) when this post tells the reader what a recent post already did."""
    text = persona_loader.visible_text(post_html).strip()
    if not text:
        return False, ""

    recent = db.recent_published_posts(config.ECHO_WINDOW_HOURS, config.ECHO_MAX_COMPARED)
    if not recent:
        return False, ""

    try:
        vector = await embeddings.embed_one(textclean.for_embedding(text))
        if vector is None:
            return False, ""

        best_score, best_text, best_when = 0.0, "", ""
        for row in recent:
            other_text = persona_loader.visible_text(row["post_html"]).strip()
            other = await embeddings.embed_one(textclean.for_embedding(other_text))
            if other is None:
                continue
            score = embeddings.cosine(vector, other)
            if score > best_score:
                best_score, best_text, best_when = score, other_text, row["sent_at"]
    except Exception as error:  # noqa: BLE001
        log.warning("Could not compare against recent posts (%s) — sending", error)
        return False, ""

    if best_score < config.ECHO_SHORTLIST:
        return False, ""

    # Deliberately generous: the shortlist is wide and the judge is the filter.
    # The judge reads a pair of wire items, so the two posts are handed to it in
    # that shape. The timestamps matter to it: its hardest pairs are recurring
    # reports that differ only by date.
    from nodes import judge
    verdict, reason = await judge.execute_three_way(
        {"title": text.splitlines()[0][:200], "body": text,
         "source_name": "this channel", "fetched_at": "now"},
        {"title": best_text.splitlines()[0][:200], "body": best_text,
         "source_name": "this channel", "fetched_at": best_when},
    )

    if verdict is None:
        log.warning("Judge unavailable on a %.3f match — sending anyway", best_score)
        return False, ""

    if verdict == "same_event":
        log.info("Held: this post repeats one from the last %dh (%.3f) — %s",
                 config.ECHO_WINDOW_HOURS, best_score, reason[:120])
        db.bump_counter("echo_held")
        return True, f"repeats a post from the last {config.ECHO_WINDOW_HOURS}h: {reason[:200]}"

    log.info("Looked alike (%.3f) but judged %s — sending", best_score, verdict)
    return False, ""
