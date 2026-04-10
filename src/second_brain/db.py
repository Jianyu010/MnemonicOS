from __future__ import annotations

from pathlib import Path
import sqlite3


def connect_db(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(db_path)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute("PRAGMA journal_mode = WAL")
    return connection


def _migration_table_exists(connection: sqlite3.Connection) -> bool:
    row = connection.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'schema_migrations'"
    ).fetchone()
    return row is not None


def _applied_versions(connection: sqlite3.Connection) -> set[str]:
    if not _migration_table_exists(connection):
        return set()
    rows = connection.execute("SELECT version FROM schema_migrations").fetchall()
    return {str(row["version"]) for row in rows}


def run_migrations(connection: sqlite3.Connection, migrations_dir: Path) -> list[str]:
    applied = _applied_versions(connection)
    executed: list[str] = []

    for migration_path in sorted(migrations_dir.glob("*.sql")):
        version = migration_path.stem
        if version in applied:
            continue
        script = migration_path.read_text()
        with connection:
            connection.executescript(script)
        executed.append(version)
        applied.add(version)

    return executed
