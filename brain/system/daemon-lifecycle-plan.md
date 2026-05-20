# Daemon Process Lifecycle Plan

Governs how MnemonicOS starts, tracks, and stops every process it owns.
Authoritative over `daemon.py`, `daemon_processes.py`, `cli.py` stop path, and `extraction/worker.py`.

---

## Problem Statement

`mnemonicos daemon stop` previously sent SIGTERM to the daemon's main PID and printed
`daemon_stopped` as soon as `os.kill(pid, 0)` raised — without verifying extraction
subprocesses, job threads, socket closure, or daemon-managed provider processes. This
left orphaned `ollama runner` children and extraction subprocesses alive after every stop.

---

## DaemonProcessRegistry

**Module:** `src/second_brain/daemon_processes.py`

A thread-safe registry of `subprocess.Popen` objects the daemon has started and owns.

### API

```python
class DaemonProcessRegistry:
    def register(self, process: Popen, *, label: str, process_group: bool = False) -> None: ...
    def unregister(self, process: Popen) -> None: ...
    def running(self) -> list[RegisteredProcess]: ...
    def stop_all(self, *, grace_seconds: float = 5.0, kill_seconds: float = 5.0) -> bool: ...
```

### Signal escalation inside `stop_all`

1. Send SIGTERM to all running registered entries (process group or single PID).
2. Wait up to `grace_seconds` for all to exit.
3. Send SIGKILL to any that remain.
4. Wait up to `kill_seconds`.
5. Return `True` if no running entries remain.

`process_group=True` uses `os.killpg(pid, sig)` instead of `process.send_signal(sig)`.

### Threading model

All mutations (`register`, `unregister`, cleanup inside `stop_all`) take `_lock`.
`running()` reads under lock, then polls `process.poll()` outside lock to avoid
blocking on subprocess state.

---

## Extraction Subprocess Ownership

**File:** `src/second_brain/extraction/worker.py`, function `_run_item_subprocess`

Extraction spawns one Python subprocess per queue row. These must be:
- Spawned with `start_new_session=True` (creates a new process group, isolating them from the daemon's group).
- Registered with the registry as `process_group=True` immediately after `Popen(...)`.
- Unregistered in the `finally` block after the subprocess finishes or times out.

### Registry thread path

The registry is created at `Daemon.__init__` and threaded through:

```
Daemon.process_registry
  └─ Scheduler.process_registry
       └─ JobRegistry._wrap_job(process_registry=...)
            └─ run_job(..., process_registry=...)
                 └─ process_extraction_queue(..., process_registry=...)
                      └─ _run_item_subprocess(..., process_registry=...)
```

All intermediate signatures accept `process_registry: DaemonProcessRegistry | None = None`
and forward it. `None` means registry is unavailable (e.g. in standalone `run_job` calls).

---

## Shutdown Sequence

### Inside the daemon (`Daemon.run()` finally block)

```
1. shutdown_event.set()
2. process_registry.stop_all(grace=5s, kill=5s)   # first pass: unblock communicate()
3. join_running_jobs(timeout_per_job=30s)           # wait for job threads
4. process_registry.stop_all(grace=5s, kill=5s)   # second pass: sweep late registrations
5. socket.close()
6. os.unlink(pid_path)
7. log "daemon stopped"
```

**Rationale for two-pass stop_all:**
- First pass kills extraction subprocesses before joining job threads, so threads blocked
  on `proc.communicate()` can unblock and exit cleanly.
- Second pass catches any subprocess registered by a job thread that was still starting
  when the first pass ran.

### From the CLI (`_daemon_stop` in `cli.py`)

```
1. Read PID from pid_path.
2. Try socket {"cmd": "shutdown"} → daemon handles teardown cooperatively.
3. If socket unreachable → send SIGTERM to daemon PID.
4. Poll until PID gone AND socket file gone (up to configurable timeout).
5. If still alive → send SIGTERM to daemon process group (pgid of the daemon PID).
6. If still alive → send SIGKILL to daemon process group.
7. Check: PID gone, socket gone, _process_group_alive(pid) == False.
8. Print daemon_stopped on success; daemon_stop_timeout on failure.
```

### Postcondition of `daemon_stopped`

When `daemon_stopped` prints, all of the following are true:
- Daemon main PID is not alive.
- Unix socket file is removed.
- Daemon process group has no living members.
- All extraction subprocesses registered in `DaemonProcessRegistry` have exited.
- All daemon-managed provider processes (e.g. `ollama serve` when `ollama_managed=true`) have exited.

---

## Opt-In Provider Ownership

External providers MnemonicOS calls (Ollama HTTP at `127.0.0.1:11434`) are NOT started
or stopped by default. This is correct for the macOS `Ollama.app` workflow.

### Config fields (added to `DaemonConfig`)

```python
ollama_managed: bool = False   # default: do not touch Ollama
ollama_command: str = ""       # shell command to start Ollama when managed
```

### Behavior when `ollama_managed = true`

- `Daemon.run()` launches `shlex.split(ollama_command)` with `start_new_session=True`
  immediately after startup logging.
- Registers the process with `self.process_registry` as `label="ollama", process_group=True`.
- Shutdown sequence (above) terminates the Ollama process group as part of `stop_all`.
- `daemon_stopped` will not print until the Ollama process group exits.

### Behavior when `ollama_managed = false` (default)

`daemon stop` does not touch any Ollama process. Leaving Ollama running after stop is
correct and expected.

### Example config

```toml
[daemon]
ollama_managed = true
ollama_command = "/usr/local/bin/ollama serve"
```

---

## Files Modified

| File | Change |
|------|--------|
| `src/second_brain/daemon_processes.py` | New — DaemonProcessRegistry |
| `src/second_brain/daemon.py` | Add process_registry, two-pass stop_all, socket shutdown command |
| `src/second_brain/cli.py` | Replace _daemon_stop with 7-step escalation + verified daemon_stopped |
| `src/second_brain/extraction/worker.py` | spawn with start_new_session, register/unregister subprocess |
| `src/second_brain/daemon_jobs.py` | Thread process_registry through _wrap_job → run_job |
| `src/second_brain/jobs.py` | Accept and forward process_registry in process_extraction_queue |
| `src/second_brain/config.py` | Add ollama_managed, ollama_command to DaemonConfig |
| `config/memory.example.toml` | Document new [daemon] provider fields |
| `tests/test_daemon_jobs.py` | Tests for registry, socket shutdown, CLI stop, Ollama config |
| `docs/daemon-and-socket.md` | Document shutdown command, stop escalation, provider ownership |
| `docs/cli-reference.md` | Update daemon stop description |

---

## Implementation Plan

This file is the governing implementation plan for the daemon shutdown lifecycle,
including the TDD task breakdown for process registry, socket shutdown, CLI stop
escalation, and provider ownership.
