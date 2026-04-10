from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
import tomllib


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


@dataclass(slots=True)
class EmbeddingsConfig:
    enabled: bool = True
    provider: str = "hash"
    model: str = "hash-256-v1"
    dimensions: int = 256
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
class AgentConfig:
    session_prefix: str
    can_promote: bool


@dataclass(slots=True)
class AppConfig:
    paths: PathsConfig
    retrieval: RetrievalConfig = field(default_factory=RetrievalConfig)
    ingest: IngestConfig = field(default_factory=IngestConfig)
    embeddings: EmbeddingsConfig = field(default_factory=EmbeddingsConfig)
    graph: GraphConfig = field(default_factory=GraphConfig)
    trust: TrustConfig = field(default_factory=TrustConfig)
    agents: dict[str, AgentConfig] = field(default_factory=dict)
    config_path: Path | None = None


def _resolve_path(base_dir: Path, raw_value: str) -> Path:
    path = Path(raw_value)
    if path.is_absolute():
        return path
    return (base_dir / path).resolve()


def load_config(config_path: str | Path | None = None, workspace_root: str | Path | None = None) -> AppConfig:
    root = Path(workspace_root or Path.cwd()).resolve()
    resolved_config = Path(config_path or root / "config" / "memory.example.toml").resolve()
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
    ingest = IngestConfig(
        chunk_tokens=int(ingest_data.get("chunk_tokens", 400)),
        chunk_overlap_tokens=int(ingest_data.get("chunk_overlap_tokens", 50)),
        default_archive_type=str(ingest_data.get("default_archive_type", "session")),
        explicit_markers_only=bool(ingest_data.get("explicit_markers_only", False)),
    )

    embeddings_data = data.get("embeddings", {})
    embeddings = EmbeddingsConfig(
        enabled=bool(embeddings_data.get("enabled", True)),
        provider=str(embeddings_data.get("provider", "hash")),
        model=str(embeddings_data.get("model", "hash-256-v1")),
        dimensions=int(embeddings_data.get("dimensions", 256)),
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

    agents: dict[str, AgentConfig] = {}
    for agent_name, agent_data in data.get("agents", {}).items():
        agents[agent_name] = AgentConfig(
            session_prefix=str(agent_data.get("session_prefix", agent_name)),
            can_promote=bool(agent_data.get("can_promote", False)),
        )

    return AppConfig(
        paths=paths,
        retrieval=retrieval,
        ingest=ingest,
        embeddings=embeddings,
        graph=graph,
        trust=trust,
        agents=agents,
        config_path=resolved_config,
    )
