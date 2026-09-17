"""Knows what data releases are scheduled this week, and matches news to them.

The free ForexFactory feed gives every scheduled release for the week with its
consensus forecast and previous value — but never the actual; that arrives on
the wire. So when "U.S. CPI: +3.4% Y/Y" comes in at 12:31, this finds the
12:30 USD "CPI y/y" entry and pins its forecast and previous onto the item.

Three stations then see two numbers they never had. The sorter learns this is
an official scheduled print, not chatter, and which economy it belongs to. The
writer can say "3.4% (forecast 3.4%, previous 3.4%)" from the feed rather than
from memory. The editor sees the calendar line as part of the source, so those
numbers are not drift.

Matching is deliberately dumb: right country, a title keyword in the item text,
and the item arriving inside a short window around the release. Fuzzier than
that and a Fed governor's speech starts matching every mention of the Fed.

IT FAILS OPEN. No feed, no match, no fields — the item is exactly what it was
before this existed.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import httpx

import config
from utils import db, logger as log_setup

log = log_setup.get("calendar")

FEED_URL = "https://nfs.faireconomy.media/ff_calendar_thisweek.json"

# The feed names a release one way and the wire another. Each entry: words
# that identify the release in a feed title -> words that identify it in an
# item. Both sides lower-case. A release matches when one word from each side
# is present.
_ALIASES: list[tuple[tuple[str, ...], tuple[str, ...]]] = [
    (("cpi",),                       ("cpi", "consumer price", "inflation")),
    (("core cpi",),                  ("core cpi", "core inflation")),
    (("ppi",),                       ("ppi", "producer price")),
    (("non-farm", "nonfarm"),        ("payroll", "nfp", "nonfarm", "non-farm", "jobs report")),
    (("unemployment claims",),       ("jobless claims", "initial claims", "unemployment claims")),
    (("unemployment rate",),         ("unemployment rate", "jobless rate")),
    (("retail sales",),              ("retail sales",)),
    (("gdp",),                       ("gdp", "gross domestic")),
    # The decision itself, not the day. "Fed" alone would attach the rate
    # forecast to every remark the chair makes for three hours afterwards.
    (("federal funds rate",),        ("raises rates", "cuts rates", "holds rates", "rate decision",
                                      "raises interest rates", "cuts interest rates", "bps to",
                                      "basis points to", "fed funds", "target range")),
    (("fomc economic projections",), ("dot plot", "projections", "median forecast")),
    (("fomc statement",),            ("fomc statement", "fed statement", "statement says")),
    (("press conference",),          ("press conference",)),
    (("boj policy rate",),           ("boj", "bank of japan")),
    (("official bank rate",),        ("boe", "bank of england")),
    (("main refinancing rate", "ecb"), ("ecb", "european central bank", "lagarde")),
    (("pmi",),                       ("pmi",)),
    (("crude oil inventories",),     ("crude inventories", "oil inventories", "eia")),
]

# The feed's currency code, and how the wire names that economy.
_COUNTRY_WORDS = {
    "USD": ("us ", "u.s.", "united states", "fed", "fomc", "american", "🇺🇸"),
    "EUR": ("euro", "ecb", "eurozone", "🇪🇺"),
    "GBP": ("uk ", "u.k.", "britain", "british", "boe", "bank of england", "🇬🇧"),
    "JPY": ("japan", "boj", "🇯🇵"),
    "CNY": ("china", "chinese", "pboc", "🇨🇳"),
    "CAD": ("canada", "canadian", "🇨🇦"),
    "AUD": ("australia", "rba", "🇦🇺"),
    "CHF": ("swiss", "snb", "🇨🇭"),
}

# A release is a moment. The number is reported within the hour; after that
# the wire is carrying reaction and commentary, which must not inherit the
# forecast. A preview can arrive a little early.
_AFTER = timedelta(minutes=45)
_BEFORE = timedelta(minutes=15)


async def refresh() -> int:
    """Pull this week's feed into the calendar table. Returns rows stored."""
    try:
        async with httpx.AsyncClient(timeout=20, headers={"User-Agent": "Mozilla/5.0"}) as client:
            response = await client.get(FEED_URL)
            response.raise_for_status()
            events = response.json()
    except Exception as error:  # noqa: BLE001
        log.warning("Could not fetch the economic calendar: %s", error)
        return 0

    rows = []
    for e in events:
        if e.get("impact") not in ("High", "Medium"):
            continue
        try:
            at = datetime.fromisoformat(e["date"]).astimezone(timezone.utc)
        except (KeyError, ValueError):
            continue
        rows.append((e.get("country", ""), e.get("title", ""), at.strftime("%Y-%m-%d %H:%M:%S"),
                     e.get("impact", ""), e.get("forecast") or "", e.get("previous") or ""))

    db.replace_calendar(rows)
    log.info("Calendar refreshed: %d High/Medium releases this week", len(rows))
    return len(rows)


def match(text: str, when: datetime) -> dict | None:
    """The scheduled release this item is about, or None.

    `when` is when the item arrived. Candidates are releases whose time is
    within a short window of it, for an economy the text names, whose title
    the text refers to. The nearest in time wins.
    """
    lowered = f" {text.lower()} "
    since = (when - _AFTER).strftime("%Y-%m-%d %H:%M:%S")
    until = (when + _BEFORE).strftime("%Y-%m-%d %H:%M:%S")

    best, best_gap = None, None
    for row in db.calendar_between(since, until):
        country_words = _COUNTRY_WORDS.get(row["country"])
        if not country_words or not any(w in lowered for w in country_words):
            continue
        title = row["title"].lower()
        if not any(any(f in title for f in feed_words) and any(i in lowered for i in item_words)
                   for feed_words, item_words in _ALIASES):
            continue
        # "Core Retail Sales" and "Retail Sales" share a slot. The core one is
        # only the match when the item says so.
        if "core" in title and "core" not in lowered:
            continue
        gap = abs((when - datetime.fromisoformat(row["at_utc"]).replace(tzinfo=timezone.utc)).total_seconds())
        if best is None or gap < best_gap:
            best, best_gap = row, gap

    return dict(best) if best is not None else None


def describe(item) -> str:
    """The calendar line that goes into a prompt, or "" if the item matched nothing."""
    title = item["calendar_title"] if "calendar_title" in item.keys() else ""
    if not title:
        return ""
    parts = [f"Scheduled release: {title}"]
    if item["calendar_forecast"]:
        parts.append(f"forecast {item['calendar_forecast']}")
    if item["calendar_previous"]:
        parts.append(f"previous {item['calendar_previous']}")
    return ", ".join(parts)
