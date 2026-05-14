from __future__ import annotations

from pathlib import Path
import re

from .base_connector import (
    ConnectorConfig,
    ConnectorResult,
    FigureMetadata,
    FigurePayload,
    connector_metadata,
    status_for_text,
)

PAGE_RE = re.compile(rb"/Type\s*/Page\b")
IMAGE_RE = re.compile(rb"/Subtype\s*/Image\b")

# Maximum pages rendered as figures during OCR / VLM pass.
_MAX_OCR_PAGES = 50


def _extract_text_pymupdf(path: Path) -> tuple[str, list[str]]:
    warnings: list[str] = []
    try:
        import fitz  # type: ignore[import-not-found]

        doc = fitz.open(str(path))
        pages = [doc[i].get_text() for i in range(len(doc))]
        doc.close()
        return "\n\n".join(pages), warnings
    except Exception as exc:
        warnings.append(f"pymupdf_extraction_failed:{exc.__class__.__name__}")
        return "", warnings


def _extract_text_ocr(path: Path, max_pages: int = _MAX_OCR_PAGES) -> tuple[str, list[str]]:
    warnings: list[str] = []
    try:
        from pdf2image import convert_from_path  # type: ignore[import-not-found]
        import pytesseract  # type: ignore[import-not-found]

        images = convert_from_path(str(path), last_page=max_pages)
        texts = [pytesseract.image_to_string(img) for img in images]
        if len(images) >= max_pages:
            warnings.append(f"ocr_truncated_at:{max_pages}_pages")
        return "\n\n".join(texts), warnings
    except Exception as exc:
        warnings.append(f"ocr_failed:{exc.__class__.__name__}")
        return "", warnings


def _render_pages_as_figures(
    path: Path,
    config: ConnectorConfig,
    max_pages: int = _MAX_OCR_PAGES,
) -> tuple[list[FigureMetadata], list[FigurePayload]]:
    figures: list[FigureMetadata] = []
    payloads: list[FigurePayload] = []
    try:
        import fitz  # type: ignore[import-not-found]

        doc = fitz.open(str(path))
        mat = fitz.Matrix(2, 2)
        for i in range(min(len(doc), max_pages)):
            pix = doc[i].get_pixmap(matrix=mat)
            data: bytes = pix.tobytes("png")
            if config.max_in_memory_figure_bytes and len(data) > config.max_in_memory_figure_bytes:
                break
            figure_id = f"page_{i + 1:04d}"
            figures.append(
                FigureMetadata(
                    figure_id=figure_id,
                    storage_policy="in_memory_extracted",
                    source_page=i + 1,
                    source_index=i + 1,
                    width_px=pix.width,
                    height_px=pix.height,
                    mime_type="image/png",
                    byte_size=len(data),
                    structural_location=f"page:{i + 1}",
                    needs_visual_pass=True,
                )
            )
            payloads.append(FigurePayload(figure_id=figure_id, data=data, mime_type="image/png"))
        doc.close()
    except Exception:
        pass
    return figures, payloads


class PdfConnector:
    name = "pdf_connector"
    source_format = "pdf"

    def extract(self, path: Path, *, config: ConnectorConfig) -> ConnectorResult:
        data = path.read_bytes()
        page_count = len(PAGE_RE.findall(data))
        figure_count = len(IMAGE_RE.findall(data))

        if config.skip_text_extraction:
            # Lazy ingest: accept immediately, defer text extraction to the worker.
            # image_only_pdf is unknown until extraction; set needs_visual_pass when figures detected.
            meta = connector_metadata(
                self.name,
                self.source_format,
                page_count=page_count,
                figure_count=figure_count,
                needs_visual_pass=figure_count > 0,
                needs_manual_projection=False,
                image_only_pdf=False,
            )
            return ConnectorResult(usable_raw_text="", metadata=meta, status="accepted")

        text, warnings = _extract_text_pymupdf(path)
        figures: list[FigureMetadata] = []
        figure_payloads: list[FigurePayload] = []
        image_only_pdf = bool(page_count and figure_count and not text.strip())

        if image_only_pdf and config.figures_enabled:
            ocr_text, ocr_warnings = _extract_text_ocr(path)
            warnings.extend(ocr_warnings)
            if ocr_text.strip():
                text = ocr_text
                image_only_pdf = False
            figures, figure_payloads = _render_pages_as_figures(path, config)

        failure_reason = None if text.strip() else "no_text_extracted"
        status = status_for_text(text, warnings, failure_reason)
        if not text.strip():
            status = "failed_extraction"

        usable = text.strip()
        rendered_figure_count = max(figure_count, len(figures))
        if rendered_figure_count and usable:
            usable = f"{usable}\n\n[FIGURES: count={rendered_figure_count} needs_visual_pass=true]"

        meta = connector_metadata(
            self.name,
            self.source_format,
            page_count=page_count,
            figure_count=rendered_figure_count,
            needs_visual_pass=rendered_figure_count > 0,
            needs_manual_projection=status == "failed_extraction",
            image_only_pdf=image_only_pdf,
        )
        return ConnectorResult(
            usable_raw_text=usable,
            metadata=meta,
            figures=figures,
            figure_payloads=figure_payloads,
            warnings=warnings,
            status=status,
            failure_reason=failure_reason,
        )
