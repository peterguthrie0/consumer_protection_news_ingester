"""Network ingestion and snapshot retention."""

from __future__ import annotations

import hashlib
import sqlite3
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .config import Source
from .feed import parse_feed
from .store import save_items

USER_AGENT = "ConsumerProtectionNewsIngester/0.1 (editorial research)"


def ingest_source(connection: sqlite3.Connection, source: Source, snapshots: Path) -> int:
    state = connection.execute("SELECT * FROM source_state WHERE source_id = ?", (source.id,)).fetchone()
    headers = {"User-Agent": USER_AGENT, "Accept": "application/atom+xml, application/rss+xml, application/xml, text/xml"}
    if state and state["etag"]:
        headers["If-None-Match"] = state["etag"]
    if state and state["last_modified"]:
        headers["If-Modified-Since"] = state["last_modified"]
    request = urllib.request.Request(source.url, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            payload = response.read(10_000_001)
            if len(payload) > 10_000_000:
                raise ValueError(f"{source.id}: feed exceeds 10 MB limit")
            etag = response.headers.get("ETag")
            last_modified = response.headers.get("Last-Modified")
    except urllib.error.HTTPError as error:
        if error.code == 304:
            timestamp = datetime.now(timezone.utc).isoformat()
            connection.execute("UPDATE source_state SET last_checked_at=?, last_success_at=?, last_error=NULL WHERE source_id=?", (timestamp, timestamp, source.id))
            connection.commit()
            return 0
        raise

    now = datetime.now(timezone.utc)
    directory = snapshots / source.id
    directory.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256(payload).hexdigest()
    snapshot = directory / f"{now:%Y%m%dT%H%M%S%fZ}-{digest[:12]}.xml"
    snapshot.write_bytes(payload)
    inserted = save_items(connection, source, parse_feed(payload), str(snapshot.resolve()))
    timestamp = now.isoformat()
    connection.execute(
        """INSERT INTO source_state (source_id, etag, last_modified, last_checked_at, last_success_at)
           VALUES (?, ?, ?, ?, ?)
           ON CONFLICT(source_id) DO UPDATE SET etag=excluded.etag,
           last_modified=excluded.last_modified, last_checked_at=excluded.last_checked_at,
           last_success_at=excluded.last_success_at, last_error=NULL""",
        (source.id, etag, last_modified, timestamp, timestamp),
    )
    connection.commit()
    return inserted


def purge_snapshots(root: Path, retention_days: int = 30) -> int:
    cutoff = datetime.now(timezone.utc) - timedelta(days=retention_days)
    removed = 0
    if not root.exists():
        return removed
    for path in root.glob("*/*.xml"):
        modified = datetime.fromtimestamp(path.stat().st_mtime, timezone.utc)
        if modified < cutoff:
            path.unlink()
            removed += 1
    return removed
