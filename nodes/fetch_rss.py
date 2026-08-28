"""Reads every enabled feed and returns the articles we have not seen before.

WHAT MAKES THIS FREE: each read hands us an ETag and a Last-Modified date, and
handing them back next time gets "304 Not Modified" with no content when nothing
has been published. That is why polling every 10 minutes costs essentially
nothing, for us and for the sites.

The XML is parsed by hand rather than with feedparser, which is not installed
here and would be a new dependency for about forty lines of work.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

import httpx
from bs4 import BeautifulSoup

import config
from utils import db, logger as log_setup, textclean

log = log_setup.get("rss")

# THREE feed formats in the wild, which is why most lookups below appear three
# times: RSS 2.0 (plain tags), Atom (namespaced), RSS 1.0 / RDF (another
# namespace). Getting it wrong fails SILENTLY — a valid page with no articles
# found in it, which is what happened with Deutsche Welle on the first run.
_ATOM = "{http://www.w3.org/2005/Atom}"
_RSS1 = "{http://purl.org/rss/1.0/}"

# RSS 1.0 usually carries its date in the Dublin Core namespace instead.
_DC = "{http://purl.org/dc/elements/1.1/}"

# Some sites block requests that do not identify themselves.
_HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; NewsChannelBot/1.0)",
    "Accept": "application/rss+xml, application/atom+xml, application/xml, text/xml, */*",
}

_TIMEOUT = httpx.Timeout(connect=10.0, read=20.0, write=10.0, pool=5.0)

# A feed that suddenly lists 200 items should not flood the queue in one cycle.
_MAX_PER_FEED = 15

# A feed that fails this many times in a row is worth telling you about. It
# usually means the address has changed or the site dropped its feed.
_ALERT_AFTER_FAILURES = 5


@dataclass
class Article:
    """A single story pulled from a feed, cleaned up and ready to store."""

    source_name: str
    topic: str
    url: str
    title: str
    summary: str
    published: datetime | None   # None when the feed didn't say


def _parse_date(raw: str) -> datetime | None:
    """Turn a feed's date string into a proper UTC timestamp, or None if unreadable.

    The two formats in the wild look like this:
        RSS:  "Tue, 24 Jun 2026 21:05:00 GMT"
        Atom: "2026-06-24T21:05:00Z"
    We try both. Returning None just means "we don't know when", which callers
    treat as "assume it's recent" rather than throwing the article away.
    """
    raw = (raw or "").strip()
    if not raw:
        return None

    # RSS style
    try:
        parsed = parsedate_to_datetime(raw)
        if parsed is not None:
            return (parsed.astimezone(timezone.utc) if parsed.tzinfo
                    else parsed.replace(tzinfo=timezone.utc))
    except (TypeError, ValueError):
        pass

    # Atom style
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        return (parsed.astimezone(timezone.utc) if parsed.tzinfo
                else parsed.replace(tzinfo=timezone.utc))
    except ValueError:
        return None


def _parse_feed(xml_text: str, source_name: str, topic: str) -> list[Article]:
    """Turn the raw XML of a feed into a list of Articles.

    Handles both feed formats. Skips anything missing a title or a link, since
    we can neither write about nor link to those.
    """
    root = ET.fromstring(xml_text)

    # Look for articles under all three names, in order of how common they are.
    nodes = (
        root.findall(".//item")               # RSS 2.0
        or root.findall(f".//{_ATOM}entry")   # Atom
        or root.findall(f".//{_RSS1}item")    # RSS 1.0 / RDF
    )

    articles: list[Article] = []
    for node in nodes:
        title = (
            node.findtext("title")
            or node.findtext(f"{_ATOM}title")
            or node.findtext(f"{_RSS1}title")
            or ""
        ).strip()

        # RSS puts the address inside <link>. Atom puts it in an attribute:
        # <link href="..."/>, so findtext returns nothing and we look again.
        link = (node.findtext("link") or node.findtext(f"{_RSS1}link") or "").strip()
        if not link:
            link_element = node.find(f"{_ATOM}link")
            if link_element is not None:
                link = (link_element.get("href") or "").strip()

        if not title or not link:
            continue

        # The summary is usually a lump of HTML. Strip it back to plain text —
        # the AI reads better prose than markup, and we pay per word.
        raw_summary = (
            node.findtext("description")
            or node.findtext(f"{_ATOM}summary")
            or node.findtext(f"{_ATOM}content")
            or node.findtext(f"{_RSS1}description")
            or ""
        )
        summary = BeautifulSoup(raw_summary, "lxml").get_text(" ", strip=True)

        published = _parse_date(
            node.findtext("pubDate")
            or node.findtext(f"{_ATOM}published")
            or node.findtext(f"{_ATOM}updated")
            or node.findtext(f"{_DC}date")      # RSS 1.0 keeps its date here
            or ""
        )

        articles.append(Article(
            source_name=source_name,
            topic=topic,
            url=link,
            title=title,
            summary=summary[: config.MAX_BODY_CHARS],
            published=published,
        ))

    return articles


async def fetch_one(client: httpx.AsyncClient, source: dict) -> tuple[list[Article], str]:
    """Fetch a single feed and return (articles, status word).

    The status word is one of:
        "ok"        we got new content
        "cached"    the site said nothing has changed — nothing to do
        "error:..." something went wrong (the reason follows the colon)

    Never raises: a broken feed must not stop the other twelve from being read.
    """
    name = source["name"]

    # Hand back the tokens the site gave us last time. If nothing has been
    # published since, it will reply "304 Not Modified" and save us both the work.
    headers = dict(_HEADERS)
    if source.get("etag"):
        headers["If-None-Match"] = source["etag"]
    if source.get("last_modified"):
        headers["If-Modified-Since"] = source["last_modified"]

    try:
        response = await client.get(
            source["url"], headers=headers, timeout=_TIMEOUT, follow_redirects=True
        )
    except Exception as error:  # noqa: BLE001 - network problems are routine
        db.record_source_failure(name, str(error))
        return [], f"error:{type(error).__name__}"

    # 304 = "nothing new since you last asked". The happy path most of the time.
    if response.status_code == 304:
        db.record_source_success(name, source.get("etag", ""), source.get("last_modified", ""))
        return [], "cached"

    if response.status_code != 200:
        db.record_source_failure(name, f"HTTP {response.status_code}")
        return [], f"error:HTTP {response.status_code}"

    try:
        articles = _parse_feed(response.text, name, source["topic"])
    except ET.ParseError as error:
        # Usually means the address returns a normal web page, not a feed.
        db.record_source_failure(name, f"not valid XML: {error}")
        return [], "error:not a valid feed (is that really an RSS address?)"
    except Exception as error:  # noqa: BLE001
        db.record_source_failure(name, str(error))
        return [], f"error:{type(error).__name__}"

    # Remember the new tokens for next time.
    db.record_source_success(
        name,
        response.headers.get("ETag", ""),
        response.headers.get("Last-Modified", ""),
    )
    return articles[:_MAX_PER_FEED], "ok"


async def execute() -> list[Article]:
    """Read all enabled feeds and return every article they offered.

    Duplicates are NOT filtered here — that happens in nodes/dedup.py, which is
    the only place that decides what counts as "already seen". This node's only
    job is fetching.
    """
    sources = db.get_enabled_sources()
    if not sources:
        log.warning("No feeds are enabled — check config.SOURCES")
        return []

    collected: list[Article] = []
    counts = {"ok": 0, "cached": 0, "error": 0}

    # One client for all the feeds, so connections get reused.
    async with httpx.AsyncClient(timeout=_TIMEOUT, follow_redirects=True) as client:
        for source in sources:
            articles, status = await fetch_one(client, dict(source))

            if status == "cached":
                counts["cached"] += 1
            elif status.startswith("error"):
                counts["error"] += 1
                fail_count = dict(source).get("fail_count", 0) + 1
                log.warning("Feed '%s' failed (%s in a row): %s",
                            source["name"], fail_count, status[6:])

                # Tell you once it looks like a real, lasting breakage rather
                # than a passing network hiccup.
                if fail_count == _ALERT_AFTER_FAILURES:
                    from utils import telegram_error
                    telegram_error.send_error(
                        f"Feed '{source['name']}' has failed {fail_count} times in a row: "
                        f"{status[6:]}. Its address may have changed.",
                        node_name="fetch_rss",
                    )
            else:
                counts["ok"] += 1
                collected.extend(articles)

    log.info("Feeds polled: %d fresh, %d unchanged, %d failed → %d articles",
             counts["ok"], counts["cached"], counts["error"], len(collected))
    return collected
