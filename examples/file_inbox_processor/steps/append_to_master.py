from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
import csv
import os
from pathlib import Path
import tempfile
import time

from rpacore import BusinessException, ProcessContext, Step, Status, SystemException, resolve_config_path

MASTER_COLUMNS = ("source_file", "source_sha256", "branch_id", "date", "revenue", "headcount", "revenue_per_headcount")
LEGACY_COLUMNS = tuple(column for column in MASTER_COLUMNS if column != "source_sha256")
MASTER_LOCK_TIMEOUT_SECONDS = 10.0
MASTER_LOCK_POLL_SECONDS = 0.05


class AppendToMaster(Step):
    """Append the processed report to the consolidated CSV."""

    def execute(self, ctx: ProcessContext) -> None:
        if ctx.optional_state("validation_failed", bool, False, action=self.name):
            self.status = Status.SKIPPED
            return

        report = ctx.require_state("processed_report", dict, action=self.name)
        if not isinstance(report, dict):
            raise SystemException("No processed report in context", action=self.name)
        source_sha256 = ctx.require_state("source_sha256", str, action=self.name)

        master_csv = ctx.require_config("master_csv", str, action=self.name)
        if not master_csv:
            raise SystemException("Config key 'master_csv' must be a non-empty string", action=self.name)

        path = Path(master_csv)
        path.parent.mkdir(parents=True, exist_ok=True)
        raw_source = ctx.optional_state("report_file", str, "", action=self.name) or ctx.optional_state(
            "file_path",
            str,
            "",
            action=self.name,
        )
        if not isinstance(raw_source, str) or not raw_source:
            raise SystemException("No source file available", action=self.name)

        # Skip validation when inbox_dir is absent (unit tests); production always provides it.
        inbox_dir = ctx.config.get("inbox_dir")
        if isinstance(inbox_dir, str) and inbox_dir:
            source_path = Path(
                resolve_config_path(
                    raw_source,
                    base_dir=inbox_dir,
                    root=inbox_dir,
                    key=self.name,
                )
            )
        else:
            source_path = Path(raw_source)
        source_file = source_path.name

        with _master_csv_lock(path):
            columns, rows = _read_master(path)
            matches = [row for row in rows if row["source_file"] == source_file]
            if len(matches) > 1:
                raise SystemException(f"Multiple master rows for {source_file}", action=self.name)
            if matches:
                if matches[0].get("source_sha256") != source_sha256:
                    raise BusinessException(
                        f"Filename collision or unverified legacy row: {source_file}",
                        action=self.name,
                        halts_remaining_steps=True,
                    )
                ctx.state["duplicate_input"] = True
            else:
                done_dir = ctx.config.get("done_dir")
                if isinstance(done_dir, str) and (
                    (Path(done_dir) / source_file).exists()
                    or (Path(done_dir) / source_file).is_symlink()
                ):
                    raise BusinessException(
                        f"Archived file has no matching master row: {source_file}",
                        action=self.name,
                        halts_remaining_steps=True,
                    )
                row = {column: str(report[column]) for column in LEGACY_COLUMNS if column != "source_file"}
                row["source_file"] = source_file
                row["source_sha256"] = source_sha256
                if columns == LEGACY_COLUMNS:
                    _append_migrating_legacy(path, rows, row)
                else:
                    try:
                        with path.open("a", encoding="utf-8", newline="") as handle:
                            writer = csv.DictWriter(handle, fieldnames=MASTER_COLUMNS)
                            if handle.tell() == 0:
                                writer.writeheader()
                            else:
                                with path.open("rb") as existing:
                                    existing.seek(-1, os.SEEK_END)
                                    if existing.read(1) not in (b"\r", b"\n"):
                                        handle.write("\n")
                            writer.writerow(row)
                    except OSError as exc:
                        raise SystemException(
                            f"Unable to append to master CSV {path}: {exc}", action=self.name
                        ) from exc

        ctx.state["master_csv"] = str(path)
        ctx.add_artifact("master_csv", str(path), kind="csv", metadata={"source_file": source_file})


def _read_master(path: Path) -> tuple[tuple[str, ...] | None, list[dict[str, str]]]:
    try:
        if not path.exists() or path.stat().st_size == 0:
            return None, []
        with path.open("r", encoding="utf-8", newline="") as handle:
            reader = csv.DictReader(handle)
            columns = tuple(reader.fieldnames or ())
            if columns not in (MASTER_COLUMNS, LEGACY_COLUMNS):
                raise SystemException(f"Unsupported master CSV columns: {path}", action="append_to_master")
            rows = list(reader)
            if any(None in row or any(row.get(column) is None for column in columns) for row in rows):
                raise SystemException(f"Malformed master CSV row: {path}", action="append_to_master")
            return columns, rows
    except OSError as exc:
        raise SystemException(
            f"Unable to inspect master CSV {path}: {exc}",
            action="append_to_master",
        ) from exc


def _append_migrating_legacy(path: Path, rows: list[dict[str, str]], row: dict[str, str]) -> None:
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            "w", encoding="utf-8", newline="", dir=path.parent,
            prefix=f".{path.name}.", suffix=".tmp", delete=False,
        ) as handle:
            temporary = Path(handle.name)
            writer = csv.DictWriter(handle, fieldnames=MASTER_COLUMNS)
            writer.writeheader()
            for old_row in rows:
                writer.writerow({**old_row, "source_sha256": ""})
            writer.writerow(row)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except OSError as exc:
        raise SystemException(f"Unable to update legacy master CSV {path}: {exc}", action="append_to_master") from exc
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


@contextmanager
def _master_csv_lock(path: Path) -> Iterator[None]:
    lock_path = path.with_name(f"{path.name}.lock")
    deadline = time.monotonic() + MASTER_LOCK_TIMEOUT_SECONDS
    fd: int | None = None

    while fd is None:
        try:
            fd = os.open(str(lock_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError as exc:
            if time.monotonic() >= deadline:
                raise SystemException(
                    f"Timed out waiting for master CSV lock: {lock_path}",
                    action="append_to_master",
                ) from exc
            time.sleep(MASTER_LOCK_POLL_SECONDS)
        except OSError as exc:
            raise SystemException(
                f"Unable to lock master CSV {path}: {exc}",
                action="append_to_master",
            ) from exc

    try:
        yield
    finally:
        os.close(fd)
        try:
            lock_path.unlink()
        except FileNotFoundError:
            pass
        except OSError as exc:
            raise SystemException(
                f"Unable to remove master CSV lock {lock_path}: {exc}",
                action="append_to_master",
            ) from exc
