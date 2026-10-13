"""Single-item extraction subprocess entry point.

Called by process_extraction_queue per item. The parent has already called
mark_processing on the item before spawning this subprocess.

Responsibilities:
- Run _process_item (which calls mark_finished on success)
- Call mark_error on any exception
- Exit 0 on success, 1 on error
"""
from __future__ import annotations

import argparse
import sqlite3
import sys

# Number of attempts (including the current one, which mark_processing has
# already counted) after which a non-timeout failure is tombstoned.
_TOMBSTONE_AFTER_ATTEMPTS = 2


class _FileProgress(dict):
    """dict subclass that flushes to a JSON file on every update."""

    def __init__(self, path: str) -> None:
        super().__init__()
        self._path = path

    def update(self, *args, **kwargs) -> None:  # type: ignore[override]
        super().update(*args, **kwargs)
        self._flush()

    def __setitem__(self, key, value) -> None:  # type: ignore[override]
        super().__setitem__(key, value)
        self._flush()

    def __delitem__(self, key) -> None:  # type: ignore[override]
        super().__delitem__(key)
        self._flush()

    def clear(self) -> None:
        super().clear()
        self._flush()

    def _flush(self) -> None:
        import json
        import os
        import tempfile
        tmp = self._path + ".tmp"
        try:
            with open(tmp, "w") as f:
                json.dump(dict(self), f)
            os.replace(tmp, self._path)
        except Exception:
            pass


def _run(item_id: int, *, config_path: str | None = None, progress_file: str | None = None) -> int:
    from second_brain.config import load_config
    from second_brain.db import connect_db, run_migrations
    from second_brain.ingest import _migrations_dir
    from second_brain.projection.pipeline import ProjectionService

    from .providers import InferenceTimeoutError
    from .queue import _row_to_item, mark_error
    from .worker import _process_item

    config = load_config(config_path) if config_path else load_config()
    connection = connect_db(config.paths.db_path)
    run_migrations(connection, _migrations_dir(config))
    connection.row_factory = sqlite3.Row

    row = connection.execute(
        "SELECT * FROM extraction_queue WHERE id = ?", (item_id,)
    ).fetchone()
    if row is None:
        print(f"item {item_id} not found in extraction_queue", file=sys.stderr)
        return 1

    item = _row_to_item(row)
    start_window = int(row["completed_window_count"]) if row["completed_window_count"] is not None else 0
    progress: _FileProgress | None = _FileProgress(progress_file) if progress_file else None
    service = ProjectionService(config)
    try:
        _process_item(config, service, connection, item=item, dry_run=False, progress=progress, start_window=start_window)
        return 0
    except InferenceTimeoutError as exc:
        # Written with the "timeout:" prefix, never "tombstoned:" here.
        # retry_failed_extractions requeues timeout errors with exponential
        # backoff and tombstones them only after _MAX_TIMEOUT_ATTEMPTS.
        error_text = f"timeout: inference call exceeded per-call timeout: {exc}"
        try:
            with connection:
                mark_error(connection, queue_id=item_id, error=error_text)
        except Exception:
            pass
        print(f"extraction timeout for item {item_id}: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:
        error_text = str(exc)
        # The parent's mark_processing already bumped attempt_count for THIS run,
        # so a first attempt reads as attempt_count == 1. Only tombstone when at
        # least one earlier attempt also failed (attempt_count >= 2); a first
        # failure stays a plain retryable error for retry_failed_extractions.
        if item.attempt_count >= _TOMBSTONE_AFTER_ATTEMPTS:
            error_text = f"tombstoned: failed after retry (attempt {item.attempt_count}): {error_text}"
        try:
            with connection:
                mark_error(connection, queue_id=item_id, error=error_text)
        except Exception:
            pass
        print(f"extraction error for item {item_id}: {exc}", file=sys.stderr)
        return 1


def main() -> None:
    parser = argparse.ArgumentParser(description="Extract a single queue item in a subprocess")
    parser.add_argument("--config", default=None, help="Path to the MnemonicOS config file")
    parser.add_argument("--item-id", type=int, required=True)
    parser.add_argument("--progress-file", default=None, help="Path to write progress JSON for parent process")
    args = parser.parse_args()
    sys.exit(_run(args.item_id, config_path=args.config, progress_file=args.progress_file))


if __name__ == "__main__":
    main()
