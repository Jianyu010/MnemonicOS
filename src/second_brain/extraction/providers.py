from __future__ import annotations

from dataclasses import asdict
import base64
import json
from pathlib import Path
from typing import Any, Protocol
from urllib import request

from second_brain.config import AppConfig
from second_brain.projection.pipeline_models import ArtifactView, TextWindow

from .models import SCHEMA_VERSION, VisualSnippet


class VLMProvider(Protocol):
    provider_name: str
    model_name: str

    def describe(self, artifact: ArtifactView) -> VisualSnippet:
        ...


class LLMProvider(Protocol):
    provider_name: str
    model_name: str

    def extract_candidates(self, packet: dict[str, Any]) -> str:
        ...


class FixtureVLMProvider:
    provider_name = "fixture"

    def __init__(self, model_name: str = "fixture-vlm") -> None:
        self.model_name = model_name

    def describe(self, artifact: ArtifactView) -> VisualSnippet:
        warnings = list(artifact.warnings)
        has_payload = artifact.payload is not None or artifact.source_attachment_path is not None
        if not has_payload:
            warnings.append("visual_payload_missing")
        description = f"{artifact.kind} {artifact.artifact_id} at {artifact.source_locator}"
        visible_text = artifact.marker if artifact.marker else artifact.artifact_id
        return VisualSnippet(
            artifact_id=artifact.artifact_id,
            marker=artifact.marker,
            source_locator=artifact.source_locator,
            visual_description=description,
            visible_text=visible_text,
            warnings=warnings,
            model_metadata={"provider": self.provider_name, "model": self.model_name},
        )


class FixtureLLMProvider:
    provider_name = "fixture"

    def __init__(self, model_name: str = "fixture-llm") -> None:
        self.model_name = model_name

    def extract_candidates(self, packet: dict[str, Any]) -> str:
        windows = packet.get("text_windows") if isinstance(packet.get("text_windows"), list) else []
        source_ref = str(packet.get("active_source_ref") or "")
        first_window = windows[0] if windows else {}
        text = str(first_window.get("text") or "").strip()
        title = _title_from_text(text) or "Extracted Source"
        evidence_id = str(first_window.get("window_id") or source_ref)
        components = _components_from_text(text, evidence_id=evidence_id)
        temporal_order = [str(component["component_id"]) for component in components]
        candidates = [
            {
                "type": "source",
                "title": title,
                "summary": text[:240] or "Source projection had no text windows.",
                "body": text[:1000] or "No text content was available in this projection.",
                "confidence": "high" if text else "medium",
                "source_refs": [source_ref],
                "evidence": [{"kind": "text_window", "id": evidence_id, "quote": text[:160]}],
                "components": components
                or [
                    {
                        "component_id": "c1",
                        "component_type": "observation",
                        "text": "Source projection had no text windows.",
                        "why_it_matters": "Records that extraction had an empty text lane.",
                        "evidence": [{"kind": "text_window", "id": evidence_id, "quote": ""}],
                        "links": [],
                    }
                ],
                "traces": [
                    {
                        "trace_id": "trace-1",
                        "title": title,
                        "component_ids": temporal_order or ["c1"],
                    }
                ],
                "temporal_order": temporal_order or ["c1"],
            }
        ]
        if text:
            concept_title = _concept_title_from_source_title(title)
            candidates.append(
                {
                    "type": "concept",
                    "title": concept_title,
                    "summary": text[:240],
                    "body": text[:1000],
                    "confidence": "medium",
                    "source_refs": [source_ref],
                    "evidence": [{"kind": "text_window", "id": evidence_id, "quote": text[:160]}],
                    "components": components,
                    "traces": [
                        {
                            "trace_id": "trace-1",
                            "title": concept_title,
                            "component_ids": temporal_order,
                        }
                    ],
                    "temporal_order": temporal_order,
                }
            )
        intra_source_relations = []
        if len(candidates) >= 2:
            intra_source_relations = [
                {
                    "source_index": 1,
                    "target_index": 0,
                    "relation_type": "derived_from",
                    "rationale": "Concept candidate derived from source material.",
                }
            ]
        return json.dumps(
            {
                "schema_version": SCHEMA_VERSION,
                "candidates": candidates,
                "intra_source_relations": intra_source_relations,
            },
            sort_keys=True,
        )


class OllamaVLMProvider:
    provider_name = "ollama"

    def __init__(self, model_name: str, *, base_url: str = "http://127.0.0.1:11434") -> None:
        self.model_name = model_name
        self.base_url = base_url.rstrip("/")

    def describe(self, artifact: ArtifactView) -> VisualSnippet:
        image_bytes = artifact.payload
        if image_bytes is None and artifact.source_attachment_path:
            path = Path(artifact.source_attachment_path)
            image_bytes = path.read_bytes() if path.exists() else None
        if image_bytes is None:
            return VisualSnippet(
                artifact_id=artifact.artifact_id,
                marker=artifact.marker,
                source_locator=artifact.source_locator,
                visual_description="",
                visible_text="",
                warnings=[*artifact.warnings, "visual_payload_missing"],
                model_metadata={"provider": self.provider_name, "model": self.model_name},
            )
        prompt = (
            "Describe this MnemonicOS artifact for memory extraction. "
            "Return concise JSON with keys visual_description, visible_text, warnings."
        )
        response = _ollama_generate(
            self.base_url,
            model=self.model_name,
            prompt=prompt,
            images=[base64.b64encode(image_bytes).decode("ascii")],
        )
        parsed = _parse_jsonish_response(response)
        warnings = parsed.get("warnings") if isinstance(parsed.get("warnings"), list) else []
        return VisualSnippet(
            artifact_id=artifact.artifact_id,
            marker=artifact.marker,
            source_locator=artifact.source_locator,
            visual_description=str(parsed.get("visual_description") or response).strip(),
            visible_text=str(parsed.get("visible_text") or "").strip(),
            warnings=[*artifact.warnings, *[str(item) for item in warnings]],
            model_metadata={"provider": self.provider_name, "model": self.model_name},
        )


class OllamaLLMProvider:
    provider_name = "ollama"

    def __init__(self, model_name: str, *, base_url: str = "http://127.0.0.1:11434") -> None:
        self.model_name = model_name
        self.base_url = base_url.rstrip("/")

    def extract_candidates(self, packet: dict[str, Any]) -> str:
        source_ref = packet.get("active_source_ref", "")
        window_ids = [w.get("window_id", "") for w in packet.get("text_windows", [])]
        ids_str = ", ".join(f'"{w}"' for w in window_ids[:8])
        prompt = (
            "You are the MnemonicOS extraction layer.\n"
            "Return a single JSON object. No markdown, no code fences, no prose — raw JSON only.\n\n"
            "REQUIRED top-level keys: schema_version, candidates, intra_source_relations.\n"
            f'schema_version must be "{SCHEMA_VERSION}".\n\n'
            "CANDIDATE RULES:\n"
            "- Each candidate: type, title, summary, body, confidence, source_refs, evidence, components\n"
            "- Candidate titles should be clear and descriptive; relation resolution uses candidate indices.\n"
            f'- type MUST be one of: "source", "concept", "procedure", "decision", "incident"\n'
            f'- confidence MUST be one of: "low", "medium", "high"\n'
            f'- source_refs MUST include "{source_ref}"\n'
            "- evidence: array of objects, each with an \"id\" field matching a window_id from the packet\n"
            f"  Available window_ids: [{ids_str}]\n\n"
            "COMPONENT RULES:\n"
            "- Each component: component_id (unique string), component_type, text, evidence, links\n"
            '- component_type MUST be one of: "user_request", "agent_action", "failure", "observation", '
            '"web_finding", "milestone", "instruction", "definition", "claim", "experiment", '
            '"derivation", "theorem", "example", "procedure_step", "open_question"\n'
            "- component evidence: same format as candidate evidence — objects with \"id\" matching window_ids\n"
            "- links: array of {\"target\": \"<other_component_id>\", \"relation\": \"<string>\"}\n\n"
            "TRACE RULES (optional):\n"
            "- traces: array of {trace_id, component_ids, temporal_order}\n"
            "- All component_ids in traces must exist in this candidate's components\n\n"
            "INTRA-SOURCE RELATION RULES:\n"
            "- After listing all candidates, add a top-level \"intra_source_relations\" array.\n"
            "- Each entry: source_index (0-based integer position of the source candidate in\n"
            "  the candidates array you just produced), target_index (0-based integer position\n"
            "  of the target candidate, must differ from source_index),\n"
            "  relation_type (one of: derived_from, refines, supersedes, evidence_for,\n"
            "  depends_on, part_of, related_to, caused_by, resolved_by, attempts_to_solve,\n"
            "  failed_due_to, supports_solution, similar_to, contrasts_with, before, after,\n"
            "  part_of_chain, linked_decision, linked_procedure),\n"
            "  and optional rationale (short string grounded in the source).\n"
            "- source_index != target_index (no self-relations).\n"
            "- Both indices must be valid positions in the candidates array (0 to N-1).\n"
            "- Only assert relations the source explicitly supports. Do not invent connections.\n"
            "- If no clear intra-source relations exist, emit an empty array.\n\n"
            "Use only the provided packet as evidence. Reject nothing silently — "
            "if a window has usable content, extract it.\n\n"
            f"{json.dumps(packet, sort_keys=True)}"
        )
        return _ollama_generate(self.base_url, model=self.model_name, prompt=prompt)


def build_vlm_provider(config: AppConfig) -> VLMProvider:
    if config.extraction.vlm_provider == "fixture":
        return FixtureVLMProvider(config.extraction.vlm_model)
    if config.extraction.vlm_provider == "ollama":
        return OllamaVLMProvider(config.extraction.vlm_model, base_url=config.extraction.ollama_base_url)
    raise ValueError(f"unsupported VLM provider: {config.extraction.vlm_provider}")


def build_llm_provider(config: AppConfig) -> LLMProvider:
    if config.extraction.llm_provider == "fixture":
        return FixtureLLMProvider(config.extraction.llm_model)
    if config.extraction.llm_provider == "ollama":
        return OllamaLLMProvider(config.extraction.llm_model, base_url=config.extraction.ollama_base_url)
    raise ValueError(f"unsupported LLM provider: {config.extraction.llm_provider}")


def make_llm_packet(
    *,
    active_source_ref: str,
    projection_metadata: dict[str, Any],
    windows: list[TextWindow],
    visual_snippets: list[VisualSnippet],
) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "active_source_ref": active_source_ref,
        "projection_metadata": projection_metadata,
        "text_windows": [
            {
                "window_id": window.window_id,
                "text": window.text,
                "source_spans": [asdict(span) for span in window.source_spans],
                "artifact_markers": [asdict(marker) for marker in window.artifact_markers],
                "token_estimate": window.token_estimate,
            }
            for window in windows
        ],
        "visual_snippets": [snippet.to_payload() for snippet in visual_snippets],
    }


def _title_from_text(text: str) -> str:
    for line in text.splitlines():
        cleaned = line.strip().strip("#").strip()
        if cleaned:
            return cleaned[:80]
    return ""


def _concept_title_from_source_title(title: str) -> str:
    cleaned = title.strip() or "Extracted Source"
    if cleaned.casefold().endswith(" concept"):
        return cleaned
    return f"{cleaned} Concept"[:80]


def _components_from_text(text: str, *, evidence_id: str) -> list[dict[str, Any]]:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if not lines and text.strip():
        lines = [text.strip()]
    components: list[dict[str, Any]] = []
    for index, line in enumerate(lines[:12], start=1):
        component_id = f"c{index}"
        component_type = _infer_component_type(line)
        links = []
        if index > 1:
            links.append({"type": "next", "target": f"c{index - 1}"})
        components.append(
            {
                "component_id": component_id,
                "component_type": component_type,
                "text": line,
                "why_it_matters": "",
                "evidence": [{"kind": "text_window", "id": evidence_id, "quote": line[:160]}],
                "links": links,
            }
        )
    return components


def _infer_component_type(text: str) -> str:
    lowered = text.casefold()
    if "error" in lowered or "failed" in lowered or "failure" in lowered:
        return "failure"
    if "user" in lowered and ("request" in lowered or "asked" in lowered):
        return "user_request"
    if "agent" in lowered and ("tried" in lowered or "ran" in lowered or "built" in lowered):
        return "agent_action"
    if "found" in lowered or "search" in lowered:
        return "web_finding"
    if "success" in lowered or "completed" in lowered or "milestone" in lowered:
        return "milestone"
    if "definition" in lowered or " is " in lowered:
        return "definition"
    if "experiment" in lowered:
        return "experiment"
    if "theorem" in lowered:
        return "theorem"
    if "therefore" in lowered or "derive" in lowered:
        return "derivation"
    if lowered.startswith(("do ", "avoid ", "use ", "run ")):
        return "procedure_step"
    return "observation"


def _ollama_generate(base_url: str, *, model: str, prompt: str, images: list[str] | None = None) -> str:
    payload: dict[str, Any] = {
        "model": model,
        "prompt": prompt,
        "stream": False,
    }
    if images:
        payload["images"] = images
    req = request.Request(
        f"{base_url}/api/generate",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with request.urlopen(req, timeout=180) as response:
        data = json.loads(response.read().decode("utf-8"))
    return str(data.get("response") or "").strip()


def _parse_jsonish_response(text: str) -> dict[str, Any]:
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}
