from __future__ import annotations

import math
import re


STOPWORDS = {
    "a",
    "an",
    "and",
    "are",
    "as",
    "at",
    "be",
    "by",
    "for",
    "from",
    "in",
    "is",
    "it",
    "of",
    "on",
    "or",
    "that",
    "the",
    "this",
    "to",
    "we",
    "with",
}

TOKEN_PATTERN = re.compile(r"[A-Za-z0-9][\w/-]*")
SENTENCE_PATTERN = re.compile(r"(?<=[.!?])\s+|\n{2,}")


def _clean_lines(text: str) -> str:
    lines: list[str] = []
    in_code = False
    for raw_line in text.splitlines():
        line = raw_line.rstrip()
        stripped = line.strip()
        if stripped.startswith("```"):
            in_code = not in_code
            continue
        if in_code:
            continue
        if stripped.startswith("#"):
            continue
        lines.append(line)
    return "\n".join(lines).strip()


def _sentences(text: str) -> list[str]:
    cleaned = _clean_lines(text)
    chunks = [part.strip() for part in SENTENCE_PATTERN.split(cleaned)]
    sentences = [chunk for chunk in chunks if chunk]
    return sentences


def summarize_text(text: str, *, max_sentences: int = 2, max_chars: int = 320) -> str:
    sentences = _sentences(text)
    if not sentences:
        return ""
    if len(sentences) <= max_sentences:
        return " ".join(sentences)[:max_chars]

    frequencies: dict[str, int] = {}
    tokenized: list[list[str]] = []
    for sentence in sentences:
        tokens = [token.casefold() for token in TOKEN_PATTERN.findall(sentence)]
        tokenized.append(tokens)
        for token in tokens:
            if token in STOPWORDS:
                continue
            frequencies[token] = frequencies.get(token, 0) + 1

    ranked: list[tuple[float, int, str]] = []
    for index, sentence in enumerate(sentences):
        tokens = tokenized[index]
        informative = [token for token in tokens if token not in STOPWORDS]
        if not informative:
            score = 0.1 / (index + 1)
        else:
            score = sum(frequencies.get(token, 0) for token in informative) / math.sqrt(len(informative))
            score += 0.2 / (index + 1)
        ranked.append((score, index, sentence))

    selected = sorted(ranked, key=lambda item: (-item[0], item[1]))[:max_sentences]
    selected_sorted = [sentence for _, _, sentence in sorted(selected, key=lambda item: item[1])]
    summary = " ".join(selected_sorted).strip()
    if len(summary) > max_chars:
        summary = summary[: max_chars - 1].rstrip() + "…"
    return summary
