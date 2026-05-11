# Embedding Model Decision for Knowledge Maintenance Layer

## Decision

Use **`nomic-embed-text`** via Ollama as the initial embedding model for
cross-wiki neighbor linking (Phase 6) and LLM cluster validation (Phase 7).

Pull command: `ollama pull nomic-embed-text`

---

## Recommendation rationale

| Model | Params | Dims | License | Fine-tunable | Notes |
|---|---|---|---|---|---|
| **nomic-embed-text** | 137M | 768 | Apache-2.0 | Yes (BERT-style) | **Recommended** |
| mxbai-embed-large | 335M | 1024 | Apache-2.0 | Yes | Better quality, 2.5× RAM |
| bge-small-en-v1.5 | 33M | 384 | MIT | Yes | Fast, lower recall |
| all-minilm | 22M | 384 | Apache-2.0 | Yes | Fastest, lowest quality |
| bge-m3 | 570M | 1024 | MIT | Yes | Multilingual, heaviest |

`nomic-embed-text` is the best starting point because:

1. **Apache-2.0**: allows fine-tuning and commercial deployment without restriction.
2. **BERT-style bidirectional encoder**: the architecture the RL fine-tuning path
   requires. Self-attention over the full input sequence means every token attends
   to every other — the resulting embedding encodes global semantic structure, not
   just local token statistics.
3. **137M / 768d**: large enough to encode rich semantic relationships; small
   enough to run comfortably on a single GPU or CPU at inference time.
4. **Strong general-purpose benchmark performance**: top-tier on MTEB retrieval
   tasks, meaning cross-wiki neighbor searches start with a high-quality signal
   before any domain fine-tuning.

---

## How to install

```bash
ollama pull nomic-embed-text
```

Verify:

```bash
ollama run nomic-embed-text "test" 2>/dev/null && echo "OK"
```

Or via the API:

```bash
curl http://localhost:11434/api/embeddings \
  -d '{"model": "nomic-embed-text", "prompt": "test sentence"}'
```

Set `config.embeddings.enabled = true` and `config.embeddings.model = "nomic-embed-text"`
in `config/memory.toml` or equivalent.

---

## RL training path

Phase 6 and 7 together generate a natural training signal for contrastive
fine-tuning of the embedding model on MnemonicOS-specific domain knowledge.

### Signal generation

Every LLM cluster validation (Phase 7) produces a binary label per note pair:

- **Positive pair**: LLM confirms `similar_to` link — two notes are semantically
  related in MnemonicOS's domain.
- **Negative pair**: LLM rejects `similar_to` link — two notes are not meaningfully
  related despite embedding proximity.

These labels accumulate as the system runs, building a domain-specific dataset
of `(note_a_embedding, note_b_embedding, label)` triples.

### Fine-tuning method

**SimCSE / InfoNCE contrastive loss** on the accumulated pairs:

```
L = -log [ exp(sim(a, b+) / τ) / Σ_neg exp(sim(a, b-) / τ) ]
```

Where `sim` is cosine similarity, `τ` is a temperature parameter, `b+` is a
positive note, and `b-` are in-batch negatives plus hard negatives from rejected
pairs.

The fine-tuned model embeds MnemonicOS concepts more tightly than `nomic-embed-text`
out-of-the-box, reducing false-positive neighbor matches and improving recall for
domain-specific patterns.

### LLM-as-judge for synthetic pairs

Beyond Phase 7 validation, an LLM can generate additional synthetic training pairs
by reading clusters of wiki notes and identifying:

- **Hard positives**: notes that are semantically equivalent despite different
  vocabulary (e.g., "cache invalidation strategy" and "stale data eviction policy").
- **Hard negatives**: notes that share surface vocabulary but describe different
  concepts (e.g., "Python function `len()`" and "Python function as a first-class
  value").

Hard negatives are the most valuable training signal — they teach the embedding
model to distinguish surface similarity from semantic similarity.

### Training frequency

Contrastive fine-tuning is expensive. Planned schedule:
- Accumulate ≥ 500 validated pairs before first fine-tuning run.
- Re-train quarterly or when link precision (validated pairs / total pairs) drops
  below 0.70.
- Checkpoint after each fine-tuning; keep prior checkpoint as rollback.

---

## How encoders capture abstract patterns

A common question: how does an embedding model recognize that
"cheetah chases deer" and "predator pursuing prey" share an abstract pattern,
despite sharing no vocabulary?

### The mechanism

BERT-style encoders do not encode words in isolation. The transformer's
self-attention mechanism allows every token to attend to every other token in the
input sequence before producing a single embedding vector. As a result, the model
encodes **relationships between entities**, not just entity identity.

"cheetah chases deer" produces an embedding that encodes:
- Subject: predator (high-speed, carnivore)
- Action: directed pursuit
- Object: prey (smaller, herbivore)
- Outcome: implied consumption

"predator pursuing prey" produces an embedding that encodes the same structure
at a higher level of abstraction.

After training on a large text corpus, the two embeddings end up close in 768d
space because they appear in similar distributional contexts — the surrounding
sentences discuss similar topics: wildlife documentaries, evolutionary biology,
ecological balance.

### Why threshold matters

At θ=0.78, the encoder's distributional similarity is sufficient to surface
these abstract structural matches. At θ=0.92, only near-paraphrases match —
useful for dedup, not for pattern discovery.

The two thresholds serve different purposes:
- **θ=0.78**: broad semantic neighborhood → `similar_to` links → pattern clusters
- **θ=0.92**: near-duplicate detection → `merge_review` items

### How fine-tuning improves pattern detection

Out-of-the-box, `nomic-embed-text` is trained on a general corpus. It may not
know that "rollback after failed migration" and "undo after broken deploy" are
semantically identical in the MnemonicOS incident domain.

After contrastive fine-tuning on MnemonicOS-validated pairs, the embedding space
reorganizes so that domain-specific synonyms cluster more tightly. The model
learns to weight the dimensions that encode "recovery operation after failure" more
heavily than surface vocabulary, specifically within the MnemonicOS incident type.

This is the RL training path: LLM validates links → pairs accumulate → fine-tuning
reorganizes embedding space → future neighbor searches are more domain-accurate →
LLM validates fewer false positives → training signal improves.

---

## Integration point

`encode_text()` in `src/second_brain/semantics.py` is the existing embedding
infrastructure. Phase 6 calls it to embed note summaries for neighbor search.
No new inference infrastructure is needed — only the model pull and config flag.
