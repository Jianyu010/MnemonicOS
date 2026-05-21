from __future__ import annotations

import dataclasses as _dc
from dataclasses import dataclass, field
import os
from pathlib import Path
import tomllib
from typing import TypeVar

_T = TypeVar("_T")

_MODULE_ROOT = Path(__file__).resolve().parents[2]


def _apply_toml(instance: _T, toml_data: dict[str, object]) -> _T:
    """Merge TOML values over a dataclass instance using its field defaults.

    Only keys present in *toml_data* are overridden; absent keys keep the
    dataclass default values.  Only plain scalar types (bool, int, float, str)
    are handled here — complex types (Path, dict, int | None, …) are left to
    the caller.
    """
    if not toml_data:
        return instance
    overrides: dict[str, object] = {}
    for f in _dc.fields(instance):  # type: ignore[arg-type]
        if f.name not in toml_data:
            continue
        raw = toml_data[f.name]
        default_val = getattr(instance, f.name)
        base_type = type(default_val)
        if base_type is bool:
            overrides[f.name] = bool(raw)
        elif base_type is int:
            overrides[f.name] = int(raw)
        elif base_type is float:
            overrides[f.name] = float(raw)
        elif base_type is str:
            overrides[f.name] = str(raw)
        # else: skip complex types — handled separately by caller
    return _dc.replace(instance, **overrides)  # type: ignore[return-value]


@dataclass(slots=True)
class PathsConfig:
    workspace_root: Path
    vault_root: Path
    db_path: Path
    vectors_dir: Path
    graph_dir: Path
    eval_queries: Path


@dataclass(slots=True)
class RetrievalConfig:
    top_k: int = 5  # deprecated: treated as explicit caller override cap only
    include_archive_fallback: bool = True
    pinned_token_budget: int = 2000
    total_context_budget: int = 6000
    bm25_weight: float = 0.30
    semantic_weight: float = 0.25
    type_prior_weight: float = 0.15
    freshness_weight: float = 0.10
    memory_strength_weight: float = 0.10
    trust_weight: float = 0.15  # rerank-model fallback weight (DEFAULT_RERANK_WEIGHTS)
    importance_weight: float = 0.10
    quality_weight: float = 0.08
    exact_alias_weight: float = 0.10
    # Phase 6: multiplicative scoring + relevance gate
    min_relevance_score: float = 0.35
    bm25_saturation: float = 15.0
    # Phase 4 (updated): dynamic top-k
    min_hits: int = 3
    max_hits: int = 15
    pool_fraction: float = 0.10
    tokens_per_hit_estimate: int = 120


@dataclass(slots=True)
class IngestConfig:
    chunk_tokens: int = 400
    chunk_overlap_tokens: int = 50
    default_archive_type: str = "session"
    explicit_markers_only: bool = False
    auto_trigger_pipeline: bool = True
    lazy_pdf_ingest: bool = True


@dataclass(slots=True)
class TextProcessingConfig:
    cjk_enabled: bool = True
    cjk_ngram_min: int = 1
    cjk_ngram_max: int = 3
    latin_min_token_chars: int = 3


@dataclass(slots=True)
class EmbeddingsConfig:
    enabled: bool = True
    provider: str = "hash"
    model: str = "hash-256-v1"
    dimensions: int = 256
    base_url: str = ""
    api_key_env: str = ""
    timeout_seconds: int = 60
    reembed_on_summary_change_only: bool = True


@dataclass(slots=True)
class GraphConfig:
    enabled: bool = True
    focal_limit: int = 3
    expand_limit: int = 8
    graph_weight: float = 0.20


@dataclass(slots=True)
class TrustConfig:
    min_training_samples: int = 8
    current_query_stale_penalty: float = 0.30
    current_query_contested_penalty: float = 0.65
    historical_query_relief: float = 0.20
    section_limit: int = 5
    # freshness multipliers used in trust.freshness_multiplier()
    historical_contested_multiplier: float = 0.75
    historical_stale_min: float = 0.60
    historical_suspect_multiplier: float = 0.75
    current_contested_min: float = 0.05
    current_stale_min: float = 0.10
    current_suspect_multiplier: float = 0.70
    current_aging_multiplier: float = 0.88


@dataclass(slots=True)
class ExtractionConfig:
    vlm_provider: str = "fixture"
    vlm_model: str = "llama3.2-vision:latest"
    llm_provider: str = "fixture"
    llm_model: str = "gemma4:e4b"
    ollama_base_url: str = "http://127.0.0.1:11434"
    llm_base_url: str = ""
    llm_api_key_env: str = ""
    llm_command: str = ""
    llm_timeout_seconds: int | None = None
    vlm_base_url: str = ""
    vlm_api_key_env: str = ""
    vlm_timeout_seconds: int | None = None
    max_queue_batch: int = 10
    max_windows_per_batch: int = 1
    max_embedded_images: int = 100
    min_embedded_image_px: int = 64
    max_extraction_item_seconds: int = 7200
    stall_timeout_seconds: int = 900
    dual_lane_enabled: bool = False
    quick_lane_batch: int = 5
    slow_lane_batch: int = 5


@dataclass(slots=True)
class PromotionConfig:
    enabled: bool = True
    max_components_per_candidate: int = 50
    max_candidates_per_run: int = 20
    min_confidence_for_direct_write: str = "medium"
    auto_review_on_conflict: bool = True
    retry_after_hours: int = 24
    conflict_stale_threshold_days: int = 30
    conflict_glitch_threshold_days: int = 1


@dataclass(slots=True)
class BridgeConfig:
    session_prefix: str
    can_promote: bool


@dataclass(slots=True)
class DaemonConfig:
    socket_path: Path
    pid_path: Path
    log_path: Path
    schedules: dict[str, str] = field(default_factory=dict)
    max_pending: int = 200
    max_oldest_age_minutes: int = 1440
    stale_episode_threshold_hours: int = 2
    ollama_managed: bool = False
    ollama_command: str = ""


@dataclass(slots=True)
class MaintenanceConfig:
    lifecycle_verified_threshold: int = 2
    lifecycle_canonical_threshold: int = 4
    lifecycle_drift_days: int = 30
    graph_rebuild_enabled: bool = True
    lifecycle_audit_enabled: bool = True
    importance_scoring_enabled: bool = True
    semantic_dedup_threshold: float = 0.92
    semantic_dedup_enabled: bool = True
    embedding_neighbor_enabled: bool = True
    neighbor_similarity_threshold: float = 0.78
    neighbor_k: int = 10
    neighbor_scan_max_age_days: int = 7
    cluster_validation_enabled: bool = True
    cluster_validation_max_size: int = 12
    cluster_validation_min_cluster_size: int = 2
    pattern_discovery_enabled: bool = True
    requeue_enabled: bool = True
    requeue_min_age_days: int = 30
    requeue_context_enabled: bool = True
    requeue_context_top_k: int = 5
    requeue_max_per_run: int = 20
    passive_feedback_enabled: bool = True
    passive_feedback_inline_only: bool = False
    # passive feedback tuning
    passive_inline_boost_threshold: float = 0.85
    passive_weak_confidence_default: float = 0.20
    passive_misleading_min_confidence: float = 0.50
    passive_max_promoted_per_session: int = 10
    passive_retrieval_hits_limit: int = 500
    # pattern discovery tuning
    pattern_role_similarity_threshold: float = 0.85
    pattern_ch3_candidate_score: float = 0.70
    pattern_ch2_max_pairs: int = 20
    # importance scoring formula weights
    importance_out_degree_weight: float = 0.40
    importance_sigmoid_scale: float = 5.0
    importance_sigmoid_amp: float = 2.0
    importance_connectivity_weight: float = 0.35
    importance_trust_weight: float = 0.25
    importance_freshness_weight: float = 0.25
    importance_lifecycle_weight: float = 0.15
    # lifecycle weights used in importance scoring
    lifecycle_weight_canonical: float = 1.00
    lifecycle_weight_verified: float = 0.70
    lifecycle_weight_draft: float = 0.40
    lifecycle_weight_review: float = 0.30
    lifecycle_weight_contested: float = 0.20
    lifecycle_weight_archived: float = 0.05


@dataclass(slots=True)
class RerankerConfig:
    """Tuning knobs for the reranker scoring and diversity logic."""
    historical_bonus_max: float = 0.20
    historical_bonus_stale: float = 0.20
    historical_bonus_suspect: float = 0.12
    historical_bonus_aging: float = 0.06
    min_type_prior_weight: float = 0.25
    min_freshness_weight_what_changed: float = 0.25
    chain_diversity_min: int = 2
    min_archive_hits: int = 3


@dataclass(slots=True)
class SignalConfig:
    """Thresholds and constants for signal integration and quality scoring."""
    default_freshness_score: float = 0.20
    freshness_max_age_days: int = 365
    min_summary_word_count: int = 8
    min_excerpt_word_count: int = 20
    excerpt_char_limit: int = 240
    passive_used_weight: float = 0.30
    passive_useful_weight: float = 0.60
    passive_misleading_weight: float = 0.60


@dataclass(slots=True)
class QueryParserConfig:
    """Constants for query classification and anchor term extraction."""
    confidence_base: float = 0.45
    confidence_primary_bonus: float = 0.20
    confidence_secondary_bonus: float = 0.10
    max_anchor_terms: int = 5
    max_candidate_tokens: int = 5
    max_expanded_terms: int = 3


@dataclass(slots=True)
class OrchestratorConfig:
    """Token budgets and retry limits for the retrieval orchestrator."""
    default_token_budget: int = 3000
    research_token_budget: int = 5000
    max_focused_retries: int = 1


@dataclass(slots=True)
class BridgeMatchingConfig:
    """Score thresholds for the bridge candidate-matching logic."""
    exact_title_score: float = 1.00
    id_slug_score: float = 0.99
    slug_score: float = 0.98
    alias_exact_score: float = 0.96
    min_candidate_score: float = 0.55
    candidate_limit: int = 5


@dataclass(slots=True)
class AppConfig:
    paths: PathsConfig
    retrieval: RetrievalConfig = field(default_factory=RetrievalConfig)
    ingest: IngestConfig = field(default_factory=IngestConfig)
    text_processing: TextProcessingConfig = field(default_factory=TextProcessingConfig)
    embeddings: EmbeddingsConfig = field(default_factory=EmbeddingsConfig)
    graph: GraphConfig = field(default_factory=GraphConfig)
    trust: TrustConfig = field(default_factory=TrustConfig)
    extraction: ExtractionConfig = field(default_factory=ExtractionConfig)
    promotion: PromotionConfig = field(default_factory=PromotionConfig)
    maintenance: MaintenanceConfig = field(default_factory=MaintenanceConfig)
    reranker: RerankerConfig = field(default_factory=RerankerConfig)
    signal: SignalConfig = field(default_factory=SignalConfig)
    query_parser: QueryParserConfig = field(default_factory=QueryParserConfig)
    orchestrator: OrchestratorConfig = field(default_factory=OrchestratorConfig)
    bridge_matching: BridgeMatchingConfig = field(default_factory=BridgeMatchingConfig)
    daemon: DaemonConfig | None = None
    bridges: dict[str, BridgeConfig] = field(default_factory=dict)
    config_path: Path | None = None


def _parse_timeout(value: object) -> int | None:
    if value is None:
        return None
    as_int = int(value)
    return None if as_int <= 0 else as_int


def _resolve_path(base_dir: Path, raw_value: str) -> Path:
    path = Path(raw_value)
    if path.is_absolute():
        return path
    return (base_dir / path).resolve()


def _config_in_workspace(root: Path) -> Path | None:
    primary = root / "config" / "memory.toml"
    if primary.exists():
        return primary.resolve()
    example = root / "config" / "memory.example.toml"
    if example.exists():
        return example.resolve()
    return None


def _env_bool(name: str) -> bool | None:
    raw = os.environ.get(name)
    if raw is None:
        return None
    value = raw.strip().casefold()
    if value in {"1", "true", "yes", "on"}:
        return True
    if value in {"0", "false", "no", "off"}:
        return False
    return None


def _discover_workspace_root(start: str | Path | None = None) -> Path:
    start_root = Path(start or Path.cwd()).resolve()
    for candidate in (start_root, *start_root.parents):
        if _config_in_workspace(candidate) is not None:
            return candidate
    if _config_in_workspace(_MODULE_ROOT) is not None:
        return _MODULE_ROOT
    return start_root


def _workspace_root_from_config_path(config_path: Path) -> Path:
    if config_path.parent.name == "config":
        return config_path.parent.parent.resolve()
    return config_path.parent.resolve()


def default_config_path(workspace_root: str | Path | None = None) -> Path:
    root = Path(workspace_root).resolve() if workspace_root is not None else _discover_workspace_root()
    config = _config_in_workspace(root)
    if config is not None:
        return config
    return (root / "config" / "memory.example.toml").resolve()


def load_config(config_path: str | Path | None = None, workspace_root: str | Path | None = None) -> AppConfig:
    if config_path is None:
        root = Path(workspace_root).resolve() if workspace_root is not None else _discover_workspace_root()
        resolved_config = default_config_path(root)
    else:
        _cp = Path(config_path).resolve()
        if _cp.is_dir():
            root = _cp
            resolved_config = default_config_path(root)
        else:
            resolved_config = _cp
            root = Path(workspace_root).resolve() if workspace_root is not None else _workspace_root_from_config_path(resolved_config)
    data = tomllib.loads(resolved_config.read_text())

    # --- paths (no defaults helper; Path construction requires special handling) ---
    paths_data = data.get("paths", {})
    _pd = PathsConfig(
        workspace_root=root,
        vault_root=root / "brain",
        db_path=root / "brain/data/archive.db",
        vectors_dir=root / "brain/data/vectors",
        graph_dir=root / "brain/data/graph",
        eval_queries=root / "evals/queries.jsonl",
    )
    paths = PathsConfig(
        workspace_root=root,
        vault_root=_resolve_path(root, paths_data.get("vault_root", str(_pd.vault_root.relative_to(root)))),
        db_path=_resolve_path(root, paths_data.get("db_path", str(_pd.db_path.relative_to(root)))),
        vectors_dir=_resolve_path(root, paths_data.get("vectors_dir", str(_pd.vectors_dir.relative_to(root)))),
        graph_dir=_resolve_path(root, paths_data.get("graph_dir", str(_pd.graph_dir.relative_to(root)))),
        eval_queries=_resolve_path(root, paths_data.get("eval_queries", str(_pd.eval_queries.relative_to(root)))),
    )

    # --- simple scalar sections: defaults from dataclass, overrides from TOML ---
    retrieval = _apply_toml(RetrievalConfig(), data.get("retrieval", {}))
    graph = _apply_toml(GraphConfig(), data.get("graph", {}))
    trust = _apply_toml(TrustConfig(), data.get("trust", {}))
    promotion = _apply_toml(PromotionConfig(), data.get("promotion", {}))
    reranker = _apply_toml(RerankerConfig(), data.get("reranker", {}))
    signal = _apply_toml(SignalConfig(), data.get("signal", {}))
    query_parser_cfg = _apply_toml(QueryParserConfig(), data.get("query_parser", {}))
    orchestrator = _apply_toml(OrchestratorConfig(), data.get("orchestrator", {}))
    bridge_matching = _apply_toml(BridgeMatchingConfig(), data.get("bridge_matching", {}))
    maintenance = _apply_toml(MaintenanceConfig(), data.get("maintenance", {}))
    text_processing = _apply_toml(TextProcessingConfig(), data.get("text_processing", {}))

    # --- ingest: env-var override for auto_trigger_pipeline ---
    auto_trigger_override = _env_bool("MNEMOS_AUTO_TRIGGER_PIPELINE")
    ingest = _apply_toml(IngestConfig(), data.get("ingest", {}))
    if auto_trigger_override is not None:
        ingest = _dc.replace(ingest, auto_trigger_pipeline=auto_trigger_override)

    # --- embeddings: some fields default to "" so need explicit string coercion ---
    embeddings = _apply_toml(EmbeddingsConfig(), data.get("embeddings", {}))

    # --- extraction: complex multi-source fallback logic kept explicit ---
    extraction_data = data.get("extraction", {})
    models_data = data.get("models", {})
    llm_data = models_data.get("llm", {}) if isinstance(models_data.get("llm", {}), dict) else {}
    vlm_data = models_data.get("vlm", {}) if isinstance(models_data.get("vlm", {}), dict) else {}
    _ed = ExtractionConfig()
    default_ollama_base_url = str(extraction_data.get("ollama_base_url", _ed.ollama_base_url))
    extraction = ExtractionConfig(
        vlm_provider=str(vlm_data.get("provider", extraction_data.get("vlm_provider", _ed.vlm_provider))),
        vlm_model=str(vlm_data.get("model", extraction_data.get("vlm_model", _ed.vlm_model))),
        llm_provider=str(llm_data.get("provider", extraction_data.get("llm_provider", _ed.llm_provider))),
        llm_model=str(llm_data.get("model", extraction_data.get("llm_model", _ed.llm_model))),
        ollama_base_url=default_ollama_base_url,
        llm_base_url=str(llm_data.get("base_url", extraction_data.get("llm_base_url", default_ollama_base_url))),
        llm_api_key_env=str(llm_data.get("api_key_env", extraction_data.get("llm_api_key_env", _ed.llm_api_key_env))),
        llm_command=str(llm_data.get("command", extraction_data.get("llm_command", _ed.llm_command))),
        llm_timeout_seconds=_parse_timeout(llm_data.get("timeout_seconds", extraction_data.get("llm_timeout_seconds"))),
        vlm_base_url=str(vlm_data.get("base_url", extraction_data.get("vlm_base_url", default_ollama_base_url))),
        vlm_api_key_env=str(vlm_data.get("api_key_env", extraction_data.get("vlm_api_key_env", _ed.vlm_api_key_env))),
        vlm_timeout_seconds=_parse_timeout(vlm_data.get("timeout_seconds", extraction_data.get("vlm_timeout_seconds"))),
        max_queue_batch=int(extraction_data.get("max_queue_batch", _ed.max_queue_batch)),
        max_windows_per_batch=int(extraction_data.get("max_windows_per_batch", _ed.max_windows_per_batch)),
        max_embedded_images=int(extraction_data.get("max_embedded_images", _ed.max_embedded_images)),
        min_embedded_image_px=int(extraction_data.get("min_embedded_image_px", _ed.min_embedded_image_px)),
        max_extraction_item_seconds=int(extraction_data.get("max_extraction_item_seconds", _ed.max_extraction_item_seconds)),
        stall_timeout_seconds=int(extraction_data.get("stall_timeout_seconds", _ed.stall_timeout_seconds)),
        dual_lane_enabled=bool(extraction_data.get("dual_lane_enabled", _ed.dual_lane_enabled)),
        quick_lane_batch=int(extraction_data.get("quick_lane_batch", _ed.quick_lane_batch)),
        slow_lane_batch=int(extraction_data.get("slow_lane_batch", _ed.slow_lane_batch)),
    )

    # --- daemon: Path construction ---
    daemon_data = data.get("daemon", {})
    daemon_schedules = daemon_data.get("schedules", {})
    daemon = DaemonConfig(
        socket_path=_resolve_path(root, str(daemon_data.get("socket_path", "brain/data/mnemonic.sock"))),
        pid_path=_resolve_path(root, str(daemon_data.get("pid_path", "brain/data/mnemonic.pid"))),
        log_path=_resolve_path(root, str(daemon_data.get("log_path", "brain/logs/daemon.log"))),
        schedules={str(name): str(schedule) for name, schedule in daemon_schedules.items()},
        max_pending=int(daemon_data.get("max_pending", DaemonConfig.__dataclass_fields__["max_pending"].default)),
        max_oldest_age_minutes=int(daemon_data.get("max_oldest_age_minutes", DaemonConfig.__dataclass_fields__["max_oldest_age_minutes"].default)),
        stale_episode_threshold_hours=int(daemon_data.get("stale_episode_threshold_hours", DaemonConfig.__dataclass_fields__["stale_episode_threshold_hours"].default)),
        ollama_managed=bool(daemon_data.get("ollama_managed", DaemonConfig.__dataclass_fields__["ollama_managed"].default)),
        ollama_command=str(daemon_data.get("ollama_command", DaemonConfig.__dataclass_fields__["ollama_command"].default)),
    )

    # --- per-bridge config: dynamic keys ---
    bridges: dict[str, BridgeConfig] = {}
    bridges_data = data.get("bridges")
    if bridges_data is None:
        bridges_data = data.get("agents", {})
    for bridge_name, bridge_data in bridges_data.items():
        bridges[bridge_name] = BridgeConfig(
            session_prefix=str(bridge_data.get("session_prefix", bridge_name)),
            can_promote=bool(bridge_data.get("can_promote", False)),
        )

    return AppConfig(
        paths=paths,
        retrieval=retrieval,
        ingest=ingest,
        text_processing=text_processing,
        embeddings=embeddings,
        graph=graph,
        trust=trust,
        extraction=extraction,
        promotion=promotion,
        maintenance=maintenance,
        reranker=reranker,
        signal=signal,
        query_parser=query_parser_cfg,
        orchestrator=orchestrator,
        bridge_matching=bridge_matching,
        daemon=daemon,
        bridges=bridges,
        config_path=resolved_config,
    )
