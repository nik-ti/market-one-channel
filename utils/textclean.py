"""Text cleaning for the duplicate detector.

Publishers write the same headline slightly differently every time — a curly
quote, a trailing slash — and to a computer those are different strings. These
helpers strip the cosmetic differences so only real ones remain.
"""

from __future__ import annotations

import hashlib
import re
from urllib.parse import urlparse, urlunparse

_PUNCT_FOLD = str.maketrans({
    "‘": "'", "’": "'",     # ' '  curly single quotes
    "“": '"', "”": '"',     # " "  curly double quotes
    "–": "-", "—": "-",     # – —  en dash and em dash
    "…": "...",                  # …    ellipsis
    " ": " ",                    #      non-breaking space
})

_KEEP_CHARS = re.compile(r"[^a-z0-9 ]+")

# Used to answer "do these two headlines differ ONLY by a number?"
_DIGIT_RUN = re.compile(r"[0-9]+")

# Sirens, flags and other pictographs. See for_embedding() for why they come off.
_DECORATION = re.compile(
    "[\U0001F000-\U0001FAFF"     # pictographs, symbols, flags, transport
    "☀-➿"              # miscellaneous symbols and dingbats
    "⬀-⯿←-⇿]"    # arrows and misc symbols
    "|[︀-️‍⃣]"   # variation selectors, joiners, keycaps
)

# A cashtag is a dollar sign plus CAPITALS. A money amount like "$66,000" is
# digits and is deliberately left alone — the number is usually the news.
_CASHTAG = re.compile(r"\$[A-Z]{2,6}\b")

_ALERT_OPENER = re.compile(r"\b(JUST IN|BREAKING|ALERT|UPDATE|NEW|LATEST)\b:?", re.IGNORECASE)


def for_embedding(text: str) -> str:
    """Strip a source's house decoration before measuring what a story means.

    A tweet and a wire story about the same event are written in opposite
    registers, and that difference alone sinks the similarity score. Measured:
    the same Solana vote from CoinDesk and crypto_banter scored 0.7162 raw —
    under the 0.72 floor, so it published twice — and 0.7786 once the siren,
    capitals and cashtag came off.

    Numbers are left alone: "$66,000 Bitcoin" and "$71,000 Bitcoin" must stay
    far apart. Unlike normalise_headline(), this keeps the sentence readable,
    because an embedding model reads it as language rather than as a key.
    """
    if not text:
        return ""
    cleaned = _DECORATION.sub(" ", text)
    cleaned = _CASHTAG.sub(" ", cleaned)
    cleaned = _ALERT_OPENER.sub(" ", cleaned)
    cleaned = cleaned.translate(_PUNCT_FOLD).lower()
    return " ".join(cleaned.split())


def normalise_url(url: str) -> str:
    """Drop the query string, fragment and trailing slash, and lowercase the host."""
    if not url:
        return ""
    parsed = urlparse(url.strip())
    path = parsed.path.rstrip("/")
    return urlunparse((parsed.scheme, parsed.netloc.lower(), path, "", "", ""))


def normalise_headline(title: str) -> str:
    """Reduce a headline to lowercase letters, digits and single spaces.

    Digits are deliberately KEPT. "Fed cuts 25bp" and "Fed cuts 50bp" are
    different events and must never collapse onto one key.
    """
    if not title:
        return ""
    folded = title.lower().translate(_PUNCT_FOLD)
    stripped = _KEEP_CHARS.sub(" ", folded)
    return " ".join(stripped.split())


def title_hash(title: str) -> str:
    """Fingerprint a headline for instant lookup.

    We hash the headline rather than the URL: some feeds hand out a different
    link for the same story each time. A side effect is that one story from two
    feeds produces one fingerprint.
    """
    normalised = normalise_headline(title)
    if not normalised:
        return ""
    return hashlib.sha256(normalised.encode("utf-8")).hexdigest()


def same_but_for_digits(a: str, b: str) -> bool:
    """True if two headlines are identical once every number is blanked out.

    "16 dead" -> "17 dead" is the same story updated; "Fed cuts 25bp" -> "50bp"
    is two different events. Both score ~97% similar and only meaning separates
    them, so we refuse to merge either. The cost is an occasional duplicate
    about a rising death toll; the thing it prevents is publishing the wrong
    rate cut.
    """
    return _DIGIT_RUN.sub("#", a) == _DIGIT_RUN.sub("#", b)


def tweet_to_title(text: str, max_chars: int = 200) -> str:
    """Take a tweet's first meaningful line as its headline.

    Tweets have no title field but the duplicate checks need something
    headline-shaped, and a news tweet's opening line almost always is one.
    """
    if not text:
        return ""

    without_links = re.sub(r"https?://\S+", "", text).strip()

    first_line = ""
    for line in without_links.splitlines():
        if line.strip():
            first_line = line.strip()
            break
    if not first_line:
        first_line = without_links.strip()

    # Many news accounts prefix everything with "BREAKING:" or "JUST IN:".
    # Removing it means the same story from two accounts matches up.
    first_line = re.sub(
        r"^(breaking|just in|update|urgent|alert|news)\s*[:\-–]\s*",
        "", first_line, flags=re.IGNORECASE,
    ).strip()

    return first_line[:max_chars]


def strip_html(text: str) -> str:
    """Remove HTML tags and collapse whitespace, without a full parser."""
    if not text:
        return ""
    return " ".join(re.sub(r"<[^>]+>", " ", text).split())
