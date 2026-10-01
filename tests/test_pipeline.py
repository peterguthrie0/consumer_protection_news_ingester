from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from consumer_news.config import ConfigurationError, Source, load_sources
from consumer_news.feed import parse_feed
from consumer_news.ingest import ingest_source
from consumer_news.store import (
    connect,
    export_markdown,
    list_articles,
    save_items,
    set_status,
    statistics,
)


RSS = b"""<?xml version="1.0"?><rss version="2.0"><channel><title>News</title>
<item><title>AG reaches consumer privacy settlement</title><link>https://ag.example.gov/one</link>
<description><![CDATA[Relief follows a data breach complaint.]]></description>
<pubDate>Mon, 21 Sep 2026 14:00:00 GMT</pubDate></item></channel></rss>"""


class PipelineTests(unittest.TestCase):
    def test_ingest_preserves_snapshot_and_state(self) -> None:
        source = Source("example", "Example AG", "attorney_general", "US", "EX", "rss", "https://ag.example.gov/feed", True)

        class Response:
            headers = {"ETag": '"version-1"', "Last-Modified": "Mon, 21 Sep 2026 14:00:00 GMT"}

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return None

            def read(self, limit: int) -> bytes:
                self.limit = limit
                return RSS

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            database = connect(root / "news.db")
            with patch("urllib.request.urlopen", return_value=Response()) as request:
                self.assertEqual(ingest_source(database, source, root / "snapshots"), 1)
            self.assertEqual(request.call_args.kwargs["timeout"], 30)
            self.assertEqual(len(list((root / "snapshots" / "example").glob("*.xml"))), 1)
            state = database.execute("SELECT * FROM source_state").fetchone()
            self.assertEqual(state["etag"], '"version-1"')
            database.close()

    def test_parse_store_deduplicate_and_export(self) -> None:
        items = parse_feed(RSS)
        self.assertEqual(items[0].published_at, "2026-09-21T14:00:00+00:00")
        source = Source("example", "Example AG", "attorney_general", "US", "EX", "rss", "https://ag.example.gov/feed", True)
        with tempfile.TemporaryDirectory() as directory:
            database = connect(Path(directory) / "news.db")
            self.assertEqual(save_items(database, source, items), 1)
            self.assertEqual(save_items(database, source, items), 0)
            self.assertEqual(len(list_articles(database)), 1)
            set_status(database, 1, "approved")
            self.assertEqual(len(list_articles(database, "pending")), 0)
            self.assertEqual(len(list_articles(database, "approved")), 1)
            self.assertEqual(len(list_articles(database, "approved", query="privacy")), 1)
            self.assertEqual(len(list_articles(database, "approved", min_score=60)), 0)
            self.assertEqual(statistics(database)["approved"], 1)
            output = Path(directory) / "weekly.md"
            self.assertEqual(
                export_markdown(
                    database, output, days=30, status="approved",
                    include_metadata=False, title="Custom Weekly",
                ),
                1,
            )
            draft = output.read_text()
            self.assertIn("# Custom Weekly", draft)
            self.assertIn("AG reaches consumer privacy settlement", draft)
            self.assertNotIn("score 50", draft)
            self.assertNotIn("status approved", draft)
            database.close()

    def test_rejects_unknown_article_decision(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database = connect(Path(directory) / "news.db")
            with self.assertRaisesRegex(ValueError, "does not exist"):
                set_status(database, 404, "approved")
            database.close()

    def test_load_only_enabled_sources(self) -> None:
        fixture = [{"id": "one", "name": "One", "publisher_type": "attorney_general",
                    "jurisdiction": {"country": "US", "region": "NY"},
                    "discovery": {"method": "atom", "url": "https://example.gov/feed"},
                    "enabled": True}]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "sources.json"
            path.write_text(json.dumps(fixture))
            self.assertEqual(load_sources(path)[0].region, "NY")

    def test_rejects_insecure_source(self) -> None:
        fixture = [{"id": "one", "name": "One", "publisher_type": "attorney_general",
                    "jurisdiction": {"country": "US", "region": "NY"},
                    "discovery": {"method": "rss", "url": "http://example.gov/feed"},
                    "enabled": True}]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "sources.json"
            path.write_text(json.dumps(fixture))
            with self.assertRaises(ConfigurationError):
                load_sources(path)


if __name__ == "__main__":
    unittest.main()
