# JSON Event Log Processor

An RPA Core example that processes JSON event log files through a batch pipeline.

## Overview

This example demonstrates processing JSON event logs through a 5-step pipeline:

1. **Load** — Read and parse JSON files from the inbox folder
2. **Validate** — Check events conform to the expected schema (required fields, valid event_type)
3. **Normalize** — Standardize timestamps to UTC, map event types to severity codes, enrich with metadata
4. **Output** — Write normalized events as JSONL (JSON Lines) to the results folder
5. **Error Report** — Generate a JSON summary of failed transactions for debugging

## Architecture

```
sample_data/              # Default committed input: JSON event log files
  ├── events_001.json     # Valid: 5 events
  ├── events_002.json     # Valid: 3 events
  ├── events_003.json     # Invalid: missing required fields
  ├── events_004.json     # Invalid: invalid event_type values
  └── events_005.json     # Malformed: not valid JSON

results/                  # Output: normalized JSONL files
  ├── events_001_cleaned.jsonl
  ├── events_002_cleaned.jsonl
  └── error_report.json   # Summary of failed transactions and persistence errors

rpacore.db                   # SQLite database tracking transaction status
```

## Prerequisites

- Python 3.12+
- RPA Core installed through `requirements.txt` (`rpacore>=0.3.0,<0.4.0`)

## Setup

```bash
# Create a virtual environment (recommended)
python -m venv .venv
source .venv/bin/activate  # On Windows: .venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt
```

## Usage

Run the processor:

```bash
python main.py
```

The command loads the committed `config.toml` beside `main.py`, so it has the
same behavior when launched from this directory or a nested working directory.
The configured database, inbox, and results paths must remain under the
example directory.

This will:
1. Process all five committed `.json` files in `sample_data/`
2. Output normalized JSONL files to the `results/` folder
3. Write transaction history to `rpacore.db`
4. Generate `results/error_report.json` with failed transaction and persistence details
5. Exit 0 when every selected file reached a durable outcome and the error
   report was published and persisted (see [Exit Codes](#exit-codes))

To process a different local directory, copy the sample data with `make sample`
and set `inbox_dir = "inbox"` in `config.toml`, or point `inbox_dir` at another
contained directory. The committed default stays reproducible for a fresh clone.

## Input Readiness

A `*.json` entry in the inbox is a **complete, published input**. The processor
selects exactly `inbox/*.json`; anything else — including a partially written
file — is ignored until a producer finishes it.

A producer must therefore write under a non-`.json` name and rename it into
place only once the whole document is on disk:

```sh
cp events.json inbox/events.json.tmp
mv inbox/events.json.tmp inbox/events.json   # the input is now published
```

`make sample` uses this handoff. Because a rename within one directory is
atomic, a reader never observes a half-written `*.json` file. A producer that
exposes an unfinished `*.json` file violates this contract, and the processor
cannot tell an unfinished write apart from genuinely bad data — it will reject
the partial document as malformed JSON.

## Expected Behavior

### Successful Files

- `events_001.json` → `results/events_001_cleaned.jsonl` (5 normalized events)
- `events_002.json` → `results/events_002_cleaned.jsonl` (3 normalized events)

### Rejected Files

A rejected file is an audited business outcome. It produces no JSONL output,
is recorded in the error report, and is not retried.

- `events_003.json` → **FAILED** (`json_event_log.validation.invalid_event`) — missing required fields (event_id, event_type, timestamp, source)
- `events_004.json` → **FAILED** (`json_event_log.validation.invalid_event`) — invalid event_type values ("critical", "debug" not in allowed set)
- `events_005.json` → **FAILED** (`json_event_log.input.malformed_json`) — malformed JSON

The committed sample is a completed batch of two successes and three audited
business rejections, so `python main.py` exits 0.

### Failure Classification

Input problems and technical problems are deliberately separated.

| Failure code | Classification | Retried |
| --- | --- | --- |
| `json_event_log.input.malformed_json` | Business — the published document is not valid UTF-8 JSON | No |
| `json_event_log.input.unsupported_document` | Business — the top-level value is not an object or array | No |
| `json_event_log.validation.invalid_event` | Business — schema or timestamp rule violation | No |
| `json_event_log.input.missing_file` | System — the selected input is gone | Yes |
| `json_event_log.input.read_failed` | System — the input could not be read | Yes |

A published `*.json` file is final by contract, so unparseable content and an
unsupported top-level value can never be fixed by retrying them. `NormalizeEvents`
never receives a bad timestamp: `ValidateEvents` rejects a non-string,
non-ISO-8601, or UTC-out-of-range `timestamp` as a business rejection first.
Missing, unreadable, or otherwise failing I/O stays a system failure and is
retried up to `max_retries`.

### Normalized Output Format

Each line in the JSONL output follows this schema:

```jsonl
{
  "event_id": "evt-001",
  "event_type": "info",
  "timestamp": "2024-01-15T08:30:00+00:00",
  "source": "auth-service",
  "severity": "INFO",
  "payload": {"message": "User login successful", ...},
  "processed_at": "2024-01-15T08:30:00.000000",
  "version": "1.0"
}
```

### Error Report Format

```json
{
  "total_transactions": 5,
  "successful": 2,
  "failed": 3,
  "unresolved": 0,
  "persistence_error_count": 0,
  "persistence_errors": [],
  "failures": [
    {
      "transaction_id": 1,
      "transaction_reference": "json-file-events_003",
      "status": "FAILED",
      "retry_count": 0,
      "outcome_category": "business_failed",
      "retry_disposition": "not_requested",
      "failure_code": "json_event_log.validation.invalid_event",
      "failed_steps": [
        {
          "step_name": "validate_events",
          "step_order": 2,
          "exception_type": "business",
          "message": "Event at index 1 missing required field: event_type"
        }
      ]
    }
  ]
}
```

`failed` counts transactions that reached the terminal `FAILED` status. `unresolved`
counts persisted transactions that are neither `SUCCESSFUL` nor `FAILED`. If a file
is processed but its transaction cannot be written to `rpacore.db`, the batch
continues, the persistence problem is listed in `persistence_errors`, and the run
exits nonzero.

Each failure also includes the durable outcome category, the final retry
disposition, and an optional stable failure code. These are recorded by RPA
Core at the execution boundary; `retry_count` remains the number of retry
passes that actually ran, not a substitute for the final retry decision.

The error report queries the database by the current run's persisted `run_id` and
continues through every matching result page before loading failure details. It is
therefore scoped to the run even when more than 100 newer transactions from other
runs exist.

Both JSONL output and the error report publish atomically: an existing complete
file remains in place if creating, writing, syncing, or replacing the new file
fails.

## Exit Codes

`python main.py` exits **0** only when the batch is complete and durable:

- every selected file has a durable outcome — a success or an audited business
  rejection — with a persisted transaction record, and
- this run's error report was published and its own transaction was persisted.

It exits nonzero when any of the following hold, even if other files produced
output and an error report was written:

- any system failure (a retried-and-exhausted step, or any non-`business_failed`
  failure outcome),
- any file left unresolved (no terminal `SUCCESSFUL`/`FAILED` status),
- any file transaction that could not be written, so its outcome is not durable,
- the error report was not published, or its transaction could not be written.

Processing continues after an individual file failure, so one bad file never
suppresses the rest of the batch. A failed report write leaves any previous
complete report in place. When the report's own transaction cannot be saved, the
report is refreshed so the missing record is listed in `persistence_errors`, and
the run still exits nonzero. A report that cannot be published is recorded
itself as a failed `error-report` transaction, so the missing report remains
auditable in `rpacore.db` even though no report file was written.

## Event Schema

Events must conform to this schema:

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `event_id` | string | Yes | Unique event identifier |
| `event_type` | string | Yes | One of: `info`, `warning`, `error` |
| `timestamp` | string | Yes | ISO 8601 timestamp (e.g., `2024-01-15T08:30:00Z`) |
| `source` | string | Yes | Service name that generated the event |
| `payload` | object | No | Optional event payload data |

A `timestamp` that is present and truthy but is not an ISO 8601 string — a
truthy number, for example — is rejected by `ValidateEvents` rather than being
retried later as a normalization error.

## Testing

Run all tests:

```bash
cd examples/json_event_log_processor
python -m pytest tests/ -v
```

Run only unit tests:

```bash
python -m pytest tests/unit/ -v
```

Run only integration tests:

```bash
python -m pytest tests/integration/ -v
```

`tests/integration/test_batch_exit_codes.py` runs the real `python main.py`
entrypoint in a disposable project copy, so the exit code and the durable
SQLite, JSONL, and report facts are checked together.

## Steps

| Step | Order | Description |
|-------|-------|-------------|
| `LoadJsonFile` | 1 | Read and parse JSON files |
| `ValidateEvents` | 2 | Validate event schema |
| `NormalizeEvents` | 3 | Standardize and enrich events |
| `WriteOutput` | 4 | Write normalized JSONL files |
| `WriteErrorReport` | 5 | Generate failure report |

## Key Design Decisions

1. **Validation short-circuit**: Unlike the `rest_api_batch` example where `BusinessException` doesn't stop execution, this pipeline raises validation errors with `halts_remaining_steps=True` so downstream steps do not run for invalid files.
2. **JSONL output**: Each normalized event is written as a single JSON line, suitable for streaming and log analysis tools.
3. **Per-file transactions**: Each input file is processed as a separate RPA Core transaction, enabling partial failure handling.
4. **Persistence error visibility**: Transaction-save failures are captured in the error report while the remaining files continue processing.
5. **UTC normalization**: All timestamps are normalized to UTC with timezone offset format.
6. **Published-input contract**: a `*.json` inbox entry is complete by contract, so bad content is a business rejection and the producer owns the write-then-rename handoff.
7. **Exit reflects durability**: a batch is successful only when every file outcome and the error report are durably recorded, so a scheduler can trust the exit code.

## License

MIT
