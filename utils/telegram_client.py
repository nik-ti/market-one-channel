# --- Sending messages to the Telegram channel ---
#
# WHAT THIS FILE DOES
#   Wraps the Telegram bot library so the rest of the project can just say
#   "send this post" without worrying about formatting rules, rate limits, or
#   what to do when Telegram says no.
#
# WHERE IT FITS
#   Used only by nodes/publisher.py. Error alerts go through a different file
#   (utils/telegram_error.py) so that a broken channel can still tell you it is
#   broken.
#
# THE RULE THAT SHAPES THIS FILE
#   A post is never lost to a formatting problem. If Telegram rejects our
#   carefully formatted message, we strip the formatting and send it as plain
#   text. A plain post is worth far more than no post.
#
# DEPENDENCIES
#   python-telegram-bot (the workspace rule: always this library, no exceptions).
#   Uses TELEGRAM_BOT_TOKEN and CHANNEL_ID from config.

from __future__ import annotations

import asyncio

from telegram import Bot
from telegram.error import BadRequest, RetryAfter

import config
from utils import logger as log_setup, telegram_html

log = log_setup.get("telegram")

# One bot connection, created the first time it's needed and kept afterwards.
_bot: Bot | None = None

# The phrases Telegram uses when it dislikes our formatting. When we see one of
# these, retrying identically is pointless — but retrying WITHOUT formatting
# will work.
_FORMATTING_COMPLAINTS = (
    "can't parse entities",
    "unsupported start tag",
    "unexpected end tag",
    "unclosed",
    "can't find end tag",
    "entity",
)


async def _get_bot() -> Bot:
    """Return the shared bot connection, setting it up on first use."""
    global _bot
    if _bot is None:
        if not config.TELEGRAM_BOT_TOKEN:
            raise RuntimeError("TELEGRAM_BOT_TOKEN is not set in .env")
        _bot = Bot(token=config.TELEGRAM_BOT_TOKEN)
        await _bot.initialize()
    return _bot


def build_post_url(message_id: int) -> str:
    """Return the shareable link to one of our channel messages.

    The address is built differently depending on how the channel is identified:
        @mychannel        →  https://t.me/mychannel/123
        -1001234567890    →  https://t.me/c/1234567890/123   (the -100 comes off)
    """
    channel = config.CHANNEL_ID.strip()
    if not channel:
        return ""
    if channel.startswith("@"):
        return f"https://t.me/{channel.lstrip('@')}/{message_id}"
    if channel.startswith("-100"):
        return f"https://t.me/c/{channel[4:]}/{message_id}"
    return ""


def _is_formatting_problem(error: Exception) -> bool:
    """True if Telegram rejected the message because of how it was formatted."""
    text = str(error).lower()
    return any(complaint in text for complaint in _FORMATTING_COMPLAINTS)


async def send_text(html: str, reply_to_message_id: int | None = None) -> int:
    """Send a text message to the channel. Returns Telegram's message number.

    Args:
        reply_to_message_id: if given, Telegram renders this message as a
                             reply to that earlier channel message.

    Raises on failure, so the caller can count the attempt and retry later.
    """
    bot = await _get_bot()
    safe = telegram_html.safe_truncate(telegram_html.sanitize(html),
                                       telegram_html.TEXT_LIMIT)
    reply_kwargs = {}
    if reply_to_message_id is not None:
        reply_kwargs["reply_to_message_id"] = reply_to_message_id

    try:
        message = await bot.send_message(
            chat_id=config.CHANNEL_ID,
            text=safe,
            parse_mode="HTML",
            disable_web_page_preview=True,
            **reply_kwargs,
        )
        return message.message_id

    except RetryAfter as error:
        # Telegram is asking us to slow down. Wait exactly as long as it says,
        # plus a second for safety, then try once more.
        wait = float(error.retry_after) + 1
        log.warning("Telegram asked us to wait %.0f seconds — pausing", wait)
        await asyncio.sleep(wait)
        message = await bot.send_message(
            chat_id=config.CHANNEL_ID, text=safe,
            parse_mode="HTML", disable_web_page_preview=True,
            **reply_kwargs,
        )
        return message.message_id

    except BadRequest as error:
        if not _is_formatting_problem(error):
            raise
        # Our formatting upset it. Rather than lose the post, send it plain.
        log.warning("Telegram rejected the formatting (%s) — resending as plain text", error)
        plain = telegram_html.safe_truncate(
            telegram_html.strip_all_tags(html), telegram_html.TEXT_LIMIT
        )
        message = await bot.send_message(
            chat_id=config.CHANNEL_ID, text=plain, disable_web_page_preview=True,
            **reply_kwargs,
        )
        return message.message_id


async def send_photo(image_url: str, caption_html: str,
                     reply_to_message_id: int | None = None) -> int:
    """Send a photo with the post as its caption. Returns the message number.

    Args:
        reply_to_message_id: if given, Telegram renders this message as a
                             reply to that earlier channel message.

    Telegram fetches the image from the address itself, so we never download it.
    That is faster, but it does mean Telegram sometimes cannot reach an image —
    which is why the caller keeps a text-only fallback ready.
    """
    bot = await _get_bot()
    safe = telegram_html.safe_truncate(telegram_html.sanitize(caption_html),
                                       telegram_html.CAPTION_LIMIT)
    reply_kwargs = {}
    if reply_to_message_id is not None:
        reply_kwargs["reply_to_message_id"] = reply_to_message_id

    try:
        message = await bot.send_photo(
            chat_id=config.CHANNEL_ID,
            photo=image_url,
            caption=safe,
            parse_mode="HTML",
            **reply_kwargs,
        )
        return message.message_id

    except RetryAfter as error:
        wait = float(error.retry_after) + 1
        log.warning("Telegram asked us to wait %.0f seconds — pausing", wait)
        await asyncio.sleep(wait)
        message = await bot.send_photo(
            chat_id=config.CHANNEL_ID, photo=image_url,
            caption=safe, parse_mode="HTML",
            **reply_kwargs,
        )
        return message.message_id

    except BadRequest as error:
        if not _is_formatting_problem(error):
            raise    # a broken image address; the caller falls back to text
        log.warning("Telegram rejected the caption formatting (%s) — sending it plain", error)
        plain = telegram_html.safe_truncate(
            telegram_html.strip_all_tags(caption_html), telegram_html.CAPTION_LIMIT
        )
        message = await bot.send_photo(
            chat_id=config.CHANNEL_ID, photo=image_url, caption=plain,
            **reply_kwargs,
        )
        return message.message_id


