from __future__ import annotations

from dataclasses import asdict, dataclass, field


@dataclass(slots=True)
class GateResult:
    ok: bool
    errors: list[str]

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(slots=True)
class PromotionDecision:
    decision: str  # 'new' | 'confirm' | 'update' | 'review' | 'rejected'
    target_note_id: str | None
    reason: str
    conflict_note_ids: list[str] = field(default_factory=list)


@dataclass(slots=True)
class PromotionRunResult:
    promoted: int = 0
    review_routed: int = 0
    rejected: int = 0
    requeued: int = 0
    skipped: int = 0
    relations_applied: int = 0
    errors: list[str] = field(default_factory=list)

    def to_stats(self) -> dict[str, int]:
        return {
            "promoted": self.promoted,
            "review_routed": self.review_routed,
            "rejected": self.rejected,
            "requeued": self.requeued,
            "skipped": self.skipped,
            "relations_applied": self.relations_applied,
            "errors": len(self.errors),
        }
