from __future__ import annotations

from pathlib import Path
import re
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

A_NS = {"a": "http://schemas.openxmlformats.org/drawingml/2006/main"}
SLIDE_RE = re.compile(r"ppt/slides/slide(\d+)\.xml$")


class PptxConnector:
    name = "pptx_connector"
    source_format = "pptx"

    def extract(self, path: Path, *, config: ConnectorConfig) -> ConnectorResult:
        warnings: list[str] = []
        figures = []
        payloads: list[FigurePayload] = []
        slide_parts: list[str] = []
        table_count = 0
        try:
            with ZipFile(path) as archive:
                slide_names = sorted(
                    (name for name in archive.namelist() if SLIDE_RE.match(name)),
                    key=lambda value: int(SLIDE_RE.match(value).group(1)),  # type: ignore[union-attr]
                )
                for slide_name in slide_names:
                    slide_no = int(SLIDE_RE.match(slide_name).group(1))  # type: ignore[union-attr]
                    root = ET.fromstring(archive.read(slide_name))
                    texts = [node.text or "" for node in root.findall(".//a:t", A_NS)]
                    text = " ".join(part.strip() for part in texts if part and part.strip()).strip()
                    table_count += len(root.findall(".//a:tbl", A_NS))
                    if text:
                        slide_parts.append(f"## Slide {slide_no}\n\n{text}")
                if config.figures_enabled:
                    media_names = sorted(name for name in archive.namelist() if name.startswith("ppt/media/"))
                    referenced = label_reference_present("\n".join(slide_parts))
                    for index, name in enumerate(media_names, start=1):
                        data = archive.read(name)
                        figure_id = f"fig_{index:03d}"
                        structural_location = f"deck/image:{index}"
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
                        slide_parts.append(f"[FIGURE: {figure_id} index={index} needs_visual_pass=true]")
        except (BadZipFile, ET.ParseError, ValueError) as exc:
            metadata = connector_metadata(
                self.name,
                self.source_format,
                needs_manual_projection=True,
            )
            return ConnectorResult(
                usable_raw_text="",
                metadata=metadata,
                warnings=[f"pptx_extraction_failed:{exc}"],
                status="failed_extraction",
                failure_reason=str(exc),
            )
        usable = "\n\n".join(slide_parts).strip()
        failure_reason = None if usable else "no_text_extracted"
        status = status_for_text(usable, warnings, failure_reason)
        metadata = connector_metadata(
            self.name,
            self.source_format,
            slide_count=len(slide_names),
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
