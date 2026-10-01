from __future__ import annotations

import json
import re
import tempfile
import unittest
import urllib.error
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

try:
    from fastapi.testclient import TestClient
    from consumer_news.web import create_app
except ImportError:
    TestClient = None

from consumer_news.config import ConfigurationError, Source, load_sources
from consumer_news.feed import FeedItem, parse_feed
from consumer_news.ingest import ingest_source
from consumer_news.services import add_article, run_ingestion
from consumer_news.store import connect, list_articles, newsletter_articles, render_markdown, save_items, set_status


@unittest.skipIf(TestClient is None, 'Install .[web,dev] to run browser endpoint tests')
class DashboardTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.database = self.root / "news.db"
        self.config = self.root / "sources.json"
        self.client = TestClient(create_app(self.database, self.config, self.root / "snapshots"))
        self.token = re.search(r'name="csrf_token" value="([^"]+)"', self.client.get("/").text)[1]

    def tearDown(self):
        self.client.close()
        self.temp.cleanup()

    def post(self, path, **values):
        return self.client.post(path, data={"csrf_token": self.token, **values})

    def test_complete_editorial_workflow_and_reopen(self):
        self.assertIn("Your news desk starts here", self.client.get("/").text)
        self.assertEqual(self.post("/demo").status_code, 200)
        self.assertEqual(self.post("/demo").status_code, 409)
        queue = self.client.get("/?query=privacy&min_score=20")
        self.assertIn("consumer privacy settlement", queue.text)
        self.assertNotIn("annual staff picnic photographs", queue.text)
        approved = self.post("/articles/1/status", status="approved")
        self.assertEqual(approved.status_code, 200)
        self.assertIn("Story marked approved", approved.text)
        self.post("/articles/3/status", status="rejected")
        preview = self.client.get("/newsletter?days=30&title=My+Weekly")
        self.assertIn("My Weekly", preview.text)
        self.assertIn("consumer privacy settlement", preview.text)
        exported = self.client.get("/newsletter.md?days=30&title=My+Weekly")
        self.assertIn("attachment;", exported.headers["content-disposition"])
        self.assertIn("# My Weekly", exported.text)
        self.assertNotIn("Editorial metadata", exported.text)
        self.assertNotIn("staff picnic", exported.text)
        self.assertNotIn("subscription-competition", exported.text)
        self.post("/articles/1/status", status="pending")
        self.assertNotIn("privacy-settlement", self.client.get("/newsletter.md?days=30").text)

    def test_validation_duplicate_and_unknown_publication(self):
        invalid = self.post("/add", title="Saved title", url="https://", source="Agency", region="CA")
        self.assertIn("complete HTTPS", invalid.text)
        self.assertIn('value="Saved title"', invalid.text)
        fields = dict(title="New privacy action", url="https://agency.example.gov/action", summary="A complaint.", source="Agency", region="CA")
        self.post("/add", **fields)
        self.post("/articles/1/status", status="approved")
        duplicate = self.post("/add", **fields)
        self.assertIn("already saved", duplicate.text)
        with closing(connect(self.database)) as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM articles").fetchone()[0], 1)
            self.assertEqual(db.execute("SELECT editorial_status FROM articles").fetchone()[0], "approved")
        self.assertIn("Publication date unknown; discovered", self.client.get("/newsletter.md").text)
        invalid_date = self.post("/add", **fields, published="yesterday")
        self.assertIn("ISO publication date", invalid_date.text)

    def test_long_manual_title_has_a_valid_redirect(self):
        response = self.post("/add", title="Long agency headline " * 15, url="https://example.gov/long", source="Agency", region="US")
        self.assertEqual(response.status_code, 200)
        self.assertIn("Story added to the review queue", response.text)

    def test_csrf_host_validation_and_markup_escaping(self):
        self.assertEqual(self.client.post("/demo").status_code, 403)
        self.assertEqual(self.client.post("/demo", data={"csrf_token": self.token}, headers={"origin": "https://evil.example"}).status_code, 403)
        self.assertEqual(self.client.get("/", headers={"host": "evil.example"}).status_code, 400)
        attack = '<script>alert("bad")</script>'
        self.post("/add", title=attack, url="https://agency.example.gov/escape", summary=attack, source="Agency", region="US")
        page = self.client.get("/")
        self.assertNotIn(attack, page.text)
        self.assertIn("&lt;script&gt;", page.text)
        self.assertIn("frame-ancestors 'none'", page.headers["content-security-policy"])
        self.assertEqual(page.headers["referrer-policy"], "same-origin")

    def test_htmx_fragments_and_error_statuses(self):
        self.post("/demo")
        page = self.client.get("/?query=privacy", headers={"HX-Request": "true"})
        self.assertNotIn("<!doctype", page.text)
        self.assertIn("STORY DETAILS", page.text)
        self.assertEqual(page.headers["HX-Push-Url"], "/?query=privacy")
        self.assertEqual(self.client.get("/?min_score=-1").status_code, 422)
        self.assertEqual(self.client.get("/newsletter?days=0").status_code, 422)
        self.assertEqual(self.post("/articles/404/status", status="approved").status_code, 404)
        self.assertEqual(self.post("/articles/1/status", status="invalid").status_code, 422)

    def test_source_management_and_refresh(self):
        data = dict(source_id="ca-ag", name="California AG", region="CA", url="https://agency.example.gov/feed", method="rss")
        self.assertIn("Feed saved", self.post("/sources", **data).text)
        self.assertEqual(len(load_sources(self.config)), 1)
        self.assertIn("duplicate id", self.post("/sources", **data).text)
        self.assertEqual(len(load_sources(self.config)), 1)
        self.post("/sources/ca-ag/toggle", enabled="false")
        self.assertEqual(load_sources(self.config), [])
        self.assertIn("No enabled feeds", self.post("/ingest").text)
        self.post("/sources/ca-ag/toggle", enabled="true")
        with patch("consumer_news.services.ingest_source", side_effect=ValueError("Feed is unavailable")):
            refreshed = self.post("/ingest")
        self.assertIn("Feed is unavailable", refreshed.text)
        self.assertIn("Fetch failed", refreshed.text)
        self.assertEqual(refreshed.headers["HX-Push-Url"], "/sources")

    def test_pagination_and_static_assets(self):
        with closing(connect(self.database)) as db:
            source = Source("test", "Test AG", "attorney_general", "US", "NY", "rss", "https://example.gov/feed", True)
            save_items(db, source, [FeedItem(f"Story {n}", f"https://example.gov/{n}", "consumer", None) for n in range(30)])
        self.assertIn("Next →", self.client.get("/").text)
        second = self.client.get("/?page_number=2")
        self.assertIn("5 STORIES ON THIS PAGE", second.text)
        self.assertIn("← Previous", second.text)
        for asset in ("style.css", "app.js", "htmx.min.js"):
            self.assertEqual(self.client.get("/static/" + asset).status_code, 200)


class ReliabilityTests(unittest.TestCase):
    def test_html_response_is_a_feed_failure(self):
        with self.assertRaisesRegex(ValueError, "not an RSS"):
            parse_feed(b"<html><body>Service unavailable</body></html>")

    def test_atom_dates_and_unsafe_links(self):
        payload = b'<feed xmlns="http://www.w3.org/2005/Atom"><entry><title>A privacy complaint</title><link href="https://example.gov/story"/><summary>Consumer relief</summary><updated>2026-09-25T10:00:00-07:00</updated></entry></feed>'
        items = parse_feed(payload)
        self.assertEqual(items[0].published_at, "2026-09-25T17:00:00+00:00")
        with tempfile.TemporaryDirectory() as directory, closing(connect(Path(directory) / "news.db")) as db:
            source = Source("test", "Test", "attorney_general", "US", "NY", "atom", "https://example.gov/feed", True)
            self.assertEqual(save_items(db, source, items + [FeedItem("Bad", "javascript:alert(1)", "", None)]), 1)

    def test_conditional_request_updates_health_and_preserves_articles(self):
        with tempfile.TemporaryDirectory() as directory, closing(connect(Path(directory) / "news.db")) as db:
            db.execute("INSERT INTO source_state VALUES (?, ?, ?, ?, ?, ?)", ("test", '"version"', None, "2020-01-01", "2020-01-01", "old error"))
            db.commit()
            source = Source("test", "Test", "attorney_general", "US", "NY", "rss", "https://example.gov/feed", True)
            with patch("urllib.request.urlopen", side_effect=urllib.error.HTTPError(source.url, 304, "Not Modified", {}, None)) as request:
                self.assertEqual(ingest_source(db, source, Path(directory) / "snapshots"), 0)
            self.assertEqual(request.call_args.args[0].get_header("If-none-match"), '"version"')
            state = db.execute("SELECT * FROM source_state").fetchone()
            self.assertIsNone(state["last_error"])
            self.assertTrue(state["last_success_at"].startswith(str(datetime.now(timezone.utc).year)))

    def test_individual_feed_failure_does_not_stop_remaining_sources(self):
        with tempfile.TemporaryDirectory() as directory, closing(connect(Path(directory) / "news.db")) as db:
            config = Path(directory) / "sources.json"
            config.write_text(json.dumps([dict(id=f"feed-{n}", name=f"Feed {n}", publisher_type="attorney_general", jurisdiction=dict(country="US", region="CA"), discovery=dict(method="rss", url=f"https://agency{n}.example.gov/feed"), enabled=True) for n in range(2)]))
            with patch("consumer_news.services.ingest_source", side_effect=[ValueError("bad feed"), 3]):
                results = run_ingestion(db, config, Path(directory) / "snapshots")
            self.assertEqual(results[0]["error"], "bad feed")
            self.assertEqual(results[1]["inserted"], 3)

    def test_literal_search_and_markdown_text(self):
        with tempfile.TemporaryDirectory() as directory, closing(connect(Path(directory) / "news.db")) as db:
            add_article(db, title="100% consumer relief [draft]", url="https://example.gov/relief", source="Test", region="US", summary="<script>bad</script>")
            add_article(db, title="Other action", url="https://example.gov/other", source="Test", region="US")
            self.assertEqual(len(list_articles(db, query="%")), 1)
            set_status(db, 1, "approved")
            markdown = render_markdown(newsletter_articles(db), include_metadata=False)
            self.assertNotIn("<script>", markdown)
            self.assertIn(r"\[draft\]", markdown)
            self.assertIn("Publication date unknown", markdown)


if __name__ == "__main__":
    unittest.main()
