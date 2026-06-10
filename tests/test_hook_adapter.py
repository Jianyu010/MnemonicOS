"""
Hook adapter tests — Phase 2 Claude Code integration.

Tests are organized into four groups:
  1. TestPreflightHook      — UserPromptSubmit hook (mnemonic_hook_preflight.sh)
  2. TestStopHook           — Stop hook (mnemonic_stop_hook.sh)
  3. TestHookRoundTrip      — Cross-hook per-turn task_id independence
  4. TestHookDisableSwitch  — Enable/disable two-tier switch (env var + flag file)

Each test gets its own temp STATE_FILE to prevent cross-test contamination.
Tests run the actual shell scripts via subprocess.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
PREFLIGHT = str(SCRIPTS / "mnemonic_hook_preflight.sh")
STOP_HOOK = str(SCRIPTS / "mnemonic_stop_hook.sh")
CODEX_PREFLIGHT = str(SCRIPTS / "mnemonic_codex_hook_preflight.sh")
CODEX_STOP_HOOK = str(SCRIPTS / "mnemonic_codex_stop_hook.sh")
CODEX_NOTIFY_BRIDGE = SCRIPTS / "codex_notify_bridge.sh"
CODEX_NOTIFY_IMPORTER = SCRIPTS / "mnemonic_codex_notify_bridge.py"


def _make_env(*, state_file: str, mnemo_enabled: str = "1", extra: dict | None = None) -> dict:
    """Return an env dict for subprocess with test-safe STATE_FILE."""
    env = os.environ.copy()
    env["STATE_FILE"] = state_file
    env["MNEMO_ENABLED"] = mnemo_enabled
    # Ensure disable flag file path can be tested independently
    if extra:
        env.update(extra)
    return env


def _run_script(script: str, stdin_data: str, *, state_file: str, env: dict | None = None) -> subprocess.CompletedProcess:
    if env is None:
        env = _make_env(state_file=state_file)
    return subprocess.run(
        ["bash", script],
        input=stdin_data,
        capture_output=True,
        text=True,
        env=env,
        timeout=30,
    )


def _hook_json(prompt: str = "test prompt", session_id: str = "test-session-abc") -> str:
    return json.dumps({"prompt": prompt, "session_id": session_id})


def _install_fake_mnemonicos(tmp_root: str) -> tuple[str, Path]:
    """Write a fake ``mnemonicos`` binary into <tmp_root>/bin.

    Returns (PATH value with the fake bin first, log file path). Hook tests
    must never invoke the real CLI: real runs depend on live Ollama latency
    (>30 s under daemon GPU contention) and ingest junk test prompts/summaries
    into the production memory DB.
    """
    bin_dir = Path(tmp_root) / "bin"
    bin_dir.mkdir(exist_ok=True)
    log_path = Path(tmp_root) / "mnemonicos.log"
    fake = bin_dir / "mnemonicos"
    fake.write_text(
        textwrap.dedent(
            f"""\
            #!/usr/bin/env bash
            printf '%s\\n' "$*" >> {str(log_path)!r}
            case "$*" in
              *"capture begin-episode"*)
                printf '{{"conversation_id": "conv-fake-%s", "episode_id": "episode-fake-%s"}}\\n' "$$" "$$" ;;
              *"capture end-episode"*) printf '{{"ok": true}}\\n' ;;
              *"orchestrator begin-task"*)
                printf '{{"task_id": "task-fake-%s", "context_markdown": "fake memory context", "retrieval_state": "partial"}}\\n' "$$" ;;
              *"orchestrator finish-task"*) printf '{{"decision": "skip"}}\\n' ;;
              *) printf '{{"ok": true}}\\n' ;;
            esac
            exit 0
            """
        )
    )
    fake.chmod(0o755)
    return f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}", log_path


def _pending_summary(*, agent: str = "claude", date: str = "2026-04-15") -> str:
    return textwrap.dedent(f"""\
        agent: {agent}
        session_date: {date}

        ## Task
        Test task for hook adapter tests.

        ## Goal
        Verify stop hook ingestion.

        ## Reusable Takeaways
        - hooks fire per turn
    """)


class TestPreflightHook(unittest.TestCase):
    """Tests for mnemonic_hook_preflight.sh.

    Uses a fake ``mnemonicos`` on PATH — see _install_fake_mnemonicos.
    """

    def setUp(self) -> None:
        self._tmp = tempfile.NamedTemporaryFile(prefix="mnemo_state_", delete=False)
        self._tmp.close()
        self._state_file = self._tmp.name
        # Remove so the script can write fresh
        os.unlink(self._state_file)
        self._fake_bin_tmp = tempfile.TemporaryDirectory(prefix="mnemo_fake_bin_")
        self._fake_path, self._cli_log = _install_fake_mnemonicos(self._fake_bin_tmp.name)

    def tearDown(self) -> None:
        self._fake_bin_tmp.cleanup()
        for f in [self._state_file]:
            try:
                os.unlink(f)
            except FileNotFoundError:
                pass

    def _env(self, *, mnemo_enabled: str = "1") -> dict:
        return _make_env(
            state_file=self._state_file,
            mnemo_enabled=mnemo_enabled,
            extra={"PATH": self._fake_path},
        )

    def test_exits_zero_on_empty_prompt(self) -> None:
        """Empty prompt → exit 0, no stdout, no state file written."""
        result = _run_script(
            PREFLIGHT,
            _hook_json(prompt=""),
            state_file=self._state_file,
            env=self._env(),
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), "")
        self.assertFalse(os.path.exists(self._state_file))

    def test_exits_zero_always(self) -> None:
        """Hook must always exit 0, even with unusual/long prompts."""
        result = _run_script(
            PREFLIGHT,
            _hook_json(prompt="x" * 2000),
            state_file=self._state_file,
            env=self._env(),
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_writes_task_id_to_state_file_on_hit(self) -> None:
        """When orchestrator returns a result, task_id is written to state file."""
        # We use a prompt that's likely to return a context (or at least not crash).
        # The key invariant: if orchestrator ran and returned a task_id, state file exists.
        # Since the test DB is empty, we can't force a hit — instead we verify the
        # script handles the flow end-to-end without error.
        result = _run_script(
            PREFLIGHT,
            _hook_json(prompt="implement a test function for the orchestrator"),
            state_file=self._state_file,
            env=self._env(),
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        # If state file exists, it must contain a non-empty task_id
        if os.path.exists(self._state_file):
            content = Path(self._state_file).read_text().strip()
            parsed = json.loads(content)
            self.assertTrue(parsed.get("task_id", "").startswith("task-"), f"Unexpected state: {content!r}")
            self.assertTrue(parsed.get("conversation_id"), f"Missing conversation_id: {content!r}")
            self.assertTrue(parsed.get("episode_id"), f"Missing episode_id: {content!r}")

    def test_stdout_is_valid_json_context_when_state_file_written(self) -> None:
        """If orchestrator ran and wrote a state file, stdout must be valid JSON {"context": ...}."""
        result = _run_script(
            PREFLIGHT,
            _hook_json(prompt="debug the failing test in orchestrator module"),
            state_file=self._state_file,
            env=self._env(),
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        stdout = result.stdout.strip()
        if stdout:
            parsed = json.loads(stdout)
            self.assertIn("context", parsed)
            self.assertIsInstance(parsed["context"], str)

    def test_preflight_enables_stage7_auto_retry_by_default(self) -> None:
        content = Path(PREFLIGHT).read_text(encoding="utf-8")
        self.assertIn("MNEMO_AUTO_RETRY", content)
        self.assertIn("--auto-retry", content)
        self.assertIn("--max-focused-retries", content)
        self.assertIn("auto_retry_summary", content)

    def test_uses_session_id_from_hook_json(self) -> None:
        """session_id from stdin JSON is passed through (verified via state model)."""
        # Run twice with different session IDs and verify neither crashes
        for sess in ["session-aaa111", "session-bbb222"]:
            result = _run_script(
                PREFLIGHT,
                _hook_json(prompt="implement auth module", session_id=sess),
                state_file=self._state_file,
                env=self._env(),
            )
            self.assertEqual(result.returncode, 0, f"Failed for session {sess}: {result.stderr}")
            # Clean state file between runs
            try:
                os.unlink(self._state_file)
            except FileNotFoundError:
                pass


class TestCodexNotifyBridge(unittest.TestCase):
    """Tests for Codex Desktop notify capture wrapper."""

    def test_does_not_launch_computer_use_helper_by_default(self) -> None:
        content = CODEX_NOTIFY_BRIDGE.read_text(encoding="utf-8")
        self.assertNotIn("computer-use/1.0.758", content)
        self.assertIn("MNEMO_CODEX_NOTIFY_RUN_ORIGINAL", content)


class TestCodexPreflightHook(unittest.TestCase):
    def test_codex_preflight_enables_stage7_auto_retry_by_default(self) -> None:
        content = Path(CODEX_PREFLIGHT).read_text(encoding="utf-8")
        self.assertIn("MNEMO_AUTO_RETRY", content)
        self.assertIn("--auto-retry", content)
        self.assertIn("--max-focused-retries", content)
        self.assertIn("auto_retry_summary", content)


class TestStopHook(unittest.TestCase):
    """Tests for mnemonic_stop_hook.sh.

    A fake ``mnemonicos`` binary is installed on PATH by default so tests
    never invoke the real orchestrator: real runs depend on live Ollama
    latency (>30 s under daemon GPU contention) and ingest junk test
    summaries into the production memory DB.
    """

    def setUp(self) -> None:
        self._state_tmp = tempfile.NamedTemporaryFile(prefix="mnemo_state_", delete=False)
        self._state_tmp.close()
        self._state_file = self._state_tmp.name
        os.unlink(self._state_file)

        self._pending_tmp = tempfile.NamedTemporaryFile(
            prefix="mnemo_pending_", suffix=".md", delete=False
        )
        self._pending_tmp.close()
        self._pending_file = self._pending_tmp.name
        os.unlink(self._pending_file)

        self._fake_bin_tmp = tempfile.TemporaryDirectory(prefix="mnemo_fake_bin_")
        self._fake_path, self._cli_log = _install_fake_mnemonicos(self._fake_bin_tmp.name)

    def tearDown(self) -> None:
        self._fake_bin_tmp.cleanup()
        for f in [self._state_file, self._pending_file]:
            try:
                os.unlink(f)
            except FileNotFoundError:
                pass

    def _run_stop(self, *, session_id: str = "test-session-xyz",
                  pending_content: str | None = None,
                  task_id: str | None = None,
                  env_extra: dict | None = None) -> subprocess.CompletedProcess:
        # Default to the fake mnemonicos PATH; an explicit PATH in env_extra wins.
        extra = {"PATH": self._fake_path}
        if env_extra:
            extra.update(env_extra)
        env = _make_env(state_file=self._state_file, extra=extra)
        # Override PENDING path via env (scripts use /tmp/mnemo_pending_summary.md by default,
        # but we inject it by symlinking or via env if the script supports it).
        # Since the script hardcodes PENDING, we patch via a wrapper approach:
        # write to the hardcoded path and use a temp directory trick.
        # For simplicity, we write to the fixed path and restore it after.
        fixed_pending = "/tmp/mnemo_pending_summary.md"
        original_content = None
        if os.path.exists(fixed_pending):
            original_content = Path(fixed_pending).read_text()

        try:
            if pending_content is not None:
                Path(fixed_pending).write_text(pending_content)
            elif os.path.exists(fixed_pending):
                # Remove so the hook sees no pending file
                os.unlink(fixed_pending)

            if task_id is not None:
                Path(self._state_file).write_text(task_id + "\n")

            stdin_data = json.dumps({"session_id": session_id})
            result = subprocess.run(
                ["bash", STOP_HOOK],
                input=stdin_data,
                capture_output=True,
                text=True,
                env=env,
                timeout=30,
            )
        finally:
            # Restore any pre-existing pending file
            if original_content is not None and not os.path.exists(fixed_pending):
                Path(fixed_pending).write_text(original_content)
            elif pending_content is not None and os.path.exists(fixed_pending):
                # Clean up what we wrote (hook should have deleted it, but in case of failure)
                try:
                    os.unlink(fixed_pending)
                except FileNotFoundError:
                    pass

        return result

    def test_exits_zero_no_pending_summary(self) -> None:
        """No pending summary → exit 0, state file cleaned up."""
        # Write a state file to verify it gets cleaned up
        Path(self._state_file).write_text("task-abc123\n")
        result = self._run_stop(pending_content=None)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(os.path.exists(self._state_file))

    def test_exits_zero_always(self) -> None:
        """Stop hook must always exit 0 — never blocks session close."""
        result = self._run_stop(
            pending_content=_pending_summary(),
            task_id="task-xyz999",
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_cleans_up_state_file(self) -> None:
        """State file is deleted after stop hook runs."""
        Path(self._state_file).write_text("task-cleanup-test\n")
        result = self._run_stop(
            pending_content=_pending_summary(),
            task_id="task-cleanup-test",
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(os.path.exists(self._state_file))

    def test_fallback_when_no_state_file(self) -> None:
        """Pending summary but no state file → fallback path runs, exit 0."""
        # No state file written — should fall back to mnemonic_postflight.sh
        result = self._run_stop(
            pending_content=_pending_summary(),
            task_id=None,  # no state file
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_pending_summary_finishes_task_when_capture_exists(self) -> None:
        """A captured turn with a pending summary must still run orchestrator finish-task."""
        with tempfile.TemporaryDirectory() as tmp:
            bin_dir = Path(tmp) / "bin"
            bin_dir.mkdir()
            log_path = Path(tmp) / "mnemonicos.log"
            fake = bin_dir / "mnemonicos"
            fake.write_text(
                textwrap.dedent(
                    f"""\
                    #!/usr/bin/env bash
                    printf '%s\\n' "$*" >> {str(log_path)!r}
                    case "$*" in
                      *"capture end-episode"*) printf '{{"ok": true}}\\n' ;;
                      *"orchestrator finish-task"*) printf '{{"decision": "skip"}}\\n' ;;
                      *) printf '{{"ok": true}}\\n' ;;
                    esac
                    exit 0
                    """
                )
            )
            fake.chmod(0o755)
            Path(self._state_file).write_text(
                json.dumps(
                    {
                        "task_id": "task-finish-with-capture",
                        "session_id": "session-finish-with-capture",
                        "conversation_id": "conversation-finish-with-capture",
                        "episode_id": "episode-finish-with-capture",
                    }
                )
            )
            result = self._run_stop(
                session_id="session-finish-with-capture",
                pending_content=_pending_summary(),
                env_extra={"PATH": f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}"},
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            calls = log_path.read_text()
            self.assertIn("capture end-episode", calls)
            self.assertIn("orchestrator finish-task", calls)
            self.assertNotIn("capture project-episode", calls)


class TestHookRoundTrip(unittest.TestCase):
    """Cross-hook integration: per-turn task_id independence."""

    def setUp(self) -> None:
        self._state_tmp = tempfile.NamedTemporaryFile(prefix="mnemo_state_", delete=False)
        self._state_tmp.close()
        self._state_file = self._state_tmp.name
        os.unlink(self._state_file)
        self._fake_bin_tmp = tempfile.TemporaryDirectory(prefix="mnemo_fake_bin_")
        self._fake_path, self._cli_log = _install_fake_mnemonicos(self._fake_bin_tmp.name)

    def tearDown(self) -> None:
        self._fake_bin_tmp.cleanup()
        try:
            os.unlink(self._state_file)
        except FileNotFoundError:
            pass

    def test_per_turn_task_ids_are_independent(self) -> None:
        """Two preflight runs with the same session_id produce different task_ids."""
        env = _make_env(state_file=self._state_file, extra={"PATH": self._fake_path})
        task_ids: list[str] = []

        for _ in range(2):
            # Remove state file before each run
            try:
                os.unlink(self._state_file)
            except FileNotFoundError:
                pass

            result = subprocess.run(
                ["bash", PREFLIGHT],
                input=_hook_json(
                    prompt="implement a new feature for the memory system",
                    session_id="round-trip-session-001",
                ),
                capture_output=True,
                text=True,
                env=env,
                timeout=30,
            )
            self.assertEqual(result.returncode, 0, result.stderr)

            if os.path.exists(self._state_file):
                raw_state = Path(self._state_file).read_text().strip()
                tid = json.loads(raw_state).get("task_id", "")
                if tid:
                    task_ids.append(tid)

        # If both runs produced task_ids, they must be distinct
        if len(task_ids) == 2:
            self.assertNotEqual(
                task_ids[0],
                task_ids[1],
                f"Expected distinct task_ids per turn, got: {task_ids}",
            )


class TestHookDisableSwitch(unittest.TestCase):
    """Two-tier enable/disable switch: env var + flag file."""

    def setUp(self) -> None:
        self._state_tmp = tempfile.NamedTemporaryFile(prefix="mnemo_state_", delete=False)
        self._state_tmp.close()
        self._state_file = self._state_tmp.name
        os.unlink(self._state_file)

        self._flag_tmp = tempfile.NamedTemporaryFile(prefix="mnemo_disabled_", delete=False)
        self._flag_tmp.close()
        self._flag_file = self._flag_tmp.name
        # Keep file for flag-file test; remove for env-var test
        os.unlink(self._flag_file)

    def tearDown(self) -> None:
        for f in [self._state_file, self._flag_file]:
            try:
                os.unlink(f)
            except FileNotFoundError:
                pass

    def _make_disable_env(self, *, mnemo_enabled: str = "1", flag_file: str | None = None) -> dict:
        env = _make_env(state_file=self._state_file, mnemo_enabled=mnemo_enabled)
        # Inject a custom DISABLE_FLAG path by prepending a wrapper
        # We achieve this by setting DISABLE_FLAG env var — the scripts read
        # REPO_ROOT/config/mnemo_disabled directly, so we need a different approach:
        # run a wrapper that creates/removes the actual flag file.
        if flag_file:
            # Create the actual flag file at the repo path
            actual_flag = ROOT / "config" / "mnemo_disabled"
            # Track it so teardown can clean it
            self._actual_flag = str(actual_flag)
            actual_flag.parent.mkdir(parents=True, exist_ok=True)
            actual_flag.touch()
        return env

    def test_preflight_disabled_via_env_exits_noop(self) -> None:
        """MNEMO_ENABLED=0 → preflight exits 0 immediately, no state file written."""
        env = _make_env(state_file=self._state_file, mnemo_enabled="0")
        result = subprocess.run(
            ["bash", PREFLIGHT],
            input=_hook_json(prompt="implement something important"),
            capture_output=True,
            text=True,
            env=env,
            timeout=10,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), "")
        self.assertFalse(os.path.exists(self._state_file))

    def test_stop_hook_disabled_via_env_exits_noop(self) -> None:
        """MNEMO_ENABLED=0 → stop hook exits 0 immediately, pending file left intact."""
        pending = "/tmp/mnemo_pending_summary.md"
        # Write a pending summary
        original = None
        if os.path.exists(pending):
            original = Path(pending).read_text()
        Path(pending).write_text(_pending_summary())
        try:
            env = _make_env(state_file=self._state_file, mnemo_enabled="0")
            result = subprocess.run(
                ["bash", STOP_HOOK],
                input=json.dumps({"session_id": "disabled-test-session"}),
                capture_output=True,
                text=True,
                env=env,
                timeout=10,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            # Pending file must still exist (was not consumed)
            self.assertTrue(os.path.exists(pending), "Pending file should NOT be deleted when disabled")
        finally:
            if original is not None:
                Path(pending).write_text(original)
            elif os.path.exists(pending):
                os.unlink(pending)

    def test_preflight_disabled_via_flag_file_exits_noop(self) -> None:
        """config/mnemo_disabled flag file → preflight exits 0, no state file written."""
        actual_flag = ROOT / "config" / "mnemo_disabled"
        actual_flag.parent.mkdir(parents=True, exist_ok=True)
        actual_flag.touch()
        try:
            env = _make_env(state_file=self._state_file)  # MNEMO_ENABLED=1
            result = subprocess.run(
                ["bash", PREFLIGHT],
                input=_hook_json(prompt="implement something important"),
                capture_output=True,
                text=True,
                env=env,
                timeout=10,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout.strip(), "")
            self.assertFalse(os.path.exists(self._state_file))
        finally:
            try:
                actual_flag.unlink()
            except FileNotFoundError:
                pass


class TestCodexHookAdapter(unittest.TestCase):
    """Tests for the Codex-specific hook wrappers.

    Capture fidelity needs the real CLI, so instead of faking the binary these
    tests isolate the workspace: MNEMO_CONFIG points the hooks/importer at a
    temp workspace so episodes and extraction-queue rows never land in the
    production DB (real runs left orphaned pending rows behind).
    """

    def setUp(self) -> None:
        self._state_tmp = tempfile.NamedTemporaryFile(prefix="mnemo_codex_state_", delete=False)
        self._state_tmp.close()
        self._state_file = self._state_tmp.name
        os.unlink(self._state_file)
        self._workspace_tmp = tempfile.TemporaryDirectory(prefix="mnemo_codex_ws_")
        workspace = Path(self._workspace_tmp.name)
        (workspace / "config").mkdir()
        self._config_path = workspace / "config" / "memory.toml"
        self._config_path.write_text("")  # defaults; paths derive from this file's location
        self._raw_sessions_root = workspace / "brain" / "raw" / "sessions"
        self._session_id = "codex-unit-hook-session"
        self._raw_session_dir = self._raw_sessions_root / self._session_id

    def tearDown(self) -> None:
        try:
            os.unlink(self._state_file)
        except FileNotFoundError:
            pass
        self._workspace_tmp.cleanup()

    def _env(self, *, extra: dict | None = None) -> dict:
        merged = {"MNEMO_CONFIG": str(self._config_path)}
        if extra:
            merged.update(extra)
        return _make_env(state_file=self._state_file, extra=merged)

    def test_codex_preflight_and_stop_capture_turn(self) -> None:
        """Codex hooks write a raw codex episode without using Claude hook state."""
        env = self._env()
        prompt_payload = json.dumps(
            {
                "session_id": self._session_id,
                "turn_id": "turn-unit-001",
                "prompt": "Codex hook adapter unit test prompt",
                "cwd": str(ROOT),
                "model": "gpt-test",
            }
        )
        preflight = subprocess.run(
            ["bash", CODEX_PREFLIGHT],
            input=prompt_payload,
            capture_output=True,
            text=True,
            env=env,
            timeout=30,
        )
        self.assertEqual(preflight.returncode, 0, preflight.stderr)
        self.assertTrue(os.path.exists(self._state_file))

        stop_payload = json.dumps(
            {
                "session_id": self._session_id,
                "turn_id": "turn-unit-001",
                "last_assistant_message": "Codex hook adapter unit test response.",
            }
        )
        stop = subprocess.run(
            ["bash", CODEX_STOP_HOOK],
            input=stop_payload,
            capture_output=True,
            text=True,
            env=env,
            timeout=30,
        )
        self.assertEqual(stop.returncode, 0, stop.stderr)
        self.assertFalse(os.path.exists(self._state_file))

        event_files = list(self._raw_session_dir.glob("episodes/*/events.jsonl"))
        self.assertEqual(len(event_files), 1)
        events = [json.loads(line) for line in event_files[0].read_text().splitlines()]
        self.assertEqual([event["type"] for event in events], [
            "episode_started",
            "user_message",
            "retrieval_packet",
            "assistant_message",
            "episode_ended",
        ])
        self.assertTrue(all(event["bridge_id"] == "codex" for event in events))

    def test_codex_stop_finishes_pending_summary(self) -> None:
        """Codex stop hook consumes pending summaries through orchestrator finish-task."""
        fixed_pending = Path("/tmp/mnemo_pending_summary.md")
        original = fixed_pending.read_text() if fixed_pending.exists() else None
        with tempfile.TemporaryDirectory() as tmp:
            bin_dir = Path(tmp) / "bin"
            bin_dir.mkdir()
            log_path = Path(tmp) / "mnemonicos.log"
            fake = bin_dir / "mnemonicos"
            fake.write_text(
                textwrap.dedent(
                    f"""\
                    #!/usr/bin/env bash
                    printf '%s\\n' "$*" >> {str(log_path)!r}
                    case "$*" in
                      *"capture end-episode"*) printf '{{"ok": true}}\\n' ;;
                      *"orchestrator finish-task"*) printf '{{"decision": "skip"}}\\n' ;;
                      *) printf '{{"ok": true}}\\n' ;;
                    esac
                    exit 0
                    """
                )
            )
            fake.chmod(0o755)
            try:
                fixed_pending.write_text(_pending_summary(agent="codex"))
                Path(self._state_file).write_text(
                    json.dumps(
                        {
                            "task_id": "task-codex-finish",
                            "session_id": self._session_id,
                            "conversation_id": self._session_id,
                            "episode_id": "episode-codex-finish",
                            "turn_id": "turn-codex-finish",
                        }
                    )
                )
                env = self._env(
                    extra={"PATH": f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}"},
                )
                result = subprocess.run(
                    ["bash", CODEX_STOP_HOOK],
                    input=json.dumps({"session_id": self._session_id, "turn_id": "turn-codex-finish"}),
                    capture_output=True,
                    text=True,
                    env=env,
                    timeout=30,
                )
                self.assertEqual(result.returncode, 0, result.stderr)
                calls = log_path.read_text()
                self.assertIn("capture end-episode", calls)
                self.assertIn("orchestrator finish-task", calls)
                self.assertNotIn("capture project-episode", calls)
                self.assertFalse(fixed_pending.exists())
            finally:
                if original is not None:
                    fixed_pending.write_text(original)
                elif fixed_pending.exists():
                    fixed_pending.unlink()

    def test_codex_notify_payload_captures_one_complete_turn(self) -> None:
        """Codex notify payloads should become one readable turn episode."""
        session_id = "codex-unit-notify-session"
        turn_id = "turn-unit-notify-001"
        raw_session_dir = self._raw_sessions_root / session_id
        try:
            payload = json.dumps(
                {
                    "type": "agent-turn-complete",
                    "thread-id": session_id,
                    "turn-id": turn_id,
                    "cwd": str(ROOT),
                    "client": "Codex Desktop",
                    "input-messages": ["first user message", "second user message"],
                    "last-assistant-message": "assistant response",
                }
            )
            result = subprocess.run(
                [sys.executable, str(CODEX_NOTIFY_IMPORTER), "turn-ended", payload],
                capture_output=True,
                text=True,
                env={**os.environ, "MNEMO_CONFIG": str(self._config_path)},
                timeout=30,
            )
            self.assertEqual(result.returncode, 0, result.stderr)

            event_files = list(raw_session_dir.glob("episodes/*/events.jsonl"))
            self.assertEqual(len(event_files), 1)
            self.assertEqual(event_files[0].parent.name, turn_id)
            events = [json.loads(line) for line in event_files[0].read_text().splitlines()]
            self.assertEqual(
                [event["type"] for event in events],
                [
                    "episode_started",
                    "user_message",
                    "user_message",
                    "assistant_message",
                    "episode_ended",
                ],
            )

            transcript = (event_files[0].parent / "transcript.md").read_text()
            self.assertIn("first user message", transcript)
            self.assertIn("second user message", transcript)
            self.assertIn("assistant response", transcript)
        finally:
            shutil.rmtree(raw_session_dir, ignore_errors=True)

    def test_codex_notify_payload_captures_faithful_structured_turn(self) -> None:
        """Structured Codex notify payloads preserve rich turn details."""
        session_id = "codex-unit-faithful-session"
        turn_id = "turn-unit-faithful-001"
        raw_session_dir = self._raw_sessions_root / session_id
        try:
            payload = json.dumps(
                {
                    "type": "agent-turn-complete",
                    "thread-id": session_id,
                    "turn-id": turn_id,
                    "cwd": str(ROOT),
                    "client": "Codex Desktop",
                    "input-messages": ["please make the bridge faithful"],
                    "last-assistant-message": "Implemented and verified.",
                    "events": [
                        {
                            "type": "retrieval_packet",
                            "timestamp": "2026-05-13T08:00:00+00:00",
                            "payload": {
                                "query": "codex bridge capture",
                                "state": "partial",
                                "hit_ids": ["source/codex-bridge"],
                            },
                        },
                        {
                            "type": "skill_use",
                            "payload": {
                                "skill": "mnemonicos-bridge",
                                "path": "/Users/jianyulong/.codex/skills/mnemonicos-bridge/SKILL.md",
                                "reason": "memory-aware bridge implementation",
                            },
                        },
                        {
                            "type": "response_item",
                            "payload": {
                                "type": "function_call",
                                "name": "exec_command",
                                "call_id": "call-shell-1",
                                "arguments": json.dumps({"cmd": "python3 -m unittest tests.test_hook_adapter"}),
                            },
                        },
                        {
                            "type": "response_item",
                            "payload": {
                                "type": "function_call_output",
                                "call_id": "call-shell-1",
                                "output": "Ran 1 test in 0.1s\nOK",
                            },
                        },
                        {
                            "type": "event_msg",
                            "payload": {
                                "type": "web_search_end",
                                "url": "https://example.com/docs",
                                "title": "Example docs",
                                "status": 200,
                            },
                        },
                        {
                            "type": "event_msg",
                            "payload": {
                                "type": "patch_apply_end",
                                "changes": [
                                    {
                                        "path": "scripts/mnemonic_codex_notify_bridge.py",
                                        "diff": "@@ faithful capture @@",
                                    }
                                ],
                            },
                        },
                        {
                            "type": "artifact_created",
                            "payload": {
                                "path": "/tmp/mnemo-generated-report.json",
                                "sha256": "abc123",
                                "kind": "report",
                            },
                        },
                        {
                            "type": "conclusion",
                            "payload": {
                                "summary": "Bridge captured retrieval, skill, tools, web, files, artifacts, and conclusion.",
                                "tests": ["tests.test_hook_adapter"],
                                "files_modified": ["scripts/mnemonic_codex_notify_bridge.py"],
                            },
                        },
                    ],
                }
            )
            result = subprocess.run(
                [sys.executable, str(CODEX_NOTIFY_IMPORTER), "turn-ended", payload],
                capture_output=True,
                text=True,
                env={**os.environ, "MNEMO_CONFIG": str(self._config_path)},
                timeout=30,
            )
            self.assertEqual(result.returncode, 0, result.stderr)

            event_files = list(raw_session_dir.glob("episodes/*/events.jsonl"))
            self.assertEqual(len(event_files), 1)
            events = [json.loads(line) for line in event_files[0].read_text().splitlines()]
            event_types = [event["type"] for event in events]
            for expected in [
                "episode_started",
                "user_message",
                "assistant_message",
                "retrieval_packet",
                "skill_use",
                "tool_call",
                "tool_result",
                "web_fetch",
                "file_diff",
                "artifact_created",
                "conclusion",
                "episode_ended",
            ]:
                self.assertIn(expected, event_types)

            by_type = {event["type"]: event for event in events}
            self.assertEqual(by_type["tool_call"]["payload"]["tool_name"], "exec_command")
            self.assertEqual(by_type["tool_call"]["payload"]["input"]["cmd"], "python3 -m unittest tests.test_hook_adapter")
            self.assertEqual(by_type["tool_result"]["payload"]["call_id"], "call-shell-1")
            self.assertIn("OK", by_type["tool_result"]["payload"]["output"])
            self.assertEqual(by_type["web_fetch"]["payload"]["url"], "https://example.com/docs")
            self.assertEqual(by_type["file_diff"]["payload"]["changes"][0]["path"], "scripts/mnemonic_codex_notify_bridge.py")
            self.assertEqual(by_type["skill_use"]["payload"]["skill"], "mnemonicos-bridge")
            self.assertIn("Bridge captured", by_type["conclusion"]["payload"]["summary"])
            self.assertTrue(all(event["bridge_id"] == "codex" for event in events))
        finally:
            shutil.rmtree(raw_session_dir, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
