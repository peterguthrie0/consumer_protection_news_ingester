"""Application operations shared by the CLI and browser."""

from __future__ import annotations

import json
import sqlite3
import time
from datetime import datetime, timezone
from importlib.resources import files
from pathlib import Path
from urllib.parse import urlparse

from .config import ConfigurationError, Source, load_sources, valid_url
from .feed import FeedItem, parse_feed
from .ingest import ingest_source, purge_snapshots
from .store import save_items


def add_article(connection: sqlite3.Connection, *, title: str, url: str, summary: str = "", source: str = "Manual research", region: str = "US", published: str = "") -> int:
    title, url, source, region = title.strip(), url.strip(), source.strip(), region.strip().upper()
    if not title or not source:
        raise ValueError("A title and source name are required.")
    if not valid_url(url, https_only=True):
        raise ValueError("The article URL must be a complete HTTPS address.")
    if not 2 <= len(region) <= 8:
        raise ValueError("Use a jurisdiction code of 2 to 8 characters, such as CA or US.")
    published_at = None
    if published:
        try:
            date = datetime.fromisoformat(published.replace("Z", "+00:00"))
            if date.tzinfo is None:
                date = date.replace(tzinfo=timezone.utc)
            published_at = date.astimezone(timezone.utc).isoformat()
        except ValueError as error:
            raise ValueError("Use an ISO publication date, such as 2026-10-01, or leave it blank.") from error
    manual = Source("manual", source, "other_official", "US", region, "rss", "https://manual.invalid/feed", True)
    return save_items(connection, manual, [FeedItem(title, url, summary.strip(), published_at)])


def load_demo(connection: sqlite3.Connection, feed: Path | None = None) -> int:
    payload = feed.read_bytes() if feed else files("consumer_news").joinpath("assets/demo-feed.xml").read_bytes()
    source = Source("demo-ag-news", "Demo Attorney General · fictional", "attorney_general", "US", "DEMO", "rss", "https://agency.example.gov/news/feed.xml", True)
    return save_items(connection, source, parse_feed(payload))


def registry_entries(path: Path) -> list[dict]:
    if not path.exists():
        return []
    load_sources(path, enabled_only=False)
    return json.loads(path.read_text(encoding="utf-8"))


def run_ingestion(connection: sqlite3.Connection, config: Path, snapshots: Path) -> list[dict]:
    sources = load_sources(config)
    if not sources:
        raise ConfigurationError("No enabled feeds. Add an official RSS or Atom feed in Sources first.")
    results = []
    host_next: dict[str, float] = {}
    for source in sources:
        host = urlparse(source.url).hostname or ""
        delay = host_next.get(host, 0) - time.monotonic()
        if delay > 0:
            time.sleep(delay)
        try:
            inserted = ingest_source(connection, source, snapshots)
            results.append({"name": source.name, "inserted": inserted, "error": None})
        except Exception as error:
            # Preserve a per-source failure and continue with the remaining feeds.
            connection.rollback()
            message = str(error)
            connection.execute(
                """INSERT INTO source_state (source_id, last_checked_at, last_success_at, last_error)
                   VALUES (?, ?, '', ?) ON CONFLICT(source_id) DO UPDATE SET
                   last_checked_at=excluded.last_checked_at, last_error=excluded.last_error""",
                (source.id, datetime.now(timezone.utc).isoformat(), message),
            )
            connection.commit()
            results.append({"name": source.name, "inserted": 0, "error": message})
        finally:
            host_next[host] = time.monotonic() + 60 / source.rate_limit_per_minute
    purge_snapshots(snapshots, retention_days=30)
    return results
