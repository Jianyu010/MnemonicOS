from __future__ import annotations

from .pipeline_models import ArtifactView, TextWindow


def attach_context_windows(artifacts: list[ArtifactView], windows: list[TextWindow]) -> list[ArtifactView]:
    by_artifact = {artifact.artifact_id: artifact for artifact in artifacts}
    for window in windows:
        for marker in window.artifact_markers:
            artifact = by_artifact.get(marker.artifact_id)
            if artifact and window.window_id not in artifact.context_windows:
                artifact.context_windows.append(window.window_id)
    if windows:
        fallback_id = windows[0].window_id
        for artifact in artifacts:
            if not artifact.context_windows:
                artifact.context_windows.append(fallback_id)
    return artifacts


def enforce_payload_caps(
    artifacts: list[ArtifactView],
    *,
    max_per_figure_bytes: int,
    max_total_payload_bytes: int | None,
) -> list[ArtifactView]:
    total_cap = max_total_payload_bytes if max_total_payload_bytes is not None else 4 * 1024 * 1024 * 1024
    used = 0
    capped: list[ArtifactView] = []
    for artifact in artifacts:
        payload = artifact.payload
        warnings = list(artifact.warnings)
        if payload is not None and len(payload) > max_per_figure_bytes:
            warnings.append("payload_over_per_artifact_cap")
            payload = None
        if payload is not None and used + len(payload) > total_cap:
            warnings.append("payload_over_total_lease_cap")
            payload = None
        if payload is not None:
            used += len(payload)
        capped.append(
            ArtifactView(
                artifact_id=artifact.artifact_id,
                kind=artifact.kind,
                mime_type=artifact.mime_type,
                payload=payload,
                source_attachment_path=artifact.source_attachment_path,
                source_locator=artifact.source_locator,
                context_windows=list(artifact.context_windows),
                marker=artifact.marker,
                warnings=warnings,
            )
        )
    return capped
