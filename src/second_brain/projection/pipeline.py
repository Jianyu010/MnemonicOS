from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
import json
from pathlib import Path
import sqlite3
from uuid import uuid4

from second_brain.config import AppConfig
from second_brain.connectors import ConnectorConfig, connector_for_path
from second_brain.db import connect_db, run_migrations
from second_brain.ingest import _migrations_dir

from .chunker import iter_text_windows
from .figure_context import attach_context_windows, enforce_payload_caps
from .markers import format_marker
from .pipeline_models import (
    ArtifactMarker,
    ArtifactView,
    ProjectionLease,
    ProjectionOptions,
    SourceRef,
    SourceSpan,
    TextWindow,
)
from .session_assembler import assemble_session_projection


@dataclass(slots=True)
class _OpenProjection:
    lease: ProjectionLease
    windows: list[TextWindow]
    artifacts: list[ArtifactView]
    idle_expires_at: datetime
    hard_expires_at: datetime
    idle_ttl_seconds: int


class ProjectionService:
    def __init__(self, config: AppConfig) -> None:
        self.config = config
        self._leases: dict[str, _OpenProjection] = {}

    def open_projection(
        self,
        source_ref: SourceRef,
        *,
        options: ProjectionOptions | None = None,
    ) -> ProjectionLease:
        opts = options or ProjectionOptions()
        connection = connect_db(self.config.paths.db_path)
        run_migrations(connection, _migrations_dir(self.config))
        try:
            if source_ref.source_kind == "artifact":
                lease, windows, artifacts = self._open_artifact(connection, source_ref, opts)
            elif source_ref.source_kind in {"session", "episode"}:
                lease, windows, artifacts = self._open_session(connection, source_ref, opts)
            else:
                raise ValueError(f"unsupported source kind: {source_ref.source_kind}")
        finally:
            connection.close()
        self._leases[lease.lease_id] = _OpenProjection(
            lease=lease,
            windows=windows,
            artifacts=artifacts,
            idle_expires_at=datetime.now() + timedelta(seconds=opts.lease_idle_ttl_seconds),
            hard_expires_at=lease.expires_at,
            idle_ttl_seconds=opts.lease_idle_ttl_seconds,
        )
        return lease

    def iter_text_windows(self, lease_id: str):
        opened = self._require_lease(lease_id)
        for window in opened.windows:
            yield window

    def iter_artifacts(self, lease_id: str):
        opened = self._require_lease(lease_id)
        for artifact in opened.artifacts:
            yield artifact

    def close_projection(self, lease_id: str) -> None:
        self._leases.pop(lease_id, None)

    def _open_artifact(
        self,
        connection: sqlite3.Connection,
        source_ref: SourceRef,
        options: ProjectionOptions,
    ) -> tuple[ProjectionLease, list[TextWindow], list[ArtifactView]]:
        if not source_ref.archive_id:
            raise ValueError("artifact projection requires archive_id")
        row = connection.execute(
            """
            SELECT id, path, artifact_path, duplicate_state, canonical_archive_id,
                   warnings_json, figure_count, table_count
            FROM archive
            WHERE id = ? AND id LIKE 'artifact/%'
            """,
            (source_ref.archive_id,),
        ).fetchone()
        if row is None:
            raise ValueError(f"artifact archive not found: {source_ref.archive_id}")
        duplicate_state = str(row["duplicate_state"] or "unique")
        canonical_archive_id = row["canonical_archive_id"]
        canonical = SourceRef("artifact", archive_id=str(canonical_archive_id)) if canonical_archive_id else None
        now = datetime.now()
        if duplicate_state == "exact_duplicate":
            lease = ProjectionLease(
                lease_id=str(uuid4()),
                source_ref=source_ref,
                duplicate_state=duplicate_state,
                canonical_source_ref=canonical,
                opened_at=now,
                expires_at=now + timedelta(seconds=options.lease_hard_ttl_seconds),
                warnings=_json_warnings(row["warnings_json"]),
                projection_metadata={
                    "canonical_redirect_archive_id": canonical_archive_id,
                    "pre_parse_short_circuited": True,
                    "text_window_count": 0,
                    "figure_count": 0,
                    "table_count": int(row["table_count"] or 0),
                },
            )
            return lease, [], []

        raw_path = Path(str(row["artifact_path"] or row["path"]))
        connector = connector_for_path(raw_path)
        result = connector.extract(raw_path, config=ConnectorConfig(figures_enabled=options.figures_enabled))
        text = result.usable_raw_text.strip()
        artifacts = _artifact_views_from_connector(result)
        artifacts.extend(_artifact_views_from_rows(connection, source_ref.archive_id))
        windows = list(
            iter_text_windows(
                text,
                source_kind="artifact",
                source_locator=source_ref.archive_id,
                token_target=options.window_token_target,
                token_overlap=options.window_token_overlap,
            )
        )
        artifacts = attach_context_windows(artifacts, windows)
        artifacts = enforce_payload_caps(
            artifacts,
            max_per_figure_bytes=options.max_per_figure_bytes,
            max_total_payload_bytes=options.max_total_payload_bytes,
        )
        lease = ProjectionLease(
            lease_id=str(uuid4()),
            source_ref=source_ref,
            duplicate_state=duplicate_state,
            canonical_source_ref=canonical,
            opened_at=now,
            expires_at=now + timedelta(seconds=options.lease_hard_ttl_seconds),
            warnings=[*_json_warnings(row["warnings_json"]), *result.warnings],
            projection_metadata={
                "canonical_redirect_archive_id": canonical_archive_id,
                "pre_parse_short_circuited": False,
                "text_window_count": len(windows),
                "figure_count": len(artifacts),
                "table_count": int(row["table_count"] or 0),
            },
        )
        return lease, windows, artifacts

    def _open_session(
        self,
        connection: sqlite3.Connection,
        source_ref: SourceRef,
        options: ProjectionOptions,
    ) -> tuple[ProjectionLease, list[TextWindow], list[ArtifactView]]:
        if source_ref.source_kind == "session" and source_ref.archive_id:
            row = connection.execute(
                """
                SELECT path
                FROM archive
                WHERE id = ? AND type = 'session'
                """,
                (source_ref.archive_id,),
            ).fetchone()
            if row is None:
                raise ValueError(f"session archive not found: {source_ref.archive_id}")
            raw_path = Path(str(row["path"]))
            text = raw_path.read_text()
            metadata = {"archive_id": source_ref.archive_id}
            warnings = []
            source_locator = source_ref.archive_id
        else:
            text, metadata, warnings = assemble_session_projection(connection, source_ref)
            source_locator = f"{source_ref.conversation_id}/{source_ref.episode_id}"
        windows = list(
            iter_text_windows(
                text,
                source_kind=source_ref.source_kind,
                source_locator=source_locator,
                token_target=options.window_token_target,
                token_overlap=options.window_token_overlap,
            )
        )
        now = datetime.now()
        lease = ProjectionLease(
            lease_id=str(uuid4()),
            source_ref=source_ref,
            duplicate_state="unique",
            canonical_source_ref=None,
            opened_at=now,
            expires_at=now + timedelta(seconds=options.lease_hard_ttl_seconds),
            warnings=warnings,
            projection_metadata={**metadata, "text_window_count": len(windows), "figure_count": 0, "table_count": 0},
        )
        return lease, windows, []

    def _require_lease(self, lease_id: str) -> _OpenProjection:
        opened = self._leases.get(lease_id)
        now = datetime.now()
        if opened is None:
            raise KeyError(lease_id)
        if now >= opened.hard_expires_at or now >= opened.idle_expires_at:
            self.close_projection(lease_id)
            raise KeyError(lease_id)
        opened.idle_expires_at = min(
            now + timedelta(seconds=opened.idle_ttl_seconds),
            opened.hard_expires_at,
        )
        return opened


def _json_warnings(value: object) -> list[str]:
    if not value:
        return []
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return [value]
    else:
        parsed = value
    if isinstance(parsed, list):
        return [str(item) for item in parsed]
    return [str(parsed)]


def _artifact_views_from_connector(result: object) -> list[ArtifactView]:
    figures = {figure.figure_id: figure for figure in getattr(result, "figures", [])}
    artifacts: list[ArtifactView] = []
    for payload in getattr(result, "figure_payloads", []):
        figure = figures.get(payload.figure_id)
        source_locator = str(getattr(figure, "structural_location", "") or payload.figure_id)
        marker = format_marker(payload.figure_id, "figure", source=source_locator, needs_visual_pass=True)
        artifacts.append(
            ArtifactView(
                artifact_id=payload.figure_id,
                kind="figure",
                mime_type=payload.mime_type,
                payload=payload.data,
                source_attachment_path=None,
                source_locator=source_locator,
                marker=marker,
                warnings=list(getattr(figure, "warnings", []) if figure else []),
            )
        )
    return artifacts


def _artifact_views_from_rows(connection: sqlite3.Connection, archive_id: str) -> list[ArtifactView]:
    rows = connection.execute(
        """
        SELECT figure_id, storage_policy, attachment_path, source_page, source_slide,
               source_index, mime_type, structural_location, needs_visual_pass, warnings_json
        FROM artifact_figures
        WHERE archive_id = ? AND storage_policy = 'source_attachment'
        ORDER BY source_index, figure_id
        """,
        (archive_id,),
    ).fetchall()
    artifacts: list[ArtifactView] = []
    for row in rows:
        path = str(row["attachment_path"] or "")
        payload = Path(path).read_bytes() if path and Path(path).exists() else None
        source_locator = str(row["structural_location"] or f"attachment:{row['figure_id']}")
        marker = format_marker(str(row["figure_id"]), "figure", source=source_locator, needs_visual_pass=bool(row["needs_visual_pass"]))
        artifacts.append(
            ArtifactView(
                artifact_id=str(row["figure_id"]),
                kind="figure",
                mime_type=row["mime_type"],
                payload=payload,
                source_attachment_path=path or None,
                source_locator=source_locator,
                marker=marker,
                warnings=_json_warnings(row["warnings_json"]),
            )
        )
    return artifacts
