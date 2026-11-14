# Imperial AI Archive Backend

Imperial AI Archive Backend is a Python crawler and analysis pipeline for collecting, classifying, and archiving AI product websites. It combines site discovery, browser-based crawling, provider/model detection, Gemini-assisted analysis, and Supabase persistence.

## What It Does

- Discover AI-related sites from search queries
- Crawl pages with Playwright
- Detect AI providers and model hints from network/runtime signals
- Analyze trust, category, pricing, and capabilities with Gemini
- Store normalized records in Supabase
- Resume long scans from checkpoints
- Export the archive to JSON or CSV

## Main Components

- `discovery/`: search and URL discovery logic
- `crawler/`: Playwright-based crawling and network interception
- `analyzer/`: Gemini analysis, key rotation, and deep/pattern analyzers
- `db/`: Supabase persistence layer
- `utils/`: config and logging helpers
- `run_scanner.py`: standalone scanner flow
- `run_cli.py`: command-line entrypoints

## Requirements

- Python 3.10+
- Playwright browsers installed
- Supabase project with the expected schema
- At least one Gemini API key

Dependencies are listed in [requirements.txt](C:/Users/emreg/Desktop/HUCRELER/RL-cell-deneme2/imperial-ai-archive-backend/requirements.txt:1).

## Environment Variables

Create a local `.env` file from `.env.example`.

Required:

- `SUPABASE_URL`
- `SUPABASE_SERVICE_ROLE_KEY`
- `GEMINI_API_KEY`

Optional:

- `GEMINI_API_KEY_1` ... `GEMINI_API_KEY_10`
- `HEADLESS=true|false`

Example:

```bash
cp .env.example .env
```

## Setup

Install dependencies:

```bash
pip install -r requirements.txt
```

Install Playwright browsers:

```bash
python -m playwright install
```

## Usage

Run a fresh scan:

```bash
python run_cli.py scan --target 1000 --batch 20
```

Resume from checkpoint:

```bash
python run_cli.py resume --batch 20
```

Test a single URL:

```bash
python run_cli.py test https://example.com --save
```

Show archive stats:

```bash
python run_cli.py stats --detailed
```

Show Gemini API key status:

```bash
python run_cli.py api-status
```

Export records:

```bash
python run_cli.py export --format csv --limit 5000
```

## Checkpoints

The scanner can persist progress to local checkpoint files such as:

- `checkpoint_sites.json`
- `checkpoint_progress.json`

These are treated as runtime artifacts and should not be committed after active runs.

## Notes

- This project is backend-heavy and research/prototype-oriented.
- Gemini key rotation is implemented to reduce cooldown bottlenecks during large scans.
- Supabase is used as the long-term archive, not as an ephemeral cache.

## Suggested GitHub Description

`Python crawler and Gemini-assisted analysis pipeline for discovering, classifying, and archiving AI product websites into Supabase.`
