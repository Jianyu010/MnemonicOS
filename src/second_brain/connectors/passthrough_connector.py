from __future__ import annotations

from pathlib import Path

from .base_connector import ConnectorConfig, ConnectorResult, connector_metadata, status_for_text


class PassthroughConnector:
    name = "passthrough"

    def __init__(self, source_format: str) -> None:
        self.source_format = source_format

    def extract(self, path: Path, *, config: ConnectorConfig) -> ConnectorResult:
        warnings: list[str] = []
        data = path.read_bytes()
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError:
            text = data.decode("utf-8", errors="replace")
            warnings.append("decoded_with_replacement_characters")
        failure_reason = None if text.strip() else "no_text_extracted"
        status = status_for_text(text, warnings, failure_reason)
        metadata = connector_metadata(
            self.name,
            self.source_format,
            needs_manual_projection=status == "failed_extraction",
        )
        return ConnectorResult(
            usable_raw_text=text,
            metadata=metadata,
            warnings=warnings,
            status=status,
            failure_reason=failure_reason,
        )
