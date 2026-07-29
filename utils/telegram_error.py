# --- Error alerts: tell you on Telegram when something breaks ---
#
# WHAT THIS FILE DOES
#   Sends a short "something went wrong" message to your own Telegram DM. This
#   is how you find out about a problem without having to read log files.
#
# WHERE IT FITS
#   Called from anywhere that catches an error. It goes to your PRIVATE chat,
#   never to the public channel — readers should never see the plumbing.
#
# THE ONE RULE IN THIS FILE
#   Nothing here may ever raise an exception. If alerting fails, we log it and
#   move on. An error while reporting an error must not become a second, worse
#   problem — and it must never take down the loop that was merely trying to
#   tell you something.
#
# DEPENDENCIES
#   httpx. Uses TELEGRAM_BOT_TOKEN and ERROR_CHAT_ID from config.

from __future__ import annotations

import html
import logging
import time

import httpx

import config

logger = logging.getLogger("news-channel.alert")

# Don't send the same message over and over. If a feed is broken and the loop
# retries every 10 minutes, one alert an hour is plenty — otherwise a single
# fault turns into hundreds of notifications.
_QUIET_SECONDS = 3600
_last_sent: dict[str, float] = {}


def send_error(message: str, node_name: str = "unknown", quiet: bool = True) -> None:
    """Send an error alert to your Telegram DM. Never raises.

    Args:
        message:   what went wrong.
        node_name: which part of the program it happened in, so you know where
                   to look without reading the whole log.
        quiet:     if True (the default), suppress repeats of the same error
                   from the same place within the hour.
    """
    try:
        if not config.TELEGRAM_BOT_TOKEN or not config.ERROR_CHAT_ID:
            # Perfectly normal during the early build phases, before the bot
            # token exists. The log still has the full story.
            logger.warning("[%s] %s (no Telegram alert: token/chat not configured)",
                           node_name, message)
            return

        # Rate-limit repeats of the same fault from the same place.
        key = f"{node_name}:{message[:120]}"
        now = time.time()
        if quiet and now - _last_sent.get(key, 0) < _QUIET_SECONDS:
            return
        _last_sent[key] = now

        # escape() so that an error message containing "<" can't break the
        # message formatting — errors often contain HTML fragments.
        text = (
            "🚨 <b>News Channel error</b>\n"
            f"📍 Node: <code>{html.escape(node_name)}</code>\n"
            f"❌ {html.escape(message[:1500])}"
        )

        response = httpx.post(
            f"https://api.telegram.org/bot{config.TELEGRAM_BOT_TOKEN}/sendMessage",
            json={
                "chat_id": config.ERROR_CHAT_ID,
                "text": text,
                "parse_mode": "HTML",
                "disable_web_page_preview": True,
            },
            timeout=10.0,
        )
        if response.status_code != 200:
            logger.warning("Could not send error alert (HTTP %s): %s",
                           response.status_code, response.text[:200])

    except Exception as error:  # noqa: BLE001 - alerting must never break anything
        logger.warning("Error alert itself failed: %s", error)


def send_notice(message: str) -> None:
    """Send an informational note to your DM — not an error.

    Used for things worth knowing but not alarming: "the editor is rejecting an
    unusual number of posts", or "the channel started up".
    """
    try:
        if not config.TELEGRAM_BOT_TOKEN or not config.ERROR_CHAT_ID:
            logger.info("(no Telegram notice sent: token/chat not configured) %s", message)
            return

        httpx.post(
            f"https://api.telegram.org/bot{config.TELEGRAM_BOT_TOKEN}/sendMessage",
            json={
                "chat_id": config.ERROR_CHAT_ID,
                "text": f"ℹ️ <b>News Channel</b>\n{html.escape(message[:1500])}",
                "parse_mode": "HTML",
                "disable_web_page_preview": True,
            },
            timeout=10.0,
        )
    except Exception as error:  # noqa: BLE001
        logger.warning("Notice failed to send: %s", error)
