"""Local editorial dashboard. Start with ``cp-news web``."""

from __future__ import annotations

import hmac
import secrets
import threading
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlencode

from fastapi import Depends, FastAPI, Form, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, PlainTextResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.trustedhost import TrustedHostMiddleware

from .config import ConfigurationError, load_sources, write_registry
from .services import add_article, load_demo, registry_entries, run_ingestion
from .store import connect, list_articles, newsletter_articles, render_markdown, set_status, statistics

ROOT = Path(__file__).parent


def create_app(database: Path = Path("data/news.db"), config: Path = Path("config/sources.json"), snapshots: Path = Path("data/snapshots")) -> FastAPI:
    database, config, snapshots = database.resolve(), config.resolve(), snapshots.resolve()
    with closing(connect(database)):
        pass
    app = FastAPI(title="Consumer Protection News Desk", docs_url=None, redoc_url=None, openapi_url=None)
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=["localhost", "127.0.0.1", "[::1]", "testserver"])
    app.mount("/static", StaticFiles(directory=ROOT / "static"), name="static")
    templates = Jinja2Templates(directory=ROOT / "templates")
    csrf = secrets.token_urlsafe(32)
    registry_lock, ingestion_lock = threading.Lock(), threading.Lock()

    def display_date(value):
        if not value:
            return "Not available"
        try:
            return datetime.fromisoformat(value).strftime("%b %d, %Y")
        except ValueError:
            return value

    templates.env.filters["date"] = display_date

    @app.middleware("http")
    async def response_headers(request: Request, call_next):
        if request.method == "POST":
            origin = request.headers.get("origin")
            if request.headers.get("sec-fetch-site") == "cross-site" or (origin and origin != str(request.base_url).rstrip("/")):
                return PlainTextResponse("Cross-site requests are not allowed.", status_code=403)
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "same-origin"
        response.headers["Content-Security-Policy"] = "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'"
        return response

    def verify_csrf(csrf_token: str = Form("")):
        if not hmac.compare_digest(csrf_token, csrf):
            raise HTTPException(403, "This form expired. Reload the page and try again.")

    def page(request: Request, view: str, **context):
        with closing(connect(database)) as db:
            counts = statistics(db)
            is_demo = bool(db.execute("SELECT 1 FROM articles WHERE source_id='demo-ag-news' LIMIT 1").fetchone())
        context.update(request=request, csrf=csrf, view=view, counts=counts, is_demo=is_demo,
                       active=view.split(".")[0], today=datetime.now(timezone.utc).strftime("%A, %B %d, %Y"))
        template = view if request.headers.get("HX-Request") == "true" else "base.html"
        response = templates.TemplateResponse(request=request, name=template, context=context)
        response.headers["Vary"] = "HX-Request"
        response.headers["HX-Push-Url"] = request.url.path + ("?" + request.url.query if request.url.query else "")
        if request.method == "POST":
            response.headers["HX-Push-Url"] = "/sources" if view == "sources.html" else "/add"
        return response

    @app.get("/", response_class=HTMLResponse)
    def queue(request: Request, status: str = Query("pending", pattern="^(pending|approved|rejected|all)$"), query: str = Query("", max_length=200), min_score: int = Query(0, ge=0, le=100), region: str = "", source_id: str = "", page_number: int = Query(1, ge=1), article: int | None = None, notice: str = ""):
        filters = dict(status=status, query=query, min_score=min_score, region=region, source_id=source_id, page_number=page_number)
        with closing(connect(database)) as db:
            rows = list_articles(db, status, 26, query, min_score, region=region, source_id=source_id, offset=(page_number - 1) * 25)
            more = len(rows) > 25
            rows = rows[:25]
            selected = next((row for row in rows if row["id"] == article), rows[0] if rows else None)
            regions = [r[0] for r in db.execute("SELECT DISTINCT region FROM articles ORDER BY region")]
            sources = db.execute("SELECT source_id, MIN(source_name) AS name FROM articles GROUP BY source_id ORDER BY name").fetchall()
        def queue_url(**changes):
            return "/?" + urlencode({**filters, **changes})
        return page(request, "queue.html", rows=rows, selected=selected, filters=filters, more=more,
                    regions=regions, sources=sources, queue_url=queue_url, notice=notice)

    @app.post("/articles/{article_id}/status", dependencies=[Depends(verify_csrf)])
    def decide(article_id: int, status: str = Form(...), queue_status: str = Form("pending"), query: str = Form(""), min_score: int = Form(0), region: str = Form(""), source_id: str = Form(""), page_number: int = Form(1)):
        with closing(connect(database)) as db:
            try:
                set_status(db, article_id, status)
            except ValueError as error:
                raise HTTPException(404 if "does not exist" in str(error) else 422, str(error)) from error
        return RedirectResponse("/?" + urlencode(dict(status=queue_status, query=query, min_score=min_score, region=region, source_id=source_id, page_number=page_number, notice=f"Story marked {status}.")), status_code=303)

    @app.post("/demo", dependencies=[Depends(verify_csrf)])
    def demo():
        with closing(connect(database)) as db:
            if statistics(db)["total"]:
                raise HTTPException(409, "Demo loading is available for an empty database. Use a separate demo database from the README.")
            load_demo(db)
        return RedirectResponse("/?notice=Fictional+demo+loaded.+These+stories+are+for+practice+only.", status_code=303)

    @app.get("/add", response_class=HTMLResponse)
    def add_form(request: Request):
        return page(request, "add.html", values={}, error=None)

    @app.post("/add", dependencies=[Depends(verify_csrf)], response_class=HTMLResponse)
    def add(request: Request, title: str = Form("", max_length=500), url: str = Form("", max_length=2048), summary: str = Form("", max_length=20000), source: str = Form("", max_length=200), region: str = Form("US", max_length=8), published: str = Form("")):
        values = dict(title=title, url=url, summary=summary, source=source, region=region, published=published)
        try:
            with closing(connect(database)) as db:
                inserted = add_article(db, **values)
        except ValueError as error:
            return page(request, "add.html", values=values, error=str(error))
        notice = "Story added to the review queue." if inserted else "That URL is already saved. Its editorial decision has been preserved."
        return RedirectResponse("/?" + urlencode(dict(status="all", query=title[:200], notice=notice)), status_code=303)

    @app.get("/newsletter", response_class=HTMLResponse)
    def newsletter(request: Request, days: int = Query(7, ge=1, le=3650), title: str = Query("Consumer Protection Weekly", min_length=1, max_length=200)):
        with closing(connect(database)) as db:
            rows = newsletter_articles(db, days, "approved")
        markdown = render_markdown(rows, title, include_metadata=False)
        return page(request, "newsletter.html", rows=rows, days=days, title=title, markdown=markdown,
                    download_url="/newsletter.md?" + urlencode(dict(days=days, title=title)))

    @app.get("/newsletter.md")
    def download(days: int = Query(7, ge=1, le=3650), title: str = Query("Consumer Protection Weekly", min_length=1, max_length=200)):
        with closing(connect(database)) as db:
            content = render_markdown(newsletter_articles(db, days, "approved"), title, include_metadata=False)
        return PlainTextResponse(content, media_type="text/markdown", headers={"Content-Disposition": 'attachment; filename="consumer-protection-weekly.md"'})

    def sources_page(request: Request, error=None, notice="", results=None, values=None):
        try:
            sources = load_sources(config, enabled_only=False) if config.exists() else []
        except ConfigurationError as exc:
            sources, error = [], str(exc)
        with closing(connect(database)) as db:
            states = {r["source_id"]: dict(r) for r in db.execute("SELECT * FROM source_state")}
        now = datetime.now(timezone.utc)
        for source in sources:
            state = states.setdefault(source.id, {})
            last = state.get("last_success_at")
            state["stale"] = bool(last and (now - datetime.fromisoformat(last)).total_seconds() > source.expected_freshness_hours * 3600)
        return page(request, "sources.html", sources=sources, states=states, error=error, notice=notice,
                    results=results, values=values or {}, enabled_count=sum(s.enabled for s in sources))

    @app.get("/sources", response_class=HTMLResponse)
    def sources(request: Request, notice: str = ""):
        return sources_page(request, notice=notice)

    @app.post("/sources", dependencies=[Depends(verify_csrf)], response_class=HTMLResponse)
    def add_source(request: Request, source_id: str = Form("", max_length=80), name: str = Form("", max_length=200), region: str = Form("", max_length=8), url: str = Form("", max_length=2048), method: str = Form("rss"), publisher_type: str = Form("attorney_general")):
        values = dict(source_id=source_id, name=name, region=region, url=url, method=method, publisher_type=publisher_type)
        try:
            with registry_lock:
                entries = registry_entries(config)
                entries.append(dict(id=source_id.strip(), name=name.strip(), publisher_type=publisher_type,
                                    jurisdiction=dict(country="US", region=region.strip().upper()),
                                    discovery=dict(method=method, url=url.strip()), enabled=True))
                write_registry(config, entries)
        except (ValueError, OSError) as error:
            return sources_page(request, error=str(error), values=values)
        return RedirectResponse("/sources?" + urlencode(dict(notice="Feed saved. Refresh enabled feeds to collect stories.")), status_code=303)

    @app.post("/sources/{source_id}/toggle", dependencies=[Depends(verify_csrf)])
    def toggle(request: Request, source_id: str, enabled: bool = Form(...)):
        try:
            with registry_lock:
                entries = registry_entries(config)
                entry = next((item for item in entries if item["id"] == source_id), None)
                if entry is None:
                    raise HTTPException(404, "Source not found.")
                entry["enabled"] = enabled
                write_registry(config, entries)
        except (ValueError, OSError) as error:
            return sources_page(request, error=str(error))
        return RedirectResponse("/sources", status_code=303)

    @app.post("/ingest", dependencies=[Depends(verify_csrf)])
    def ingest(request: Request):
        if not ingestion_lock.acquire(blocking=False):
            return sources_page(request, error="A refresh is already running. Please wait before starting another.")
        try:
            with closing(connect(database)) as db:
                results = run_ingestion(db, config, snapshots)
            return sources_page(request, results=results, notice=f"Refresh finished. {sum(r['inserted'] for r in results)} new stories added.")
        except (ValueError, OSError) as error:
            return sources_page(request, error=str(error))
        finally:
            ingestion_lock.release()

    return app
