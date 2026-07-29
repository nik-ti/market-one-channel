# --- Making text safe for Telegram to display ---
#
# WHAT THIS FILE DOES
#   Telegram is extremely fussy about HTML. If a message contains one tag it
#   doesn't recognise, or one unclosed tag, it rejects the WHOLE message with an
#   error — the post is simply lost. This file makes that impossible.
#
# WHERE IT FITS
#   Last stop before anything is sent. utils/telegram_client.py runs everything
#   through here first.
#
# THE TWO PROBLEMS IT SOLVES
#
#   1. Stray angle brackets in ordinary text.
#      News writing is full of them: "yields above <6%", "profits <$1m",
#      "the <script> tag". Telegram sees "<6%" and tries to interpret it as a
#      tag, fails, and throws the message away. We escape anything that isn't a
#      real, allowed tag.
#
#   2. Cutting a message short can break it.
#      Slice a message at 1024 characters and you might land in the middle of
#      "<b>", or cut off the "</b>" that closes it. Either one gets the message
#      rejected. So we cut at a safe point and close anything left dangling.
#
# WHERE THIS CAME FROM
#   Adapted from systems/news-saas, which wrote it after discovering that
#   articles were being silently dropped. Note this version is deliberately NOT
#   the one from nmd_consulting: that one permits <br>, and <br> is itself one
#   of the tags Telegram rejects — so its "safety" function was causing the very
#   failure it was meant to prevent.
#
# DEPENDENCIES
#   Python standard library only.

from __future__ import annotations

import re

# The complete list of tags Telegram accepts in HTML mode.
#
# Two things to note:
#   * The longer names come first, so that "strike" isn't matched as "s"
#     followed by stray letters.
#   * <br> and <spoiler> are NOT on this list, because Telegram does not accept
#     them. A single <br> from an AI model means a rejected message and a lost
#     post. We convert <br> to a real line break instead. Telegram's own spoiler
#     tag is spelled <tg-spoiler>.
_ALLOWED_TAG = re.compile(
    r"</?(?:tg-spoiler|blockquote|strike|strong|code|pre|del|ins|em|b|i|u|s|a)"
    r"(?:\s[^<>]*)?>",
    re.IGNORECASE,
)

_BR = re.compile(r"<br\s*/?>", re.IGNORECASE)

# Tags that could be left hanging open if we cut a message short. Listed
# outermost-first, so that closing them in this order produces valid nesting.
_CLOSEABLE = ("a", "code", "pre", "i", "b")

# Telegram's own limits.
TEXT_LIMIT = 4096      # a normal message
CAPTION_LIMIT = 1024   # the text under a photo — four times shorter


def _escape(text: str) -> str:
    """Turn angle brackets into their harmless written-out form."""
    return text.replace("<", "&lt;").replace(">", "&gt;")


# --- Make any text safe to send ---
def sanitize(text: str) -> str:
    """Escape every angle bracket that isn't part of a tag Telegram allows.

    Real tags pass through untouched. Everything else — "<6%", "<script",
    "<div>", or a lone "<" with no closing bracket — gets escaped so Telegram
    displays it as text instead of choking on it.
    """
    if not text:
        return ""

    # A line break is what the model meant by <br>, so give it one.
    text = _BR.sub("\n", text)

    pieces: list[str] = []
    position = 0
    for match in _ALLOWED_TAG.finditer(text):
        pieces.append(_escape(text[position:match.start()]))   # ordinary text
        pieces.append(match.group(0))                          # a real tag
        position = match.end()
    pieces.append(_escape(text[position:]))

    return "".join(pieces)


# --- Shorten a message without breaking it ---
def safe_truncate(html: str, limit: int = TEXT_LIMIT) -> str:
    """Cut a message down to `limit` characters without producing broken HTML.

    Cuts at a tag boundary rather than mid-tag, then closes anything still open.
    Returns the text unchanged if it already fits.
    """
    if len(html) <= limit:
        return html

    # Leave room for any closing tags we might have to add.
    budget = limit - sum(len(f"</{tag}>") for tag in _CLOSEABLE)
    cut = html[:budget]

    # If the last "<" comes after the last ">", we cut inside a tag. Back up.
    last_open, last_close = cut.rfind("<"), cut.rfind(">")
    if last_open > last_close:
        cut = cut[:last_open]

    # Close anything left open, innermost last.
    for tag in _CLOSEABLE:
        opened = len(re.findall(rf"<{tag}[\s>]", cut, re.IGNORECASE))
        closed = len(re.findall(rf"</{tag}>", cut, re.IGNORECASE))
        if opened > closed:
            cut += f"</{tag}>" * (opened - closed)

    return cut


# --- Would cutting this ruin it? ---
def would_cut_mid_sentence(html: str, limit: int) -> bool:
    """True if shortening this text to `limit` would stop it mid-sentence.

    Used to decide between two bad options for an over-long photo caption:
    publish a caption that trails off, or drop the picture and send the full
    text instead. The second is nearly always better, so this is how we choose.
    """
    if len(html) <= limit:
        return False

    truncated = safe_truncate(html, limit)
    # Ignore any closing tags we appended when looking at the final character.
    without_tags = re.sub(r"(</[a-zA-Z-]+>\s*)+$", "", truncated).rstrip()
    return not without_tags.endswith((".", "!", "?", '"', "'", ")", "”", "’"))


# --- Last-resort fallback ---
def strip_all_tags(html: str) -> str:
    """Remove every tag, leaving plain text.

    Used only when Telegram has already rejected a message for bad formatting.
    Sending it as plain text loses the bold headline but keeps the news, which
    beats losing the post entirely.
    """
    text = _BR.sub("\n", html or "")
    text = re.sub(r"<[^>]+>", "", text)
    # Put back the characters that escaping turned into their written form.
    return (text.replace("&lt;", "<").replace("&gt;", ">")
                .replace("&amp;", "&").replace("&quot;", '"'))
