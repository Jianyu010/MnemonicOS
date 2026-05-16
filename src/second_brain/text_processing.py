from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable, Sequence


TOKEN_PATTERN = re.compile(r"[\w/-]+", re.UNICODE)


@dataclass(frozen=True, slots=True)
class TextProcessingOptions:
    cjk_enabled: bool = True
    cjk_ngram_min: int = 1
    cjk_ngram_max: int = 3
    latin_min_token_chars: int = 3


def is_cjk_char(char: str) -> bool:
    codepoint = ord(char)
    return (
        0x3400 <= codepoint <= 0x4DBF
        or 0x4E00 <= codepoint <= 0x9FFF
        or 0xF900 <= codepoint <= 0xFAFF
        or 0x20000 <= codepoint <= 0x2A6DF
        or 0x2A700 <= codepoint <= 0x2B73F
        or 0x2B740 <= codepoint <= 0x2B81F
        or 0x2B820 <= codepoint <= 0x2CEAF
        or 0x2CEB0 <= codepoint <= 0x2EBEF
        or 0x30000 <= codepoint <= 0x3134F
    )


def contains_cjk(text: str) -> bool:
    return any(is_cjk_char(char) for char in text)


def _cjk_runs(text: str) -> Iterable[str]:
    run: list[str] = []
    for char in text:
        if is_cjk_char(char):
            run.append(char)
            continue
        if run:
            yield "".join(run)
            run = []
    if run:
        yield "".join(run)


def cjk_ngrams(text: str, *, min_n: int = 1, max_n: int = 3) -> list[str]:
    if min_n <= 0 or max_n < min_n:
        return []
    terms: list[str] = []
    seen: set[str] = set()
    for run in _cjk_runs(text):
        for size in range(min_n, max_n + 1):
            if len(run) < size:
                continue
            for index in range(0, len(run) - size + 1):
                term = run[index : index + size]
                if term in seen:
                    continue
                terms.append(term)
                seen.add(term)
    return terms


def query_terms(
    text: str,
    *,
    stopwords: set[str] | frozenset[str] = frozenset(),
    options: TextProcessingOptions | None = None,
) -> list[str]:
    opts = options or TextProcessingOptions()
    terms: list[str] = []
    seen: set[str] = set()
    for raw_token in TOKEN_PATTERN.findall(text):
        if opts.cjk_enabled and contains_cjk(raw_token):
            candidates = cjk_ngrams(raw_token, min_n=opts.cjk_ngram_min, max_n=opts.cjk_ngram_max)
        else:
            lowered = raw_token.casefold()
            if len(lowered) < opts.latin_min_token_chars or lowered in stopwords:
                candidates = []
            else:
                candidates = [lowered]
        for candidate in candidates:
            if candidate in seen:
                continue
            terms.append(candidate)
            seen.add(candidate)
    return terms


def prepare_fts_text(
    parts: Sequence[str],
    *,
    options: TextProcessingOptions | None = None,
) -> str:
    opts = options or TextProcessingOptions()
    originals = [part.strip() for part in parts if part and part.strip()]
    if not opts.cjk_enabled:
        return " ".join(originals)
    generated: list[str] = []
    seen: set[str] = set()
    for part in originals:
        for term in cjk_ngrams(part, min_n=opts.cjk_ngram_min, max_n=opts.cjk_ngram_max):
            if term in seen:
                continue
            generated.append(term)
            seen.add(term)
    return " ".join([*originals, *generated]).strip()


def _unit_spans(text: str) -> list[tuple[int, int]]:
    spans: list[tuple[int, int]] = []
    index = 0
    while index < len(text):
        char = text[index]
        if char.isspace():
            index += 1
            continue
        if is_cjk_char(char):
            spans.append((index, index + 1))
            index += 1
            continue
        start = index
        while index < len(text) and not text[index].isspace() and not is_cjk_char(text[index]):
            index += 1
        spans.append((start, index))
    return spans


def count_chunk_units(text: str) -> int:
    return len(_unit_spans(text))


def chunk_text_by_units(text: str, *, chunk_units: int, overlap_units: int) -> list[str]:
    spans = _unit_spans(text)
    if not spans:
        return []
    chunks: list[str] = []
    step = max(chunk_units - overlap_units, 1)
    for start_index in range(0, len(spans), step):
        chunk_spans = spans[start_index : start_index + chunk_units]
        if not chunk_spans:
            continue
        start_char = chunk_spans[0][0]
        end_char = chunk_spans[-1][1]
        chunk = text[start_char:end_char].strip()
        if chunk:
            chunks.append(chunk)
        if start_index + chunk_units >= len(spans):
            break
    return chunks
