# Database Reconciliation

An RPA Core example that reconciles internal payment records against a bank statement export.

## Overview

This example demonstrates a finance-style batch automation:

1. **Load internal records** - Read ERP/accounting payments from CSV
2. **Load bank statement** - Read bank transactions from CSV and build a reference index
3. **Match transaction** - Find candidate bank entries for each internal payment
4. **Classify outcome** - Mark each payment as matched, missing, or amount mismatch
5. **Write report** - Produce a deterministic reconciliation report CSV

## Architecture

```text
sample_data/
  internal_payments.csv
  bank_statement.csv

main.py
  setup transaction loads both CSV files
  one RPA Core transaction per internal payment
  final report transaction writes output/reconciliation_report.csv

rpacore.db
  persisted transaction history
```

## Usage

```bash
cd examples/database_reconciliation
python main.py
```

The sample data includes 20 internal payments, 17 exact matches, 2 amount mismatches, and 1 missing bank entry.

## Output

`output/reconciliation_report.csv` contains:

| Field | Description |
|-------|-------------|
| `payment_id` | Internal payment ID |
| `date` | Internal payment date |
| `reference` | Shared payment/reference key |
| `vendor` | Internal vendor name |
| `internal_amount` | Amount from the internal system |
| `bank_amount` | Amount from the bank statement, if present |
| `bank_date` | Bank posted date, if present |
| `status` | `matched`, `amount_mismatch`, `missing_from_bank`, or `type_error` |
| `reason_code` | Empty for matched records, otherwise the discrepancy reason |

## RPA Core Behavior

Each internal payment is one transaction. Matched payments complete successfully.
Missing and mismatched payments raise `BusinessException`, so they are persisted as failed transactions with clear reason messages.

Unreadable or malformed source files raise `SystemException` during setup and stop the run.
Invalid payment or bank amount types are system failures recorded as
`type_error` rows, preserving their domain reason code in the CSV.

Configuration is loaded from the `config.toml` beside `main.py`. The database,
input CSV, and report paths must be non-empty and remain within this example
directory. The report is published atomically, so a write failure leaves an
existing report untouched.

## Exit Codes

`python main.py` exits 0 only when the report transaction completed
successfully, its record is persisted, and no payment has a discrepancy. It
exits 1 when:

- setup fails, so no reconciliation is attempted;
- the report transaction ends in any non-successful status — the failure is
  logged and the process exits before any completion line, whether or not
  payments also have discrepancies;
- at least one payment has a discrepancy, after the report was published.

A run with no internal payments is a completed run: it publishes a
header-only report and exits 0.

A report publication failure leaves the prior report in place and records the
failed report attempt as a failed transaction, so the missing report stays
auditable. A report that cannot be persisted raises the error out of
`main.py`: the process exits nonzero without announcing completion, the already
published report file is not rolled back, and no durable transaction records
that attempt, so that run is auditable only through its exit code and log.

The CSV keeps its domain `status` and `reason_code` values. Persisted payment
transactions separately record their terminal outcome, retry disposition, and
a stable failure code for operator inspection.

## Testing

```bash
cd examples/database_reconciliation
python -m pytest tests/ -v
```
