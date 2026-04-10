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


@dataclass(slots=True)
class IngestConfig:
    chunk_tokens: int = 400
    chunk_overlap_tokens: int = 50
    default_archive_type: str = "session"


@dataclass(slots=True)
class AgentConfig:
    session_prefix: str
    can_promote: bool


@dataclass(slots=True)
class AppConfig:
    paths: PathsConfig
    retrieval: RetrievalConfig = field(default_factory=RetrievalConfig)
    ingest: IngestConfig = field(default_factory=IngestConfig)
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
    )

    ingest_data = data.get("ingest", {})
    ingest = IngestConfig(
        chunk_tokens=int(ingest_data.get("chunk_tokens", 400)),
        chunk_overlap_tokens=int(ingest_data.get("chunk_overlap_tokens", 50)),
        default_archive_type=str(ingest_data.get("default_archive_type", "session")),
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
        agents=agents,
        config_path=resolved_config,
    )
