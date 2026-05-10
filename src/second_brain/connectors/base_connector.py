from __future__ import annotations

from dataclasses import asdict, dataclass, field
from hashlib import sha256
from pathlib import Path
import re
import struct
from typing import Protocol


CONNECTOR_VERSION = "west-flange-v1"

LABEL_REF_RE = re.compile(r"\b(Fig(?:ure)?\.?\s*\d+[a-zA-Z]?(?:[–\-]\d+[a-zA-Z]?)?)\b")


@dataclass(slots=True)
class ConnectorConfig:
    figures_enabled: bool = True
    persist_extracted_figures: bool = False
    archive_source_attachments: bool = True
    max_in_memory_figure_bytes: int | None = None
    optional_dependencies: dict[str, bool] = field(default_factory=dict)


@dataclass(slots=True)
class FigureMetadata:
    figure_id: str
    storage_policy: str = "in_memory_extracted"
    attachment_path: str | None = None
    source_page: int | None = None
    source_slide: int | None = None
    source_index: int | None = None
    width_px: int | None = None
    height_px: int | None = None
    mime_type: str | None = None
    byte_size: int | None = None
    sha256: str | None = None
    structural_location: str | None = None
    has_caption: bool = False
    referenced_by_label: bool = False
    needs_visual_pass: bool = True
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(slots=True)
class FigurePayload:
    figure_id: str
    data: bytes
    mime_type: str | None = None


@dataclass(slots=True)
class ConnectorResult:
    usable_raw_text: str
    metadata: dict[str, object]
    figures: list[FigureMetadata] = field(default_factory=list)
    figure_payloads: list[FigurePayload] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    status: str = "accepted"
    failure_reason: str | None = None


class ArtifactConnector(Protocol):
    name: str
    source_format: str

    def extract(self, path: Path, *, config: ConnectorConfig) -> ConnectorResult:
        ...


def connector_metadata(name: str, source_format: str, **extra: object) -> dict[str, object]:
    metadata: dict[str, object] = {
        "connector_name": name,
        "connector_version": CONNECTOR_VERSION,
        "source_format": source_format,
        "page_count": 0,
        "slide_count": 0,
        "figure_count": 0,
        "table_count": 0,
        "needs_visual_pass": False,
        "needs_manual_projection": False,
        "image_only_pdf": False,
        "partial_extraction": False,
    }
    metadata.update(extra)
    return metadata


def status_for_text(text: str, warnings: list[str], failure_reason: str | None = None) -> str:
    if not text.strip():
        return "failed_extraction"
    if warnings or failure_reason:
        return "accepted_with_warnings"
    return "accepted"


def label_reference_present(text: str) -> bool:
    return LABEL_REF_RE.search(text) is not None


def image_metadata(data: bytes, mime_type: str | None = None) -> tuple[int | None, int | None, str | None]:
    detected = mime_type
    if data.startswith(b"\x89PNG\r\n\x1a\n") and len(data) >= 24:
        width, height = struct.unpack(">II", data[16:24])
        return int(width), int(height), detected or "image/png"
    if data.startswith(b"\xff\xd8"):
        index = 2
        while index + 9 < len(data):
            if data[index] != 0xFF:
                index += 1
                continue
            marker = data[index + 1]
            index += 2
            if marker in {0xD8, 0xD9}:
                continue
            if index + 2 > len(data):
                break
            segment_length = int.from_bytes(data[index : index + 2], "big")
            if segment_length < 2:
                break
            if marker in range(0xC0, 0xC4) or marker in range(0xC5, 0xC8) or marker in range(0xC9, 0xCC) or marker in range(0xCD, 0xD0):
                if index + 7 <= len(data):
                    height = int.from_bytes(data[index + 3 : index + 5], "big")
                    width = int.from_bytes(data[index + 5 : index + 7], "big")
                    return width, height, detected or "image/jpeg"
                break
            index += segment_length
        return None, None, detected or "image/jpeg"
    if data.startswith(b"GIF87a") or data.startswith(b"GIF89a"):
        if len(data) >= 10:
            width, height = struct.unpack("<HH", data[6:10])
            return int(width), int(height), detected or "image/gif"
        return None, None, detected or "image/gif"
    return None, None, detected


def figure_from_bytes(
    *,
    figure_id: str,
    data: bytes,
    storage_policy: str,
    attachment_path: str | None = None,
    source_page: int | None = None,
    source_slide: int | None = None,
    source_index: int | None = None,
    structural_location: str | None = None,
    mime_type: str | None = None,
    referenced_by_label: bool = False,
) -> FigureMetadata:
    width, height, detected_mime = image_metadata(data, mime_type)
    should_hash = storage_policy == "source_attachment"
    return FigureMetadata(
        figure_id=figure_id,
        storage_policy=storage_policy,
        attachment_path=attachment_path,
        source_page=source_page,
        source_slide=source_slide,
        source_index=source_index,
        width_px=width,
        height_px=height,
        mime_type=detected_mime,
        byte_size=len(data),
        sha256=sha256(data).hexdigest() if should_hash else None,
        structural_location=structural_location,
        referenced_by_label=referenced_by_label,
    )
