from __future__ import annotations

import argparse
from pathlib import Path

from .config import load_config
from .db import connect_db, run_migrations
from .retrieval import result_to_json, retrieve
from .sync import sync_vault


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="MnemonicOS Phase 1 CLI")
    parser.add_argument(
        "--config",
        default="config/memory.example.toml",
        help="Path to the TOML config file.",
    )

    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("init-db", help="Apply SQLite migrations.")

    sync_parser = subparsers.add_parser("sync", help="Synchronize vault notes into SQLite.")
    sync_parser.add_argument("--mode", choices=("incremental", "full"), default="incremental")
    sync_parser.add_argument("paths", nargs="*", help="Optional subset of files to sync.")

    retrieve_parser = subparsers.add_parser("retrieve", help="Run exact and BM25 retrieval.")
    retrieve_parser.add_argument("query", help="Query text.")
    retrieve_parser.add_argument("--top-k", type=int, default=None)
    retrieve_parser.add_argument("--query-type-hint", default=None)
    retrieve_parser.add_argument("--json", action="store_true", help="Emit JSON output.")

    return parser


def _init_db(config_path: str) -> int:
    config = load_config(config_path)
    connection = connect_db(config.paths.db_path)
    versions = run_migrations(connection, config.paths.workspace_root / "migrations")
    connection.close()
    if versions:
        print(f"applied migrations: {', '.join(versions)}")
    else:
        print("database already up to date")
    return 0


def _sync(config_path: str, mode: str, paths: list[str]) -> int:
    config = load_config(config_path)
    result = sync_vault(config, mode=mode, selected_paths=paths)
    print(f"scanned_paths={result.scanned_paths}")
    print(f"synced_notes={result.synced_notes}")
    print(f"deleted_notes={result.deleted_notes}")
    print(f"parse_errors={result.parse_errors}")
    return 0 if result.parse_errors == 0 else 1


def _retrieve(config_path: str, query: str, top_k: int | None, query_type_hint: str | None, emit_json: bool) -> int:
    config = load_config(config_path)
    result = retrieve(config, query, top_k=top_k, query_type_hint=query_type_hint)
    if emit_json:
        print(result_to_json(result))
        return 0

    print(f"query_id={result.query_id}")
    if result.pinned_paths:
        print("pinned_paths:")
        for path in result.pinned_paths:
            print(f"  - {path}")
    if not result.hits:
        print("no hits")
        return 0

    for index, hit in enumerate(result.hits, start=1):
        channels = ",".join(hit.channels)
        print(f"{index}. {hit.id} [{hit.type}] channels={channels} score={hit.final_score:.3f}")
        print(f"   title: {hit.title}")
        print(f"   path: {hit.body_path}")
        if hit.summary:
            print(f"   summary: {hit.summary}")
        elif hit.excerpt:
            print(f"   excerpt: {hit.excerpt}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    if args.command == "init-db":
        return _init_db(args.config)
    if args.command == "sync":
        return _sync(args.config, args.mode, args.paths)
    if args.command == "retrieve":
        return _retrieve(args.config, args.query, args.top_k, args.query_type_hint, args.json)

    parser.error(f"unknown command: {args.command}")
    return 2
