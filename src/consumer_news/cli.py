"""Command-line entry point."""

from __future__ import annotations

import argparse
from pathlib import Path

from .config import load_sources
from .store import (
    connect,
    export_markdown,
    list_articles,
    set_status,
    statistics,
)
from .services import add_article, load_demo, run_ingestion


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description="Collect official consumer-protection news")
    result.add_argument("--config", type=Path, default=Path("config/sources.json"))
    result.add_argument("--database", type=Path, default=Path("data/news.db"))
    result.add_argument("--snapshots", type=Path, default=Path("data/snapshots"))
    subcommands = result.add_subparsers(dest="command", required=True)
    subcommands.add_parser("ingest", help="fetch every enabled source")
    subcommands.add_parser("sources", help="validate and display enabled sources")
    export = subcommands.add_parser("export", help="write a Substack-friendly Markdown draft")
    export.add_argument("--output", type=Path, default=Path("output/weekly.md"))
    export.add_argument("--days", type=int, default=7)
    export.add_argument("--status", choices=("pending", "approved", "rejected"))
    export.add_argument("--clean", action="store_true", help="omit internal editorial metadata")
    export.add_argument("--title", default="Consumer Protection Weekly Briefing")
    review = subcommands.add_parser("review", help="show the editorial queue")
    review.add_argument("--status", choices=("pending", "approved", "rejected"), default="pending")
    review.add_argument("--limit", type=int, default=50)
    review.add_argument("--query", help="search title, summary, or jurisdiction")
    review.add_argument("--min-score", type=int, default=0)
    decide = subcommands.add_parser("set-status", help="approve, reject, or reopen an article")
    decide.add_argument("article_id", type=int)
    decide.add_argument("status", choices=("pending", "approved", "rejected"))
    demo = subcommands.add_parser("demo", help="load sample stories and write a sample briefing")
    demo.add_argument("--feed", type=Path)
    demo.add_argument("--output", type=Path, default=Path("output/demo-weekly.md"))
    add = subcommands.add_parser("add", help="manually add a discovered article")
    add.add_argument("--title", required=True)
    add.add_argument("--url", required=True)
    add.add_argument("--summary", default="")
    add.add_argument("--source", default="Manual research")
    add.add_argument("--region", default="US")
    add.add_argument("--published", help="ISO-8601 date or timestamp")
    subcommands.add_parser("stats", help="show editorial queue counts")
    web = subcommands.add_parser("web", help="open the local editorial dashboard")
    web.add_argument("--port", type=int, default=8000)
    return result


def main() -> None:
    args = parser().parse_args()
    if args.command == "web":
        try:
            import uvicorn
            from .web import create_app
        except ImportError:
            raise SystemExit('Install the browser dependencies with: python -m pip install -e ".[web]"')
        if not 1 <= args.port <= 65535:
            raise SystemExit("port must be between 1 and 65535")
        print(f"Open http://127.0.0.1:{args.port} in your browser")
        uvicorn.run(create_app(args.database, args.config, args.snapshots), host="127.0.0.1", port=args.port)
        return
    connection = connect(args.database)
    try:
        execute(args, connection)
    except (ValueError, OSError) as error:
        raise SystemExit(str(error)) from error
    finally:
        connection.close()


def execute(args, connection) -> None:
    if args.command == "sources":
        sources = load_sources(args.config)
        for source in sources:
            print(f"{source.id}\t{source.region}\t{source.method}\t{source.url}")
        print(f"{len(sources)} enabled source(s)")
    elif args.command == "ingest":
        results = run_ingestion(connection, args.config, args.snapshots)
        for result in results:
            print(f"{result['name']}: " + (f"ERROR: {result['error']}" if result['error'] else f"{result['inserted']} new articles"))
        failures = sum(bool(result["error"]) for result in results)
        print(f"complete: {sum(result['inserted'] for result in results)} new articles; {failures} failures")
        if failures:
            raise SystemExit(1)
    elif args.command == "export":
        count = export_markdown(
            connection, args.output, args.days, args.status,
            include_metadata=not args.clean, title=args.title,
        )
        print(f"exported {count} articles to {args.output}")
    elif args.command == "review":
        rows = list_articles(
            connection, args.status, args.limit, args.query, args.min_score
        )
        for row in rows:
            print(f"{row['id']:>4}  {row['relevance_score']:>3}  {row['region']:<4}  {row['title']}")
            print(f"      {row['url']}")
        print(f"{len(rows)} {args.status} article(s)")
    elif args.command == "set-status":
        set_status(connection, args.article_id, args.status)
        print(f"article {args.article_id}: {args.status}")
    elif args.command == "demo":
        inserted = load_demo(connection, args.feed)
        count = export_markdown(connection, args.output, days=30)
        print(f"demo ready: {inserted} new articles; {count} exported to {args.output}")
    elif args.command == "add":
        inserted = add_article(connection, title=args.title, url=args.url, summary=args.summary, source=args.source, region=args.region, published=args.published or "")
        print("article added" if inserted else "article already exists")
    else:
        counts = statistics(connection)
        print("  ".join(f"{name}: {count}" for name, count in counts.items()))


if __name__ == "__main__":
    main()
