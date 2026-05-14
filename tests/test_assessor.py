from __future__ import annotations

from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from second_brain.assessor import assess_hits, assess_packet
from second_brain.models import MemoryPacket, RetrieveHit, RuntimeMemory


def _memory(
    note_id: str,
    *,
    note_type: str = "procedure",
    summary: str = "Run the current procedure with verified steps.",
    freshness: str | None = "fresh",
    role: str | None = "current_reusable_procedure",
    chain_id: str | None = "trace/test",
) -> RuntimeMemory:
    return RuntimeMemory(
        id=note_id,
        title=note_id.rsplit("/", 1)[-1].replace("-", " ").title(),
        type=note_type,
        summary=summary,
        body_path=f"/tmp/{note_id}.md",
        chain_id=chain_id,
        freshness_state=freshness,
        lifecycle_state="active",
        channels=["test"],
        final_score=1.0,
        assembly_role=role,
        assembly_order=60,
    )


def _hit(
    note_id: str,
    *,
    note_type: str = "procedure",
    summary: str = "Run the current procedure with verified steps.",
    freshness: str | None = "fresh",
) -> RetrieveHit:
    return RetrieveHit(
        id=note_id,
        title=note_id.rsplit("/", 1)[-1].replace("-", " ").title(),
        type=note_type,
        status="active",
        chain_id="trace/test",
        freshness_state=freshness,
        lifecycle_state="active",
        body_path=f"/tmp/{note_id}.md",
        summary=summary,
        channels=["test"],
        raw_score=1.0,
        final_score=1.0,
        excerpt=summary,
    )


def _path_item(memory: RuntimeMemory) -> dict[str, object]:
    return {
        "id": memory.id,
        "title": memory.title,
        "type": memory.type,
        "slot": "primary_memories",
        "assembly_role": memory.assembly_role,
        "assembly_order": memory.assembly_order,
        "rank_order": memory.rank_order,
        "final_score": memory.final_score,
        "chain_id": memory.chain_id,
        "relation_types": list(memory.relation_types),
    }


class AssessorTest(unittest.TestCase):
    def test_hit_assessment_applies_quality_gate_to_all_primary_hits(self) -> None:
        good = _hit("procedure/good")
        stub = _hit("procedure/stub", summary="")

        assessment = assess_hits([good, stub], "how do we deploy", temporality_override="current")

        self.assertEqual(assessment.state, "partial")
        self.assertEqual(assessment.recommendation, "reuse_with_verification")
        self.assertIn("procedure/stub", assessment.partial_ids)

    def test_historical_hits_reuse_with_verification(self) -> None:
        assessment = assess_hits([_hit("procedure/old", freshness="stale")], "previous rollout", temporality_override="historical")

        self.assertEqual(assessment.state, "sufficient_historical")
        self.assertEqual(assessment.recommendation, "reuse_with_verification")

    def test_packet_complete_current_path_direct_reuse(self) -> None:
        incident = _memory("incident/failure", note_type="incident", role="failed_attempt")
        procedure = _memory("procedure/recovery", role="current_reusable_procedure")
        packet = MemoryPacket(primary_memories=[procedure], same_episode_context=[incident])
        packet.functional_path = [_path_item(incident), _path_item(procedure)]
        packet.assessment_reservoir = [
            {
                "branch_id": "branch/1",
                "item_ids": [incident.id, procedure.id],
                "terminal_state": "success",
                "path_confidence": "explicit",
                "default_emission_candidate": True,
                "defer_reason": None,
                "missing_roles": [],
            }
        ]
        packet.assembly_notes = {
            "assessment_reservoir_branch_count": 1,
            "default_branch_id": "branch/1",
            "missing_functional_roles": [],
        }

        directive = assess_packet(packet, query="recover service", task_kind="debugging", temporality="current", query_type="how_to")

        self.assertEqual(directive.retrieval_state, "sufficient_current")
        self.assertEqual(directive.completion_state, "complete_path")
        self.assertEqual(directive.reuse_mode, "direct_reuse")
        self.assertEqual(directive.emission_profile, "concise_success_path")
        self.assertEqual(directive.selected_branch_ids, ["branch/1"])

    def test_packet_overabundant_defers_extra_branch(self) -> None:
        procedure = _memory("procedure/current")
        packet = MemoryPacket(primary_memories=[procedure])
        packet.functional_path = [_path_item(procedure)]
        packet.assessment_reservoir = [
            {"branch_id": "branch/1", "item_ids": [procedure.id], "terminal_state": "success", "default_emission_candidate": True},
            {"branch_id": "branch/2", "item_ids": ["incident/old"], "terminal_state": "failure", "defer_reason": "overabundant"},
        ]
        packet.assembly_notes = {
            "assessment_reservoir_branch_count": 2,
            "default_branch_id": "branch/1",
            "deferred_branch_ids": ["branch/2"],
            "missing_functional_roles": [],
        }

        directive = assess_packet(packet, query="deploy service", task_kind="coding", temporality="current", query_type="how_to")

        self.assertEqual(directive.completion_state, "overabundant")
        self.assertEqual(directive.selected_branch_ids, ["branch/1"])
        self.assertEqual(directive.deferred_branch_ids, ["branch/2"])
        self.assertEqual(directive.followup_query_policy, "on_first_failure")

    def test_packet_missing_required_role_requests_focused_requery(self) -> None:
        incident = _memory("incident/failure", note_type="incident", role="failed_attempt")
        packet = MemoryPacket(primary_memories=[incident])
        packet.functional_path = [_path_item(incident)]
        packet.assembly_notes = {
            "missing_functional_roles": ["success_resolution"],
            "assessment_reservoir_branch_count": 0,
        }

        directive = assess_packet(packet, query="debug service failure", task_kind="debugging", temporality="current", query_type="how_to")

        self.assertEqual(directive.completion_state, "partial_chain")
        self.assertEqual(directive.fresh_pipeline_threshold, "missing_required_role")
        self.assertEqual(directive.followup_query_policy, "immediate_focused_requery")

    def test_packet_contradiction_blocks_use(self) -> None:
        procedure = _memory("procedure/current")
        packet = MemoryPacket(
            primary_memories=[procedure],
            contradictions=[{"item_ids": [procedure.id], "source": "test", "severity": "high"}],
        )
        packet.functional_path = [_path_item(procedure)]

        directive = assess_packet(packet, query="deploy service", task_kind="coding", temporality="current", query_type="how_to")

        self.assertEqual(directive.retrieval_state, "insufficient_contested")
        self.assertEqual(directive.fresh_pipeline_threshold, "contradiction_blocks_use")
        self.assertEqual(directive.recommendation, "research_fresh")

    def test_packet_archive_only_actionable_threshold(self) -> None:
        archive = _memory("archive/session-1#chunk-0", note_type="archive_chunk", role="raw_evidence", chain_id=None)
        packet = MemoryPacket(archive_context=[archive])
        packet.assembly_notes = {"archive_only": True}

        directive = assess_packet(packet, query="fix deploy failure", task_kind="debugging", temporality="current", query_type="how_to")

        self.assertEqual(directive.retrieval_state, "partial")
        self.assertEqual(directive.fresh_pipeline_threshold, "archive_only_actionable")
        self.assertEqual(directive.emission_profile, "warning_only")


if __name__ == "__main__":
    unittest.main()
