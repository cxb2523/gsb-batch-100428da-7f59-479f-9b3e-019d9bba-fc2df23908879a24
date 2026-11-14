"""The ``gmaps-collect`` command."""

import argparse
import json
import logging
import sys
from typing import List, Optional

from . import __version__
from .collector import MAX_WORKERS, collect
from .exceptions import GMapsExtractorError
from .output import FORMATS, write_rows

EPILOG = """examples:
  gmaps-collect "Austin, Texas" "coffee shop" --max-results 10
  gmaps-collect "Lyon, France" "bakery" --no-hours -o bakeries.csv
  GMAPS_PROXY_URL=http://user:pass@host:port gmaps-collect "Leeds, UK" "dentist"
"""


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="gmaps-collect",
        description="Search Google Maps for places in an area and print them with their opening hours.",
        epilog=EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
        allow_abbrev=False,
    )
    p.add_argument("area", help="area name, e.g. 'Austin, Texas'")
    p.add_argument("query", help="what to search for, e.g. 'coffee shop'")
    p.add_argument("-n", "--max-results", type=int, default=None, help="stop after this many places")
    p.add_argument("--no-hours", action="store_true", help="skip the opening-hours request per place")
    p.add_argument("-o", "--output", help="output file (default: standard output)")
    p.add_argument("-f", "--format", choices=FORMATS, help="json (default), jsonl or csv; guessed from -o")
    p.add_argument(
        "--proxy",
        help="proxy URL, e.g. http://user:pass@host:port (or set GMAPS_PROXY_URL; the variable keeps it "
        "out of your shell history)",
    )
    p.add_argument("-w", "--workers", type=int, default=4, help=f"parallel requests, 1-{MAX_WORKERS} (default 4)")
    p.add_argument("--min-interval", type=float, default=0.5, help="seconds between requests (default 0.5)")
    p.add_argument("-b", "--buffer", type=float, default=5.0, help="km kept around the area box (default 5)")
    p.add_argument("--cell-size", type=int, default=None, help="grid cell size in metres (default: by area size)")
    p.add_argument("-q", "--quiet", action="store_true", help="no progress messages")
    p.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    return p


def main(argv: Optional[List[str]] = None) -> int:
    args = _parser().parse_args(argv)
    fmt = args.format
    if fmt is None:
        fmt = "json"
        if args.output:
            for candidate in FORMATS:
                if args.output.lower().endswith("." + candidate):
                    fmt = candidate
    if not args.quiet:
        logging.basicConfig(level=logging.INFO, format="%(message)s", stream=sys.stderr)
        logging.getLogger("httpx").setLevel(logging.WARNING)

    try:
        result = collect(
            args.area,
            args.query,
            max_results=args.max_results,
            hours=not args.no_hours,
            proxy=args.proxy,
            workers=args.workers,
            buffer_km=args.buffer,
            cell_size_meters=args.cell_size,
            min_interval=args.min_interval,
        )
    except GMapsExtractorError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("interrupted", file=sys.stderr)
        return 130

    if args.output:
        newline = "" if fmt == "csv" else None
        with open(args.output, "w", encoding="utf-8", newline=newline) as handle:
            write_rows(result, handle, fmt)
    else:
        write_rows(result, sys.stdout, fmt)
    if not args.quiet:
        print(json.dumps(result.stats), file=sys.stderr)
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
