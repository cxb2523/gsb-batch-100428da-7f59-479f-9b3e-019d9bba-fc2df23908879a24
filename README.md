# Wealthsimpleton

A Python script that scrapes your Wealthsimple activity history and saves the data in a JSON file.

The data scraped includes:
- Transaction description
- Transaction type
- Transaction amount
- Transaction date

## Usage

1. Ensure Python dependencies are installed: `pip install -r requirements.txt`
2. Ensure you have Chromium or Google Chrome installed.
3. Ensure you have Chrome Webdriver installed and that it is compatible with the version of Chromium/Chrome you have.
   - On Linux, you can run `installChromeDriver.sh` to automatically install/update ChromeDriver in `/usr/local/bin`,
4. Run the script: `python main.py`
   - You can use the `--after` argument to only include orders after a certain date/time (format is `%Y-%m-%d %H:%M`; defaults to 45 days ago).
   - The output is printed to the terminal; if you would like to also save it to a file, use the `--file` argument with a valid file path.

## Incremental output and recovery

When `--file` is used, every successfully scraped transaction is written to the
file immediately (via an atomic temp-file-and-rename, so the file is always a
complete, parseable JSON array — never a half-written one). If the process is
killed, the browser times out, or the login session expires, just rerun with the
same `--file`: records already on file are loaded and skipped automatically, and
scraping resumes from there without duplicates. A file that cannot be parsed as
a JSON array is moved aside to `<file>.corrupt-<pid>` rather than overwritten.

Every transaction that cannot be scraped — a panel that never opens, a missing
amount, an unparseable date, an inactive status, or any other error — is logged
to **stderr** with its position (e.g. `Skipped 12/80`), a readable description
and the reason, and counted in the final summary:

```
Scrape complete: 77 succeeded (75 newly written, 2 already on file), 3 skipped
```

Pagination also tolerates unparseable section headers: if the oldest visible
date cannot be parsed, a warning is logged and the next page is loaded anyway.
Paging stops only once a parseable header confirms the list has crossed the
`--after` cutoff, or the "Load more" button disappears.

If a write fails mid-run (full disk, lost share, etc.), the error is logged and
counted; scraping continues with the records held in memory, a final retry is
attempted at the end, and the full result is still printed to stdout.

## Tests

`test_main.py` exercises the scraper against an in-memory fake driver
(`test_fakes.py`) — no browser required:

```
python -m pytest test_main.py
```

It covers normal multi-transaction scraping, panel timeouts, missing amounts,
unparseable dates, cutoff handling, pagination past an unparseable header,
resume/dedup, corrupt-output recovery, and a mid-run write failure that leaves
the output file parseable by `json.load`.
