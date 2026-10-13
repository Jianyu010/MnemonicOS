from __future__ import annotations

from dataclasses import asdict
import base64
import json
import os
from pathlib import Path
import shlex
import socket
import subprocess
import time
from typing import Any, Protocol
from urllib import request
from urllib.parse import urlparse

from second_brain.config import AppConfig
from second_brain.projection.pipeline_models import ArtifactView, TextWindow

from .models import SCHEMA_VERSION, VisualSnippet


class InferenceTimeoutError(RuntimeError):
    """Raised when an Ollama or OpenAI-compatible inference call times out.

    Caught by _subprocess_entry._run() before the generic handler and written as
    ``"timeout: …"`` — never ``"tombstoned: …"`` — so retry_failed_extractions
    requeues the item regardless of attempt count.
    """


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
        # If packet includes retrieval_context_note_ids, add attribution to first candidate.
        retrieval_note_ids: list[str] = packet.get("retrieval_context_note_ids") or []
        if retrieval_note_ids and candidates:
            candidates[0]["retrieval_attribution"] = [
                {
                    "note_id": retrieval_note_ids[0],
                    "evidence_type": "semantic_reliance",
                    "confidence": 0.7,
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

    def __init__(
        self,
        model_name: str,
        *,
        base_url: str = "http://127.0.0.1:11434",
        timeout_seconds: int | None = None,
        keep_alive: int = 0,
    ) -> None:
        self.model_name = model_name
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds
        self.keep_alive = keep_alive

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
            timeout_seconds=self.timeout_seconds,
            keep_alive=self.keep_alive,
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

    def __init__(
        self,
        model_name: str,
        *,
        base_url: str = "http://127.0.0.1:11434",
        timeout_seconds: int | None = None,
        keep_alive: int = 0,
    ) -> None:
        self.model_name = model_name
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds
        self.keep_alive = keep_alive

    def extract_candidates(self, packet: dict[str, Any]) -> str:
        prompt = _candidate_extraction_prompt(packet)
        return _ollama_generate(
            self.base_url,
            model=self.model_name,
            prompt=prompt,
            timeout_seconds=self.timeout_seconds,
            keep_alive=self.keep_alive,
        )


class OpenAICompatibleLLMProvider:
    provider_name = "openai_compatible"

    def __init__(
        self,
        model_name: str,
        *,
        base_url: str,
        api_key_env: str = "",
        timeout_seconds: int | None = None,
    ) -> None:
        self.model_name = model_name
        self.base_url = base_url.rstrip("/")
        self.api_key_env = api_key_env
        self.timeout_seconds = timeout_seconds

    def extract_candidates(self, packet: dict[str, Any]) -> str:
        payload = {
            "model": self.model_name,
            "messages": [
                {"role": "system", "content": "You emit raw JSON only."},
                {"role": "user", "content": _candidate_extraction_prompt(packet)},
            ],
            "temperature": 0,
        }
        data = _json_request(
            f"{self.base_url}/chat/completions",
            payload,
            headers=_auth_headers(self.api_key_env),
            timeout_seconds=self.timeout_seconds,
        )
        choices = data.get("choices")
        if not isinstance(choices, list) or not choices:
            raise RuntimeError("chat completion response missing choices")
        message = choices[0].get("message") if isinstance(choices[0], dict) else None
        if not isinstance(message, dict):
            raise RuntimeError("chat completion response missing message")
        return str(message.get("content") or "").strip()


class OpenAICompatibleVLMProvider:
    provider_name = "openai_compatible"

    def __init__(
        self,
        model_name: str,
        *,
        base_url: str,
        api_key_env: str = "",
        timeout_seconds: int | None = None,
    ) -> None:
        self.model_name = model_name
        self.base_url = base_url.rstrip("/")
        self.api_key_env = api_key_env
        self.timeout_seconds = timeout_seconds

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
        data_uri = f"data:image/png;base64,{base64.b64encode(image_bytes).decode('ascii')}"
        payload = {
            "model": self.model_name,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "text",
                            "text": "Describe this MnemonicOS artifact. Return JSON with visual_description, visible_text, warnings.",
                        },
                        {"type": "image_url", "image_url": {"url": data_uri}},
                    ],
                }
            ],
            "temperature": 0,
        }
        data = _json_request(
            f"{self.base_url}/chat/completions",
            payload,
            headers=_auth_headers(self.api_key_env),
            timeout_seconds=self.timeout_seconds,
        )
        choices = data.get("choices")
        if not isinstance(choices, list) or not choices:
            raise RuntimeError("vision completion response missing choices")
        message = choices[0].get("message") if isinstance(choices[0], dict) else None
        text = str(message.get("content") or "").strip() if isinstance(message, dict) else ""
        parsed = _parse_jsonish_response(text)
        warnings = parsed.get("warnings") if isinstance(parsed.get("warnings"), list) else []
        return VisualSnippet(
            artifact_id=artifact.artifact_id,
            marker=artifact.marker,
            source_locator=artifact.source_locator,
            visual_description=str(parsed.get("visual_description") or text).strip(),
            visible_text=str(parsed.get("visible_text") or "").strip(),
            warnings=[*artifact.warnings, *[str(item) for item in warnings]],
            model_metadata={"provider": self.provider_name, "model": self.model_name},
        )


class CLICommandLLMProvider:
    provider_name = "cli_command"

    def __init__(self, model_name: str, *, command: str, timeout_seconds: int = 180) -> None:
        self.model_name = model_name
        self.command = command
        self.timeout_seconds = timeout_seconds

    def extract_candidates(self, packet: dict[str, Any]) -> str:
        if not self.command.strip():
            raise ValueError("llm command is required for cli_command provider")
        completed = subprocess.run(
            [*shlex.split(self.command), _candidate_extraction_prompt(packet)],
            check=False,
            capture_output=True,
            text=True,
            timeout=self.timeout_seconds,
        )
        if completed.returncode != 0:
            raise RuntimeError(completed.stderr.strip() or f"cli_command exited {completed.returncode}")
        return completed.stdout.strip()


def build_vlm_provider(config: AppConfig) -> VLMProvider:
    if config.extraction.vlm_provider == "fixture":
        return FixtureVLMProvider(config.extraction.vlm_model)
    if config.extraction.vlm_provider == "ollama":
        return OllamaVLMProvider(
            config.extraction.vlm_model,
            base_url=config.extraction.vlm_base_url or config.extraction.ollama_base_url,
            timeout_seconds=config.extraction.vlm_timeout_seconds or 240,
            keep_alive=0,
        )
    if config.extraction.vlm_provider in {"openai", "openai_compatible"}:
        return OpenAICompatibleVLMProvider(
            config.extraction.vlm_model,
            base_url=config.extraction.vlm_base_url,
            api_key_env=config.extraction.vlm_api_key_env,
            timeout_seconds=config.extraction.vlm_timeout_seconds,
        )
    raise ValueError(f"unsupported VLM provider: {config.extraction.vlm_provider}")


def build_llm_provider(config: AppConfig) -> LLMProvider:
    if config.extraction.llm_provider == "fixture":
        return FixtureLLMProvider(config.extraction.llm_model)
    if config.extraction.llm_provider == "ollama":
        return OllamaLLMProvider(
            config.extraction.llm_model,
            base_url=config.extraction.llm_base_url or config.extraction.ollama_base_url,
            timeout_seconds=config.extraction.llm_timeout_seconds or 600,
        )
    if config.extraction.llm_provider in {"openai", "openai_compatible"}:
        return OpenAICompatibleLLMProvider(
            config.extraction.llm_model,
            base_url=config.extraction.llm_base_url,
            api_key_env=config.extraction.llm_api_key_env,
            timeout_seconds=config.extraction.llm_timeout_seconds,
        )
    if config.extraction.llm_provider == "cli_command":
        return CLICommandLLMProvider(
            config.extraction.llm_model,
            command=config.extraction.llm_command,
            timeout_seconds=config.extraction.llm_timeout_seconds,
        )
    raise ValueError(f"unsupported LLM provider: {config.extraction.llm_provider}")


def make_llm_packet(
    *,
    active_source_ref: str,
    projection_metadata: dict[str, Any],
    windows: list[TextWindow],
    visual_snippets: list[VisualSnippet],
    retrieval_context_note_ids: list[str] | None = None,
) -> dict[str, Any]:
    packet = {
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
    if retrieval_context_note_ids:
        packet["retrieval_context_note_ids"] = list(retrieval_context_note_ids)
    return packet


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


def _candidate_extraction_prompt(packet: dict[str, Any]) -> str:
    source_ref = packet.get("active_source_ref", "")
    window_ids = [w.get("window_id", "") for w in packet.get("text_windows", [])]
    ids_str = ", ".join(f'"{w}"' for w in window_ids[:8])
    retrieval_note_ids: list[str] = packet.get("retrieval_context_note_ids") or []
    retrieval_section = ""
    if retrieval_note_ids:
        ids_listed = ", ".join(f'"{nid}"' for nid in retrieval_note_ids)
        retrieval_section = (
            "RETRIEVAL ATTRIBUTION RULES:\n"
            "The text windows may contain a '<!-- MnemonicOS context block' section with\n"
            "a '## Memory Context' header. This section lists notes that were retrieved\n"
            "from the MnemonicOS wiki and provided as context to an agent before it acted.\n"
            f"The retrieved note IDs present in this session are: [{ids_listed}]\n"
            "For each candidate you produce:\n"
            "- If the candidate's content appears to paraphrase, follow, rely on, correct,\n"
            "  contradict, or supersede one of these retrieved notes, add a top-level\n"
            '  "retrieval_attribution" array to that candidate.\n'
            "- Each entry: {\"note_id\": \"<id from the list above>\", "
            '"evidence_type\": \"<type>\", \"confidence\": <0.0-1.0>}\n'
            "- evidence_type MUST be one of: paraphrase, semantic_reliance, task_trajectory,\n"
            "  correction, contradiction, supersession\n"
            "- Only use note_id values from the list above. Do not invent IDs.\n"
            "- Do not assign outcome-quality labels (useful/misleading) — those require\n"
            "  evidence from later episodes that is not available here.\n"
            "- Do not re-extract retrieved note content as a new standalone candidate\n"
            "  unless it genuinely contains new information beyond the retrieved note.\n"
            "- If no attribution is evident for a candidate, omit 'retrieval_attribution'.\n\n"
        )
    return (
        "You are the MnemonicOS extraction layer.\n"
        "Return a single JSON object. No markdown, no code fences, no prose — raw JSON only.\n\n"
        "REQUIRED top-level keys: schema_version, candidates, intra_source_relations.\n"
        f'schema_version must be "{SCHEMA_VERSION}".\n\n'
        "CANDIDATE RULES:\n"
        "- Each candidate: type, title, summary, body, confidence, source_refs, evidence, components\n"
        "- Candidate titles should be clear and descriptive; relation resolution uses candidate indices.\n"
        f'- type MUST be one of: "source", "concept", "procedure", "decision", "incident"\n'
        '- confidence MUST be one of: "low", "medium", "high"\n'
        '  - "high": claim directly and explicitly stated in source windows; '
        'evidence quote alone would convince a sceptical reader\n'
        '  - "medium": claim requires inference or synthesis across multiple windows, '
        'or was initially stated but later corrected within this session\n'
        '  - "low": claim is speculative, ambiguous, weakly evidenced, or the source '
        'itself expresses uncertainty\n'
        '  NOTE: confidence measures source evidence quality only. Do NOT factor in '
        'whether the claim seems "already known" or "novel" — that is not your concern.\n'
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
        "INTRA-SESSION CORRECTION RULES:\n"
        "- Sessions may contain self-corrections: an early statement (A) followed by a\n"
        "  later statement (B) that contradicts or refines A based on more evidence.\n"
        "- When you detect this pattern: produce B as the candidate, not A.\n"
        "- Assign B's confidence based on the quality of B's source evidence, ignoring A.\n"
        "- If A and B are both extractable as distinct facts (not a simple correction),\n"
        "  produce both and assert an intra-source relation: B supersedes A.\n"
        "- Do not produce A alone when B clearly supersedes it within the same session.\n\n"
        + retrieval_section
        + "Use only the provided packet as evidence. Reject nothing silently — "
        "if a window has usable content, extract it.\n\n"
        f"{json.dumps(packet, sort_keys=True)}"
    )


def _ollama_generate(
    base_url: str,
    *,
    model: str,
    prompt: str,
    images: list[str] | None = None,
    timeout_seconds: int | None = None,
    keep_alive: int | None = None,
) -> str:
    payload: dict[str, Any] = {
        "model": model,
        "prompt": prompt,
        "stream": False,
    }
    if images:
        payload["images"] = images
    if keep_alive is not None:
        payload["keep_alive"] = keep_alive
    req = request.Request(
        f"{base_url}/api/generate",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with _open_request(req, timeout_seconds=timeout_seconds) as response:
        data = json.loads(response.read().decode("utf-8"))
    return str(data.get("response") or "").strip()


def _auth_headers(api_key_env: str) -> dict[str, str]:
    headers = {"Content-Type": "application/json"}
    if api_key_env:
        token = os.environ.get(api_key_env)
        if token:
            headers["Authorization"] = f"Bearer {token}"
    return headers


def _json_request(
    url: str,
    payload: dict[str, Any],
    *,
    headers: dict[str, str],
    timeout_seconds: int | None,
) -> dict[str, Any]:
    req = request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers=headers,
        method="POST",
    )
    with _open_request(req, timeout_seconds=timeout_seconds) as response:
        data = json.loads(response.read().decode("utf-8"))
    if not isinstance(data, dict):
        raise RuntimeError("provider response was not a JSON object")
    return data


_TRANSIENT_ERRORS = (
    BrokenPipeError,
    ConnectionResetError,
    ConnectionAbortedError,
    # Ollama restarting / not yet listening. Without this, a refused connection
    # surfaces as a generic error and the item is tombstoned after one retry.
    ConnectionRefusedError,
)
# HTTP status codes from the inference endpoint that are worth retrying.
_TRANSIENT_HTTP_STATUS_MIN = 500
# Timeout errors are NOT retried — they surface as InferenceTimeoutError immediately.
_TIMEOUT_ERRORS = (TimeoutError, socket.timeout)
_RETRY_DELAYS = (1.0, 3.0, 8.0)


def _open_request(req: request.Request, *, timeout_seconds: int | None):
    import http.client
    from urllib.error import HTTPError, URLError
    transient = _TRANSIENT_ERRORS + (http.client.RemoteDisconnected,)
    host = urlparse(req.full_url).hostname or ""
    use_proxy_bypass = host in {"127.0.0.1", "localhost", "::1"}
    last_exc: BaseException | None = None
    for attempt, delay in enumerate((*_RETRY_DELAYS, None)):
        try:
            if use_proxy_bypass:
                opener = request.build_opener(request.ProxyHandler({}))
                return opener.open(req, timeout=timeout_seconds)
            return request.urlopen(req, timeout=timeout_seconds)
        except HTTPError as exc:
            # 5xx from Ollama/OpenAI-compatible servers (model loading, OOM
            # recovery, upstream restart) is transient; 4xx is a real error.
            if exc.code >= _TRANSIENT_HTTP_STATUS_MIN:
                last_exc = exc
                if delay is None:
                    break
                time.sleep(delay)
                continue
            raise
        except URLError as exc:
            if isinstance(exc.reason, _TIMEOUT_ERRORS):
                raise InferenceTimeoutError(
                    f"inference call timed out after {timeout_seconds}s"
                ) from exc
            if isinstance(exc.reason, transient):
                last_exc = exc
                if delay is None:
                    break
                time.sleep(delay)
            else:
                raise
        except _TIMEOUT_ERRORS as exc:
            raise InferenceTimeoutError(
                f"inference call timed out after {timeout_seconds}s"
            ) from exc
        except _TRANSIENT_ERRORS as exc:
            last_exc = exc
            if delay is None:
                break
            time.sleep(delay)
    assert last_exc is not None
    raise last_exc


def _parse_jsonish_response(text: str) -> dict[str, Any]:
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}
