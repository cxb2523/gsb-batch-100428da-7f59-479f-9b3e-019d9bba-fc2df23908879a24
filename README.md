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

TGPhind automatically tests and maps links across these domains:

- `telegra.ph`
- `graph.org`
- `te.legra.ph`

## License

This project is licensed under the [MIT License](LICENSE).
