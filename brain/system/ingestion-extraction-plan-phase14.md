# LLM/VLM Layer Phase 14: Graceful Ollama Inference Timeout + VLM keep_alive=0

## Goal

Make `mnemonicos stop` (and extraction subprocess kill) actually mean stop — not
leave Ollama stuck in `Stopping…` for hours.

## Problem

`OllamaVLMProvider` and `OllamaLLMProvider` call `_ollama_generate()` with
`timeout_seconds=None`, so urllib waits forever for a response.  When Ollama
hangs mid-CUDA-kernel the parent's stall detector fires after 5 min → SIGKILLs
the subprocess → TCP connection severed mid-stream → Ollama stuck.

Also, `socket.timeout` is in `_TRANSIENT_ERRORS`, meaning a timeout is silently
retried three times before propagating — adding up to 3× the hang time before
the stall kill fires.

## Fix

Two targeted changes:

1. **HTTP-level timeout on every Ollama call.**  Wire `vlm_timeout_seconds`
   (default 240 s) and `llm_timeout_seconds` (default 600 s) through to
   `_ollama_generate()`.  240 s < `stall_timeout_seconds` (300 s), so the
   subprocess exits via exception before the process-group kill fires.  Ollama
   gets a clean TCP close → context cancel propagates → GPU work aborted.

2. **`keep_alive=0` on every VLM call.**  After each artifact the VLM model is
   immediately unloaded from VRAM.  On the next artifact it is reloaded (~30 s).
   Trade-off: ~25 % overhead on large PDFs, eliminated stuck-model accumulation
   between sessions.

## New exception: `InferenceTimeoutError`

`_open_request` now raises `InferenceTimeoutError` (instead of retrying) when
`socket.timeout` or `TimeoutError` fires.  `_subprocess_entry._run()` catches
it before the generic handler and always writes `"timeout: …"` — never
`"tombstoned: …"` — so `retry_failed_extractions` requeues the item.

## Key invariant

`vlm_timeout_seconds` (240 s) < `stall_timeout_seconds` (300 s).  HTTP timeout
fires first → clean subprocess exit → no SIGKILL.

## Configuration

```toml
[extraction]
vlm_timeout_seconds = 240   # per-artifact VLM HTTP timeout
llm_timeout_seconds = 600   # per-window LLM HTTP timeout
```

If unset, `build_vlm_provider` defaults to 240 s and `build_llm_provider` to
600 s for the Ollama path.

## Implementation Scope

| File | Change |
|------|--------|
| `src/second_brain/extraction/providers.py` | `InferenceTimeoutError`; timeout + keep_alive on Ollama providers; remove `socket.timeout`/`TimeoutError` from `_TRANSIENT_ERRORS` |
| `src/second_brain/extraction/_subprocess_entry.py` | Catch `InferenceTimeoutError` before generic handler → retryable |
| `config/memory.toml` | `vlm_timeout_seconds = 240`, `llm_timeout_seconds = 600` |
| `config/memory.example.toml` | Document new keys |
| `tests/test_extraction_layer.py` | Timeout wiring + retryable-error tests |

## Verification

```bash
python -m pytest tests/test_extraction_layer.py -k "timeout or keep_alive or inference" -v
python -m pytest tests/ -q
```
