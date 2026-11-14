from __future__ import annotations

import hashlib
import shutil
from pathlib import Path

from rpacore import BusinessException, ProcessContext, Step, Status, SystemException, resolve_config_path


class MoveFile(Step):
    """Move successfully processed files to the done folder."""

    def execute(self, ctx: ProcessContext) -> None:
        if ctx.optional_state("validation_failed", bool, False, action=self.name):
            self.status = Status.SKIPPED
            return

        source = ctx.optional_state("report_file", str, "", action=self.name) or ctx.optional_state(
            "file_path",
            str,
            "",
            action=self.name,
        )
        done_dir = ctx.require_config("done_dir", str, action=self.name)
        if not isinstance(source, str) or not source:
            raise SystemException("No source file available to move", action=self.name)
        if not done_dir:
            raise SystemException("Config key 'done_dir' must be a non-empty string", action=self.name)

        # Skip validation when inbox_dir is absent (unit tests); production always provides it.
        inbox_dir = ctx.config.get("inbox_dir")
        if isinstance(inbox_dir, str) and inbox_dir:
            src = Path(
                resolve_config_path(
                    source,
                    base_dir=inbox_dir,
                    root=inbox_dir,
                    key=self.name,
                )
            )
        else:
            src = Path(source)
        dst_dir = Path(done_dir)
        dst_dir.mkdir(parents=True, exist_ok=True)
        dst = dst_dir / src.name

        try:
            source_sha256 = hashlib.sha256(src.read_bytes()).hexdigest()
            expected_sha256 = ctx.optional_state("source_sha256", str, "", action=self.name)
            if expected_sha256 and source_sha256 != expected_sha256:
                raise SystemException("Source file changed after it was read", action=self.name)
            if dst.exists() or dst.is_symlink():
                if (
                    ctx.optional_state("duplicate_input", bool, False, action=self.name)
                    and not dst.is_symlink()
                    and hashlib.sha256(dst.read_bytes()).hexdigest() == source_sha256
                ):
                    src.unlink()
                else:
                    raise BusinessException(
                        f"Archive filename collision: {src.name}",
                        action=self.name,
                        halts_remaining_steps=True,
                    )
            else:
                shutil.move(str(src), str(dst))
        except OSError as exc:
            raise SystemException(f"Unable to move {src} to {dst}: {exc}", action=self.name) from exc

        ctx.state["moved_file"] = str(dst)
