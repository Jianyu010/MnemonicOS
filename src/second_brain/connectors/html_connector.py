from __future__ import annotations

from html import unescape
from pathlib import Path
import re

from .base_connector import ConnectorConfig, ConnectorResult, connector_metadata, status_for_text

SCRIPT_STYLE_RE = re.compile(r"<(script|style)\b.*?</\1>", re.IGNORECASE | re.DOTALL)
IMG_RE = re.compile(r"<img\b[^>]*>", re.IGNORECASE)
TAG_RE = re.compile(r"<[^>]+>")
BLOCK_RE = re.compile(r"</?(p|div|section|article|br|li|h[1-6]|tr|table|ul|ol)\b[^>]*>", re.IGNORECASE)


class HtmlConnector:
    name = "html_connector"
    source_format = "html"

    def extract(self, path: Path, *, config: ConnectorConfig) -> ConnectorResult:
        warnings: list[str] = []
        data = path.read_bytes()
        try:
            raw = data.decode("utf-8")
        except UnicodeDecodeError:
            raw = data.decode("utf-8", errors="replace")
            warnings.append("decoded_with_replacement_characters")
        image_count = len(IMG_RE.findall(raw))
        without_noise = SCRIPT_STYLE_RE.sub(" ", raw)
        with_breaks = BLOCK_RE.sub("\n", without_noise)
        text = unescape(TAG_RE.sub(" ", with_breaks))
        lines = [" ".join(line.split()) for line in text.splitlines()]
        usable = "\n".join(line for line in lines if line).strip()
        if image_count:
            usable = f"{usable}\n\n[FIGURE: html_images count={image_count} needs_visual_pass=true]".strip()
        failure_reason = None if usable else "no_text_extracted"
        status = status_for_text(usable, warnings, failure_reason)
        metadata = connector_metadata(
            self.name,
            self.source_format,
            figure_count=image_count,
            needs_visual_pass=image_count > 0,
            needs_manual_projection=status == "failed_extraction",
        )
        return ConnectorResult(
            usable_raw_text=usable,
            metadata=metadata,
            warnings=warnings,
            status=status,
            failure_reason=failure_reason,
        )
