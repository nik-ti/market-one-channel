"""Make text safe for Telegram to display.

Telegram rejects a WHOLE message for one tag it does not recognise or one tag
left unclosed, and the post is lost. Two things cause that: stray angle
brackets in ordinary text ("yields above <6%"), and cutting a long message
mid-tag. This is the last stop before anything is sent.
"""

from __future__ import annotations

import re

# Every tag Telegram accepts in HTML mode. Longer names come first so "strike"
# is not matched as "s". <br> is NOT accepted — one of them means a rejected
# message, so we turn it into a real line break instead.
_ALLOWED_TAG = re.compile(
    r"</?(?:tg-spoiler|blockquote|strike|strong|code|pre|del|ins|em|b|i|u|s|a)"
    r"(?:\s[^<>]*)?>",
    re.IGNORECASE,
)

_BR = re.compile(r"<br\s*/?>", re.IGNORECASE)

# Outermost-first, so closing them in this order produces valid nesting.
_CLOSEABLE = ("a", "code", "pre", "i", "b")

# Telegram's own limits.
TEXT_LIMIT = 4096      # a normal message
CAPTION_LIMIT = 1024   # the text under a photo


def _escape(text: str) -> str:
    """Turn angle brackets into their harmless written-out form."""
    return text.replace("<", "&lt;").replace(">", "&gt;")


def sanitize(text: str) -> str:
    """Escape every angle bracket that is not part of a tag Telegram allows."""
    if not text:
        return ""

    text = _BR.sub("\n", text)

    pieces: list[str] = []
    position = 0
    for match in _ALLOWED_TAG.finditer(text):
        pieces.append(_escape(text[position:match.start()]))
        pieces.append(match.group(0))
        position = match.end()
    pieces.append(_escape(text[position:]))

    return "".join(pieces)


def safe_truncate(html: str, limit: int = TEXT_LIMIT) -> str:
    """Cut a message down to `limit` characters without producing broken HTML.

    Cuts at a tag boundary rather than mid-tag, then closes anything still open.
    Returns the text unchanged if it already fits.
    """
    if len(html) <= limit:
        return html

    # Leave room for closing tags we may have to add.
    budget = limit - sum(len(f"</{tag}>") for tag in _CLOSEABLE)
    cut = html[:budget]

    # If the last "<" comes after the last ">", we cut inside a tag. Back up.
    last_open, last_close = cut.rfind("<"), cut.rfind(">")
    if last_open > last_close:
        cut = cut[:last_open]

    for tag in _CLOSEABLE:
        opened = len(re.findall(rf"<{tag}[\s>]", cut, re.IGNORECASE))
        closed = len(re.findall(rf"</{tag}>", cut, re.IGNORECASE))
        if opened > closed:
            cut += f"</{tag}>" * (opened - closed)

    return cut


def would_cut_mid_sentence(html: str, limit: int) -> bool:
    """True if shortening this text to `limit` would stop it mid-sentence.

    Decides between two bad options for an over-long caption: a caption that
    trails off, or dropping the picture. The second is nearly always better.
    """
    if len(html) <= limit:
        return False

    truncated = safe_truncate(html, limit)
    # Ignore closing tags we appended when looking at the final character.
    without_tags = re.sub(r"(</[a-zA-Z-]+>\s*)+$", "", truncated).rstrip()
    return not without_tags.endswith((".", "!", "?", '"', "'", ")", "”", "’"))


def strip_all_tags(html: str) -> str:
    """Remove every tag, leaving plain text.

    Last resort, after Telegram has already rejected a message. Loses the bold
    headline but keeps the news.
    """
    text = _BR.sub("\n", html or "")
    text = re.sub(r"<[^>]+>", "", text)
    return (text.replace("&lt;", "<").replace("&gt;", ">")
                .replace("&amp;", "&").replace("&quot;", '"'))
