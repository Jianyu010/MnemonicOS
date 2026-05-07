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
        candidates = [
            {
                "type": "source",
                "title": title,
                "summary": text[:240] or "Source projection had no text windows.",
                "body": text[:1000] or "No text content was available in this projection.",
                "confidence": "high" if text else "medium",
                "source_refs": [source_ref],
                "evidence": [{"kind": "text_window", "id": evidence_id, "quote": text[:160]}],
            }
        ]
        if text:
            candidates.append(
                {
                    "type": "concept",
                    "title": title,
                    "summary": text[:240],
                    "body": text[:1000],
                    "confidence": "medium",
                    "source_refs": [source_ref],
                    "evidence": [{"kind": "text_window", "id": evidence_id, "quote": text[:160]}],
                }
            )
        return json.dumps(
            {
                "schema_version": SCHEMA_VERSION,
                "candidates": candidates,
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
        prompt = (
            "You are the MnemonicOS extraction layer. Return JSON only using schema_version "
            f"{SCHEMA_VERSION}. Required top-level keys: schema_version, candidates. "
            "Each candidate must include type, title, summary, body, confidence, source_refs, evidence. "
            "Use only the provided packet as evidence.\n\n"
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
