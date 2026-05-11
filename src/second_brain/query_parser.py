from __future__ import annotations

from dataclasses import dataclass, field
import logging
import re
import sqlite3
from typing import Literal, TypeAlias


logger = logging.getLogger(__name__)

CallerType: TypeAlias = Literal["human", "agent"]
PersonaType: TypeAlias = Literal["knowledge", "skill", "rag"]
TemporalityType: TypeAlias = Literal["current", "historical", "mixed", "unknown"]

AGENT_BRIDGE_IDS = frozenset({"claude", "codex"})
NOTE_ID_PREFIXES = frozenset(
    {
        "procedure",
        "decision",
        "concept",
        "incident",
        "person",
        "project",
        "repo",
        "source",
        "journal",
        "overview",
    }
)
RAG_SIGNALS = frozenset(
    {
        "know",
        "surface",
        "research",
        "background",
        "context",
        "explore",
        "everything",
        "about",
    }
)
BACK_FEED_HOOKS: dict[str, str] = {
    "knowledge": "none",
    "skill": "skill_outcome",
    "rag": "rag_synthesis",
}
QUERY_KEYWORD_SIGNATURES: dict[str, tuple[set[str], set[str]]] = {
    "preference_identity": (
        {"prefer", "preference", "style", "identity"},
        {"user", "settings", "personality"},
    ),
    "how_to": (
        {"how", "steps", "procedure", "run", "deploy", "release", "install", "setup"},
        {"do", "execute", "build"},
    ),
    "decision": (
        {"why", "reason", "rationale", "decide", "chose", "picked", "because", "should"},
        {"decision", "choose"},
    ),
    "who_where_relation": (
        {"who", "owner", "owns", "responsible", "maintainer", "team"},
        {"where", "contact"},
    ),
    "what_changed": (
        {"changed", "recent", "latest", "new", "update", "modified", "diff"},
        {"since", "added", "removed"},
    ),
    "broad_synthesis": (
        {"state", "status", "summary", "overview", "describe", "explain"},
        {"all", "total", "list"},
    ),
}
TYPE_PRIORS: dict[str, dict[str, float]] = {
    "preference_identity": {"person": 0.9, "concept": 0.5},
    "how_to": {"procedure": 0.9, "repo": 0.6, "project": 0.4},
    "decision": {"decision": 0.9, "incident": 0.6, "project": 0.4},
    "who_where_relation": {"person": 0.9, "project": 0.7, "repo": 0.5},
    "what_changed": {"incident": 0.8, "project": 0.7, "decision": 0.6},
    "broad_synthesis": {"overview": 0.9, "project": 0.7, "concept": 0.5},
}
SYNONYM_MAP: dict[str, list[str]] = {
    "deploy": ["release", "rollout", "publish"],
    "release": ["deploy", "publish"],
    "decision": ["reason", "rationale", "choice"],
    "owner": ["maintainer", "responsible", "contact"],
    "incident": ["outage", "failure", "bug"],
    "database": ["db", "storage", "postgres", "sqlite"],
    "procedure": ["steps", "guide", "how-to"],
    "concept": ["idea", "pattern", "definition"],
    "setup": ["install", "configure", "init"],
    "debug": ["investigate", "diagnose", "troubleshoot"],
}
HISTORICAL_INDICATORS = frozenset(
    {
        "before",
        "previously",
        "historical",
        "history",
        "used",
        "earlier",
        "then",
        "prior",
        "ago",
        "old",
        "former",
        "originally",
        "past",
        "when",
        "legacy",
        "was",
        "were",
        "had",
    }
)
CURRENT_INDICATORS = frozenset(
    {
        "current",
        "now",
        "today",
        "latest",
        "currently",
        "still",
        "active",
        "live",
        "present",
        "existing",
    }
)
TOKEN_PATTERN = re.compile(r"[\w/-]+", re.UNICODE)
STOPWORDS = frozenset(
    {
        "a",
        "an",
        "and",
        "are",
        "as",
        "at",
        "be",
        "by",
        "did",
        "do",
        "for",
        "from",
        "has",
        "have",
        "how",
        "i",
        "in",
        "is",
        "it",
        "its",
        "me",
        "my",
        "of",
        "on",
        "or",
        "our",
        "that",
        "the",
        "to",
        "us",
        "was",
        "we",
        "what",
        "which",
        "who",
        "with",
    }
)


@dataclass(frozen=True, slots=True)
class QueryScope:
    workspace_path: str | None = None
    repo: str | None = None
    tags: list[str] = field(default_factory=list)


@dataclass(slots=True)
class QueryHints:
    query_type_hint: str | None = None
    temporality_override: str | None = None
    token_budget: int | None = None
    scope: QueryScope | None = None
    top_k: int | None = None
    caller_type_hint: str | None = None


@dataclass(frozen=True, slots=True)
class ParsedQuery:
    raw: str
    tokens: list[str]
    caller_type: str
    persona: str
    back_feed_hook: str
    query_type: str | None
    classifier_confidence: float
    temporality: str
    anchor_terms: list[str]
    expanded_terms: list[str]
    scope: QueryScope | None
    token_budget: int | None
    top_k: int


def tokenize(query: str) -> list[str]:
    tokens: list[str] = []
    seen: set[str] = set()
    for raw_token in TOKEN_PATTERN.findall(query):
        lowered = raw_token.casefold()
        if len(lowered) < 3:
            continue
        if lowered in STOPWORDS:
            continue
        if lowered in seen:
            continue
        tokens.append(lowered)
        seen.add(lowered)
    return tokens if tokens else ["*"]


def detect_caller(bridge_id: str | None, hint: str | None) -> str:
    if hint in {"human", "agent"}:
        return hint
    if not bridge_id:
        return "human"
    lower_id = str(bridge_id).casefold()
    if lower_id in AGENT_BRIDGE_IDS:
        return "agent"
    if lower_id.startswith(("bridge/", "api/")):
        return "agent"
    return "human"


def classify_persona(caller_type: str, query_type: str | None, tokens: set[str]) -> str:
    if caller_type == "human":
        return "knowledge"
    if query_type == "how_to":
        return "skill"
    if query_type == "broad_synthesis" or tokens & RAG_SIGNALS:
        return "rag"
    return "rag"


def classify_query(tokens: list[str], hint: str | None) -> tuple[str | None, float]:
    if hint and hint in QUERY_KEYWORD_SIGNATURES:
        return hint, 1.0

    token_set = set(tokens)
    best_type: str | None = None
    best_score = 0.0
    best_primary_hits = 0
    best_secondary_hits = 0

    for query_type, (primary_kw, secondary_kw) in QUERY_KEYWORD_SIGNATURES.items():
        primary_hits = len(token_set & primary_kw)
        secondary_hits = len(token_set & secondary_kw)
        score = primary_hits * 1.0 + secondary_hits * 0.5
        if score > best_score:
            best_type = query_type
            best_score = score
            best_primary_hits = primary_hits
            best_secondary_hits = secondary_hits

    if best_type is None or best_score == 0.0:
        return None, 0.0

    confidence = min(1.0, 0.45 + 0.20 * best_primary_hits + 0.10 * best_secondary_hits)
    return best_type, confidence


def infer_temporality(tokens: list[str], override: str | None) -> str:
    valid_states = {"current", "historical", "mixed", "unknown"}
    if override in valid_states:
        return override

    token_set = set(tokens)
    historical = bool(token_set & HISTORICAL_INDICATORS)
    current = bool(token_set & CURRENT_INDICATORS)

    if historical and current:
        return "mixed"
    if historical:
        return "historical"
    if current:
        return "current"
    return "unknown"


def extract_anchor_terms(
    tokens: list[str],
    connection: sqlite3.Connection | None,
    alias_titles_out: dict[str, str] | None = None,
) -> list[str]:
    if connection is None:
        return []

    anchors: list[str] = []
    seen: set[str] = set()

    for token in tokens:
        if "/" not in token:
            continue
        prefix, _, _ = token.partition("/")
        if prefix in NOTE_ID_PREFIXES and token not in seen:
            anchors.append(token)
            seen.add(token)
            if len(anchors) >= 5:
                return anchors

    eligible = sorted(
        (token for token in tokens if len(token) >= 4 and token not in seen),
        key=len,
        reverse=True,
    )[:5]

    for token in eligible:
        if len(anchors) >= 5:
            break
        row = connection.execute(
            """
            SELECT a.alias, n.title
            FROM aliases a
            JOIN notes n ON n.id = a.note_id
            WHERE lower(a.alias) = lower(?)
            LIMIT 1
            """,
            (token,),
        ).fetchone()
        if row and token not in seen:
            anchors.append(token)
            seen.add(token)
            if alias_titles_out is not None:
                alias_titles_out[token] = str(row["title"])

    return anchors


def expand_terms(tokens: list[str], anchor_terms: list[str], alias_titles: dict[str, str]) -> list[str]:
    candidates: list[str] = []
    seen: set[str] = set(tokens)

    for token in tokens:
        for synonym in SYNONYM_MAP.get(token, []):
            if synonym not in seen:
                candidates.append(synonym)
                seen.add(synonym)
            if len(candidates) >= 3:
                return candidates[:3]

    for anchor in anchor_terms:
        title = alias_titles.get(anchor)
        if not title:
            continue
        for title_token in tokenize(title):
            if title_token == "*":
                continue
            if title_token not in seen:
                candidates.append(title_token)
                seen.add(title_token)
            if len(candidates) >= 3:
                return candidates[:3]

    return candidates[:3]


def parse_query(
    raw: str,
    hints: QueryHints | None = None,
    *,
    config: object,
    connection: sqlite3.Connection | None = None,
    bridge_id: str | None = None,
) -> ParsedQuery:
    hints = hints or QueryHints()

    tokens = tokenize(raw)
    query_type, classifier_confidence = classify_query(tokens, hints.query_type_hint)
    temporality = infer_temporality(tokens, hints.temporality_override)
    caller_type = detect_caller(bridge_id, hints.caller_type_hint)
    persona = classify_persona(caller_type, query_type, set(tokens))
    back_feed_hook = BACK_FEED_HOOKS[persona]

    alias_titles: dict[str, str] = {}
    try:
        anchor_terms = extract_anchor_terms(tokens, connection, alias_titles)
    except Exception:
        logger.warning("anchor extraction failed; continuing with empty anchors", exc_info=True)
        anchor_terms = []
        alias_titles = {}
    expanded_terms = expand_terms(tokens, anchor_terms, alias_titles)

    retrieval_config = getattr(config, "retrieval")
    top_k = hints.top_k if hints.top_k is not None else int(getattr(retrieval_config, "top_k"))

    return ParsedQuery(
        raw=raw,
        tokens=tokens,
        caller_type=caller_type,
        persona=persona,
        back_feed_hook=back_feed_hook,
        query_type=query_type,
        classifier_confidence=classifier_confidence,
        temporality=temporality,
        anchor_terms=anchor_terms,
        expanded_terms=expanded_terms,
        scope=hints.scope,
        token_budget=hints.token_budget,
        top_k=top_k,
    )
