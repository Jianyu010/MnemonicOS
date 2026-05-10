# West-Flange Phase 3: Projection Lease API

## Goal

Expose usable raw as a temporary, pull-based in-memory projection. The API lets
later extraction code stream text windows and artifact views without persisting
usable raw.

## Projection Pipeline

```text
0. pre_parse_exact_identity       # Phase 2, may return thin duplicate lease
1. connector.parse(true_raw)      # Phase 1 connector result
2. source_shape_normalize
3. within_source_dedup            # Phase 2
4. post_parse_identity_labels     # Phase 2, labels only
5. artifact_context_attach        # Phase 4
6. chunk_into_windows
7. emit ProjectionLease
```

Stage 0 is the only short-circuit. Stage 4 never short-circuits.

## Source Shape Normalization

- Single documents project directly.
- Captured sessions assemble `events.jsonl` plus referenced sidecars into one
  chronological in-memory projection.
- The assembled view is not archival. Captured episode files remain true raw.

## Python API

v1 ships an in-process Python service. No HTTP transport is required.

```python
class ProjectionService:
    def open_projection(
        self,
        source_ref: SourceRef,
        *,
        options: ProjectionOptions | None = None,
    ) -> ProjectionLease:
        ...

    def iter_text_windows(self, lease_id: str) -> Iterator[TextWindow]:
        ...

    def iter_artifacts(self, lease_id: str) -> Iterator[ArtifactView]:
        ...

    def close_projection(self, lease_id: str) -> None:
        ...
```

## Core Models

```python
@dataclass
class SourceRef:
    source_kind: str
    archive_id: str | None
    conversation_id: str | None
    episode_id: str | None

@dataclass
class SourceSpan:
    source_kind: str
    source_locator: str
    char_start: int | None
    char_end: int | None

@dataclass
class ArtifactMarker:
    artifact_id: str
    kind: str
    marker_text: str
    char_offset: int

@dataclass
class ProjectionOptions:
    window_token_target: int = 10_000
    window_token_overlap: int = 512
    figures_enabled: bool = True
    max_per_figure_bytes: int = 256 * 1024 * 1024
    max_total_payload_bytes: int | None = None
    lease_idle_ttl_seconds: int = 1_800
    lease_hard_ttl_seconds: int = 21_600

@dataclass
class ProjectionLease:
    lease_id: str
    source_ref: SourceRef
    duplicate_state: str
    canonical_source_ref: SourceRef | None
    opened_at: datetime
    expires_at: datetime
    warnings: list[str]
    projection_metadata: dict[str, object]

@dataclass
class TextWindow:
    window_id: str
    text: str
    source_spans: list[SourceSpan]
    artifact_markers: list[ArtifactMarker]
    token_estimate: int

@dataclass
class ArtifactView:
    artifact_id: str
    kind: str
    mime_type: str | None
    payload: bytes | None
    source_attachment_path: str | None
    source_locator: str
    context_windows: list[str]
    marker: str
    warnings: list[str]
```

## Duplicate-State Behavior

- `exact_duplicate`: return a thin redirect lease with `canonical_source_ref`,
  no text windows, and no payloads.
- `canonical_duplicate`: project normally and set `canonical_source_ref`.
- `possible_duplicate`: project normally and set `canonical_source_ref` when a
  best candidate exists.
- `overlap_related`: project normally and preserve unique delta content.
- `unique`: project normally.

## Projection Metadata

`projection_metadata` is additive and non-content-only. Consumers must tolerate
unknown keys.

Defined keys:

| Key | Type | Meaning |
|---|---|---|
| `deduplicated_repeated_prefix` | bool | Cumulative transcript prefix collapsed |
| `cumulative_prefix_collapsed` | bool | Same condition for episode projections |
| `deduplicated_event_count` | int | Events dropped during within-source dedup |
| `episode_event_count` | int | Raw episode event count before dedup |
| `unique_event_count` | int | Events retained after dedup |
| `lsh_candidate_count` | int | Candidates returned by LSH lookup |
| `canonical_redirect_archive_id` | str or null | Canonical redirect target |
| `figure_count` | int | Artifact-lane figures |
| `table_count` | int | Artifact-lane tables |
| `text_window_count` | int | Total yielded text windows |
| `pre_parse_short_circuited` | bool | Stage 0 skipped connector/projection |

Forbidden: any key carrying extracted text, captions, OCR text, or visual
descriptions.

## Text Windowing

Defaults:

- `window_token_target = 10_000`
- `window_token_overlap = 512`

Splitting policy:

- paragraph-aware
- never split mid-paragraph if the next paragraph fits within target plus 20%
  slack
- never split an artifact marker away from the paragraph that contains it
- preserve source order and source locators
- use overlap only to avoid broken paragraphs, references, or markers

Text windows are scheduling units for downstream models. They are not persisted
archive chunks.

## Lease TTL and Eviction

- Idle TTL: 1800 seconds. Reset on each window iteration, artifact iteration, or
  payload fetch.
- Hard TTL: 21600 seconds. Absolute upper bound.
- Manual close is always available.
- Reprocess of the same `archive_id` invalidates open leases for that artifact.
- New ingestion does not close unrelated leases.
- Eviction releases payload bytes. A short-lived evicted-lease debug record may
  remain in memory, but usable raw must not be persisted.

## Payload Caps

- Per artifact payload cap: 256 MB.
- Total per-lease payload cap:
  - if `psutil` is installed: `min(25% of available memory, 4 GB)`
  - otherwise: 4 GB
- Enforce total cap lazily as payloads are fetched.
- Payload over cap yields metadata and a warning but no payload bytes.
- Memory-budget exhaustion does not prevent text-window streaming.

## Components

```text
src/second_brain/projection/
    pipeline.py
    session_assembler.py
    chunker.py
```

Responsibilities:

- `pipeline.py`: orchestrates pipeline stages, lease lifecycle, TTL, eviction,
  payload budget, duplicate-state behavior.
- `session_assembler.py`: assembles captured episode events and sidecar pointers
  into a chronological in-memory projection.
- `chunker.py`: emits bounded `TextWindow` objects with marker-aware overlap.

Phase 4 adds artifact context and markers. Phase 2 supplies identity labels.

## Deferred HTTP Transport

HTTP transport is v2. When needed, mirror the Python API:

- `POST /v1/projections/open`
- `GET /v1/projections/{lease_id}/text-windows`
- `GET /v1/projections/{lease_id}/artifacts`
- `GET /v1/projections/{lease_id}/artifacts/{artifact_id}/payload`
- `POST /v1/projections/{lease_id}/close`

v2 requirements:

- opaque UUID v4 lease IDs
- API key for non-loopback bind
- TLS for non-loopback bind
- chunked streaming payloads
- `404` unknown lease, `410` closed/evicted, `423` reprocess lock, `413` payload
  too large, `503` memory budget exhausted

## Verification

- Exact duplicate lease yields no windows or payloads and has
  `pre_parse_short_circuited = true`.
- Canonical, possible, overlap-related, and unique leases yield windows normally.
- Windowing preserves paragraph boundaries and markers.
- Lease TTL eviction releases payload bytes.
- Payload cap warnings do not block text windows.
- Session cumulative-prefix input projects as delta-only text windows with
  non-content dedup counters.
