from __future__ import annotations

from hashlib import sha256
import json
from pathlib import Path
import sqlite3

from .config import AppConfig
from .db import connect_db, run_migrations
from .graph import demote_or_delete_note_node, rebuild_note_graph
from .models import NoteRecord, SyncResult
from .parser import NoteParseError, parse_note
from .paths import VaultPaths
from .semantics import encode_text, upsert_note_vector


def _file_hash(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()


def _upsert_sync_state(
    connection: sqlite3.Connection,
    *,
    path: Path,
    kind: str,
    content_hash: str,
    note_id: str | None,
    parse_status: str,
    last_error: str | None,
) -> None:
    connection.execute(
        """
        INSERT INTO sync_state(path, kind, note_id, content_hash, last_synced_at, parse_status, last_error)
        VALUES (?, ?, ?, ?, datetime('now'), ?, ?)
        ON CONFLICT(path) DO UPDATE SET
            kind = excluded.kind,
            note_id = excluded.note_id,
            content_hash = excluded.content_hash,
            last_synced_at = excluded.last_synced_at,
            parse_status = excluded.parse_status,
            last_error = excluded.last_error
        """,
        (str(path.resolve()), kind, note_id, content_hash, parse_status, last_error),
    )


def _delete_note_by_path(connection: sqlite3.Connection, path: Path) -> int:
    row = connection.execute(
        "SELECT note_id FROM sync_state WHERE path = ? AND kind = 'note'",
        (str(path.resolve()),),
    ).fetchone()
    if row is None:
        connection.execute("DELETE FROM sync_state WHERE path = ?", (str(path.resolve()),))
        return 0

    note_id = row["note_id"]
    connection.execute("DELETE FROM notes WHERE id = ?", (note_id,))
    demote_or_delete_note_node(connection, str(note_id))
    connection.execute("DELETE FROM sync_state WHERE path = ?", (str(path.resolve()),))
    return 1


def _build_aliases(note: NoteRecord) -> list[str]:
    aliases = [note.title, *note.aliases]
    seen: set[str] = set()
    ordered: list[str] = []
    for alias in aliases:
        normalized = alias.strip()
        if not normalized:
            continue
        lowered = normalized.casefold()
        if lowered in seen:
            continue
        ordered.append(normalized)
        seen.add(lowered)
    return ordered


def _upsert_note(connection: sqlite3.Connection, note: NoteRecord) -> tuple[list[str], str]:
    connection.execute(
        "DELETE FROM notes WHERE body_path = ? AND id != ?",
        (str(note.body_path), note.id),
    )
    connection.execute(
        """
        INSERT INTO notes(
            id, type, title, status, confidence, tags, entities, source_refs,
            valid_from, valid_to, updated_at, created_at, last_verified_at,
            verified_by, last_observed_at, body_path, summary, content_hash, revision
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0)
        ON CONFLICT(id) DO UPDATE SET
            type = excluded.type,
            title = excluded.title,
            status = excluded.status,
            confidence = excluded.confidence,
            tags = excluded.tags,
            entities = excluded.entities,
            source_refs = excluded.source_refs,
            valid_from = excluded.valid_from,
            valid_to = excluded.valid_to,
            updated_at = excluded.updated_at,
            created_at = excluded.created_at,
            last_verified_at = excluded.last_verified_at,
            verified_by = excluded.verified_by,
            last_observed_at = excluded.last_observed_at,
            body_path = excluded.body_path,
            summary = excluded.summary,
            content_hash = excluded.content_hash,
            revision = notes.revision + 1
        """,
        (
            note.id,
            note.type,
            note.title,
            note.status,
            note.confidence,
            json.dumps(note.tags),
            json.dumps(note.entities),
            json.dumps(note.source_refs),
            note.valid_from,
            note.valid_to,
            note.updated_at,
            note.created_at,
            note.last_verified_at,
            note.verified_by,
            note.last_observed_at,
            str(note.body_path),
            note.summary,
            note.content_hash,
        ),
    )

    aliases = _build_aliases(note)
    connection.execute("DELETE FROM aliases WHERE note_id = ?", (note.id,))
    for index, alias in enumerate(aliases):
        connection.execute(
            "INSERT INTO aliases(alias, note_id, is_primary) VALUES (?, ?, ?)",
            (alias, note.id, 1 if index == 0 else 0),
        )

    connection.execute(
        """
        INSERT INTO notes_search(note_id, title, aliases_flat, summary, body)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(note_id) DO UPDATE SET
            title = excluded.title,
            aliases_flat = excluded.aliases_flat,
            summary = excluded.summary,
            body = excluded.body
        """,
        (note.id, note.title, " ".join(aliases), note.summary, note.body),
    )

    semantic_source = "\n".join([note.title, " ".join(aliases), note.summary]).strip()
    return aliases, semantic_source


def _sync_system_file(connection: sqlite3.Connection, path: Path) -> None:
    _upsert_sync_state(
        connection,
        path=path,
        kind="system",
        content_hash=_file_hash(path),
        note_id=None,
        parse_status="ok",
        last_error=None,
    )


def _load_previous_sync_state(connection: sqlite3.Connection, kind: str) -> dict[str, sqlite3.Row]:
    rows = connection.execute(
        "SELECT * FROM sync_state WHERE kind = ?",
        (kind,),
    ).fetchall()
    return {str(row["path"]): row for row in rows}


def _graph_note_missing(connection: sqlite3.Connection, note_id: str | None) -> bool:
    if not note_id:
        return True
    row = connection.execute(
        "SELECT 1 FROM graph_nodes WHERE id = ? AND node_type = 'note'",
        (note_id,),
    ).fetchone()
    return row is None


def sync_vault(config: AppConfig, mode: str = "incremental", selected_paths: list[str] | None = None) -> SyncResult:
    paths = VaultPaths(workspace_root=config.paths.workspace_root, vault_root=config.paths.vault_root)
    connection = connect_db(config.paths.db_path)
    run_migrations(connection, config.paths.workspace_root / "migrations")

    note_files = {str(path.resolve()): path for path in paths.note_files()}
    system_files = {str(path.resolve()): path for path in paths.system_files() if path.exists()}
    selected = {str(Path(item).resolve()) for item in selected_paths or []}

    previous_notes = _load_previous_sync_state(connection, "note")
    previous_system = _load_previous_sync_state(connection, "system")

    scanned_paths = 0
    synced_notes = 0
    deleted_notes = 0
    parse_errors = 0

    with connection:
        for resolved_path, path in system_files.items():
            if selected and resolved_path not in selected:
                continue
            if mode == "incremental":
                previous = previous_system.get(resolved_path)
                current_hash = _file_hash(path)
                if previous is not None and previous["content_hash"] == current_hash:
                    continue
            scanned_paths += 1
            _sync_system_file(connection, path)

        for resolved_path, path in note_files.items():
            if selected and resolved_path not in selected:
                continue

            current_hash = _file_hash(path)
            previous = previous_notes.get(resolved_path)
            if mode == "incremental" and previous is not None and previous["content_hash"] == current_hash:
                if not config.graph.enabled or not _graph_note_missing(connection, str(previous["note_id"] or "")):
                    continue

            scanned_paths += 1
            try:
                note = parse_note(path)
                aliases, semantic_source = _upsert_note(connection, note)
                if config.embeddings.enabled:
                    vector = encode_text(semantic_source, dimensions=config.embeddings.dimensions)
                    upsert_note_vector(
                        connection,
                        note_id=note.id,
                        model=config.embeddings.model,
                        dimensions=config.embeddings.dimensions,
                        vector=vector,
                        source_hash=note.content_hash,
                    )
                if config.graph.enabled:
                    rebuild_note_graph(connection, note)
                _upsert_sync_state(
                    connection,
                    path=path,
                    kind="note",
                    content_hash=current_hash,
                    note_id=note.id,
                    parse_status="ok",
                    last_error=None,
                )
                synced_notes += 1
            except NoteParseError as exc:
                deleted_notes += _delete_note_by_path(connection, path)
                _upsert_sync_state(
                    connection,
                    path=path,
                    kind="note",
                    content_hash=current_hash,
                    note_id=previous["note_id"] if previous is not None else None,
                    parse_status="error",
                    last_error=str(exc),
                )
                parse_errors += 1

        if not selected:
            current_note_paths = set(note_files)
            for resolved_path in set(previous_notes) - current_note_paths:
                deleted_notes += _delete_note_by_path(connection, Path(resolved_path))

            current_system_paths = set(system_files)
            for resolved_path in set(previous_system) - current_system_paths:
                connection.execute("DELETE FROM sync_state WHERE path = ?", (resolved_path,))

    connection.close()
    return SyncResult(
        scanned_paths=scanned_paths,
        synced_notes=synced_notes,
        deleted_notes=deleted_notes,
        parse_errors=parse_errors,
    )
