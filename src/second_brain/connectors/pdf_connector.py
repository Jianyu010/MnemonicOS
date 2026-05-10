from __future__ import annotations

from pathlib import Path
import re

from .base_connector import ConnectorConfig, ConnectorResult, connector_metadata, status_for_text

PAGE_RE = re.compile(rb"/Type\s*/Page\b")
IMAGE_RE = re.compile(rb"/Subtype\s*/Image\b")


class PdfConnector:
    name = "pdf_connector"
    source_format = "pdf"

    def extract(self, path: Path, *, config: ConnectorConfig) -> ConnectorResult:
        warnings: list[str] = []
        data = path.read_bytes()
        page_count = len(PAGE_RE.findall(data))
        figure_count = len(IMAGE_RE.findall(data))
        text = ""
        try:
            from pdfminer.high_level import extract_text  # type: ignore[import-not-found]

            text = extract_text(str(path)) or ""
        except Exception as exc:  # optional dependency or parser failure
            warnings.append(f"pdf_text_extraction_unavailable:{exc.__class__.__name__}")
        image_only_pdf = bool(page_count and figure_count and not text.strip())
        failure_reason = None if text.strip() else "no_text_extracted"
        status = status_for_text(text, warnings, failure_reason)
        if not text.strip():
            status = "failed_extraction"
        metadata = connector_metadata(
            self.name,
            self.source_format,
            page_count=page_count,
            figure_count=figure_count,
            needs_visual_pass=figure_count > 0,
            needs_manual_projection=status == "failed_extraction",
            image_only_pdf=image_only_pdf,
        )
        usable = text.strip()
        if figure_count and usable:
            usable = f"{usable}\n\n[FIGURES: count={figure_count} needs_visual_pass=true]"
        return ConnectorResult(
            usable_raw_text=usable,
            metadata=metadata,
            warnings=warnings,
            status=status,
            failure_reason=failure_reason,
        )
