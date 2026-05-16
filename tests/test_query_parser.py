from __future__ import annotations

from dataclasses import FrozenInstanceError
from types import SimpleNamespace
import sqlite3
import unittest

from second_brain.models import ResolveTaskContextRequest
from second_brain.query_parser import (
    BACK_FEED_HOOKS,
    ParsedQuery,
    QueryHints,
    QueryScope,
    TYPE_PRIORS,
    classify_persona,
    classify_query,
    detect_caller,
    expand_terms,
    extract_anchor_terms,
    infer_temporality,
    parse_query,
    tokenize,
)


class QueryParserPhase1Test(unittest.TestCase):
    def test_tokenize_filters_stopwords_and_deduplicates(self) -> None:
        self.assertEqual(tokenize("how do we deploy the auth service"), ["deploy", "auth", "service"])
        self.assertEqual(tokenize("who owns it"), ["owns"])
        self.assertEqual(tokenize("deploy deploy deploy"), ["deploy"])

    def test_tokenize_returns_wildcard_for_empty_term_set(self) -> None:
        self.assertEqual(tokenize(""), ["*"])
        self.assertEqual(tokenize("a an and the"), ["*"])
        self.assertEqual(tokenize("us"), ["*"])

    def test_tokenize_preserves_unicode_terms(self) -> None:
        self.assertEqual(tokenize("Ünïcödé tëst"), ["ünïcödé", "tëst"])

    def test_tokenize_supports_cjk_terms(self) -> None:
        self.assertEqual(tokenize("中文 支持 检索"), ["中", "文", "中文", "支", "持", "支持", "检", "索", "检索"])
        tokens = tokenize("embedding layer 支持中文吗")
        self.assertIn("embedding", tokens)
        self.assertIn("layer", tokens)
        self.assertIn("支持", tokens)
        self.assertIn("中文", tokens)

    def test_parsed_query_is_frozen(self) -> None:
        parsed = ParsedQuery(
            raw="x",
            tokens=["x"],
            caller_type="human",
            persona="knowledge",
            back_feed_hook="none",
            query_type=None,
            classifier_confidence=0.0,
            temporality="unknown",
            anchor_terms=[],
            expanded_terms=[],
            scope=None,
            token_budget=None,
            top_k=5,
        )
        with self.assertRaises(FrozenInstanceError):
            parsed.raw = "y"  # type: ignore[misc]

    def test_query_hints_defaults(self) -> None:
        hints = QueryHints()
        self.assertIsNone(hints.query_type_hint)
        self.assertIsNone(hints.temporality_override)
        self.assertIsNone(hints.token_budget)
        self.assertIsNone(hints.scope)
        self.assertIsNone(hints.top_k)
        self.assertIsNone(hints.caller_type_hint)

    def test_query_scope_defaults(self) -> None:
        scope = QueryScope()
        self.assertIsNone(scope.workspace_path)
        self.assertIsNone(scope.repo)
        self.assertEqual(scope.tags, [])

    def test_resolve_task_context_accepts_caller_type(self) -> None:
        request = ResolveTaskContextRequest(
            bridge_id="local_cli",
            session_id="session-1",
            task_id="task-1",
            task_text="deploy auth",
            caller_type="agent",
        )
        self.assertEqual(request.caller_type, "agent")

    def test_detect_caller_defaults_cli_to_human(self) -> None:
        self.assertEqual(detect_caller(None, None), "human")
        self.assertEqual(detect_caller("", None), "human")

    def test_detect_caller_recognizes_agent_bridges(self) -> None:
        self.assertEqual(detect_caller("claude", None), "agent")
        self.assertEqual(detect_caller("CLAUDE", None), "agent")
        self.assertEqual(detect_caller("codex", None), "agent")
        self.assertEqual(detect_caller("bridge/my-bot", None), "agent")
        self.assertEqual(detect_caller("api/v1", None), "agent")

    def test_detect_caller_defaults_unknown_bridge_to_human(self) -> None:
        self.assertEqual(detect_caller("unknown-tool", None), "human")

    def test_detect_caller_hint_overrides_detection(self) -> None:
        self.assertEqual(detect_caller("claude", "human"), "human")
        self.assertEqual(detect_caller(None, "agent"), "agent")
        self.assertEqual(detect_caller("unknown", "agent"), "agent")

    def test_classify_persona_humans_are_knowledge(self) -> None:
        self.assertEqual(classify_persona("human", "how_to", set()), "knowledge")
        self.assertEqual(classify_persona("human", None, set()), "knowledge")
        self.assertEqual(classify_persona("human", "decision", {"why", "choose"}), "knowledge")

    def test_classify_persona_agent_how_to_is_skill(self) -> None:
        self.assertEqual(classify_persona("agent", "how_to", set()), "skill")
        self.assertEqual(classify_persona("agent", "how_to", {"research"}), "skill")

    def test_classify_persona_agent_defaults_to_rag(self) -> None:
        self.assertEqual(classify_persona("agent", "broad_synthesis", set()), "rag")
        self.assertEqual(classify_persona("agent", "decision", set()), "rag")
        self.assertEqual(classify_persona("agent", "preference_identity", set()), "rag")
        self.assertEqual(classify_persona("agent", None, {"research"}), "rag")
        self.assertEqual(classify_persona("agent", None, set()), "rag")

    def test_back_feed_hooks(self) -> None:
        self.assertEqual(BACK_FEED_HOOKS["knowledge"], "none")
        self.assertEqual(BACK_FEED_HOOKS["skill"], "skill_outcome")
        self.assertEqual(BACK_FEED_HOOKS["rag"], "rag_synthesis")

    def test_classify_query_keyword_matches(self) -> None:
        query_type, confidence = classify_query(["deploy", "auth", "service"], None)
        self.assertEqual(query_type, "how_to")
        self.assertGreaterEqual(confidence, 0.65)

        query_type, confidence = classify_query(["why", "postgres"], None)
        self.assertEqual(query_type, "decision")
        self.assertGreaterEqual(confidence, 0.65)

        query_type, confidence = classify_query(["who", "owns", "team"], None)
        self.assertEqual(query_type, "who_where_relation")
        self.assertGreaterEqual(confidence, 0.65)

        query_type, confidence = classify_query(["state", "status", "overview"], None)
        self.assertEqual(query_type, "broad_synthesis")
        self.assertGreaterEqual(confidence, 0.85)

    def test_classify_query_hint_handling(self) -> None:
        self.assertEqual(classify_query([], "decision"), ("decision", 1.0))
        self.assertEqual(classify_query(["deploy"], "decision"), ("decision", 1.0))

        query_type, confidence = classify_query(["deploy"], "invalid_type")
        self.assertEqual(query_type, "how_to")
        self.assertGreaterEqual(confidence, 0.65)

    def test_classify_query_empty_or_unknown(self) -> None:
        self.assertEqual(classify_query(["hello", "world"], None), (None, 0.0))
        self.assertEqual(classify_query([], None), (None, 0.0))

    def test_type_priors_exported(self) -> None:
        self.assertEqual(TYPE_PRIORS["how_to"]["procedure"], 0.9)

    def test_infer_temporality_from_indicators(self) -> None:
        self.assertEqual(infer_temporality(["historical", "deploy"], None), "historical")
        self.assertEqual(infer_temporality(["current", "state"], None), "current")
        self.assertEqual(infer_temporality(["before", "now"], None), "mixed")
        self.assertEqual(infer_temporality(["was", "now"], None), "mixed")

    def test_infer_temporality_defaults_to_unknown(self) -> None:
        self.assertEqual(infer_temporality(["deploy", "auth"], None), "unknown")
        self.assertEqual(infer_temporality(["*"], None), "unknown")
        self.assertEqual(infer_temporality([], None), "unknown")

    def test_infer_temporality_override(self) -> None:
        self.assertEqual(infer_temporality(["anything"], "historical"), "historical")
        self.assertEqual(infer_temporality(["anything"], "mixed"), "mixed")
        self.assertEqual(infer_temporality(["anything"], "invalid"), "unknown")

    def test_extract_anchor_terms_detects_note_id_prefixes(self) -> None:
        connection = _anchor_connection()
        try:
            self.assertEqual(extract_anchor_terms(["procedure/deploy-auth"], connection), ["procedure/deploy-auth"])
            self.assertEqual(extract_anchor_terms(["concept/caching"], connection), ["concept/caching"])
            self.assertEqual(extract_anchor_terms(["procedure"], connection), [])
        finally:
            connection.close()

    def test_extract_anchor_terms_reads_alias_table(self) -> None:
        connection = _anchor_connection()
        try:
            alias_titles: dict[str, str] = {}
            anchors = extract_anchor_terms(["foo", "deploy-auth"], connection, alias_titles)
            self.assertEqual(anchors, ["deploy-auth"])
            self.assertEqual(alias_titles, {"deploy-auth": "Deploy Auth Service"})
        finally:
            connection.close()

    def test_extract_anchor_terms_degraded_and_bounded(self) -> None:
        connection = _anchor_connection()
        try:
            self.assertEqual(extract_anchor_terms(["foo"], None), [])
            anchors = extract_anchor_terms(
                [
                    "procedure/one",
                    "decision/two",
                    "concept/three",
                    "incident/four",
                    "person/five",
                    "project/six",
                ],
                connection,
            )
            self.assertEqual(len(anchors), 5)
            self.assertEqual(anchors[-1], "person/five")
        finally:
            connection.close()

    def test_expand_terms_uses_synonyms_and_alias_titles(self) -> None:
        self.assertEqual(expand_terms(["deploy"], [], {}), ["release", "rollout", "publish"])
        self.assertLessEqual(len(expand_terms(["deploy", "incident", "decision"], [], {})), 3)
        self.assertEqual(expand_terms(["unknown_token"], [], {}), [])
        self.assertEqual(
            expand_terms(["xyz"], ["concept/caching"], {"concept/caching": "Caching Strategy"}),
            ["caching", "strategy"],
        )
        self.assertEqual(expand_terms(["deploy", "deploy"], [], {}), ["release", "rollout", "publish"])

    def test_parse_query_full_agent_skill_path(self) -> None:
        connection = _anchor_connection()
        try:
            parsed = parse_query(
                "how do we deploy auth",
                config=_parse_config(),
                connection=connection,
                bridge_id="claude",
            )
        finally:
            connection.close()
        self.assertEqual(parsed.query_type, "how_to")
        self.assertEqual(parsed.tokens, ["deploy", "auth"])
        self.assertEqual(parsed.temporality, "unknown")
        self.assertEqual(parsed.caller_type, "agent")
        self.assertEqual(parsed.persona, "skill")
        self.assertEqual(parsed.back_feed_hook, "skill_outcome")
        self.assertIn("release", parsed.expanded_terms)

    def test_parse_query_human_and_degraded_paths(self) -> None:
        parsed = parse_query("what is caching", config=_parse_config(), connection=None, bridge_id=None)
        self.assertEqual(parsed.caller_type, "human")
        self.assertEqual(parsed.persona, "knowledge")
        self.assertEqual(parsed.back_feed_hook, "none")
        self.assertEqual(parsed.anchor_terms, [])

        degraded = parse_query("deploy auth", config=_parse_config(), connection=None)
        self.assertEqual(degraded.anchor_terms, [])
        self.assertIsInstance(degraded.expanded_terms, list)

    def test_parse_query_rag_path_and_frozen_result(self) -> None:
        parsed = parse_query(
            "give me an overview of the project state",
            config=_parse_config(),
            connection=None,
            bridge_id="codex",
        )
        self.assertEqual(parsed.persona, "rag")
        self.assertEqual(parsed.back_feed_hook, "rag_synthesis")
        with self.assertRaises(FrozenInstanceError):
            parsed.raw = "modified"  # type: ignore[misc]


def _parse_config() -> object:
    return SimpleNamespace(retrieval=SimpleNamespace(top_k=5))


def _anchor_connection() -> sqlite3.Connection:
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    connection.execute("CREATE TABLE notes (id TEXT PRIMARY KEY, title TEXT NOT NULL)")
    connection.execute("CREATE TABLE aliases (alias TEXT NOT NULL, note_id TEXT NOT NULL)")
    connection.execute("INSERT INTO notes(id, title) VALUES (?, ?)", ("procedure/deploy-auth", "Deploy Auth Service"))
    connection.execute("INSERT INTO aliases(alias, note_id) VALUES (?, ?)", ("deploy-auth", "procedure/deploy-auth"))
    connection.execute("INSERT INTO aliases(alias, note_id) VALUES (?, ?)", ("auth", "procedure/deploy-auth"))
    return connection


if __name__ == "__main__":
    unittest.main()
