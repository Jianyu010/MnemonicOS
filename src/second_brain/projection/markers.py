from __future__ import annotations

from dataclasses import dataclass
import re


MARKER_RE = re.compile(
    r"\[(?P<kind>FIGURE|TABLE|EQUATION|CODE):\s+"
    r"(?P<artifact_id>(?:fig|tbl|eq|code)_\d{3})"
    r"(?P<attrs>[^\]]*)\]"
)


@dataclass(slots=True)
class ParsedMarker:
    artifact_id: str
    kind: str
    marker_text: str
    char_offset: int
    attrs: dict[str, str]


def format_marker(artifact_id: str, kind: str, *, source: str, needs_visual_pass: bool = False, **attrs: object) -> str:
    upper_kind = kind.upper()
    parts = [f"source={source}"]
    for key, value in attrs.items():
        if value is None:
            continue
        parts.append(f"{key}={str(value).lower() if isinstance(value, bool) else value}")
    parts.append(f"needs_visual_pass={str(needs_visual_pass).lower()}")
    return f"[{upper_kind}: {artifact_id} {' '.join(parts)}]"


def deterministic_artifact_id(kind: str, index: int) -> str:
    prefix = {"figure": "fig", "table": "tbl", "equation": "eq", "code": "code"}.get(kind, kind)
    return f"{prefix}_{index:03d}"


def parse_markers(text: str) -> list[ParsedMarker]:
    markers: list[ParsedMarker] = []
    for match in MARKER_RE.finditer(text):
        attrs: dict[str, str] = {}
        for raw in match.group("attrs").strip().split():
            if "=" not in raw:
                continue
            key, value = raw.split("=", 1)
            attrs[key] = value
        kind = match.group("kind").casefold()
        markers.append(
            ParsedMarker(
                artifact_id=match.group("artifact_id"),
                kind={"figure": "figure", "table": "table", "equation": "equation", "code": "code"}[kind],
                marker_text=match.group(0),
                char_offset=match.start(),
                attrs=attrs,
            )
        )
    return markers
