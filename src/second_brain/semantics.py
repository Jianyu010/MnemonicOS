from __future__ import annotations

import hashlib
import json
import math
import re
import sqlite3
from typing import Iterable


TOKEN_PATTERN = re.compile(r"[A-Za-z0-9][\w/-]*")

SYNONYM_MAP: dict[str, tuple[str, ...]] = {
    "deploy": ("release", "rollout", "ship", "production"),
    "release": ("deploy", "rollout", "ship", "production"),
    "rollout": ("deploy", "release", "promotion"),
    "why": ("reason", "rationale", "decision", "choice"),
    "decision": ("reason", "rationale", "choice", "picked"),
    "owner": ("maintainer", "responsible", "lead"),
    "incident": ("outage", "failure", "issue"),
    "outage": ("incident", "failure", "downtime"),
    "database": ("db", "postgres", "mysql", "sqlite", "relational"),
    "db": ("database", "postgres", "mysql", "sqlite"),
    "postgres": ("database", "db", "relational", "sql"),
    "pooling": ("connection", "pool", "pgbouncer"),
    "production": ("release", "deploy", "rollout"),
}


def _tokenize(text: str) -> list[str]:
    return [token.casefold() for token in TOKEN_PATTERN.findall(text)]


def _char_ngrams(token: str, size: int = 3) -> Iterable[str]:
    if len(token) < size:
        return ()
    return tuple(token[index : index + size] for index in range(len(token) - size + 1))


def semantic_features(text: str) -> list[str]:
    tokens = _tokenize(text)
    features: list[str] = []
    for token in tokens:
        features.append(f"tok:{token}")
        for alias in SYNONYM_MAP.get(token, ()):
            features.append(f"syn:{alias}")
        if len(token) >= 5:
            for ngram in _char_ngrams(token):
                features.append(f"tri:{ngram}")
    return features


def encode_text(text: str, *, dimensions: int) -> list[float]:
    vector = [0.0] * dimensions
    features = semantic_features(text)
    if not features:
        return vector

    for feature in features:
        digest = hashlib.sha256(feature.encode("utf-8")).digest()
        index = int.from_bytes(digest[:4], "big") % dimensions
        sign = 1.0 if digest[4] % 2 == 0 else -1.0
        vector[index] += sign

    norm = math.sqrt(sum(value * value for value in vector))
    if norm == 0.0:
        return vector
    return [value / norm for value in vector]


def cosine_similarity(left: list[float], right: list[float]) -> float:
    if not left or not right or len(left) != len(right):
        return 0.0
    return sum(a * b for a, b in zip(left, right))


def vector_to_json(vector: list[float]) -> str:
    return json.dumps(vector, separators=(",", ":"))


def vector_from_json(payload: str) -> list[float]:
    return [float(value) for value in json.loads(payload)]


def upsert_note_vector(
    connection: sqlite3.Connection,
    *,
    note_id: str,
    model: str,
    dimensions: int,
    vector: list[float],
    source_hash: str,
) -> None:
    connection.execute(
        """
        INSERT INTO note_vectors(note_id, model, dimensions, vector_json, source_hash, updated_at)
        VALUES (?, ?, ?, ?, ?, datetime('now'))
        ON CONFLICT(note_id) DO UPDATE SET
            model = excluded.model,
            dimensions = excluded.dimensions,
            vector_json = excluded.vector_json,
            source_hash = excluded.source_hash,
            updated_at = excluded.updated_at
        """,
        (note_id, model, dimensions, vector_to_json(vector), source_hash),
    )


def upsert_archive_chunk_vector(
    connection: sqlite3.Connection,
    *,
    chunk_id: int,
    model: str,
    dimensions: int,
    vector: list[float],
    source_hash: str,
) -> None:
    connection.execute(
        """
        INSERT INTO archive_chunk_vectors(chunk_id, model, dimensions, vector_json, source_hash, updated_at)
        VALUES (?, ?, ?, ?, ?, datetime('now'))
        ON CONFLICT(chunk_id) DO UPDATE SET
            model = excluded.model,
            dimensions = excluded.dimensions,
            vector_json = excluded.vector_json,
            source_hash = excluded.source_hash,
            updated_at = excluded.updated_at
        """,
        (chunk_id, model, dimensions, vector_to_json(vector), source_hash),
    )
