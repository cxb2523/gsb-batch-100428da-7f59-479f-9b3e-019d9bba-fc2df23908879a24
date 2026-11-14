#!/usr/bin/env python3
"""Watch a Cinemark showing for seat openings and newly added dates.

Everything Cinemark serves is plain server-rendered HTML, so a sweep is just:
fetch the theater page (all sellable dates), fetch each date's page (showtime
ids for the configured movie), fetch each showtime's seat map, and diff seat
availability against the previous sweep. What to watch and which seats qualify
comes from config.toml.

State persists in state.json. Each sweep records, in state["last_sweep"], one
outcome per showtime in its watch list (checked / fetch_failed / no_data, with a
short reason when it failed) plus a count summary; --report prints that record
without touching the network. Alerts append to alerts.log and are forwarded to
an executable ./notify-hook (if present) as: notify-hook TITLE MESSAGE.

Usage:
  python3 watch.py --once             # single sweep (what the CI cron runs)
  python3 watch.py                    # loop forever
  python3 watch.py --report           # print availability from state; no network
  python3 watch.py --dates 2026-08-08 # restrict a sweep (debugging)
"""
from __future__ import annotations

import argparse
import gzip
import json
import os
import random
import re
import subprocess
import time
import tomllib
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

HERE = Path(__file__).parent
STATE_FILE = HERE / "state.json"
ALERT_LOG = HERE / "alerts.log"

_cfg = tomllib.loads((HERE / "config.toml").read_text())
TARGET, FILTERS, PACING = _cfg["target"], _cfg["filters"], _cfg.get("pacing", {})

THEATER = TARGET["theater"]
MOVIE_ID = str(TARGET["movie_id"])
MOVIE_NAME = TARGET.get("movie_name", f"movie {MOVIE_ID}")
TZ = ZoneInfo(TARGET.get("timezone", "UTC"))
EXCLUDED_ROWS = set(FILTERS.get("excluded_rows", []))
EARLIEST = FILTERS.get("earliest_showtime", "00:00")
LATEST = FILTERS.get("latest_showtime", "23:59")
PARTY_SIZE = int(FILTERS.get("party_size", 1))
REQUEST_GAP = float(PACING.get("request_gap_seconds", 8))
DATE_SCAN_EVERY = int(PACING.get("date_scan_every", 3))
POLL_MINUTES = float(PACING.get("poll_minutes", 5))

BASE = "https://www.cinemark.com"
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")
BACKOFF_SCHEDULE = [120, 300, 900]
RETRY_AFTER_CAP = 1800

# Per-showtime results recorded for each sweep in state["last_sweep"].
OUTCOME_CHECKED = "checked"            # seat map fetched (with or without openings)
OUTCOME_FETCH_FAILED = "fetch_failed"  # retries exhausted or non-retryable error
OUTCOME_NO_DATA = "no_data"            # seat page loaded but had no seat markup
OUTCOME_KEYS = (OUTCOME_CHECKED, OUTCOME_FETCH_FAILED, OUTCOME_NO_DATA)

DATE_VALUE = re.compile(r'data-datevalue="(\d{4}-\d{2}-\d{2})"')
SHOWTIME_LINK = re.compile(
    r'/TicketSeatMap/\?TheaterId=(\d+)&(?:amp;)?ShowtimeId=(\d+)&(?:amp;)?'
    r'CinemarkMovieId=' + MOVIE_ID + r'&(?:amp;)?Showtime=([\d\-T:]+)'
)
# info="F,12,5,9,635630" = row letter, seat number, physical row, column, showtime
AVAILABLE_SEAT = re.compile(
    r'<button[^>]*class="seatAvailable seatBlock"[^>]*info="([A-Z]+),(\d+),\d+,(\d+),'
)


@dataclass
class Seat:
    row: str
    number: int
    col: int

    @property
    def label(self) -> str:
        return f"{self.row}{self.number}"


def log(msg: str) -> None:
    print(f"[{datetime.now():%Y-%m-%d %H:%M:%S}] {msg}", flush=True)


class FetchError(RuntimeError):
    """Fetch failed permanently; `reason` is a short explanation for the report."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


MAX_ATTEMPTS = 1 + len(BACKOFF_SCHEDULE)
HEARTBEAT_SECONDS = 60


def backoff_sleep(wait: float, label: str, attempt: int) -> None:
    """Sleep `wait` seconds, logging progress so a long wait looks alive."""
    remaining = wait
    while remaining > 0:
        chunk = min(HEARTBEAT_SECONDS, remaining)
        time.sleep(chunk)
        remaining -= chunk
        if remaining > 0:
            log(f"backoff [{label}]: still waiting, {remaining:.0f}s left "
                f"before attempt {attempt}/{MAX_ATTEMPTS}")


def fetch(url: str, what: str = "") -> str:
    """Fetch a URL, retrying transient failures. Raises FetchError when giving up.

    `what` labels the caller (e.g. "seat 2026-11-14 7:00pm") so the backoff log
    says which date/showtime is waiting and why.
    """
    label = what or url
    req = urllib.request.Request(url, headers={
        "User-Agent": UA,
        "Accept": "text/html,application/xhtml+xml",
        "Accept-Encoding": "gzip",
    })
    retry_after = 0
    retry_after_note = ""
    last_reason = "unknown error"
    for attempt, backoff in enumerate([0, *BACKOFF_SCHEDULE], start=1):
        if backoff:
            # The 429/403/5xx path carries its Retry-After in `retry_after`; a
            # network error has none, so the plain schedule applies.
            wait = max(backoff, retry_after)
            log(f"backoff [{label}]: attempt {attempt - 1}/{MAX_ATTEMPTS} failed "
                f"({last_reason}); waiting {wait}s before attempt "
                f"{attempt}/{MAX_ATTEMPTS}{retry_after_note}")
            backoff_sleep(wait, label, attempt)
            retry_after = 0
            retry_after_note = ""
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                body = resp.read()
                if resp.headers.get("Content-Encoding") == "gzip":
                    body = gzip.decompress(body)
            time.sleep(REQUEST_GAP + REQUEST_GAP / 2 * random.random())
            return body.decode("utf-8", errors="replace")
        except urllib.error.HTTPError as e:
            last_reason = f"HTTP {e.code}"
            if e.code not in (429, 403, 500, 502, 503):
                raise FetchError(f"HTTP {e.code}") from e
            try:
                retry_after = int(e.headers.get("Retry-After", "0") or 0)
            except ValueError:
                retry_after = 0
            retry_after_note = f" (Retry-After {retry_after}s)" if retry_after else ""
            if retry_after > RETRY_AFTER_CAP:
                if attempt < MAX_ATTEMPTS:  # otherwise there is no wait to report
                    log(f"backoff [{label}]: Retry-After {retry_after}s exceeds "
                        f"cap {RETRY_AFTER_CAP}s for attempt {attempt}/{MAX_ATTEMPTS}, "
                        f"truncating to {RETRY_AFTER_CAP}s")
                else:
                    log(f"[{label}]: Retry-After {retry_after}s exceeds cap "
                        f"{RETRY_AFTER_CAP}s; last attempt, giving up")
                retry_after = RETRY_AFTER_CAP
                retry_after_note = (f" (Retry-After capped at "
                                    f"{RETRY_AFTER_CAP}s)")
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            reason = getattr(e, "reason", None) or e
            last_reason = f"network error: {reason}"
            retry_after = 0
            retry_after_note = ""
    raise FetchError(f"gave up after {MAX_ATTEMPTS} attempts ({last_reason})")


def notify(title: str, message: str) -> None:
    """Send an alert. Never raises: alert delivery must not abort a sweep."""
    log(f"ALERT: {title}: {message}")
    try:
        with ALERT_LOG.open("a") as f:
            f.write(f"{datetime.now().isoformat()}  {title}: {message}\n")
    except Exception as e:  # noqa: BLE001: alerting must never kill the sweep
        log(f"WARN: could not write alerts.log: {e!r}")
    try:
        hook = HERE / "notify-hook"
        if hook.exists() and os.access(hook, os.X_OK):
            subprocess.run([str(hook), title, message], capture_output=True, timeout=30)
    except Exception as e:  # noqa: BLE001: alerting must never kill the sweep
        log(f"WARN: notify-hook failed: {e!r}")


def safe_notify(title: str, message: str) -> None:
    """Call notify, swallowing anything it might raise (it is best-effort)."""
    try:
        notify(title, message)
    except Exception as e:  # noqa: BLE001: a failed alert must not end the sweep
        log(f"WARN: alert '{title}' not delivered: {e!r}")


def load_state() -> dict:
    state: object = {}
    if STATE_FILE.exists():
        try:
            state = json.loads(STATE_FILE.read_text())
        except (OSError, ValueError) as e:
            log(f"WARN: state.json unreadable ({e!r}); starting from empty state")
            state = {}
        if not isinstance(state, dict):
            log("WARN: state.json is not an object; starting from empty state")
            state = {}
    # Older or hand-edited files may lack these containers; normalize so the
    # sweep can rely on them without scattering guards everywhere.
    for key in ("dates", "seats"):
        if not isinstance(state.get(key), dict):
            state[key] = {}
    return state


def save_state(state: dict) -> None:
    """Persist state atomically; a failed write is logged, never fatal."""
    tmp = STATE_FILE.with_suffix(STATE_FILE.suffix + ".tmp")
    try:
        tmp.write_text(json.dumps(state, indent=1, sort_keys=True))
        os.replace(tmp, STATE_FILE)
    except Exception as e:  # noqa: BLE001: a bad write must not abandon the sweep
        log(f"WARN: could not save state: {e!r}")


def showtimes_for(date: str) -> tuple[str | None, dict[str, str]]:
    """Return (theater_id, {showtime_id: iso_start}) for the movie on a date."""
    html = fetch(f"{BASE}/theatres/{THEATER}?showDate={date}", what=f"date {date}")
    links = SHOWTIME_LINK.findall(html)
    return (links[0][0] if links else None), {sid: iso for _tid, sid, iso in links}


def qualifying(iso: str) -> bool:
    return isinstance(iso, str) and len(iso) >= 16 and EARLIEST <= iso[11:16] <= LATEST


class NoSeatData(RuntimeError):
    """Seat map page loaded but carried no seat markup (no data for this showing)."""


def available_seats(theater_id: str, showtime_id: str, iso: str,
                    label: str = "") -> list[Seat]:
    url = (f"{BASE}/TicketSeatMap/?TheaterId={theater_id}&ShowtimeId={showtime_id}"
           f"&CinemarkMovieId={MOVIE_ID}&Showtime={iso}")
    html = fetch(url, what=label)
    if "seatBlock" not in html:
        raise NoSeatData("seat page had no seat markup (page changed?)")
    return [Seat(row, int(num), int(col))
            for row, num, col in AVAILABLE_SEAT.findall(html)
            if row not in EXCLUDED_ROWS]


def seat_blocks(seats: list[Seat]) -> list[list[Seat]]:
    """Group seats into runs of physically adjacent seats (consecutive columns)."""
    blocks = []
    for row in sorted({s.row for s in seats}):
        run: list[Seat] = []
        for s in sorted((s for s in seats if s.row == row), key=lambda s: s.col):
            if run and s.col != run[-1].col + 1:
                blocks.append(run)
                run = []
            run.append(s)
        blocks.append(run)
    return blocks


def fmt_block(block: list[Seat]) -> str:
    if len(block) == 1:
        return block[0].label
    numbers = sorted(s.number for s in block)
    return f"{block[0].row}{numbers[0]}-{block[0].row}{numbers[-1]}"


def fmt_time(iso: str) -> str:
    # %-I is not portable (Windows rejects it), so format the hour ourselves.
    if not isinstance(iso, str) or len(iso) < 16:
        return str(iso or "unknown")
    try:
        hour = int(iso[11:13])
    except ValueError:
        return iso
    return f"{hour % 12 or 12}:{iso[14:16]}{'am' if hour < 12 else 'pm'}"


def prune_past(state: dict) -> None:
    today = datetime.now(TZ).date().isoformat()
    for d in [d for d in state["dates"] if d < today]:
        info = state["dates"].get(d)
        shows = info.get("showtimes") if isinstance(info, dict) else None
        for sid in shows or {}:
            state["seats"].pop(sid, None)
        del state["dates"][d]


def record_outcome(entries: list[dict], date: str, iso: str, outcome: str,
                   reason: str = "") -> None:
    entry = {"date": date, "showtime": iso, "outcome": outcome}
    if reason:
        entry["reason"] = reason
    entries.append(entry)


def summarize(entries: list[dict]) -> dict[str, int]:
    counts = {k: 0 for k in OUTCOME_KEYS}
    for e in entries:
        counts[e["outcome"]] = counts.get(e["outcome"], 0) + 1
    return counts


def log_sweep_summary(entries: list[dict]) -> None:
    counts = summarize(entries)
    log("sweep outcomes: " + ", ".join(f"{k}={counts.get(k, 0)}"
                                       for k in OUTCOME_KEYS)
        + f" (total {len(entries)})")


def publish_last_sweep(state: dict, outcomes: list[dict], *, started_at: str,
                       first_run: bool, watched: int, seats_total: int,
                       finished: bool) -> None:
    """Store the sweep's outcome record (also mid-sweep, so a killed run is readable)."""
    record = {
        "started_at": started_at,
        "first_run": first_run,
        "showtimes_in_watched_list": watched,
        "checked_seats_total": seats_total,
        "counts": summarize(outcomes),
        "outcomes": outcomes,
        "complete": finished,
    }
    if finished:
        record["finished_at"] = datetime.now(TZ).isoformat(timespec="seconds")
    state["last_sweep"] = record


def sweep(state: dict, scan_dates: bool, only_dates: list[str] | None) -> None:
    first_run = not state["dates"]
    prune_past(state)
    started_at = datetime.now(TZ).isoformat(timespec="seconds")
    outcomes: list[dict] = []

    if scan_dates or first_run or only_dates or "theater_id" not in state:
        try:
            strip = only_dates or DATE_VALUE.findall(
                fetch(f"{BASE}/theatres/{THEATER}", what="theater dates"))
        except FetchError as e:
            log(f"WARN: could not list dates: {e.reason}")
            strip = []
        except Exception as e:  # noqa: BLE001: a failed date scan must not stop seat checks
            log(f"WARN: could not list dates: {e!r}")
            strip = []
        for date in sorted(set(strip)):
            info = state["dates"].get(date)
            if isinstance(info, dict) and info.get("showtimes"):
                continue  # already tracking; showtime ids are stable
            try:
                theater_id, shows = showtimes_for(date)
            except FetchError as e:
                log(f"WARN: date probe {date} failed: {e.reason}")
                continue
            except Exception as e:  # noqa: BLE001: skip this date, keep sweeping
                log(f"WARN: date probe {date} failed: {e!r}")
                continue
            if theater_id:
                state["theater_id"] = theater_id
            state["dates"][date] = {"showtimes": shows}
            if shows and not first_run:
                safe_notify(f"New date on sale: {date}",
                            f"{MOVIE_NAME} added for {date}: "
                            + ", ".join(sorted(fmt_time(i) for i in shows.values())))
        tracked_dates = sum(1 for d in state["dates"].values()
                            if isinstance(d, dict) and d.get("showtimes"))
        log(f"date scan: tracking {tracked_dates} dates")
        save_state(state)

    watch: list[tuple[str, str, str]] = []
    for date, info in sorted(state["dates"].items()):
        if not isinstance(info, dict):
            continue
        shows = info.get("showtimes")
        if not isinstance(shows, dict):
            continue
        for sid, iso in shows.items():
            if qualifying(iso) and (not only_dates or date in only_dates):
                watch.append((date, sid, iso))
    watch.sort(key=lambda w: (w[0], w[2]))
    total = 0
    checked: set[tuple[str, str]] = set()
    try:
        for i, (date, sid, iso) in enumerate(watch):
            key = (date, sid)
            label = f"seat {date} {fmt_time(iso)}"
            try:
                seats = available_seats(state.get("theater_id", ""), sid, iso, label)
            except NoSeatData as e:
                record_outcome(outcomes, date, iso, OUTCOME_NO_DATA, str(e))
                checked.add(key)
                log(f"WARN: {label}: {e}; recorded as no data")
                continue
            except FetchError as e:
                record_outcome(outcomes, date, iso, OUTCOME_FETCH_FAILED, e.reason)
                checked.add(key)
                log(f"WARN: {label} skipped: {e.reason}")
                continue
            except Exception as e:  # noqa: BLE001: record it, keep sweeping
                record_outcome(outcomes, date, iso, OUTCOME_FETCH_FAILED,
                               f"unexpected error: {e!r}")
                checked.add(key)
                log(f"WARN: seat check {label} failed: {e!r}")
                continue

            # The check itself succeeded; record it before bookkeeping so a
            # bookkeeping or alerting problem can never lose the outcome or
            # prevent the remaining showtimes from being checked.
            record_outcome(outcomes, date, iso, OUTCOME_CHECKED)
            checked.add(key)
            try:
                total += len(seats)
                prev = set(state["seats"].get(sid, []))
                fresh = {s.label for s in seats} - prev
                state["seats"][sid] = sorted(s.label for s in seats)
                openings = [b for b in seat_blocks(seats)
                            if len(b) >= PARTY_SIZE and any(s.label in fresh for s in b)]
                if openings and not first_run:
                    safe_notify(f"Seats open {date} {fmt_time(iso)}",
                                f"{MOVIE_NAME}: " + ", ".join(fmt_block(b) for b in openings))
            except Exception as e:  # noqa: BLE001: one bad record must not stop the loop
                log(f"WARN: bookkeeping after {label} failed: {e!r}")
            if i % 10 == 9:
                # Persist progress too: if CI kills the job mid-sweep (timeout,
                # cancelled run) --report still shows how far it got.
                try:
                    publish_last_sweep(state, outcomes, started_at=started_at,
                                       first_run=first_run, watched=len(watch),
                                       seats_total=total, finished=False)
                    save_state(state)
                except Exception as e:  # noqa: BLE001: checkpointing is best-effort
                    log(f"WARN: mid-sweep checkpoint failed: {e!r}")
    finally:
        # If something unforeseen escaped the loop, still give every watch-list
        # showtime a recorded outcome so the sweep's result is readable.
        for date, sid, iso in watch:
            if (date, sid) in checked:
                continue
            try:
                record_outcome(outcomes, date, iso, OUTCOME_FETCH_FAILED,
                               "sweep ended before this showing was checked")
            except Exception as e:  # noqa: BLE001: bookkeeping must not mask errors
                log(f"WARN: could not fill in missing outcome for "
                    f"{date} {fmt_time(iso)}: {e!r}")
        try:
            publish_last_sweep(state, outcomes, started_at=started_at,
                               first_run=first_run, watched=len(watch),
                               seats_total=total, finished=True)
            save_state(state)
        except Exception as e:  # noqa: BLE001: never mask the real error
            log(f"WARN: could not record sweep outcomes: {e!r}")
    log(f"seat scan: {len(watch)} showtimes in watch list, {total} qualifying seats")
    log_sweep_summary(outcomes)
    if first_run:
        log("first run: baseline recorded, no alerts fired")


def print_last_sweep(state: dict) -> None:
    """Print the per-showtime outcomes of the last sweep, from state alone."""
    last = state.get("last_sweep")
    if not isinstance(last, dict):
        print("\nno outcome record yet (state.json predates outcome tracking, "
              "or no sweep has run)")
        return
    started = last.get("started_at", "unknown time")
    finished = last.get("finished_at")
    print(f"\nlast sweep: {started} -> {finished or 'did not finish'}")
    if last.get("complete") is False:
        print("  (incomplete: the sweep was interrupted before it finished)")
    if last.get("first_run"):
        print("  (baseline sweep: no alerts were fired)")
    entries = last.get("outcomes")
    if not isinstance(entries, list):
        entries = []
    entries = [e for e in entries if isinstance(e, dict) and e.get("date")]
    if not entries:
        print("  no showtimes were in the watch list for that sweep")
    for e in sorted(entries, key=lambda e: (str(e["date"]), str(e.get("showtime", "")))):
        iso = e.get("showtime", "")
        when = fmt_time(iso) if iso else "unknown"
        line = f"  {e['date']} {when:>8}  {e.get('outcome', 'unknown')}"
        if e.get("reason"):
            line += f"  ({e['reason']})"
        print(line)
    counts = last.get("counts")
    if not isinstance(counts, dict):
        counts = summarize(entries)
    summary = ", ".join(f"{k}={counts.get(k, 0)}" for k in OUTCOME_KEYS)
    print(f"  summary: {summary} (total {len(entries)})")


def report(state: dict) -> None:
    print(f"\n{MOVIE_NAME} @ {THEATER}")
    print(f"filters: rows {''.join(sorted(EXCLUDED_ROWS)) or 'none'} excluded, "
          f"shows {EARLIEST}-{LATEST}, party of {PARTY_SIZE}\n")
    dates = state.get("dates") or {}
    seats_by_showtime = state.get("seats") or {}
    tracked = {d: v for d, v in sorted(dates.items())
               if isinstance(v, dict) and v.get("showtimes")}
    if not tracked:
        print("no dates tracked yet: run a sweep first")
    else:
        print(f"on sale: {min(tracked)} to {max(tracked)} ({len(tracked)} dates)\n")
        empty = True
        for d, info in tracked.items():
            shows = info.get("showtimes")
            if not isinstance(shows, dict):
                continue
            for sid, iso in sorted(shows.items(),
                                   key=lambda kv: kv[1] if isinstance(kv[1], str) else ""):
                if not isinstance(iso, str):
                    continue
                seats = seats_by_showtime.get(sid, [])
                if not isinstance(seats, list):
                    seats = []
                if qualifying(iso) and seats:
                    empty = False
                    print(f"  {d} {fmt_time(iso):>8}  {len(seats):>3} seats: "
                          f"{', '.join(seats[:14])}"
                          f"{'...' if len(seats) > 14 else ''}")
        if empty:
            print("no qualifying seats right now: the watcher alerts when one opens")
    print_last_sweep(state)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--once", action="store_true", help="single sweep, then exit")
    ap.add_argument("--dates", nargs="*", help="restrict to specific YYYY-MM-DD dates")
    ap.add_argument("--report", action="store_true",
                    help="print availability from state.json and exit (no network)")
    args = ap.parse_args()

    if args.report:
        try:
            report(load_state())
        except Exception as e:  # noqa: BLE001: a stale/corrupt state must not crash
            print(f"could not read a usable state.json ({e!r}); "
                  "no outcome record available")
        return

    while True:
        state = load_state()
        # persisted so --once runs (CI) keep cadence; tolerate a hand-edited value
        try:
            cycle = int(state.get("cycle", 0))
        except (TypeError, ValueError):
            cycle = 0
        try:
            sweep(state, scan_dates=(cycle % DATE_SCAN_EVERY == 0), only_dates=args.dates)
        except Exception as e:  # noqa: BLE001: keep the loop alive on transient errors
            log(f"ERROR during sweep: {e!r}")
        state["cycle"] = cycle + 1
        save_state(state)
        if args.once:
            return
        time.sleep(POLL_MINUTES * 60)


if __name__ == "__main__":
    main()
