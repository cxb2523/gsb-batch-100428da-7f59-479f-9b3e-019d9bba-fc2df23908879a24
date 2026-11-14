"""Run a disposable copy's ``main.py`` as ``__main__`` with one injected fault.

Process-level tests need the real entrypoint in a real subprocess, but the fault
has to be installed before the entrypoint runs. Keeping that setup in a module
instead of a string inside a test keeps it debuggable and typed like any other
code in the example. Select the fault with ``RPACORE_FAULT``:

``report_write``
    The report cannot write a data row, after its header is already written, so
    ``atomic_output_path`` has a partial temporary file to clean up.
``report_save``
    The report transaction cannot be persisted.
"""

from __future__ import annotations

import csv
import os
import runpy
import sys

REPORT_REFERENCE = "write-reconciliation-report"


def _install_report_write_fault() -> None:
    """Fail the report's first data row.

    ``DictWriter.writeheader()`` calls ``writerow()``, so the first call goes
    through and leaves header bytes in the temporary file. This step is the
    only ``csv.DictWriter`` user in the example; the loaders only read.
    """
    calls = 0
    real_writerow = csv.DictWriter.writerow

    def writerow(self, rowdict):
        nonlocal calls
        calls += 1
        if calls > 1:
            raise OSError("injected report write failure")
        return real_writerow(self, rowdict)

    csv.DictWriter.writerow = writerow


def _install_report_save_fault() -> None:
    """Fail persistence of the report transaction only.

    Patching ``rpacore`` before ``main.py`` runs is what makes this work: the
    entrypoint's ``from rpacore import save_transaction`` binds the patched
    function at that import.
    """
    import rpacore

    real_save = rpacore.save_transaction

    def save_transaction(transaction, db_path="rpacore.db"):
        if transaction.reference == REPORT_REFERENCE:
            raise OSError("injected report save failure")
        real_save(transaction, db_path=db_path)

    rpacore.save_transaction = save_transaction


def main() -> None:
    fault = os.environ.get("RPACORE_FAULT", "")
    if fault == "report_write":
        _install_report_write_fault()
    elif fault == "report_save":
        _install_report_save_fault()
    else:
        raise SystemExit(f"Unknown RPACORE_FAULT: {fault!r}")

    sys.path.insert(0, os.getcwd())
    sys.argv = ["main.py"]
    runpy.run_path("main.py", run_name="__main__")


if __name__ == "__main__":
    main()
