"""Finishes an approved post and sends it to the channel.

Adds one thing: the source credit at the end. The mark at the front belongs to
the writer (config.POST_MARKS).

The pacing limits are set for readers, not for Telegram — a channel that posts
eleven times in five minutes gets muted.

A post with an image goes out as ONE message, the photo captioned, never a
photo followed by a wall of text. Telegram caps captions at 1024 characters
against 4096, so if shortening would leave the text trailing off we drop the
picture and send the full text instead.
"""

from __future__ import annotations

import asyncio
import re

import config
from utils import db, logger as log_setup, telegram_client, telegram_html

log = log_setup.get("publisher")

# A tweet can contain anything, including a referral link aimed at our readers,
# so links pointing anywhere but the cited story are removed.
_LINK_TAG = re.compile(r'<a\s+href="([^"]*)"[^>]*>(.*?)</a>', re.IGNORECASE | re.DOTALL)


def _strip_foreign_links(html: str, allowed_url: str) -> str:
    """Remove links that did not come from us, keeping their visible text.

    The prompt and the editor also guard against this; only this layer does not
    depend on a model behaving.
    """
    if not html:
        return html

    allowed_host = ""
    match = re.match(r"https?://([^/]+)", allowed_url or "")
    if match:
        allowed_host = match.group(1).lower()

    def replace(link) -> str:
        href, text = link.group(1), link.group(2)
        host_match = re.match(r"https?://([^/]+)", href)
        host = host_match.group(1).lower() if host_match else ""
        if host and allowed_host and host == allowed_host:
            return link.group(0)          # our own source — keep it
        log.info("Removed a link the model added by itself: %s", href[:80])
        return text                        # keep the words, drop the link

    return _LINK_TAG.sub(replace, html)


def compose(post_html: str, url: str, source_name: str) -> str:
    """Attach the source credit. The mark at the front belongs to the writer."""
    body = _strip_foreign_links(post_html.strip(), url)
    parts = [body]

    # The stored name is a machine name ("crypto_banter"); show the human one.
    display = config.SOURCE_DISPLAY_NAMES.get(source_name, source_name)
    if url:
        parts.append(f'\n<a href="{url}">— {display}</a>')
    else:
        parts.append(f"\n— {display}")

    return "\n".join(parts)


def check_limits() -> tuple[bool, str]:
    """Returns (allowed, why not). A minimum gap, an hourly cap, a daily cap."""
    gap = db.seconds_since_last_post()
    if gap < config.MIN_SECONDS_BETWEEN_POSTS:
        return False, (f"only {gap:.0f}s since the last post "
                       f"(minimum {config.MIN_SECONDS_BETWEEN_POSTS}s)")

    this_hour = db.posts_sent_since(60)
    if this_hour >= config.MAX_POSTS_PER_HOUR:
        return False, f"already posted {this_hour} times this hour (limit {config.MAX_POSTS_PER_HOUR})"

    today = db.posts_sent_since(60 * 24)
    if today >= config.MAX_POSTS_PER_DAY:
        return False, f"already posted {today} times today (limit {config.MAX_POSTS_PER_DAY})"

    return True, ""


async def execute(item, post_html: str, post_id: int,
                  reply_to_message_id: int | None = None) -> bool:
    """Send one approved post. True if it arrived.

    `reply_to_message_id` threads the post under an earlier one, for
    continuations. Failures are counted on the post row and eventually give up.
    """
    topic = item["topic"] or item["topic_hint"] or "crypto"
    message = compose(post_html, item["url"] or "", item["source_name"])
    image_url = item["image_url"] or ""

    try:
        message_id = None

        if image_url:
            if telegram_html.would_cut_mid_sentence(message, telegram_html.CAPTION_LIMIT):
                # A complete post without a picture beats a truncated one with.
                log.info("Post %s is too long to caption a photo — sending as text instead",
                         post_id)
                db.bump_counter("image_dropped_too_long")
            else:
                try:
                    message_id = await telegram_client.send_photo(
                        image_url, message,
                        reply_to_message_id=reply_to_message_id,
                    )
                except Exception as error:  # noqa: BLE001
                    # Telegram fetches images itself and sometimes cannot.
                    # Never lose a post over a picture.
                    log.warning("Could not send the image for post %s (%s) — "
                                "sending as text instead", post_id, error)
                    db.bump_counter("image_dropped_failed")

        if message_id is None:
            message_id = await telegram_client.send_text(
                message,
                reply_to_message_id=reply_to_message_id,
            )

        db.mark_post_sent(post_id, message_id, telegram_client.build_post_url(message_id))
        db.set_item_status(item["id"], "published", f"sent as message {message_id}")
        db.bump_counter("published")

        log.info("Published [%s] %s", topic, (item["title"] or "")[:70])
        return True

    except Exception as error:  # noqa: BLE001
        attempts = db.bump_send_attempts(post_id)
        log.warning("Could not send post %s (attempt %d of %d): %s",
                    post_id, attempts, config.MAX_ATTEMPTS, error)

        if attempts >= config.MAX_ATTEMPTS:
            db.set_post_status(post_id, "send_failed")
            db.set_item_status(item["id"], "failed", f"could not send: {error}")
            db.bump_counter("send_failed")

            from utils import telegram_error
            telegram_error.send_error(
                f"Gave up sending a post after {attempts} attempts: {error}",
                node_name="publisher",
            )
        else:
            # Back into the queue, or it sits at 'written' forever and a single
            # failed send quietly loses a finished post. The post row survives,
            # so the retry does not pay to write it again.
            db.set_item_status(item["id"], "queued", "send failed, will retry")
        return False


async def pause_between_sends() -> None:
    """Wait a moment between messages, so a burst never looks like a burst."""
    await asyncio.sleep(config.SECONDS_BETWEEN_SENDS)
