# Chinese Support Phase 5: Embedding Provider Hygiene

## Goal

Make semantic Chinese support explicit and avoid mixing incompatible vector
spaces.

## Implementation

- Keep the hash embedding provider as a deterministic degraded fallback.
- Report hash provider health as degraded for multilingual semantic retrieval.
- Treat provider, model, and dimensions as the embedding model key.
- Require external multilingual providers for robust Chinese semantic retrieval.

## Acceptance

- Switching provider/model/dimensions causes old vectors to be ignored until
  reembedded.
- Health output makes degraded hash behavior visible.
