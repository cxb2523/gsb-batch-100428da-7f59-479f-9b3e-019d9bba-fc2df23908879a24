#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Imperial AI Archive command-line entrypoints."""

import argparse
import asyncio
import csv
import json
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from analyzer.gemini_analyzer import GeminiAnalyzer
from crawler.engine import ImperialCrawler
from db.supabase_client import SupabaseClient
from run_scanner import CHECKPOINT_FILE, PROGRESS_FILE, Scanner, load_checkpoint, load_progress_checkpoint


async def cmd_scan(args):
    scanner = Scanner(batch_size=args.batch)
    try:
        await scanner.initialize()
        await scanner.run(target_sites=args.target, from_checkpoint=False)
    except KeyboardInterrupt:
        print("\nInterrupted.")


async def cmd_resume(args):
    scanner = Scanner(batch_size=args.batch)
    try:
        await scanner.initialize()

        if Path(PROGRESS_FILE).exists():
            progress = load_progress_checkpoint()
            if not progress:
                print("Could not load progress checkpoint.")
                return

            sites = load_checkpoint() or []
            if not sites:
                print("No discovery checkpoint found.")
                return

            analyzed = progress.get("analyzed", [])
            current_index = progress.get("current_index", len(analyzed))

            print(f"Resuming from progress checkpoint at index {current_index}.")
            scanner.stats["analyzed"] = len(analyzed)
            scanner.stats["saved"] = len(analyzed)
            await scanner.process(sites, resume_from=current_index)
        elif Path(CHECKPOINT_FILE).exists():
            print("Resuming from discovery checkpoint.")
            await scanner.run(from_checkpoint=True)
        else:
            print("No checkpoint found.")
    except KeyboardInterrupt:
        print("\nInterrupted.")


async def cmd_test(args):
    print(f"\nTesting {args.url}")

    async with ImperialCrawler() as crawler:
        crawl_data = await crawler.analyze(args.url)
        print(f"Title: {crawl_data.get('title', 'N/A')}")
        print(f"Status code: {crawl_data.get('status_code')}")

        network = crawl_data.get("network", {})
        providers = network.get("providers_found", [])
        models = network.get("detected_models", [])

        if providers:
            print(f"Providers: {', '.join(providers)}")
        if models:
            print(f"Models: {', '.join(models)}")

        if args.no_analyze:
            return

        analyzer = GeminiAnalyzer()
        analysis = await analyzer.analyze(crawl_data)
        if not analysis:
            print("Analysis failed.")
            return

        output = {
            "crawl": {
                "url": crawl_data.get("url"),
                "title": crawl_data.get("title"),
                "status_code": crawl_data.get("status_code"),
                "providers": providers,
                "models": models,
            },
            "analysis": analysis,
        }

        print(json.dumps(output, indent=2, ensure_ascii=False))

        if args.save:
            filename = f"test_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
            Path(filename).write_text(json.dumps(output, indent=2, ensure_ascii=False), encoding="utf-8")
            print(f"Saved to: {filename}")


async def cmd_stats(args):
    db = SupabaseClient()
    stats = db.get_stats()

    print("\nDatabase Statistics")
    print("=" * 40)
    print(f"Total:        {stats.get('total', 0)}")
    print(f"Verified:     {stats.get('verified', 0)}")
    print(f"Suspicious:   {stats.get('suspicious', 0)}")
    print(f"Fake:         {stats.get('fake', 0)}")
    print(f"Avg trust:    {stats.get('avg_trust_score', 0)}/100")

    if args.detailed:
        records = db.get_all(limit=10000)
        categories = {}
        for record in records:
            category = record.get("category", "Unknown")
            categories[category] = categories.get(category, 0) + 1

        print("\nBy category")
        for category, count in sorted(categories.items(), key=lambda item: item[1], reverse=True):
            print(f"{category:20s} {count:>5}")


async def cmd_api_status(args):
    manager = GeminiAnalyzer().api_manager
    status = manager.get_status()

    print("\nAPI Key Status")
    print("=" * 40)
    print(f"Total keys:    {status['total_keys']}")
    print(f"Available:     {status['available_keys']}")

    for key_data in status["keys"]:
        print(
            f"Key {key_data['id']}: available={key_data['available']} "
            f"cooldown={key_data['cooldown_remaining']}s requests={key_data['requests']}"
        )

    if args.reset:
        manager.reset_all()
        print("\nAll keys reset.")


async def cmd_export(args):
    db = SupabaseClient()
    records = db.get_all(limit=args.limit)
    if not records:
        print("No records found.")
        return

    output_file = args.output or f"scan_export_{datetime.now().strftime('%Y%m%d_%H%M%S')}.{args.format}"
    output_path = Path(output_file)

    if args.format == "json":
        output_path.write_text(json.dumps(records, indent=2, ensure_ascii=False), encoding="utf-8")
    else:
        with output_path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=records[0].keys())
            writer.writeheader()
            writer.writerows(records)

    print(f"Exported {len(records)} records to {output_file}")


def main():
    parser = argparse.ArgumentParser(description="Imperial AI Archive backend CLI")
    subparsers = parser.add_subparsers(dest="command", help="Available commands")

    scan_parser = subparsers.add_parser("scan", help="Run a fresh scan")
    scan_parser.add_argument("--target", type=int, default=1000, help="Target number of sites")
    scan_parser.add_argument("--batch", type=int, default=20, help="Batch size for analysis")

    resume_parser = subparsers.add_parser("resume", help="Resume from checkpoint")
    resume_parser.add_argument("--batch", type=int, default=20, help="Batch size for analysis")

    test_parser = subparsers.add_parser("test", help="Crawl and analyze a single site")
    test_parser.add_argument("url", help="Target URL")
    test_parser.add_argument("--no-analyze", action="store_true", help="Skip Gemini analysis")
    test_parser.add_argument("--save", action="store_true", help="Save the result as JSON")

    stats_parser = subparsers.add_parser("stats", help="Show Supabase archive statistics")
    stats_parser.add_argument("--detailed", action="store_true", help="Show category breakdown")

    api_parser = subparsers.add_parser("api-status", help="Show Gemini key rotation state")
    api_parser.add_argument("--reset", action="store_true", help="Reset cooldowns")

    export_parser = subparsers.add_parser("export", help="Export records from Supabase")
    export_parser.add_argument("--format", choices=["json", "csv"], default="json")
    export_parser.add_argument("--output", help="Output filename")
    export_parser.add_argument("--limit", type=int, default=10000)

    args = parser.parse_args()
    if not args.command:
        parser.print_help()
        return

    commands = {
        "scan": cmd_scan,
        "resume": cmd_resume,
        "test": cmd_test,
        "stats": cmd_stats,
        "api-status": cmd_api_status,
        "export": cmd_export,
    }
    asyncio.run(commands[args.command](args))


if __name__ == "__main__":
    main()
