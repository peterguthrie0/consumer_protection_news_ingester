# Consumer Protection News Ingester

A source-first pipeline for collecting, normalizing, deduplicating, and reviewing
consumer-protection legal developments for an email newsletter.

The project should optimize for **coverage and traceability**, not fully automated
publishing. State attorney general (AG) releases are the initial priority because
important state-level matters often do not receive broad secondary coverage.

## Start the browser dashboard

Requires Python 3.11 or newer. From a fresh clone:

```bash
git clone https://github.com/peterguthrie0/consumer_protection_news_ingester.git
cd consumer_protection_news_ingester
python3 -m venv .venv
source .venv/bin/activate
python -m pip install '.[web]'
cp-news --database data/demo.db demo
cp-news --database data/demo.db web
```

Open **http://127.0.0.1:8000**. Leave the terminal running; press Ctrl+C to stop.
If port 8000 is busy, use `cp-news --database data/demo.db web --port 8001`.
The demo is fictional and uses its own database. Repeated demo loads preserve
existing stories and editorial decisions.

### A five-minute browser tutorial

1. **Review queue:** select a story to read its summary, publication date, original
   link, and keyword score. Search or filter by jurisdiction, source, or minimum
   score. The score is a sorting aid; low-scoring stories remain available.
2. **Make decisions:** approve the two fictional legal stories and reject the
   staff-picnic story. The counts update immediately. Use the Approved or Rejected
   tab and **Return to pending** to reopen a decision.
3. **Add a story:** enter an official HTTPS URL, title, agency name, jurisdiction,
   and optional summary/date. A duplicate URL preserves the existing record and
   its decision. Missing publication dates stay visibly unknown.
4. **Newsletter:** choose a title and collection window. The preview and Markdown
   download include only approved stories and omit internal scoring metadata.
   The window uses the date a story was first collected, not its publication date.
   Expand **View and copy the Markdown** to copy it directly. Review the copy and
   formatting when transferring it to Substack; the app does not publish for you.
5. **Sources:** add an official RSS or Atom feed, enable or disable it, then choose
   **Refresh enabled feeds**. Each source shows its last successful check and any
   fetch error. A failed feed does not stop the other feeds.

### Start collecting real stories

Use a separate database for real work. For an initial live source, the included
starter registry uses the FTC's official consumer-protection press-release feed,
listed on its [RSS directory](https://www.ftc.gov/news-events/stay-connected/ftc-rss-feeds).
The feed was checked on October 1, 2026. On a fresh setup:

```bash
cp config/sources.starter.json config/sources.json
cp-news web
```

Then open **Sources** and refresh. If you already have `config/sources.json`, keep
it and add the starter source through the form instead of replacing it. Choose
**Federal agency** for the FTC. Additional state AG sources can be added as their
official feeds are verified; agencies without feeds can be covered through manual
story entry. This starter feed is not nationwide AG coverage.

The default real-work database is `data/news.db`. CLI and browser changes are
shared when both use the same database path. Data and source configuration remain
on your computer and are ignored by Git. To back up your work, stop the app and
copy `data/` and `config/sources.json` to a safe location.

### Local operation

- The web command listens on `127.0.0.1` only. It is a personal local application,
  with form tokens and host/origin checks, without user accounts or public hosting.
- Refreshes are manual. A refresh has a 30-second request timeout per source;
  you can review stories in another tab while it runs. Restarting the server
  interrupts an in-progress refresh; rerunning it deduplicates already saved URLs.
- The CLI remains usable without web dependencies. The dashboard uses FastAPI,
  Jinja templates, and a bundled copy of HTMX 2.0.11; it needs no Node build or CDN.
- To run directly from source during development, use
  `PYTHONPATH=src python -m consumer_news.cli web`.
- After pulling code updates, rerun `python -m pip install '.[web]'`, then restart
  the server and reload any open browser tabs.

## Implemented prototype

The repository includes a Python core with no runtime dependencies that can ingest RSS
and Atom feeds, retain XML snapshots for 30 days, normalize articles into SQLite,
score broad consumer-law signals, deduplicate by URL, and produce a Markdown draft
that can be pasted into Substack. It is intentionally an editorial aid: it does not
publish anything automatically. The optional local dashboard provides the same
workflow in a browser, including feed setup, refresh results, queue pagination,
source/jurisdiction filters, and clean newsletter previews.

The agreed product defaults are:

- weekly publication;
- broad coverage of consumer protection plus privacy, antitrust, insurance, and
  finance when there is a consumer-law connection;
- eventual nationwide coverage, beginning with a smaller prototype;
- manual transfer of the generated Markdown into a custom-designed Substack;
- 30-day raw snapshot retention; and
- manual approve/reject/reopen decisions, to be refined after observing pilot volume.

### Run locally

Python 3.11 or newer is required; the prototype has no runtime dependencies.

To try the complete local workflow immediately, without network access or setup,
run:

```bash
PYTHONPATH=src python -m consumer_news.cli --database data/demo.db demo
PYTHONPATH=src python -m consumer_news.cli --database data/demo.db review
```

This loads three clearly fictional stories from `examples/demo-feed.xml`, writes a
sample briefing to `output/demo-weekly.md`, and prints the pending editorial queue.
The demo is idempotent, so it is safe to run repeatedly.

Try an editorial decision and export only approved stories:

```bash
PYTHONPATH=src python -m consumer_news.cli --database data/demo.db set-status 1 approved
PYTHONPATH=src python -m consumer_news.cli --database data/demo.db export \
  --status approved --days 30 --output output/approved-weekly.md
```

For real feeds, install the command and create a local registry:

```bash
python -m venv .venv
. .venv/bin/activate
python -m pip install -e .
cp config/sources.example.json config/sources.json
# Replace the placeholder with verified official RSS/Atom endpoints and enable them.
cp-news ingest
cp-news review
cp-news export --output output/weekly.md --days 7
```

The default database is `data/news.db`, raw feeds are stored under
`data/snapshots/`, and the weekly draft is written to `output/weekly.md`. These
runtime artifacts are ignored by Git. Re-running ingestion is safe: existing URLs
are not inserted again, and conditional request headers are used when a publisher
provides them.

Useful commands are:

- `cp-news sources` — validate and display enabled sources;
- `cp-news ingest` — fetch all enabled sources while allowing healthy sources to
  continue if one source fails;
- `cp-news review` — show IDs, relevance scores, jurisdictions, titles, and URLs;
- `cp-news set-status ID approved|rejected|pending` — record a decision; and
- `cp-news add` — add an official story found through manual research;
- `cp-news stats` — summarize the current editorial workload; and
- `cp-news export --status approved --clean` — export publication-ready copy with
  internal scoring metadata removed.

## Tutorial: from empty database to weekly draft

This tutorial uses module commands so it works directly from a clone without an
editable installation. Run every command from the repository root.

### 1. Explore the demo

Start with a fresh database and load the fictional feed:

```bash
PYTHONPATH=src python -m consumer_news.cli --database data/tutorial.db demo \
  --output output/tutorial-draft.md
```

Use an unused database filename if you have already run this tutorial and want
fresh IDs and decisions. Open `output/tutorial-draft.md` in a text editor. It includes all three candidates
and their internal scores. The low-scoring staff-picnic item is intentional: the
prototype casts a wide net and leaves the final decision to an editor.

### 2. Inspect and search the review queue

```bash
PYTHONPATH=src python -m consumer_news.cli --database data/tutorial.db stats
PYTHONPATH=src python -m consumer_news.cli --database data/tutorial.db review
PYTHONPATH=src python -m consumer_news.cli --database data/tutorial.db review \
  --query privacy --min-score 20
```

The first column is the article ID used by editorial commands. Scores are sorting
signals, not publication decisions.

### 3. Make editorial decisions

With a new tutorial database, the demo IDs are 1, 2, and 3. Approve the two legal
stories and reject the irrelevant item:

```bash
PYTHONPATH=src python -m consumer_news.cli --database data/tutorial.db set-status 1 approved
PYTHONPATH=src python -m consumer_news.cli --database data/tutorial.db set-status 2 approved
PYTHONPATH=src python -m consumer_news.cli --database data/tutorial.db set-status 3 rejected
PYTHONPATH=src python -m consumer_news.cli --database data/tutorial.db stats
```

Use `pending` instead of `approved` or `rejected` to reopen a decision.

### 4. Add a story found manually

Manual additions let the weekly workflow cover a state even when its website has
no feed adapter yet. Use an official HTTPS URL:

```bash
PYTHONPATH=src python -m consumer_news.cli --database data/tutorial.db add \
  --title "Example consumer-finance enforcement action" \
  --url "https://agency.example.gov/news/example-action" \
  --summary "The agency alleges deceptive lending practices and seeks consumer relief." \
  --source "Example Department of Law" --region EX --published 2026-09-25
```

The command deduplicates the article by URL and leaves it pending for review. The
example URL above is fictional; real newsletter research should always use a
verified official source.

### 5. Export clean copy for Substack

```bash
PYTHONPATH=src python -m consumer_news.cli --database data/tutorial.db export \
  --status approved --days 30 --clean \
  --title "Consumer Protection Weekly — September 25, 2026" \
  --output output/substack-ready.md
```

Open `output/substack-ready.md`, perform the final legal and editorial review, and
paste the Markdown into the Substack editor. The `--clean` flag removes article
IDs, statuses, scores, and keyword signals. The source link, agency, jurisdiction,
date, and summary remain.

### 6. Switch from demo data to live feeds

Copy the source template and replace its placeholder values with verified official
RSS or Atom endpoints:

```bash
cp config/sources.example.json config/sources.json
# Edit config/sources.json, set verified entries to "enabled": true, then run:
PYTHONPATH=src python -m consumer_news.cli sources
PYTHONPATH=src python -m consumer_news.cli ingest
PYTHONPATH=src python -m consumer_news.cli review --min-score 10
```

Do not enable the example URL. The `sources` command confirms what will be fetched,
and `ingest` preserves raw feed snapshots under `data/snapshots/`. Running it again
does not duplicate existing article URLs.

Run all tests, including the browser endpoints:

```bash
python -m pip install '.[web,dev]'
PYTHONPATH=src python -m unittest discover -s tests -v
python -m build
```

The tests use local fixtures and mocked HTTP responses. They cover RSS/Atom parsing,
deduplication, conditional fetches, source failure isolation, editorial decisions,
filtering/pagination, form validation, HTML escaping, request protection, and export.
GitHub Actions runs the suite on Python 3.11 and 3.14. Without the web extras,
browser endpoint tests are skipped; the core tests can still run.

### Current limits

The implementation collects feed metadata and summaries. It does not yet scrape
article pages, download attachments, cluster related multistate actions, generate
AI summaries, schedule ingestion, or publish to Substack. The source registry
schema describes future discovery methods too; the runtime currently accepts RSS
and Atom only. Scheduling fields are advisory. The source-health view detects
failed or overdue fetches, not missing releases or changes in publication volume.
Snapshot files expire after 30 days; article records and decisions remain.

The following sections describe the **future roadmap**, not completed features or
validated pilot results.

## Roadmap: recommended approach

Start with a small, observable pilot rather than attempting all jurisdictions at
once. Add 5–10 diverse state AG offices, run daily for several weeks, and measure:

- **Recall:** did the pipeline capture every relevant item published by each pilot
  source?
- **Precision:** what share of captured items belongs in the newsletter?
- **Latency:** how long after publication did an item enter the review queue?
- **Reliability:** which source adapters failed, changed layout, or produced stale
  results?
- **Editorial value:** which items were selected, and why?

Once the pilot is stable, expand to all states and territories, then add state
regulators, legislatures, courts, and selected federal sources.

## Pipeline

```text
source registry
      |
scheduled discovery (RSS/API/sitemap/HTML)
      |
fetch + immutable raw snapshot
      |
extract + normalize + validate
      |
exact/near-duplicate clustering
      |
rules + relevance scoring
      |
human editorial review
      |
newsletter/CMS export
```

### 1. Maintain a source registry

Treat every monitored publication as data, not hard-coded scraper logic. Each
entry should identify its jurisdiction, publisher, source type, discovery method,
URLs, cadence, and parser configuration. The proposed contract is documented in
[`config/source.schema.json`](config/source.schema.json), with a deliberately
non-runnable template in [`config/sources.example.json`](config/sources.example.json).

Prefer discovery methods in this order:

1. official RSS or Atom feed;
2. official API;
3. official sitemap;
4. paginated listing-page crawl;
5. search-engine discovery only as a monitored fallback.

An official page remains the canonical source even when a search engine or news
article helps discover it.

### 2. Preserve raw evidence

For each fetch, retain the response body (or a content-addressed object-store
reference), URL, retrieval time, HTTP status, content type, ETag, Last-Modified
value, and SHA-256 hash. This makes parser regressions reproducible and provides
an audit trail if an agency edits or removes a release.

Use conditional requests, per-host rate limits, descriptive user-agent contact
information, retry backoff, and source-specific schedules. Respect applicable
terms and access controls; do not circumvent them.

### 3. Normalize into one article model

Store at least:

- canonical URL and discovered URL;
- source and jurisdiction IDs;
- title, deck/summary, body text, and publication/update timestamps;
- retrieval timestamp and raw snapshot reference;
- document type, agencies, companies, industries, statutes, and topics;
- attachment URLs (complaints, settlements, judgments, guidance, and legislation);
- extraction version and confidence; and
- stable content hash plus duplicate-cluster ID.

Keep `published_at`, `updated_at`, and `retrieved_at` separate. Preserve the
publisher's displayed timezone and normalized UTC time. Never silently substitute
retrieval time for an unknown publication time.

### 4. Deduplicate without losing provenance

Deduplicate in layers:

- normalized canonical URL;
- normalized body hash;
- attachment hash;
- near-duplicate similarity on title and body; and
- event clustering using entities, jurisdiction, dates, and matter identifiers.

Multi-state actions should become one story cluster with links to every
participating AG release. Do not discard the individual records; the differences
can reveal state-specific remedies or quotations.

### 5. Rank for editorial review

Begin with explainable rules, not a black-box relevance model. Candidate signals
include:

- enforcement terms (complaint, consent judgment, settlement, assurance, fine);
- consumer topics (privacy, deceptive practices, robocalls, debt, auto, housing,
  health, subscriptions, junk fees, fraud, and product safety);
- concrete legal artifacts or case/matter numbers;
- monetary or injunctive relief;
- multi-state participation;
- novelty relative to recent clusters; and
- source authority and extraction confidence.

Route every candidate to a review queue containing the official link, extracted
text, attachments, score explanation, duplicate cluster, and warnings. Editors
should approve, reject, merge, correct, and annotate items. Those decisions become
labeled data for later tuning.

### 6. Generate summaries safely

Use extractive metadata first. If an LLM drafts a blurb, require it to cite the
supporting source spans internally and constrain it to facts present in the
official material. A human should approve copy before publication. Clearly
distinguish allegations, settlements without admissions, enacted law, proposed
legislation, and final court holdings.

## Suggested implementation

- **Runtime:** Python 3.12; typed source/adaptor interfaces.
- **Scheduling:** a simple scheduled job initially; move to a workflow engine only
  when retry and backfill complexity warrants it.
- **Storage:** PostgreSQL for normalized records and review state; S3-compatible
  object storage for raw responses and attachments.
- **Fetching:** `httpx` with conditional requests, bounded concurrency, retry
  policy, and per-domain throttling.
- **Parsing:** feed parser plus source-specific HTML adapters; use structured data
  and semantic selectors before brittle CSS positions.
- **Observability:** structured logs, run/source metrics, freshness alerts, parser
  error samples, and a dead-letter queue.
- **Delivery:** export only approved clusters to the newsletter platform; keep
  ingestion credentials separate from publishing credentials.

## Delivery phases

### Phase 0 — editorial specification (2–3 days)

Define inclusion/exclusion rules, newsletter cadence, target jurisdictions,
required fields, review workflow, retention policy, and success metrics. Assemble
a hand-labeled set of approximately 100 relevant and irrelevant releases.

### Phase 1 — pilot ingestion (1–2 weeks)

Implement the registry loader, HTTP fetcher, raw snapshot store, RSS adapter, one
generic HTML listing adapter, normalized article storage, and run logging. Onboard
5–10 AG offices chosen to represent RSS, sitemap, conventional HTML, JavaScript,
and document-heavy sites.

### Phase 2 — review and quality (1 week)

Add exact and near-duplicate clustering, explainable relevance rules, a minimal
review queue, manual replay/backfill, and source-freshness alerts. Compare every
pilot source against a manual daily check.

### Phase 3 — coverage expansion (iterative)

Onboard remaining AG offices in batches. Require parser fixtures and an owner for
every source. Add state consumer agencies, financial/insurance/utility regulators,
legislative bill feeds, state supreme and appellate court opinions, and relevant
federal agencies after AG coverage meets its service-level objectives.

### Phase 4 — enrichment and delivery

Extract entities and remedies, cluster multi-state matters, draft source-grounded
summaries, support editorial digests, and integrate the newsletter platform.

## Definition of done for the pilot

- At least five official AG sources run automatically for 14 consecutive days.
- At least 95% of manually identified relevant releases appear in the review queue.
- Median discovery latency is below 24 hours.
- Every published item links to an official source and retained raw snapshot.
- Parser failures and sources with no new content beyond their expected freshness
  window alert an operator.
- An editor can see why an item scored highly and can merge duplicate coverage.
- A backfill can be replayed without creating duplicate articles.

## Decisions to revisit after the pilot

The initial editorial boundaries are recorded above. During the prototype, track
candidate volume, false positives, missed stories, and editing time so that we can
choose a sustainable reviewer workflow. Before expanding nationwide, also decide
whether charities, utilities, securities, labor, Medicaid fraud, and environmental
matters belong in the regular coverage scope or should appear only when they have a
direct consumer-law connection.
