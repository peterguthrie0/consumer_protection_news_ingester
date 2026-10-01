"""RSS and Atom parsing."""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from html import unescape


@dataclass(frozen=True)
class FeedItem:
    title: str
    url: str
    summary: str
    published_at: str | None


def _text(element: ET.Element | None) -> str:
    return "" if element is None or element.text is None else element.text.strip()


def _plain_text(value: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", unescape(value))).strip()


def _date(value: str) -> str | None:
    if not value:
        return None
    try:
        parsed = parsedate_to_datetime(value)
    except (TypeError, ValueError):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc).isoformat()


def parse_feed(payload: bytes) -> list[FeedItem]:
    """Parse RSS 2.x or Atom XML into a common item representation."""
    root = ET.fromstring(payload)
    root_name = root.tag.rsplit("}", 1)[-1].lower()
    if root_name not in {"feed", "rss"}:
        raise ValueError("The response is not an RSS 2.x or Atom feed.")
    items: list[FeedItem] = []
    if root_name == "feed":
        namespace = root.tag.split("}", 1)[0].lstrip("{") if "}" in root.tag else ""
        prefix = f"{{{namespace}}}" if namespace else ""
        for entry in root.findall(f"{prefix}entry"):
            links = entry.findall(f"{prefix}link")
            link = next((node.get("href", "") for node in links if node.get("rel", "alternate") == "alternate"), "")
            summary = _text(entry.find(f"{prefix}summary")) or _text(entry.find(f"{prefix}content"))
            items.append(FeedItem(
                _text(entry.find(f"{prefix}title")), link, _plain_text(summary),
                _date(_text(entry.find(f"{prefix}published")) or _text(entry.find(f"{prefix}updated"))),
            ))
    else:
        for item in root.findall(".//item"):
            summary = _text(item.find("description")) or _text(item.find("{*}encoded"))
            items.append(FeedItem(
                _text(item.find("title")), _text(item.find("link")), _plain_text(summary),
                _date(_text(item.find("pubDate")) or _text(item.find("date"))),
            ))
    return [item for item in items if item.title and item.url]
