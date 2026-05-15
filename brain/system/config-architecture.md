# Configuration Architecture

## Single Source of Truth

**Canonical defaults live in dataclass fields** (`src/second_brain/config.py`).  
`config/memory.toml` holds user overrides — only keys that differ from defaults need to appear.  
`load_config()` merges TOML over dataclass defaults via `_apply_toml()`.  
**No inline fallback literals exist in `load_config`** (previously every param was duplicated there).

### Merge flow

```
Dataclass field defaults   ← canonical
        │
        ▼
  _apply_toml(instance, toml_section)
        │
        ▼
  AppConfig (runtime)      ← TOML values override defaults
```

### _apply_toml helper

Iterates `dataclasses.fields(instance)`, overrides only keys present in the TOML dict, infers target type from the existing default value (`type(getattr(instance, f.name))`). Handles `bool`, `int`, `float`, `str`. Skips complex types (`Path`, `dict`, `int | None`) — those sections handled explicitly.

---

## Config Sections and Owning Modules

| TOML section | Dataclass | Primary consumer |
|---|---|---|
| `[retrieval]` | `RetrievalConfig` | `reranker.py`, `retrieval.py`, `query_parser.py` |
| `[graph]` | `GraphConfig` | `graph.py`, `reranker.py` |
| `[trust]` | `TrustConfig` | `trust.py` (multipliers + penalties) |
| `[ingest]` | `IngestConfig` | `ingest.py`, `pipeline.py` |
| `[embeddings]` | `EmbeddingsConfig` | `embedding_providers.py` |
| `[extraction]` | `ExtractionConfig` | `extraction/` |
| `[promotion]` | `PromotionConfig` | `promotion/` |
| `[maintenance]` | `MaintenanceConfig` | `maintenance.py` (all subsystems) |
| `[reranker]` | `RerankerConfig` | `reranker.py` (bonus values, diversity) |
| `[signal]` | `SignalConfig` | `signal_integration.py` |
| `[query_parser]` | `QueryParserConfig` | `query_parser.py` |
| `[orchestrator]` | `OrchestratorConfig` | `orchestrator.py`, `emission.py` |
| `[bridge_matching]` | `BridgeMatchingConfig` | `bridge.py` |
| `[daemon]` | `DaemonConfig` | `daemon.py` |
| `[bridges.*]` | `BridgeConfig` (per-bridge) | `bridge.py`, `runtime.py` |

---

## What Is NOT in Config

These are algorithmic/structural constants kept as module-level code constants:

| Constant | Location | Reason |
|---|---|---|
| `SIMHASH_BITS=64`, `SIMHASH_BANDS=4`, `SHINGLE_SIZE=5` | `artifact_identity.py` | LSH algorithm — changing breaks stored fingerprints |
| `DEFAULT_INLINE_THRESHOLD_BYTES=32768` | `session_capture.py` | Storage layout constant |
| `TRACE_MAX_DEPTH=2` etc. | `runtime.py` | Tracing display limits only |
| Identity fingerprint char limit `1000`, overlap ratio `0.6` | `artifact_identity.py` | Algorithmic |

---

## DEFAULT_RERANK_WEIGHTS (trust.py)

`DEFAULT_RERANK_WEIGHTS` is the rerank-model fallback when training data is absent. Its values now **derive from dataclass defaults** at module load time:

```python
_RC = RetrievalConfig()
_GC = GraphConfig()
DEFAULT_RERANK_WEIGHTS = {
    "bm25_weight": _RC.bm25_weight,
    "graph_weight": _GC.graph_weight,
    "trust_weight": _RC.trust_weight,
    ...
}
```

This means changing a `RetrievalConfig` default propagates to both live scoring and the fallback rerank model. The `trust_weight` field (0.15) differs from `memory_strength_weight` (0.10) — `trust_weight` is the learning fallback; `memory_strength_weight` is used in the live scoring path via `reranker._weights()`.

---

## Adding a New Tunable Constant

1. Add field with default to the relevant dataclass in `config.py`
2. Add the key to `config/memory.toml` (documentation; not required if default is acceptable)
3. Add to `config/memory.example.toml` with a comment
4. In the consuming module: read from `config.<section>.<field>` — no inline literal
5. If the function doesn't currently receive `AppConfig`, add it as a parameter

---

## History

- Phase 1 (trim-config branch): eliminated 3-way duplication (dataclass default + load_config inline fallback + TOML). Fixed `min_relevance_score` mismatch (load_config had 0.25 while dataclass and TOML had 0.35). Added `_apply_toml` helper.
- Phase 2 (same branch): added `RerankerConfig`, `SignalConfig`, `QueryParserConfig`, `OrchestratorConfig`, `BridgeMatchingConfig`. Extended `TrustConfig` (multipliers) and `MaintenanceConfig` (passive feedback, pattern discovery, importance scoring weights, lifecycle weights).
- Phase 3 (same branch): wired all scattered constants in `reranker.py`, `trust.py`, `signal_integration.py`, `query_parser.py`, `bridge.py`, `orchestrator.py`, `maintenance.py`.
