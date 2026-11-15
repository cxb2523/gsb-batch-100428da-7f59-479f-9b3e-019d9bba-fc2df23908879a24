# Offline tests for the Goose agent outcome pipeline

These tests exercise the full collection chain (Robot Framework keywords ->
`AIAgentLibrary.py` -> `goose_outcome.py`) **without** goose, docker or ollama
installed and without any network access. The Goose subprocess is replaced by
scripted fake processes (`FakeGooseLibrary.py` / monkeypatched
`subprocess.Popen`) that emit canned outputs.

## What is covered

The three required endings, for both the local (Ollama) and the Docker
(`docker exec`) collection paths:

1. **Success** - clean fake output is kept and written as a normal
   `local-agent-report<N>-<timestamp>.md` report.
2. **Known error signature** - output containing e.g. `Rate limit hit`,
   truncation markers or `Tool not found` is classified `KNOWN_ERROR`; the raw
   output and the reason are persisted into an `...-outcome-<timestamp>.md`
   file instead of aborting the run.
3. **Retry exhausted** - timeouts, missing goose binaries and non-zero exits
   without a known marker are classified `RETRY_EXHAUSTED`; whatever partial
   output was captured (possibly empty) is persisted with the reason.

The tests also assert that:

- the local and Docker paths return identical classifications for the same
  output (the rules live once in `goose_outcome.py`),
- outcome/report files never overwrite an existing file,
- the retry count and interval are honoured between attempts.

## Running the tests

From the repository root, with only Python 3 and Robot Framework installed:

```bash
# Pure-Python unit/integration tests (no Robot Framework needed beyond stdlib)
python -I -m unittest discover -s tests -p "test_*.py"

# End-to-end Robot Framework keyword tests with the fake goose process
python -m robot --pythonpath . --outputdir tests/rf-logs \
    --report NONE --log NONE tests/test_goose_outcome.robot
```

A convenience wrapper runs both:

```bash
bash tests/run-offline-tests.sh
```

Generated artifacts land in `tests/rf-offline-output/` and Robot logs in
`tests/rf-logs/`; both are safe to delete at any time.
