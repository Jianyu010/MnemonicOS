from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import json
from pathlib import Path
import re
import sqlite3
from typing import Iterable


DUPLICATE_STATES = {
    "unique",
    "exact_duplicate",
    "canonical_duplicate",
    "possible_duplicate",
    "overlap_related",
}
IDENTITY_FINGERPRINT_VERSION = "text_norm_v1"
SIMHASH_BITS = 64
SIMHASH_BANDS = 4
SIMHASH_BAND_BITS = 16
SHINGLE_SIZE = 5

_PUNCT_RE = re.compile(r"[^\w\s]", re.UNICODE)
_SPACE_RE = re.compile(r"\s+")


@dataclass(slots=True)
class IdentityResult:
    duplicate_state: str
    canonical_archive_id: str | None = None
    identity_fingerprint: str | None = None
    identity_fingerprint_version: str | None = None
    duplicate_confidence: float | None = None
    lsh_candidate_count: int = 0
    overlap_summary: dict[str, object] | None = None


def normalize_text(text: str) -> str:
    lowered = _PUNCT_RE.sub(" ", text.casefold())
    return _SPACE_RE.sub(" ", lowered).strip()


def shingle_tokens(text: str, *, size: int = SHINGLE_SIZE) -> set[str]:
    tokens = normalize_text(text).split()
    if not tokens:
        return set()
    if len(tokens) < size:
        return {" ".join(tokens)}
    return {" ".join(tokens[index : index + size]) for index in range(0, len(tokens) - size + 1)}


def identity_fingerprint(source_format: str, usable_raw_text: str) -> str:
    normalized = normalize_text(usable_raw_text)[:1000]
    return sha256(f"{source_format}|{normalized}".encode("utf-8")).hexdigest()


def simhash64(shingles: Iterable[str]) -> int:
    weights = [0] * SIMHASH_BITS
    for shingle in shingles:
        digest = int.from_bytes(sha256(shingle.encode("utf-8")).digest()[:8], "big")
        for bit in range(SIMHASH_BITS):
            if digest & (1 << bit):
                weights[bit] += 1
            else:
                weights[bit] -= 1
    value = 0
    for bit, weight in enumerate(weights):
        if weight >= 0:
            value |= 1 << bit
    return value


def simhash_buckets(value: int) -> list[tuple[str, str]]:
    buckets: list[tuple[str, str]] = []
    mask = (1 << SIMHASH_BAND_BITS) - 1
    for band in range(SIMHASH_BANDS):
        bucket_value = (value >> (band * SIMHASH_BAND_BITS)) & mask
        buckets.append((f"simhash_band_{band}", f"{bucket_value:04x}"))
    return buckets


def jaccard(left: set[str], right: set[str]) -> float:
    if not left and not right:
        return 1.0
    if not left or not right:
        return 0.0
    return len(left & right) / len(left | right)


def prefix_suffix_overlap_ratio(left: str, right: str) -> tuple[float, bool]:
    left_tokens = normalize_text(left).split()
    right_tokens = normalize_text(right).split()
    if not left_tokens or not right_tokens:
        return 0.0, False
    prefix = 0
    for left_token, right_token in zip(left_tokens, right_tokens):
        if left_token != right_token:
            break
        prefix += 1
    suffix = 0
    for left_token, right_token in zip(reversed(left_tokens), reversed(right_tokens)):
        if left_token != right_token:
            break
        suffix += 1
    overlap = min(max(prefix, suffix), min(len(left_tokens), len(right_tokens)))
    ratio = overlap / max(1, min(len(left_tokens), len(right_tokens)))
    has_unique_delta = len(left_tokens) != len(right_tokens) and ratio >= 0.6
    return ratio, has_unique_delta


def find_exact_duplicate(connection: sqlite3.Connection, *, raw_sha256: str, byte_size: int) -> sqlite3.Row | None:
    return connection.execute(
        """
        SELECT id, artifact_path, path, source_format, original_filename,
               connector_name, connector_version, ingest_status,
               page_count, slide_count, figure_count, table_count,
               needs_visual_pass, needs_manual_projection, image_only_pdf
        FROM archive
        WHERE raw_sha256 = ? AND byte_size = ? AND id LIKE 'artifact/%'
        ORDER BY ingested_at, id
        LIMIT 1
        """,
        (raw_sha256, byte_size),
    ).fetchone()


def classify_post_parse_identity(
    connection: sqlite3.Connection,
    *,
    archive_id: str,
    source_format: str,
    usable_raw_text: str,
) -> IdentityResult:
    fingerprint = identity_fingerprint(source_format, usable_raw_text)
    exact_fp = connection.execute(
        """
        SELECT id
        FROM archive
        WHERE identity_fingerprint = ?
          AND identity_fingerprint_version = ?
          AND id <> ?
          AND duplicate_state IN ('unique', 'possible_duplicate', 'overlap_related')
        ORDER BY ingested_at, id
        LIMIT 1
        """,
        (fingerprint, IDENTITY_FINGERPRINT_VERSION, archive_id),
    ).fetchone()
    if exact_fp is not None:
        return IdentityResult(
            duplicate_state="canonical_duplicate",
            canonical_archive_id=str(exact_fp["id"]),
            identity_fingerprint=fingerprint,
            identity_fingerprint_version=IDENTITY_FINGERPRINT_VERSION,
            duplicate_confidence=1.0,
        )

    shingles = shingle_tokens(usable_raw_text)
    buckets = simhash_buckets(simhash64(shingles)) if shingles else []
    candidates: dict[str, sqlite3.Row] = {}
    for bucket_kind, bucket_value in buckets:
        rows = connection.execute(
            """
            SELECT a.id, a.path
            FROM artifact_identity_buckets b
            JOIN archive a ON a.id = b.archive_id
            WHERE b.bucket_kind = ? AND b.bucket_value = ? AND a.id <> ?
            """,
            (bucket_kind, bucket_value, archive_id),
        ).fetchall()
        for row in rows:
            candidates[str(row["id"])] = row

    best_id: str | None = None
    best_score = 0.0
    best_overlap_related = False
    for candidate_id, candidate in candidates.items():
        candidate_text = _read_candidate_text(connection, candidate)
        candidate_shingles = shingle_tokens(candidate_text)
        score = jaccard(shingles, candidate_shingles)
        overlap_ratio, overlap_related = prefix_suffix_overlap_ratio(usable_raw_text, candidate_text)
        if overlap_related and overlap_ratio > score:
            score = overlap_ratio
            best_overlap_related = True
        if score > best_score:
            best_score = score
            best_id = candidate_id

    if best_id and best_overlap_related and best_score >= 0.60:
        return IdentityResult(
            duplicate_state="overlap_related",
            canonical_archive_id=best_id,
            identity_fingerprint=fingerprint,
            identity_fingerprint_version=IDENTITY_FINGERPRINT_VERSION,
            duplicate_confidence=best_score,
            lsh_candidate_count=len(candidates),
            overlap_summary={"ratio": round(best_score, 4), "candidate_count": len(candidates)},
        )
    if best_id and best_score >= 0.95:
        return IdentityResult(
            duplicate_state="canonical_duplicate",
            canonical_archive_id=best_id,
            identity_fingerprint=fingerprint,
            identity_fingerprint_version=IDENTITY_FINGERPRINT_VERSION,
            duplicate_confidence=best_score,
            lsh_candidate_count=len(candidates),
        )
    if best_id and best_score >= 0.60:
        return IdentityResult(
            duplicate_state="possible_duplicate",
            canonical_archive_id=best_id,
            identity_fingerprint=fingerprint,
            identity_fingerprint_version=IDENTITY_FINGERPRINT_VERSION,
            duplicate_confidence=best_score,
            lsh_candidate_count=len(candidates),
        )
    return IdentityResult(
        duplicate_state="unique",
        identity_fingerprint=fingerprint,
        identity_fingerprint_version=IDENTITY_FINGERPRINT_VERSION,
        lsh_candidate_count=len(candidates),
    )


def write_identity_buckets(
    connection: sqlite3.Connection,
    *,
    archive_id: str,
    usable_raw_text: str,
    duplicate_state: str,
) -> None:
    connection.execute("DELETE FROM artifact_identity_buckets WHERE archive_id = ?", (archive_id,))
    if duplicate_state not in {"unique", "possible_duplicate", "overlap_related"}:
        return
    shingles = shingle_tokens(usable_raw_text)
    if not shingles:
        return
    for bucket_kind, bucket_value in simhash_buckets(simhash64(shingles)):
        connection.execute(
            """
            INSERT OR IGNORE INTO artifact_identity_buckets(archive_id, bucket_kind, bucket_value)
            VALUES (?, ?, ?)
            """,
            (archive_id, bucket_kind, bucket_value),
        )


def update_archive_identity(
    connection: sqlite3.Connection,
    *,
    archive_id: str,
    result: IdentityResult,
) -> None:
    connection.execute(
        """
        UPDATE archive
        SET duplicate_state = ?,
            canonical_archive_id = ?,
            identity_fingerprint = ?,
            identity_fingerprint_version = ?,
            duplicate_confidence = ?,
            overlap_summary_json = ?
        WHERE id = ?
        """,
        (
            result.duplicate_state,
            result.canonical_archive_id,
            result.identity_fingerprint,
            result.identity_fingerprint_version,
            result.duplicate_confidence,
            json.dumps(result.overlap_summary or {}),
            archive_id,
        ),
    )


def _read_candidate_text(connection: sqlite3.Connection, candidate: sqlite3.Row) -> str:
    archive_id = str(candidate["id"])
    row = connection.execute(
        "SELECT artifact_path, path FROM archive WHERE id = ?",
        (archive_id,),
    ).fetchone()
    if row is None:
        return ""
    from .connectors import ConnectorConfig, connector_for_path

    path = row["artifact_path"] or row["path"]
    try:
        raw_path = Path(path)
        connector = connector_for_path(raw_path)
        return connector.extract(raw_path, config=ConnectorConfig(figures_enabled=False)).usable_raw_text
    except Exception:
        return ""
