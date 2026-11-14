# File Inbox Processor

An RPA Core example that uses the queue path to process branch CSV reports dropped into an inbox folder.

## Overview

This example demonstrates a file-system automation with one queue item per input file:

1. **Scan** - Add each `.csv` file in `inbox/` to `SqliteQueue`
2. **Read** - Parse the branch report CSV
3. **Validate** - Check schema, types, and business rules
4. **Compute** - Add `revenue_per_headcount`
5. **Append** - Write the clean record to a consolidated master CSV
6. **Move** - Move the source file to `done/` or `failed/`

## Architecture

```text
inbox/
  branch_101_2024-03-01.csv
  branch_205_2024-03-01.csv
  branch_invalid_headcount.csv

queue.db
  pending -> in_progress -> successful/failed

output/
  master_consolidated.csv

done/
failed/
```

## Usage

```bash
cd examples/file_inbox_processor
python main.py
```

Successful files are appended to `output/master_consolidated.csv` and moved to `done/`.
Files with business-rule violations are moved to `failed/`.

The entry point always reads its required `config.toml` from the example
directory, so it can be launched from a nested working directory. Configured
paths must stay within that directory; queue payload file paths are checked
against the configured inbox before they are read, appended, or moved.

The included configuration emits protected JSON log format v3. Set
`log_format = "text"` when a human-readable console log is more useful. The
run-summary event reports the public `QueueRunSummary` fields for that
invocation. Its `processed` and `failed` values are compatibility aggregates;
`retry_scheduled`, `terminal_failed`, `lease_lost`, and
`transition_unknown` describe the queue disposition precisely.

## CSV Schema

| Field | Type | Rule |
|-------|------|------|
| `branch_id` | integer | Must be positive |
| `date` | ISO date | Example: `2024-03-01` |
| `revenue` | decimal | Must be greater than or equal to zero |
| `headcount` | integer | Must be greater than zero |

## Queue Behavior

`scan_inbox` enqueues files as `QueueItem` payloads with `file_path`.
It skips files that already have pending or in-progress queue items, so a
restart after scanning does not create duplicate active work.
`run_queue_loop` claims each item atomically, runs the RPA Core transaction, and marks the queue item successful or failed.

The RPA Core engine retries retryable `SystemException` failures within the transaction according to `max_retries`. The included configuration disables queue-level retries. After engine retries are exhausted, a move error is terminal and the source is moved to `failed/` on a best-effort basis, even if its master row was already written. To finish that move after resolving the error, return the same CSV bytes to `inbox/` under the original filename and run again. If queue-level retries are enabled, the source remains in the inbox until the queue records a terminal outcome. Earlier failed items cannot dispose a newer file with the same name.

Invalid business data raises `BusinessException`, skips downstream processing, and is not retried. The master CSV records each source filename and the SHA-256 digest of the exact input bytes. Reappearing identical bytes are consumed without a second master row or archive overwrite. Changed bytes under a processed filename fail as a collision; the earlier master row and `done/` file remain intact, and the incoming file moves to `failed/`. If `failed/` already has that filename, the incoming file gets a queue-item ID suffix.

An existing six-column master gains the digest column on its first new append. Earlier rows keep an empty digest because their original bytes cannot be verified; a same-name input for one of those rows fails for manual review. Finish writing each input file before it appears in `inbox/`; changing it while a run processes it is unsupported.

## Inspect Existing State

After the example has created its transaction and queue databases, inspect
them without modifying the workflow or importing its entry point:

```bash
rpacore doctor --config config.toml --transaction-db rpacore.db --queue-db queue.db --json
```

The command is read-only and keeps diagnostics privacy bounded. It is useful
before a later run when both databases already exist. On a clean checkout, an
explicit missing database is reported as a failed check and is not created.

## Testing

```bash
cd examples/file_inbox_processor
python -m pytest tests/ -v
```
