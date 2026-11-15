from __future__ import annotations

import argparse
import sys
from multiprocessing import cpu_count
from urllib.parse import urlparse

import tgphind

__all__ = 'main',


def _proxy_type(arg: str):
    parsed = urlparse(arg)
    return tgphind.Proxy(parsed.netloc, parsed.scheme or 'https')


def _print_failures(failures, file, mirror_failures=()) -> None:
    """Write the failure summary (title, mirror, reason) to ``file``."""
    if mirror_failures:
        print(f'Unavailable mirrors ({len(mirror_failures)}):', file=file)
        for failure in mirror_failures:
            line = f'  {failure.mirror}: {failure.reason}'
            if failure.detail:
                line += f' ({failure.detail})'
            print(line, file=file)
    if failures:
        print(f'Failed probes ({len(failures)}):', file=file)
        for failure in failures:
            print(f'  {failure}', file=file)


def main():
    description = (
        "TGPhind -- Multithreaded search tool for articles on telegra.ph and its mirrors.\n\n"
        "Search patterns inside brackets (default '<>') are automatically expanded:\n"
        "  - Ranges:       '<a-d>'   -> 'a', 'b', 'c', 'd'\n"
        "  - Step ranges:  '<a2g>'   -> 'a', 'c', 'e', 'g'\n"
        "  - Sets:         '<abc>'   -> 'a', 'b', 'c'\n"
        "  - Optional char:'<s'      -> 's', ''\n\n"
        "Examples:\n"
        f"  tgphind 'article<s>-<a-d>' -tc {cpu_count()}\n"
        "  tgphind 'article[a-c]' --brackets '[]'"
    )

    arg_parser = argparse.ArgumentParser(
        prog='tgphind',
        description=description,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    arg_parser.add_argument(
        'query',
        type=str,
        help='Article title or template query string to search (e.g., "article<s>-<a-d>")'
    )
    arg_parser.add_argument(
        '-tc', '--threads-count',
        type=int,
        metavar='N',
        help='Number of worker threads (defaults to CPU core count)'
    )
    arg_parser.add_argument(
        '-b', '--brackets',
        type=str,
        default='<>',
        metavar='PAIR',
        help='Opening and closing bracket delimiters for template patterns (default: "<>")'
    )
    arg_parser.add_argument(
        '-x', '--proxy',
        type=_proxy_type,
        metavar='URL',
        help='Proxy URL (e.g., "http://127.0.0.1:8080" or "https://proxy.example.com:3128")'
    )
    args = arg_parser.parse_args()
    se = tgphind.TGPhind(args.threads_count, args.proxy, args.brackets)
    mirrors = se.test_mirrors()
    print(*mirrors, sep=', ', end='\n\n')
    if not mirrors:
        _print_failures((), sys.stderr, se.mirror_failures)
        print(
            'error: all mirrors are unavailable, no article could be probed',
            file=sys.stderr,
        )
        return 1
    result = se.search(args.query)
    print(*result, sep='\n')
    if result.failures:
        _print_failures(result.failures, sys.stderr)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
