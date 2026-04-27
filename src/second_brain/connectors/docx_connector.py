from __future__ import annotations

from pathlib import Path
import xml.etree.ElementTree as ET
from zipfile import BadZipFile, ZipFile

from .base_connector import (
    ConnectorConfig,
    ConnectorResult,
    FigurePayload,
    connector_metadata,
    figure_from_bytes,
    label_reference_present,
    status_for_text,
)

NS = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}


class DocxConnector:
    name = "docx_connector"
    source_format = "docx"

    def extract(self, path: Path, *, config: ConnectorConfig) -> ConnectorResult:
        warnings: list[str] = []
        figures = []
        payloads: list[FigurePayload] = []
        text_parts: list[str] = []
        table_count = 0
        try:
            with ZipFile(path) as archive:
                if "word/document.xml" not in archive.namelist():
                    raise ValueError("missing word/document.xml")
                root = ET.fromstring(archive.read("word/document.xml"))
                for paragraph in root.findall(".//w:p", NS):
                    texts = [node.text or "" for node in paragraph.findall(".//w:t", NS)]
                    line = "".join(texts).strip()
                    if line:
                        text_parts.append(line)
                table_count = len(root.findall(".//w:tbl", NS))
                if config.figures_enabled:
                    media_names = sorted(name for name in archive.namelist() if name.startswith("word/media/"))
                    referenced = label_reference_present("\n".join(text_parts))
                    for index, name in enumerate(media_names, start=1):
                        data = archive.read(name)
                        figure_id = f"fig_{index:03d}"
                        structural_location = f"document/image:{index}"
                        figures.append(
                            figure_from_bytes(
                                figure_id=figure_id,
                                data=data,
                                storage_policy="in_memory_extracted",
                                source_index=index,
                                structural_location=structural_location,
                                referenced_by_label=referenced,
                            )
                        )
                        payloads.append(FigurePayload(figure_id=figure_id, data=data, mime_type=figures[-1].mime_type))
                        text_parts.append(f"[FIGURE: {figure_id} index={index} needs_visual_pass=true]")
        except (BadZipFile, ET.ParseError, ValueError) as exc:
            metadata = connector_metadata(
                self.name,
                self.source_format,
                needs_manual_projection=True,
            )
            return ConnectorResult(
                usable_raw_text="",
                metadata=metadata,
                warnings=[f"docx_extraction_failed:{exc}"],
                status="failed_extraction",
                failure_reason=str(exc),
            )
        usable = "\n\n".join(text_parts).strip()
        if table_count and usable:
            usable = f"{usable}\n\n[TABLES: count={table_count}]"
        failure_reason = None if usable else "no_text_extracted"
        status = status_for_text(usable, warnings, failure_reason)
        metadata = connector_metadata(
            self.name,
            self.source_format,
            figure_count=len(figures),
            table_count=table_count,
            needs_visual_pass=bool(figures),
            needs_manual_projection=status == "failed_extraction",
        )
        return ConnectorResult(
            usable_raw_text=usable,
            metadata=metadata,
            figures=figures,
            figure_payloads=payloads,
            warnings=warnings,
            status=status,
            failure_reason=failure_reason,
        )
