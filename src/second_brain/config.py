from __future__ import annotations

from dataclasses import dataclass, field
import os
from pathlib import Path
import tomllib


_MODULE_ROOT = Path(__file__).resolve().parents[2]


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
    top_k: int = 5
    include_archive_fallback: bool = True
    pinned_token_budget: int = 2000
    total_context_budget: int = 6000
    bm25_weight: float = 0.30
    semantic_weight: float = 0.25
    type_prior_weight: float = 0.15
    freshness_weight: float = 0.10
    memory_strength_weight: float = 0.10
    exact_alias_weight: float = 0.10


@dataclass(slots=True)
class IngestConfig:
    chunk_tokens: int = 400
    chunk_overlap_tokens: int = 50
    default_archive_type: str = "session"
    explicit_markers_only: bool = False
    auto_trigger_pipeline: bool = True


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
    llm_timeout_seconds: int = 180
    vlm_base_url: str = ""
    vlm_api_key_env: str = ""
    vlm_timeout_seconds: int = 180
    max_queue_batch: int = 10


@dataclass(slots=True)
class PromotionConfig:
    enabled: bool = True
    max_components_per_candidate: int = 50
    max_candidates_per_run: int = 20
    min_confidence_for_direct_write: str = "medium"
    auto_review_on_conflict: bool = True
    retry_after_hours: int = 24


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


@dataclass(slots=True)
class AppConfig:
    paths: PathsConfig
    retrieval: RetrievalConfig = field(default_factory=RetrievalConfig)
    ingest: IngestConfig = field(default_factory=IngestConfig)
    embeddings: EmbeddingsConfig = field(default_factory=EmbeddingsConfig)
    graph: GraphConfig = field(default_factory=GraphConfig)
    trust: TrustConfig = field(default_factory=TrustConfig)
    extraction: ExtractionConfig = field(default_factory=ExtractionConfig)
    promotion: PromotionConfig = field(default_factory=PromotionConfig)
    maintenance: MaintenanceConfig = field(default_factory=MaintenanceConfig)
    daemon: DaemonConfig | None = None
    bridges: dict[str, BridgeConfig] = field(default_factory=dict)
    config_path: Path | None = None


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
        resolved_config = Path(config_path).resolve()
        root = Path(workspace_root).resolve() if workspace_root is not None else _workspace_root_from_config_path(resolved_config)
    data = tomllib.loads(resolved_config.read_text())

    paths_data = data.get("paths", {})
    paths = PathsConfig(
        workspace_root=root,
        vault_root=_resolve_path(root, paths_data.get("vault_root", "brain")),
        db_path=_resolve_path(root, paths_data.get("db_path", "brain/data/archive.db")),
        vectors_dir=_resolve_path(root, paths_data.get("vectors_dir", "brain/data/vectors")),
        graph_dir=_resolve_path(root, paths_data.get("graph_dir", "brain/data/graph")),
        eval_queries=_resolve_path(root, paths_data.get("eval_queries", "evals/queries.jsonl")),
    )

    retrieval_data = data.get("retrieval", {})
    retrieval = RetrievalConfig(
        top_k=int(retrieval_data.get("top_k", 5)),
        include_archive_fallback=bool(retrieval_data.get("include_archive_fallback", True)),
        pinned_token_budget=int(retrieval_data.get("pinned_token_budget", 2000)),
        total_context_budget=int(retrieval_data.get("total_context_budget", 6000)),
        bm25_weight=float(retrieval_data.get("bm25_weight", 0.30)),
        semantic_weight=float(retrieval_data.get("semantic_weight", 0.25)),
        type_prior_weight=float(retrieval_data.get("type_prior_weight", 0.15)),
        freshness_weight=float(retrieval_data.get("freshness_weight", 0.10)),
        memory_strength_weight=float(retrieval_data.get("memory_strength_weight", 0.10)),
        exact_alias_weight=float(retrieval_data.get("exact_alias_weight", 0.10)),
    )

    ingest_data = data.get("ingest", {})
    auto_trigger_override = _env_bool("MNEMOS_AUTO_TRIGGER_PIPELINE")
    ingest = IngestConfig(
        chunk_tokens=int(ingest_data.get("chunk_tokens", 400)),
        chunk_overlap_tokens=int(ingest_data.get("chunk_overlap_tokens", 50)),
        default_archive_type=str(ingest_data.get("default_archive_type", "session")),
        explicit_markers_only=bool(ingest_data.get("explicit_markers_only", False)),
        auto_trigger_pipeline=auto_trigger_override
        if auto_trigger_override is not None
        else bool(ingest_data.get("auto_trigger_pipeline", True)),
    )

    embeddings_data = data.get("embeddings", {})
    embeddings = EmbeddingsConfig(
        enabled=bool(embeddings_data.get("enabled", True)),
        provider=str(embeddings_data.get("provider", "hash")),
        model=str(embeddings_data.get("model", "hash-256-v1")),
        dimensions=int(embeddings_data.get("dimensions", 256)),
        base_url=str(embeddings_data.get("base_url", "")),
        api_key_env=str(embeddings_data.get("api_key_env", "")),
        timeout_seconds=int(embeddings_data.get("timeout_seconds", 60)),
        reembed_on_summary_change_only=bool(embeddings_data.get("reembed_on_summary_change_only", True)),
    )

    graph_data = data.get("graph", {})
    graph = GraphConfig(
        enabled=bool(graph_data.get("enabled", True)),
        focal_limit=int(graph_data.get("focal_limit", 3)),
        expand_limit=int(graph_data.get("expand_limit", 8)),
        graph_weight=float(graph_data.get("graph_weight", 0.20)),
    )

    trust_data = data.get("trust", {})
    trust = TrustConfig(
        min_training_samples=int(trust_data.get("min_training_samples", 8)),
        current_query_stale_penalty=float(trust_data.get("current_query_stale_penalty", 0.30)),
        current_query_contested_penalty=float(trust_data.get("current_query_contested_penalty", 0.65)),
        historical_query_relief=float(trust_data.get("historical_query_relief", 0.20)),
        section_limit=int(trust_data.get("section_limit", 5)),
    )

    extraction_data = data.get("extraction", {})
    models_data = data.get("models", {})
    llm_data = models_data.get("llm", {}) if isinstance(models_data.get("llm", {}), dict) else {}
    vlm_data = models_data.get("vlm", {}) if isinstance(models_data.get("vlm", {}), dict) else {}
    default_ollama_base_url = str(extraction_data.get("ollama_base_url", "http://127.0.0.1:11434"))
    extraction = ExtractionConfig(
        vlm_provider=str(vlm_data.get("provider", extraction_data.get("vlm_provider", "fixture"))),
        vlm_model=str(vlm_data.get("model", extraction_data.get("vlm_model", "llama3.2-vision:latest"))),
        llm_provider=str(llm_data.get("provider", extraction_data.get("llm_provider", "fixture"))),
        llm_model=str(llm_data.get("model", extraction_data.get("llm_model", "gemma4:e4b"))),
        ollama_base_url=default_ollama_base_url,
        llm_base_url=str(llm_data.get("base_url", extraction_data.get("llm_base_url", default_ollama_base_url))),
        llm_api_key_env=str(llm_data.get("api_key_env", extraction_data.get("llm_api_key_env", ""))),
        llm_command=str(llm_data.get("command", extraction_data.get("llm_command", ""))),
        llm_timeout_seconds=int(llm_data.get("timeout_seconds", extraction_data.get("llm_timeout_seconds", 180))),
        vlm_base_url=str(vlm_data.get("base_url", extraction_data.get("vlm_base_url", default_ollama_base_url))),
        vlm_api_key_env=str(vlm_data.get("api_key_env", extraction_data.get("vlm_api_key_env", ""))),
        vlm_timeout_seconds=int(vlm_data.get("timeout_seconds", extraction_data.get("vlm_timeout_seconds", 180))),
        max_queue_batch=int(extraction_data.get("max_queue_batch", 10)),
    )

    promotion_data = data.get("promotion", {})
    promotion = PromotionConfig(
        enabled=bool(promotion_data.get("enabled", True)),
        max_components_per_candidate=int(promotion_data.get("max_components_per_candidate", 50)),
        max_candidates_per_run=int(promotion_data.get("max_candidates_per_run", 20)),
        min_confidence_for_direct_write=str(promotion_data.get("min_confidence_for_direct_write", "medium")),
        auto_review_on_conflict=bool(promotion_data.get("auto_review_on_conflict", True)),
        retry_after_hours=int(promotion_data.get("retry_after_hours", 24)),
    )

    daemon_data = data.get("daemon", {})
    daemon_schedules = daemon_data.get("schedules", {})
    daemon = DaemonConfig(
        socket_path=_resolve_path(root, str(daemon_data.get("socket_path", "brain/data/mnemonic.sock"))),
        pid_path=_resolve_path(root, str(daemon_data.get("pid_path", "brain/data/mnemonic.pid"))),
        log_path=_resolve_path(root, str(daemon_data.get("log_path", "brain/logs/daemon.log"))),
        schedules={str(name): str(schedule) for name, schedule in daemon_schedules.items()},
        max_pending=int(daemon_data.get("max_pending", 200)),
        max_oldest_age_minutes=int(daemon_data.get("max_oldest_age_minutes", 1440)),
        stale_episode_threshold_hours=int(daemon_data.get("stale_episode_threshold_hours", 2)),
    )

    bridges: dict[str, BridgeConfig] = {}
    bridges_data = data.get("bridges")
    if bridges_data is None:
        bridges_data = data.get("agents", {})
    for bridge_name, bridge_data in bridges_data.items():
        bridges[bridge_name] = BridgeConfig(
            session_prefix=str(bridge_data.get("session_prefix", bridge_name)),
            can_promote=bool(bridge_data.get("can_promote", False)),
        )

    maintenance_data = data.get("maintenance", {})
    maintenance = MaintenanceConfig(
        lifecycle_verified_threshold=int(maintenance_data.get("lifecycle_verified_threshold", 2)),
        lifecycle_canonical_threshold=int(maintenance_data.get("lifecycle_canonical_threshold", 4)),
        lifecycle_drift_days=int(maintenance_data.get("lifecycle_drift_days", 30)),
        graph_rebuild_enabled=bool(maintenance_data.get("graph_rebuild_enabled", True)),
        lifecycle_audit_enabled=bool(maintenance_data.get("lifecycle_audit_enabled", True)),
        importance_scoring_enabled=bool(maintenance_data.get("importance_scoring_enabled", True)),
        semantic_dedup_threshold=float(maintenance_data.get("semantic_dedup_threshold", 0.92)),
        semantic_dedup_enabled=bool(maintenance_data.get("semantic_dedup_enabled", True)),
        embedding_neighbor_enabled=bool(maintenance_data.get("embedding_neighbor_enabled", True)),
        neighbor_similarity_threshold=float(maintenance_data.get("neighbor_similarity_threshold", 0.78)),
        neighbor_k=int(maintenance_data.get("neighbor_k", 10)),
        neighbor_scan_max_age_days=int(maintenance_data.get("neighbor_scan_max_age_days", 7)),
        cluster_validation_enabled=bool(maintenance_data.get("cluster_validation_enabled", True)),
        cluster_validation_max_size=int(maintenance_data.get("cluster_validation_max_size", 12)),
        cluster_validation_min_cluster_size=int(maintenance_data.get("cluster_validation_min_cluster_size", 2)),
        pattern_discovery_enabled=bool(maintenance_data.get("pattern_discovery_enabled", True)),
        requeue_enabled=bool(maintenance_data.get("requeue_enabled", True)),
        requeue_min_age_days=int(maintenance_data.get("requeue_min_age_days", 30)),
        requeue_context_enabled=bool(maintenance_data.get("requeue_context_enabled", True)),
        requeue_context_top_k=int(maintenance_data.get("requeue_context_top_k", 5)),
        requeue_max_per_run=int(maintenance_data.get("requeue_max_per_run", 20)),
    )

    return AppConfig(
        paths=paths,
        retrieval=retrieval,
        ingest=ingest,
        embeddings=embeddings,
        graph=graph,
        trust=trust,
        extraction=extraction,
        promotion=promotion,
        maintenance=maintenance,
        daemon=daemon,
        bridges=bridges,
        config_path=resolved_config,
    )
