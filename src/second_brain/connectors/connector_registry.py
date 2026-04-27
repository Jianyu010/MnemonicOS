from __future__ import annotations

from pathlib import Path

from .docx_connector import DocxConnector
from .html_connector import HtmlConnector
from .passthrough_connector import PassthroughConnector
from .pdf_connector import PdfConnector
from .pptx_connector import PptxConnector


def connector_for_path(path: Path):
    suffix = path.suffix.casefold()
    if suffix == ".pdf":
        return PdfConnector()
    if suffix in {".html", ".htm"}:
        return HtmlConnector()
    if suffix == ".docx":
        return DocxConnector()
    if suffix == ".pptx":
        return PptxConnector()
    if suffix in {".md", ".markdown"}:
        return PassthroughConnector("markdown")
    if suffix in {".txt", ".text", ".log"}:
        return PassthroughConnector("plain_text")
    return PassthroughConnector(suffix.lstrip(".") or "unknown")
