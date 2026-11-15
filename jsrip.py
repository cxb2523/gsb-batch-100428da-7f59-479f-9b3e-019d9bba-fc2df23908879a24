#!/usr/bin/env python3
"""
jsrip - JavaScript Ripper and Analyzer
A tool for crawling sites, downloading JavaScript, and analyzing for secrets & endpoints.
"""

import sys
import json
import argparse
from collections import Counter
from datetime import datetime
from pathlib import Path

from urllib.parse import urlparse

from core.analyzer import JSAnalyzer
from core.crawler import PlaywrightCrawler
from core.enumerator import SubdomainEnumerator, recon_summary
from core.classifier import classify_all
from core.inspector import inspect_app_files
from utils.reporter import ReportGenerator
from utils.logger import setup_logger


DEFAULT_OUTPUT_BASENAME = "./output/jsrip_output"


def build_parser():
    """Build the argument parser. Split out so tests can inspect it."""
    parser = argparse.ArgumentParser(
        description="jsrip - Find secrets and endpoints in JavaScript files",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Scan a single website
  python3 jsrip.py -u https://example.com

  # Scan multiple websites
  python3 jsrip.py -l urls.txt

  # Custom output folder
  python3 jsrip.py -u https://example.com -o ./my-scan

  # Show browser while scanning
  python3 jsrip.py -u https://example.com --show-browser
""",
    )

    # Input options
    input_group = parser.add_mutually_exclusive_group(required=True)
    input_group.add_argument("-u", "--url", help="Website URL to scan")
    input_group.add_argument("-l", "--list", help="File with URLs (one per line)")

    # Output options
    parser.add_argument(
        "-o",
        "--output",
        default=DEFAULT_OUTPUT_BASENAME,
        help="Output folder (default: auto-generated with timestamp)",
    )
    parser.add_argument(
        "-f",
        "--format",
        nargs="+",
        choices=["json", "md", "html", "csv", "pdf"],
        default=["json"],
        help="Report formats (default: json). pdf needs pandoc, "
             "markdown-pdf or weasyprint on the system.",
    )

    # Simple options
    parser.add_argument(
        "--show-browser",
        action="store_true",
        help="Show browser window during scan (useful for debugging)",
    )
    parser.add_argument(
        "--depth",
        type=int,
        default=2,
        help="How deep to crawl links (default: 2)",
    )
    parser.add_argument(
        "--max-pages",
        type=int,
        default=500,
        help="Maximum pages to visit (default: 500)",
    )
    parser.add_argument(
        "--include-external",
        action="store_true",
        help="Include URLs from external domains",
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="Show detailed progress",
    )

    # Advanced options (optional)
    parser.add_argument("--headers", help="JSON file with custom headers")
    parser.add_argument("--cookies", help="JSON file with cookies")
    parser.add_argument("--user-agent", help="Custom user agent string")

    # Subdomain enumeration
    sub_group = parser.add_argument_group("Subdomain enumeration")
    sub_group.add_argument(
        "--subdomains",
        action="store_true",
        help="Enumerate subdomains before JS crawling (crt.sh + DNS brute-force)",
    )
    sub_group.add_argument(
        "--wordlist",
        help="Custom wordlist for DNS brute-force (default: built-in ~7k list)",
    )

    return parser


def parse_arguments():
    """Parse command line arguments."""
    return build_parser().parse_args()


def normalise_url(raw: str):
    """Normalise a user-supplied target into a crawlable http(s) URL.

    Returns None if the input can't be a target. A scheme-less host is the
    most common CLI typo, and without this the crawler silently scopes it
    out and reports "0 secrets" - indistinguishable from a clean target.
    """
    if not raw:
        return None
    raw = raw.strip()
    if not raw or any(c.isspace() for c in raw):
        return None

    if "://" in raw:
        scheme = raw.split("://", 1)[0].lower()
        if scheme not in ("http", "https"):
            return None
        candidate = raw
    else:
        # bare host[:port][/path]; reject paths and scheme-like prefixes
        if raw.startswith("/"):
            return None
        hostpart = raw.split("/", 1)[0]
        if ":" in hostpart:
            host, _, port = hostpart.partition(":")
            # "javascript:alert(1)" is a scheme, not a host:port
            if not host or not port.isdigit():
                return None
        candidate = f"https://{raw}"

    parsed = urlparse(candidate)
    if parsed.scheme not in ("http", "https"):
        return None
    if not parsed.hostname:
        return None
    return candidate


def load_urls(args):
    """Load URLs from file or single URL argument."""
    if args.url:
        url = normalise_url(args.url)
        if not url:
            print(f"  Error: not a usable target URL: {args.url!r}")
            sys.exit(1)
        if url != args.url.strip():
            print(f"  Note: interpreting {args.url.strip()!r} as {url}")
        return [url]

    if args.list:
        try:
            with open(args.list, "r", encoding="utf-8") as f:
                urls = [
                    line.strip()
                    for line in f
                    if line.strip() and not line.strip().startswith("#")
                ]
            good, bad = [], []
            for u in urls:
                n = normalise_url(u)
                (good if n else bad).append(n or u)
            if bad:
                print(f"  Warning: skipped {len(bad)} unusable line(s), "
                      f"e.g. {bad[0]!r}")
            if not good:
                print(f"  Error: no usable target URLs in {args.list}")
                sys.exit(1)
            return good
        except FileNotFoundError:
            print(f"  Error: File not found: {args.list}")
            sys.exit(1)
        except Exception as e:
            print(f"  Error reading URL list: {e}")
            sys.exit(1)

    return []


def load_json_file(filepath, file_type):
    """Load JSON file for headers or cookies."""
    if not filepath:
        return None

    try:
        with open(filepath, "r", encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        print(f"  Warning: {file_type} file not found: {filepath}")
        return None
    except json.JSONDecodeError as e:
        print(f"  Warning: Invalid JSON in {file_type} file: {e}")
        return None


def extract_domains(urls: list) -> set:
    """Extract unique registrable domains from URLs or bare domains."""
    from core.crawler import registrable_domain

    domains = set()
    for raw in urls:
        raw = raw.strip()
        if not raw:
            continue
        if "://" in raw:
            host = urlparse(raw).hostname or ""
        else:
            host = raw.split("/")[0]
        rd = registrable_domain(host)
        if rd:
            domains.add(rd)
    return domains


def print_recon_summary(recon: dict):
    """Print subdomain recon results to terminal."""
    print("\n" + "=" * 60)
    print("  SUBDOMAIN ENUMERATION")
    print("=" * 60)

    total_alive = 0
    total_found = 0

    for domain, results in recon.items():
        alive = [r for r in results if r.alive]
        dead = [r for r in results if not r.alive and r.classification == "dead"]
        wildcard = [r for r in results if r.classification == "wildcard"]
        unresolved = [r for r in results if r.classification == "unresolved"]
        total_alive += len(alive)
        total_found += len(results)

        # Count by whatever sources actually contributed, so a new CT source
        # (certspotter fallback) is reported rather than silently uncounted.
        by_source = Counter(r.source for r in results)
        source_line = ", ".join(f"{name} ({n})" for name, n in sorted(by_source.items()))

        print(f"\n  {domain} - {len(results)} found, {len(alive)} alive")
        print(f"    Sources: {source_line}")
        if wildcard:
            print(f"    Wildcard-filtered: {len(wildcard)}")

        if alive:
            print(f"\n    ALIVE:")
            for r in alive:
                status = f"[{r.status_code}]" if r.status_code else ""
                print(f"      {r.subdomain:<40} {r.ip:<16} {status}")

        if wildcard:
            print(f"\n    WILDCARD (resolved to a wildcard IP, not counted):")
            for r in wildcard[:10]:
                print(f"      {r.subdomain:<40} {r.ip}")
            if len(wildcard) > 10:
                print(f"      ... and {len(wildcard) - 10} more")

        if dead:
            print(f"\n    DEAD (resolved, no HTTP):")
            for r in dead[:10]:
                print(f"      {r.subdomain:<40} {r.ip}")
            if len(dead) > 10:
                print(f"      ... and {len(dead) - 10} more")

        if unresolved:
            print(f"\n    UNRESOLVED ({len(unresolved)}):")
            for r in unresolved[:10]:
                print(f"      {r.subdomain}")
            if len(unresolved) > 10:
                print(f"      ... and {len(unresolved) - 10} more")

    print(f"\n  Total: {total_found} subdomains, {total_alive} alive")
    print("=" * 60)


def save_recon_report(recon: dict, output_dir):
    """Save subdomain recon as JSON."""
    reports_dir = Path(output_dir) / "reports"
    reports_dir.mkdir(parents=True, exist_ok=True)

    data = {domain: recon_summary(results) for domain, results in recon.items()}

    with open(reports_dir / "subdomains.json", "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def auto_output_dir(user_arg: str) -> Path:
    """Create a timestamped output dir if user didn't supply a custom name."""
    if user_arg == DEFAULT_OUTPUT_BASENAME:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        out = Path(f"{DEFAULT_OUTPUT_BASENAME}_{timestamp}")
    else:
        out = Path(user_arg)
    out.mkdir(parents=True, exist_ok=True)
    return out


async def main():
    """Main execution function."""
    args = parse_arguments()

    # Output dir (timestamped by default)
    output_dir = auto_output_dir(args.output)

    # Logging
    logger = setup_logger(output_dir, verbose=args.verbose)
    
    print("\n" + "=" * 60)
    print("  jsrip - JavaScript Ripper & Analyzer")
    print("=" * 60)

    # URLs
    urls = load_urls(args)
    print(f"  Targets: {len(urls)}")

    # Subdomain enumeration (before crawl)
    recon_data = None
    recon_enabled = bool(args.subdomains)
    # Stages: crawl, analyze, intel, reports (+1 up front if recon runs)
    stages = 5 if recon_enabled else 4
    stage_offset = 1 if recon_enabled else 0
    if args.subdomains:
        domains = extract_domains(urls)
        if domains:
            print(f"\n  [1/{stages}] Enumerating subdomains for {len(domains)} domain(s)...")
            enumerator = SubdomainEnumerator(
                domains, logger, wordlist_path=args.wordlist
            )
            recon_data = await enumerator.enumerate()
            print_recon_summary(recon_data)
            save_recon_report(recon_data, output_dir)

            # Merge alive subdomains into URL list
            alive_urls = []
            for domain_results in recon_data.values():
                for r in domain_results:
                    if r.alive:
                        alive_urls.append(f"https://{r.subdomain}")

            original_count = len(urls)
            urls = list(dict.fromkeys(urls + alive_urls))  # dedupe, preserve order
            print(f"\n  Proceeding with {len(urls)} URLs ({original_count} original + {len(urls) - original_count} discovered)")
        else:
            print("  Warning: Could not extract domains from input")

    # Headers / Cookies
    headers = load_json_file(args.headers, "headers")
    cookies = load_json_file(args.cookies, "cookies")

    # Config (simplified)
    config = {
        "output_dir": output_dir,
        "user_agent": args.user_agent or "jsrip/1.0 (Bug Bounty Scanner)",
        "headers": headers,
        "cookies": cookies,
        "headless": not args.show_browser,
        "max_depth": args.depth,
        "max_pages": args.max_pages,
        "timeout": 30,
        "beautify": True,
        "include_external_endpoints": args.include_external,
        "verbose": args.verbose,
    }

    # Results container
    scan_results = {
        "scan_info": {
            "start_time": datetime.now().isoformat(),
            "urls_count": len(urls),
            "urls": urls,
        },
        "statistics": {
            "js_files_found": 0,
            "secrets_found": 0,
            "endpoints_found": 0,
        },
        "findings": {"secrets": [], "endpoints": [], "js_files": []},
    }

    if recon_data:
        scan_results["subdomain_recon"] = {}
        for domain, results in recon_data.items():
            scan_results["subdomain_recon"][domain] = recon_summary(results)

    try:
        print(f"\n  [{1 + stage_offset}/{stages}] Crawling and ripping JavaScript...")
        crawler = PlaywrightCrawler(config, logger)
        js_files = await crawler.crawl_urls(urls)
        scan_results["statistics"]["js_files_found"] = len(js_files)
        scan_results["findings"]["js_files"] = js_files
        print(f"        {len(js_files)} JS files pulled")

        # Stage 2: Analyze
        print(f"\n  [{2 + stage_offset}/{stages}] Analyzing for secrets and endpoints...")
        analyzer = JSAnalyzer(
            output_dir=output_dir,
            beautify=True,
            verbose=config["verbose"],
            include_external_endpoints=args.include_external,
        )
        analysis_results = analyzer.analyze_files(js_files)

        scan_results["statistics"]["secrets_found"] = len(
            analysis_results.get("secrets", [])
        )
        scan_results["statistics"]["endpoints_found"] = len(
            analysis_results.get("endpoints", [])
        )
        scan_results["findings"]["secrets"] = analysis_results.get("secrets", [])
        scan_results["findings"]["endpoints"] = analysis_results.get("endpoints", [])

        print(f"        {scan_results['statistics']['secrets_found']} potential secrets")
        print(f"        {scan_results['statistics']['endpoints_found']} endpoints")

        # Stage 3: JS Intel
        print(f"\n  [{3 + stage_offset}/{stages}] Running JS intel pass...")
        classify_all(js_files)  # tags each file in-place with 'category'
        intel = inspect_app_files(js_files, scan_results["findings"]["endpoints"])
        scan_results["findings"]["intel"] = intel
        scan_results["statistics"]["app_files"] = len(intel["app_files"])
        scan_results["statistics"]["intel_sinks"] = len(intel["sinks"])
        scan_results["statistics"]["intel_gates"] = len(intel["gates"])
        scan_results["statistics"]["intel_flags"] = len(intel["flags"])
        scan_results["statistics"]["intel_postmessage"] = len(intel["postmessage_no_origin"])
        print(f"        {len(intel['app_files'])} app files (rest classified as vendor/inline)")
        print(f"        sinks: {len(intel['sinks'])}, gates: {len(intel['gates'])}, "
              f"flags: {len(intel['flags'])}, postMessage(no-origin): {len(intel['postmessage_no_origin'])}")

        # Stage 4: Reports
        print(f"\n  [{4 + stage_offset}/{stages}] Generating reports...")

        scan_results["scan_info"]["end_time"] = datetime.now().isoformat()

        # Duration
        start = datetime.fromisoformat(scan_results["scan_info"]["start_time"])
        end = datetime.fromisoformat(scan_results["scan_info"]["end_time"])
        scan_results["scan_info"]["duration"] = str(end - start)

        # Reports
        reporter = ReportGenerator(output_dir, logger)
        for fmt in args.format:
            reporter.generate_report(scan_results, fmt)

        if scan_results["findings"].get("intel"):
            reporter.generate_report(scan_results, "intel")

        print(f"        Saved: {', '.join(args.format)}")

        # Console summary
        secrets = scan_results['findings'].get('secrets', [])
        high = sum(1 for s in secrets if s.get('confidence') == 'high')
        medium = sum(1 for s in secrets if s.get('confidence') == 'medium')
        low = sum(1 for s in secrets if s.get('confidence') == 'low')
        info = sum(1 for s in secrets if s.get('confidence') == 'info')

        print("\n" + "=" * 60)
        print("  RESULTS")
        print("=" * 60)
        if recon_data:
            total_subs = sum(len(r) for r in recon_data.values())
            total_alive = sum(
                sum(1 for s in r if s.alive)
                for r in recon_data.values()
            )
            print(f"  Subdomains: {total_subs} found, {total_alive} alive")
        print(f"  JS Files:    {scan_results['statistics']['js_files_found']}")
        print(f"  Secrets:     {scan_results['statistics']['secrets_found']}")
        if secrets:
            print(f"    high:      {high}")
            print(f"    medium:    {medium}")
            print(f"    low:       {low}")
            print(f"    info:      {info}")
        print(f"  Endpoints:   {scan_results['statistics']['endpoints_found']}")
        print(f"  Duration:    {scan_results['scan_info']['duration']}")
        print(f"  Output:      {output_dir}")
        print("=" * 60)

    except KeyboardInterrupt:
        print("\n\n  Interrupted.")
        sys.exit(1)
    except Exception as e:
        print(f"\n\n  Error: {e}")
        if args.verbose:
            import traceback
            traceback.print_exc()
        print(f"\n  Log: {output_dir}/jsrip.log")
        sys.exit(1)


if __name__ == "__main__":
    import asyncio
    asyncio.run(main())