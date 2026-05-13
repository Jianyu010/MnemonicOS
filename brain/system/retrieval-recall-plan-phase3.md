# Retrieval Recall Phase 3: Semantic Channel

## Goal

Implement semantic recall over canonical wiki notes using one query embedding
per retrieve call, with no duplicate encoding and no archive/raw mixing.

## Scope

- `run_semantic_channel()` function
- exactly one query embedding per recall call
- cosine similarity scoring against note vectors
- bounded result set

## Components

```
src/second_brain/recall.py
src/second_brain/embedding_providers.py
src/second_brain/semantics.py
```

## Rules

1. If embeddings are disabled, return `[]`.
2. Encode the original raw query once.
3. Score only notes with the configured vector model.
4. Drop non-positive similarity scores.
5. Return the top `top_k * 4` semantic candidates.

## Failure Policy

If embeddings are disabled, semantic recall degrades to `[]` without error.
Provider or model errors should propagate.

## Verification

- one query encode call per recall invocation
- semantic candidates carry `semantic_score`
- disabled embeddings produce `[]`
- candidate count respects `top_k * 4`
