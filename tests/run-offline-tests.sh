#!/bin/bash
# Run the fully offline outcome tests: no goose, docker, ollama or network.
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
cd "${REPO_ROOT}"

PYTHON="${PYTHON:-python}"
if ! command -v "${PYTHON}" >/dev/null 2>&1; then
  PYTHON="python3"
fi

echo "=== Python unit/integration tests ==="
"${PYTHON}" -I -m unittest discover -s tests -p "test_*.py" -v

echo
echo "=== Robot Framework keyword tests (fake goose process) ==="
rm -rf tests/rf-offline-output tests/rf-logs
"${PYTHON}" -m robot --pythonpath . --outputdir tests/rf-logs \
  --report NONE --log NONE tests/test_goose_outcome.robot

echo
echo "All offline outcome tests passed."
