# TGPhind

[![PyPI version](https://img.shields.io/pypi/v/tgphind.svg)](https://pypi.org/project/tgphind/)
[![Downloads](https://static.pepy.tech/badge/tgphind)](https://pepy.tech/project/tgphind)
[![Python versions](https://img.shields.io/pypi/pyversions/tgphind.svg)](https://pypi.org/project/tgphind/)
[![License](https://img.shields.io/pypi/l/tgphind.svg)](https://github.com/ndenissov/tgphind/blob/main/LICENSE)
[![GitHub stars](https://img.shields.io/github/stars/ndenissov/tgphind)](https://github.com/ndenissov/tgphind/stargazers)

TGPhind is a multithreaded tool and Python library for searching articles by title and template on [Telegra.ph](https://telegra.ph) and its mirrors ([Graph.org](https://graph.org), [Te.legra.ph](https://te.legra.ph)).

## Features

- Multithreaded article availability checking with configurable worker threads.
- Flexible article title template generator supporting ranges and letter sets.
- Automatic transliteration of Cyrillic titles into URL-compatible slugs.
- Support for HTTP/HTTPS proxies.
- Automatic mirror verification and URL mapping.
- Verifiable results: every search reports which probes failed, on which mirror and why.
- Command-line interface (CLI) and Python API.

## Installation

Install via `pip`:

```bash
pip install tgphind
```

Or via `poetry`:

```bash
poetry add tgphind
```

## Quick Start

### Python API Usage

```python
from tgphind import TGPhind, Proxy

# Initialize the search engine
se = TGPhind(
    max_th=16,  # Worker threads count (defaults to CPU count)
    proxy=None,  # Optional Proxy(host='...', protocol='https')
    brackets='<>'  # Template delimiter brackets
)

# Search articles using a template pattern
results = se.search('article<s>-<a-d>')

# Map results across available mirrors
mapped_urls = se.map_hosts(results)

for urls in mapped_urls:
    for url in urls:
        print(url)
```

### Reading failures

`search()` and `search_iter()` return a `SearchResult`, a `tuple` subclass that
still compares and iterates exactly like the plain tuple of hit paths returned by
earlier versions. It additionally exposes what went wrong during the search:

```python
result = se.search('article<s>-<a-d>')

for path in result:            # unchanged behaviour: the hits
    print(path)

for failure in result.failures:
    print(failure.title, failure.mirror, failure.reason, failure.detail)

print(result.available_mirrors.first)  # mirror used for the probes
```

`failure.reason` is one of `timeout`, `connection`, `http` or `error`, and
`failure.detail` keeps the original message. A `404`/`410` answer means "no such
article" and is *not* reported as a failure. A failing probe never stops the
other titles; inside a numbered chain (`-2`, `-3`, ...) a failure stops that
chain while keeping the pages already found.

`TGPhind.failures` exposes the same list for code that does not keep the return
value.

### Mirrors

`test_mirrors()` probes every mirror and returns a `MirrorSet` with the usable
ones, in `MIRRORS` order. The global `MIRRORS` list is never modified, and
`urlopen(path)` defaults to the first mirror of that set. When no mirror is
usable, `search()`/`search_iter()` raise `NoMirrorsAvailable` (a `URLError`
subclass) instead of returning an empty result, so "no articles" and "network or
mirror problem" stay distinguishable.

```python
mirrors = se.test_mirrors()
if not mirrors:
    for failure in se.mirror_failures:
        print(failure.mirror, failure.reason, failure.detail)
```

### CLI Usage

Execute directly via the installed executable:

```bash
tgphind "article-<1-3>" -tc 8
```

Or run via the Python module:

```bash
python -m tgphind "article<s>-<a-d>" -tc 16
```

#### Command-line Options

- `query` (required): Article title or pattern string.
- `-tc`, `--threads-count`: Number of threads to execute queries.
- `-b`, `--brackets`: Custom template bracket delimiters (default: `<>`).
- `-x`, `--proxy`: Proxy string formatted as `protocol://host:port`.

Standard output keeps its historical layout: the available mirrors, a blank
line, then the hit paths, one per line. The failure summary is written to
standard error instead, so stdout stays machine readable:

```
$ tgphind "article<s>" -tc 8 2>failures.txt
```

`failures.txt` then contains the failed titles with their mirror and reason,
for example:

```
Failed probes (1):
  article @ telegra.ph: timeout (timed out) [article-01-01-3]
```

When every probe succeeds, nothing is written to standard error. If no mirror
is available at all, the CLI reports it on standard error and exits with a
non-zero status instead of printing an empty result.

## Template Syntax

The search pattern generator expands patterns wrapped inside bracket delimiters `<>`:

- Single character: `<s` generates `s` and an empty string.
- Character ranges: `<a-d>` expands to `a`, `b`, `c`, `d`.
- Character sets: `<abc>` generates `a`, `b`, `c`.
- Step ranges: `<a2g>` generates characters with step 2 (`a`, `c`, `e`, `g`).

### Example Expansion

Pattern `article<s>-<a-d>` generates the following titles:
- `article-a`, `article-b`, `article-c`, `article-d`
- `articles-a`, `articles-b`, `articles-c`, `articles-d`

Each expanded title is tested against publication date patterns (`MM-DD`) and index suffixes (`-2`, `-3`, etc.).

## Supported Mirrors

TGPhind automatically tests and maps links across these domains, in this order:

- `te.legra.ph`
- `graph.org`
- `telegra.ph`

The first mirror that answers is used for the probes. A mirror that answers a
`404`/`410` to the probe still counts as available: that response only means the
root page is missing.

## Running the tests

The test suite does not use the network; probes are served by a controllable
`urlopen` double.

```bash
python -m unittest discover -s tests -v
```

## License

This project is licensed under the [MIT License](LICENSE).
