from __future__ import annotations

from dataclasses import dataclass


@dataclass(slots=True)
class MarkdownLine:
    text: str
    style: str


def render_lines(text: str, width: int) -> list[MarkdownLine]:
    lines: list[MarkdownLine] = []
    in_code = False
    for raw_line in text.splitlines() or [""]:
        stripped = raw_line.rstrip()
        if stripped.startswith("```"):
            in_code = not in_code
            lines.append(MarkdownLine(text=stripped or "```", style="code-delimiter"))
            continue
        if in_code:
            lines.append(MarkdownLine(text=stripped, style="code"))
            continue
        if stripped.startswith("### "):
            lines.extend(_wrap_markdown(stripped[4:], width, "heading3"))
        elif stripped.startswith("## "):
            lines.extend(_wrap_markdown(stripped[3:], width, "heading2"))
        elif stripped.startswith("# "):
            lines.extend(_wrap_markdown(stripped[2:], width, "heading1"))
        elif stripped.startswith("- "):
            bullet_lines = _wrap_text(stripped[2:], max(8, width - 4))
            if bullet_lines:
                lines.append(MarkdownLine(text="• " + bullet_lines[0], style="bullet"))
                for rest in bullet_lines[1:]:
                    lines.append(MarkdownLine(text="  " + rest, style="bullet"))
            else:
                lines.append(MarkdownLine(text="•", style="bullet"))
        elif stripped[:3].isdigit() and len(stripped) > 3 and stripped[3] == " ":
            lines.extend(_wrap_markdown(stripped, width, "bullet"))
        elif stripped == "":
            lines.append(MarkdownLine(text="", style="blank"))
        else:
            lines.extend(_wrap_markdown(stripped, width, "body"))
    return lines


def _wrap_markdown(text: str, width: int, style: str) -> list[MarkdownLine]:
    return [MarkdownLine(text=line, style=style) for line in _wrap_text(text, width)]


def _wrap_text(text: str, width: int) -> list[str]:
    if width <= 1:
        return [text]
    words = text.split()
    if not words:
        return [""]
    lines = [words[0]]
    for word in words[1:]:
        candidate = f"{lines[-1]} {word}"
        if len(candidate) <= width:
            lines[-1] = candidate
        else:
            lines.append(word)
    return lines
