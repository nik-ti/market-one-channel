# --- Tidying up text so the same story always looks the same to us ---
#
# WHAT THIS FILE DOES
#   Small text-cleaning helpers used by the duplicate detector. The idea behind
#   all of them: publishers write the same headline slightly differently every
#   time — a curly quote here, a trailing slash there — and to a computer those
#   are completely different strings. These functions strip away the cosmetic
#   differences so that only real differences remain.
#
# WHERE IT FITS
#   Used by nodes/fetch_rss.py and nodes/fetch_tweets.py when items arrive, and
#   by nodes/dedup.py when comparing them.
#
# WHERE THIS CAME FROM
#   Adapted from the news-trader bot's ingest.py, which worked these rules out
#   the hard way over months of real headlines.
#
# DEPENDENCIES
#   Python standard library only.

from __future__ import annotations

import hashlib
import re
from urllib.parse import urlparse, urlunparse

# Publishers reissue the SAME headline with different typography: a curly
# apostrophe instead of a straight one, an en-dash instead of a hyphen. To a
# reader those are identical; to a computer they are different text. So we
# convert the fancy characters to plain ones before comparing anything.
_PUNCT_FOLD = str.maketrans({
    "‘": "'", "’": "'",     # ' '  curly single quotes
    "“": '"', "”": '"',     # " "  curly double quotes
    "–": "-", "—": "-",     # – —  en dash and em dash
    "…": "...",                  # …    ellipsis
    " ": " ",                    #      non-breaking space
})

# After folding we remove punctuation entirely. That is what actually collapses
#   to 'punish' Iran   and   to ‘punish’ Iran
# onto one identical key. We keep letters, digits and spaces, nothing else.
_KEEP_CHARS = re.compile(r"[^a-z0-9 ]+")

# Used to answer "do these two headlines differ ONLY by a number?"
_DIGIT_RUN = re.compile(r"[0-9]+")

# Tracking junk that sites bolt onto links. Two links that differ only by these
# point at exactly the same article.
_TRACKING_PARAMS = ("utm_", "fbclid", "gclid", "mc_cid", "mc_eid", "ref", "source")


# The decoration that news accounts put around a story but that is not part of
# the story: sirens and flags, ALL-CAPS shouting, cashtags, and the "JUST IN:"
# style opener. See for_embedding() below for why these have to come off.
_DECORATION = re.compile(
    "[\U0001F000-\U0001FAFF"     # pictographs, symbols, flags, transport
    "☀-➿"              # miscellaneous symbols and dingbats
    "⬀-⯿←-⇿]"    # arrows and misc symbols
    "|[︀-️‍⃣]"   # variation selectors, joiners, keycaps
)

# "$SOL", "$BTC" — a ticker written the social-media way. The dollar sign plus
# CAPITALS is what marks it; a money amount like "$66,000" is digits and is left
# completely alone, because the number usually IS the news.
_CASHTAG = re.compile(r"\$[A-Z]{2,6}\b")

# The openers that announce a story without describing it.
_ALERT_OPENER = re.compile(r"\b(JUST IN|BREAKING|ALERT|UPDATE|NEW|LATEST)\b:?", re.IGNORECASE)


# --- Strip a story down to its meaning, for the similarity check ---
def for_embedding(text: str) -> str:
    """Remove a source's house decoration before we measure what a story MEANS.

    WHY THIS EXISTS
        The meaning check turns a story into numbers and compares it with recent
        stories. It works well when both sides are written in the same register
        and badly when they are not — and ours never are. The same event reaches
        us twice: once as a wire headline, once as a tweet in capitals with a
        siren on the front.

        A real case, measured. On 28 August the same Solana governance vote
        arrived from CoinDesk and from crypto_banter:

            "Solana vote to double disinflation passes by a hair..."
            "🚨SOLANA DOUBLE DISINFLATION PASSES!"

        Raw, those score 0.7162 — just under the 0.72 shortlist floor, so the
        pair was never even shown to the judge, and the channel posted the story
        twice 24 minutes apart. Stripped of the siren, the capitals and the
        cashtag, the same pair scores 0.7786 and is caught comfortably.

        The siren was worth 0.06 of similarity. That is the whole bug.

    WHAT IT DELIBERATELY DOES NOT TOUCH
        Numbers. "$66,000 Bitcoin" and "$71,000 Bitcoin" must stay far apart —
        those are two different days, not one story. Only the CAPITALISED kind
        of "$SOL" cashtag comes off; a money amount is left exactly as it is.
        The same reasoning as in normalise_headline() above.

    NOT the same as normalise_headline(). That one strips punctuation entirely
    to build an exact-match key. This one keeps the sentence readable, because
    an embedding model reads it as language rather than as a key.
    """
    if not text:
        return ""
    cleaned = _DECORATION.sub(" ", text)
    cleaned = _CASHTAG.sub(" ", cleaned)
    cleaned = _ALERT_OPENER.sub(" ", cleaned)
    cleaned = cleaned.translate(_PUNCT_FOLD).lower()
    return " ".join(cleaned.split())


# --- Reduce a web address to its essentials ---
def normalise_url(url: str) -> str:
    """Strip the parts of a link that don't change which page it points at.

    Removes the query string (?utm_source=twitter...), the fragment (#comments),
    lowercases the domain, and drops a trailing slash. So all of these become
    one and the same:

        https://Example.com/news/story/?utm_source=rss
        https://example.com/news/story
        https://example.com/news/story#top
    """
    if not url:
        return ""
    parsed = urlparse(url.strip())
    path = parsed.path.rstrip("/")
    return urlunparse((parsed.scheme, parsed.netloc.lower(), path, "", "", ""))


# --- Reduce a headline to its plain identity ---
def normalise_headline(title: str) -> str:
    """Turn a headline into the bare form we use to recognise it again.

    Lowercases it, converts fancy punctuation to plain, removes punctuation
    entirely, and squashes repeated spaces. For example:

        "US strikes to 'punish' Iran"   ->   "us strikes to punish iran"

    NUMBERS ARE DELIBERATELY KEPT.
    It is tempting to strip digits too, so that a live-updating headline
    ("16 dead" becoming "17 dead") collapses onto one key. But numbers are very
    often the actual news: "Fed cuts 25bp" and "Fed cuts 50bp", or "CPI 3.5%"
    and "CPI 3.8%", are completely different events and must never be treated as
    the same story. Slowly-changing numbers are handled instead by the
    fuzzy-matching step, which has a time limit on it.
    """
    if not title:
        return ""
    folded = title.lower().translate(_PUNCT_FOLD)
    stripped = _KEEP_CHARS.sub(" ", folded)
    return " ".join(stripped.split())


# --- A short fingerprint of a headline ---
def title_hash(title: str) -> str:
    """Turn a headline into a fixed-length fingerprint we can look up instantly.

    We fingerprint the HEADLINE, not the web address. Some feeds — Google News
    especially — hand out a different link for the same story each time they are
    asked, so address-based matching would treat one story as many. The headline
    stays put.

    A useful side effect: the same story arriving from two different feeds
    produces the same fingerprint, so we notice it is one story.
    """
    normalised = normalise_headline(title)
    if not normalised:
        return ""
    return hashlib.sha256(normalised.encode("utf-8")).hexdigest()


# --- The guard that stops two different numbers being merged ---
def same_but_for_digits(a: str, b: str) -> bool:
    """True if two headlines become identical once every number is blanked out.

    This exists because similarity scoring genuinely cannot tell these apart:

        "16 dead in flooding"  ->  "17 dead in flooding"     same story, updated
        "Fed cuts rates 25bp"  ->  "Fed cuts rates 50bp"     two different events

    Both are one edited number in otherwise identical text, and both score about
    97% similar. Only meaning separates them, and we don't have meaning at this
    stage — so we fall back on which mistake is worse and refuse to merge either.

    The cost is a very occasional duplicate about a rising casualty count. The
    thing it prevents is quietly publishing "the Fed cut 25 basis points" when
    the news was 50. That trade is worth making.
    """
    return _DIGIT_RUN.sub("#", a) == _DIGIT_RUN.sub("#", b)


# --- Clean up a tweet so it reads as a headline ---
def tweet_to_title(text: str, max_chars: int = 200) -> str:
    """Take the first meaningful line of a tweet to use as its headline.

    Tweets have no title field, but the duplicate checks need something
    headline-shaped. The opening line of a news tweet is almost always the
    headline, so we take that, drop any trailing links, and cut it short.
    """
    if not text:
        return ""

    # Links at the end are the article being linked to, not part of the sentence.
    without_links = re.sub(r"https?://\S+", "", text).strip()

    # Take the first non-empty line.
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


# --- Strip HTML tags out of a block of text ---
def strip_html(text: str) -> str:
    """Remove HTML tags and collapse whitespace.

    Feed summaries usually arrive as a chunk of HTML. This is a plain-text
    fallback for when we don't want to involve a full HTML parser.
    """
    if not text:
        return ""
    return " ".join(re.sub(r"<[^>]+>", " ", text).split())
