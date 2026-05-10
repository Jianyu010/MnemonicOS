from __future__ import annotations

from collections.abc import Iterator

from .markers import parse_markers
from .pipeline_models import ArtifactMarker, SourceSpan, TextWindow


def estimate_tokens(text: str) -> int:
    return len(text.split())


def iter_text_windows(
    text: str,
    *,
    source_kind: str,
    source_locator: str,
    token_target: int,
    token_overlap: int,
) -> Iterator[TextWindow]:
    paragraphs = [paragraph for paragraph in text.split("\n\n") if paragraph.strip()]
    if not paragraphs:
        return
    window_parts: list[str] = []
    window_index = 0
    current_tokens = 0
    slack = int(token_target * 1.2)

    def emit(parts: list[str], index: int) -> TextWindow:
        window_text = "\n\n".join(parts).strip()
        markers = [
            ArtifactMarker(
                artifact_id=marker.artifact_id,
                kind=marker.kind,
                marker_text=marker.marker_text,
                char_offset=marker.char_offset,
            )
            for marker in parse_markers(window_text)
        ]
        return TextWindow(
            window_id=f"tw_{index:03d}",
            text=window_text,
            source_spans=[
                SourceSpan(
                    source_kind=source_kind,
                    source_locator=source_locator,
                    char_start=None,
                    char_end=None,
                )
            ],
            artifact_markers=markers,
            token_estimate=estimate_tokens(window_text),
        )

    for paragraph in paragraphs:
        paragraph_tokens = estimate_tokens(paragraph)
        if window_parts and current_tokens + paragraph_tokens > slack:
            yield emit(window_parts, window_index)
            window_index += 1
            overlap_parts = _overlap_paragraphs(window_parts, token_overlap)
            window_parts = overlap_parts
            current_tokens = sum(estimate_tokens(item) for item in window_parts)
        window_parts.append(paragraph)
        current_tokens += paragraph_tokens
    if window_parts:
        yield emit(window_parts, window_index)


def _overlap_paragraphs(paragraphs: list[str], token_overlap: int) -> list[str]:
    if token_overlap <= 0:
        return []
    selected: list[str] = []
    total = 0
    for paragraph in reversed(paragraphs):
        tokens = estimate_tokens(paragraph)
        if selected and total + tokens > token_overlap:
            break
        selected.append(paragraph)
        total += tokens
    return list(reversed(selected))
