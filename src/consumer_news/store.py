"""SQLite persistence and weekly Markdown export."""

from __future__ import annotations

import sqlite3
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import quote, urldefrag

from .config import Source, valid_url
from .feed import FeedItem

KEYWORDS = (
    "consumer", "privacy", "data breach", "antitrust", "competition", "insurance",
    "financial", "finance", "bank", "credit", "debt", "fraud", "deceptive",
    "robocall", "subscription", "fee", "settlement", "complaint", "enforcement",
)


def connect(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path, timeout=15)
    connection.row_factory = sqlite3.Row
    connection.executescript("""
        PRAGMA foreign_keys = ON;
        CREATE TABLE IF NOT EXISTS articles (
            id INTEGER PRIMARY KEY,
            source_id TEXT NOT NULL,
            source_name TEXT NOT NULL,
            region TEXT NOT NULL,
            title TEXT NOT NULL,
            url TEXT NOT NULL UNIQUE,
            summary TEXT NOT NULL,
            published_at TEXT,
            retrieved_at TEXT NOT NULL,
            relevance_score INTEGER NOT NULL,
            score_reasons TEXT NOT NULL,
            editorial_status TEXT NOT NULL DEFAULT 'pending'
                CHECK (editorial_status IN ('pending', 'approved', 'rejected'))
        );
        CREATE TABLE IF NOT EXISTS source_state (
            source_id TEXT PRIMARY KEY,
            etag TEXT,
            last_modified TEXT,
            last_checked_at TEXT NOT NULL,
            last_success_at TEXT NOT NULL
        );
    """)
    connection.execute("PRAGMA journal_mode = WAL")
    for table, column, declaration in (
        ("articles", "snapshot_path", "TEXT"),
        ("source_state", "last_error", "TEXT"),
    ):
        columns = {row["name"] for row in connection.execute(f"PRAGMA table_info({table})")}
        if column not in columns:
            connection.execute(f"ALTER TABLE {table} ADD COLUMN {column} {declaration}")
    return connection


def score(item: FeedItem) -> tuple[int, list[str]]:
    haystack = f"{item.title} {item.summary}".lower()
    reasons = [keyword for keyword in KEYWORDS if keyword in haystack]
    return min(len(reasons) * 10, 100), reasons


def save_items(connection: sqlite3.Connection, source: Source, items: list[FeedItem], snapshot_path: str | None = None) -> int:
    now = datetime.now(timezone.utc).isoformat()
    inserted = 0
    for item in items:
        if not item.title.strip() or not valid_url(item.url):
            continue
        relevance, reasons = score(item)
        cursor = connection.execute(
            """INSERT OR IGNORE INTO articles
               (source_id, source_name, region, title, url, summary, published_at,
                retrieved_at, relevance_score, score_reasons, snapshot_path)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (source.id, source.name, source.region, item.title.strip(), urldefrag(item.url)[0], item.summary,
             item.published_at, now, relevance, ", ".join(reasons), snapshot_path),
        )
        inserted += cursor.rowcount
    connection.commit()
    return inserted


VALID_STATUSES = {"pending", "approved", "rejected"}


def list_articles(
    connection: sqlite3.Connection,
    status: str = "pending",
    limit: int = 50,
    query: str | None = None,
    min_score: int = 0,
    *,
    region: str = "",
    source_id: str = "",
    offset: int = 0,
) -> list[sqlite3.Row]:
    """Return the highest-scoring articles in an editorial state."""
    if status not in VALID_STATUSES | {"all"}:
        raise ValueError(f"unknown editorial status: {status}")
    if not 0 <= min_score <= 100 or limit < 1 or offset < 0:
        raise ValueError("invalid queue limits or score")
    escaped = (query or "").replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    pattern = f"%{escaped}%"
    return connection.execute(
        """SELECT * FROM articles WHERE (? = 'all' OR editorial_status = ?)
           AND relevance_score >= ?
           AND (? = '' OR title LIKE ? ESCAPE '\\' OR summary LIKE ? ESCAPE '\\' OR region LIKE ? ESCAPE '\\')
           AND (? = '' OR region = ?) AND (? = '' OR source_id = ?)
           ORDER BY relevance_score DESC, COALESCE(published_at, retrieved_at) DESC, id DESC
           LIMIT ? OFFSET ?""",
        (status, status, min_score, query or "", pattern, pattern, pattern, region, region, source_id, source_id, limit, offset),
    ).fetchall()


def set_status(connection: sqlite3.Connection, article_id: int, status: str) -> None:
    """Apply an editorial decision to one article."""
    if status not in VALID_STATUSES:
        raise ValueError(f"unknown editorial status: {status}")
    cursor = connection.execute(
        "UPDATE articles SET editorial_status = ? WHERE id = ?", (status, article_id)
    )
    if cursor.rowcount != 1:
        raise ValueError(f"article {article_id} does not exist")
    connection.commit()


def newsletter_articles(connection: sqlite3.Connection, days: int = 7, status: str | None = "approved") -> list[sqlite3.Row]:
    if status is not None and status not in VALID_STATUSES:
        raise ValueError(f"unknown editorial status: {status}")
    if not 1 <= days <= 3650:
        raise ValueError("days must be between 1 and 3650")
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    if status is None:
        rows = connection.execute(
            """SELECT * FROM articles
               WHERE retrieved_at >= ? AND editorial_status != 'rejected'
               ORDER BY relevance_score DESC, COALESCE(published_at, retrieved_at) DESC""",
            (cutoff,),
        ).fetchall()
    else:
        rows = connection.execute(
            """SELECT * FROM articles
               WHERE retrieved_at >= ? AND editorial_status = ?
               ORDER BY relevance_score DESC, COALESCE(published_at, retrieved_at) DESC""",
            (cutoff, status),
        ).fetchall()
    return rows


def markdown_text(value: str) -> str:
    """Treat source text as text, including when pasted into a Markdown editor."""
    return re.sub(r"([\\`*_{}\[\]()#+.!|>~-])", r"\\\1", value.replace("<", "&lt;").replace(">", "&gt;"))


def render_markdown(rows: list[sqlite3.Row], title: str = "Consumer Protection Weekly Briefing", include_metadata: bool = True) -> str:
    lines = [f"# {markdown_text(title.strip())}", ""]
    if include_metadata:
        lines.extend(["_Draft for editorial review._", ""])
    for row in rows:
        date = row["published_at"][:10] if row["published_at"] else f"Publication date unknown; discovered {row['retrieved_at'][:10]}"
        story = [
            f"## [{markdown_text(row['title'])}]({quote(row['url'], safe=':/?&=%#@+,$;~')})",
            "",
            f"**{markdown_text(row['source_name'])} ({markdown_text(row['region'])}) · {date}**",
            "",
            markdown_text(row["summary"]) or "_Summary needed._",
            "",
        ]
        if include_metadata:
            story.extend([
                f"_Editorial metadata: article {row['id']}; status {row['editorial_status']}; "
                f"score {row['relevance_score']}; signals: {row['score_reasons'] or 'none'}._",
                "",
            ])
        lines.extend(story)
    return "\n".join(lines)


def export_markdown(
    connection: sqlite3.Connection,
    output: Path,
    days: int = 7,
    status: str | None = None,
    include_metadata: bool = True,
    title: str = "Consumer Protection Weekly Briefing",
) -> int:
    rows = newsletter_articles(connection, days, status)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(render_markdown(rows, title, include_metadata), encoding="utf-8")
    return len(rows)


def statistics(connection: sqlite3.Connection) -> dict[str, int]:
    """Return counts useful for checking editorial workload."""
    result = {status: 0 for status in VALID_STATUSES}
    for row in connection.execute(
        "SELECT editorial_status, COUNT(*) AS count FROM articles GROUP BY editorial_status"
    ):
        result[row["editorial_status"]] = row["count"]
    result["total"] = sum(result.values())
    result["unscored"] = connection.execute(
        "SELECT COUNT(*) FROM articles WHERE relevance_score = 0"
    ).fetchone()[0]
    return result
