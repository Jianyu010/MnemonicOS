from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from second_brain.assessor import assess_packet
from second_brain.emission import (
    render_composed_markdown,
    render_emission,
    render_single_markdown,
    retry_cutoff_reason,
    should_auto_retry,
)
from second_brain.models import MemoryPacket, RuntimeMemory


def _memory(
    note_id: str,
    *,
    note_type: str = "incident",
    role: str = "failed_attempt",
    chain_id: str | None = "trace/auth-rollout-recovery",
    branch_id: str | None = "branch/current-recovery",
    summary: str = "Auth rollout failed with TokenRefreshError.",
) -> RuntimeMemory:
    return RuntimeMemory(
        id=note_id,
        title=note_id.rsplit("/", 1)[-1].replace("-", " ").title(),
        type=note_type,
        summary=summary,
        body_path=f"/tmp/{note_id}.md",
        chain_id=chain_id,
        freshness_state="fresh",
        lifecycle_state="active",
        channels=["test"],
        final_score=1.0,
        assembly_role=role,
        assembly_order=10,
        branch_id=branch_id,
    )


def _result(packet: MemoryPacket, query: str = "debug TokenRefreshError auth rollout") -> SimpleNamespace:
    directive = assess_packet(packet, query=query, task_kind="debugging", temporality="current", query_type="how_to")
    return SimpleNamespace(
        protocol="mnemos-runtime",
        version="0.1",
        task_id="task-test",
        session_id="session-test",
        retrieval_state=directive.retrieval_state,
        recommendation=directive.recommendation,
        verification_action=directive.verification_action,
        assessment_directive=directive,
        memory_packet=packet,
        query_id=101,
        query_type="how_to",
        classifier_confidence=1.0,
        persona="knowledge",
        back_feed_hook="none",
        provenance_summary=[],
    )


class EmissionTest(unittest.TestCase):
    def test_followup_query_preserves_stable_anchors(self) -> None:
        incident = _memory("incident/auth-rollout-failure")
        packet = MemoryPacket(primary_memories=[incident])
        packet.functional_path = [{"id": incident.id, "assembly_role": "failed_attempt", "chain_id": incident.chain_id}]
        packet.assembly_notes = {"missing_functional_roles": ["success_resolution"]}

        emission = render_emission(
            _result(packet),
            original_task_text="debug TokenRefreshError auth rollout",
            task_kind="debugging",
            temporality="current",
            auto_retry_allowed=True,
        )

        followup = emission["followup_query"]
        self.assertEqual(followup["policy"], "immediate_focused_requery")
        self.assertIn("success_resolution", followup["missing_roles"])
        self.assertIn("incident/auth-rollout-failure", followup["anchors"]["note_ids"])
        self.assertEqual(followup["anchors"]["chain_id"], "trace/auth-rollout-recovery")
        self.assertIn("chain_id:trace/auth-rollout-recovery", followup["suggested_query"])
        self.assertTrue(should_auto_retry(emission))

    def test_retry_cutoff_no_new_ids_precedes_budget(self) -> None:
        incident = _memory("incident/auth-rollout-failure")
        packet = MemoryPacket(primary_memories=[incident])
        packet.assembly_notes = {"missing_functional_roles": ["success_resolution"]}
        first = render_emission(
            _result(packet),
            original_task_text="debug TokenRefreshError auth rollout",
            task_kind="debugging",
            temporality="current",
            auto_retry_allowed=True,
        )
        second = render_emission(
            _result(packet),
            original_task_text=first["followup_query"]["suggested_query"],
            task_kind="debugging",
            temporality="current",
            previous_ids=first["stable_ids"],
            retry_attempt=1,
        )

        cutoff = retry_cutoff_reason(
            first,
            second,
            previous_queries=["debug TokenRefreshError auth rollout", first["followup_query"]["suggested_query"]],
            max_focused_retries=1,
            retry_attempt=1,
        )

        self.assertEqual(cutoff, "no_new_ids")

    def test_composed_markdown_keeps_retry_trace(self) -> None:
        incident = _memory("incident/auth-rollout-failure")
        procedure = _memory(
            "procedure/recover-auth-rollout",
            note_type="procedure",
            role="current_reusable_procedure",
            summary="success_resolution fix path.",
        )
        first_packet = MemoryPacket(primary_memories=[incident])
        first_packet.assembly_notes = {"missing_functional_roles": ["success_resolution"]}
        second_packet = MemoryPacket(primary_memories=[incident, procedure])
        second_packet.functional_path = [
            {"id": incident.id, "assembly_role": "failed_attempt", "chain_id": incident.chain_id},
            {"id": procedure.id, "assembly_role": "current_reusable_procedure", "chain_id": procedure.chain_id},
        ]
        second_packet.assembly_notes = {"missing_functional_roles": []}

        first = render_emission(_result(first_packet), original_task_text="debug auth", task_kind="debugging", temporality="current")
        second = render_emission(_result(second_packet), original_task_text="debug auth success_resolution", task_kind="debugging", temporality="current", previous_ids=first["stable_ids"])
        md = render_composed_markdown(
            second,
            retrieval_passes=[
                {"pass_index": 1, "query_id": 101, "query_text": "debug auth", "emission": first, "triggered_followup_query": first["followup_query"]},
                {"pass_index": 2, "query_id": 102, "query_text": "debug auth success_resolution", "emission": second, "new_ids": second["new_ids"], "cutoff_reason": None},
            ],
            final_pass_index=2,
            auto_retry_summary={"attempted": True, "attempt_count": 1, "new_ids": second["new_ids"], "cutoff_reason": None},
        )

        self.assertIn("### Retry Trace", md)
        self.assertIn("Pass 1 query_id=101", md)
        self.assertIn("Pass 2 query_id=102", md)
        self.assertIn("procedure/recover-auth-rollout", md)

    def test_single_markdown_no_memory_explicit(self) -> None:
        packet = MemoryPacket()
        emission = render_emission(_result(packet, query="unknown"), original_task_text="unknown", task_kind="general", temporality="mixed")

        md = render_single_markdown(emission)

        self.assertIn("No relevant MnemonicOS notes found", md)


if __name__ == "__main__":
    unittest.main()
