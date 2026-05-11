#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sqlite3
import sys


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from second_brain.config import load_config
from second_brain.retrieval import retrieve


def _note_exists(db_path: Path, note_id: str) -> bool:
    if not note_id or not db_path.exists():
        return False
    connection = sqlite3.connect(db_path)
    try:
        row = connection.execute("SELECT 1 FROM notes WHERE id = ? LIMIT 1", (note_id,)).fetchone()
        return row is not None
    except sqlite3.Error:
        return False
    finally:
        connection.close()


def _iter_queries(path: Path) -> list[dict[str, object]]:
    queries: list[dict[str, object]] = []
    for line in path.read_text().splitlines():
        if line.strip():
            queries.append(json.loads(line))
    return queries


def main() -> int:
    parser = argparse.ArgumentParser(description="Run MnemonicOS retrieval eval queries.")
    parser.add_argument("--queries", default="evals/queries.jsonl", help="JSONL eval query file.")
    parser.add_argument("--config", default=None, help="Optional config path.")
    parser.add_argument("--compare-baseline", action="store_true", help="Fail if expected_top1 is not top-1.")
    args = parser.parse_args()

    config = load_config(args.config)
    query_path = Path(args.queries)
    if not query_path.is_absolute():
        query_path = (config.paths.workspace_root / query_path).resolve()

    rows: list[dict[str, object]] = []
    top1_passes = 0
    top5_passes = 0
    skipped = 0
    queries = _iter_queries(query_path)
    for item in queries:
        query = str(item["query"])
        expected_top1 = str(item.get("expected_top1") or "")
        expected_in_top5 = {str(value) for value in item.get("expected_in_top5", [])}
        expected_ids = {expected_top1, *expected_in_top5} - {""}
        existing_expected_ids = {note_id for note_id in expected_ids if _note_exists(config.paths.db_path, note_id)}
        if expected_ids and not existing_expected_ids:
            skipped += 1
            rows.append(
                {
                    "id": item.get("id"),
                    "query": query,
                    "expected_top1": expected_top1,
                    "hits": [],
                    "top1_ok": False,
                    "top5_ok": False,
                    "skipped": True,
                    "skip_reason": "expected notes not present in configured corpus",
                }
            )
            continue
        result = retrieve(
            config,
            query,
            top_k=5,
            query_type_hint=str(item["query_type"]) if item.get("query_type") else None,
            include_archive_fallback=False,
        )
        hit_ids = [hit.id for hit in result.hits]
        top1_ok = bool(hit_ids) and hit_ids[0] == expected_top1
        top5_ok = expected_top1 in hit_ids[:5] or bool(expected_in_top5 & set(hit_ids[:5]))
        top1_passes += 1 if top1_ok else 0
        top5_passes += 1 if top5_ok else 0
        rows.append(
            {
                "id": item.get("id"),
                "query": query,
                "expected_top1": expected_top1,
                "hits": hit_ids,
                "top1_ok": top1_ok,
                "top5_ok": top5_ok,
                "skipped": False,
            }
        )

    total = len(queries)
    evaluated = total - skipped
    summary = {
        "total": total,
        "evaluated": evaluated,
        "skipped": skipped,
        "top1_passes": top1_passes,
        "top1_recall": top1_passes / evaluated if evaluated else 0.0,
        "top5_passes": top5_passes,
        "top5_recall": top5_passes / evaluated if evaluated else 0.0,
        "rows": rows,
    }
    print(json.dumps(summary, indent=2))
    if args.compare_baseline and evaluated and top1_passes != evaluated:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
