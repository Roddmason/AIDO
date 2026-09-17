# ADR-003: Robust outlier threshold for memory conflict detection

## Estado: Aceptada

## Contexto

Nothing in the system noticed when two memory items in the same scope asserted contradictory
things. Detection needs a definition of "conflict" and, with it, a similarity threshold. The
threshold had to be justified by measurement rather than picked, and it had to be able to report
*no conflicts* — a detector that always finds something is not a detector.

Two constraints shaped the work.

**The existing index does not produce a comparable score.** `index.py:195` builds a
`faiss.IndexFlatIP` and `:242` computes a raw inner product `vectors @ query_vector`. That value is
unbounded unless the embedding provider happens to return normalized vectors; `threads/similarity.py:482`
already has to clamp its own scores with `max(0.0, min(score, 1.0))` for the same reason. A
threshold is only meaningful over a bounded metric.

**No embedding model is available in this environment to calibrate against.** The local Ollama
daemon serves only `deepseek-r1:32b` variants, whose capabilities are `tools`/`thinking`/`completion`
with no `embedding`. The `ollama` provider account is `provider_type='local'`, while
`MemoryRepository.list_indexable_embeddings` requires `api` or `gateway`. `memory_embeddings` holds
zero rows. Calibration therefore had to be done against synthetic vectors, which is the main
residual risk recorded below.

## Decisión

A conflict is a pair of **live** memory items that share `(project_id, scope, scope_id, kind)`,
have **different `hash`** (different content), and whose **cosine similarity** exceeds a threshold
derived from the project's own corpus.

Compute cosine explicitly — normalize both vectors, in `float64`, rounded to 9 decimals for
reproducibility — inside the detector. `index.py` is left untouched so the ranking of the existing
`search` does not change.

Derive the threshold as a **robust outlier fence**: `median(similarities) + k · 1.4826 · MAD`, with
`k = 5.0` by default and configurable per request. Pairs with identical hashes are excluded from the
background distribution as well as from the candidate set, because duplicates sit at cosine 1.0 and
would drag the distribution upward, masking real conflicts.

Detection **only records**. It writes a `memory_conflicts` row and emits `memory.conflict_detected`.
It never rewrites `content` and never sets `supersedes_id`; supersession remains an explicit
decision made by a person or by an agent with evidence.

### Why not a percentile, and why not Tukey

Three constructs were measured before choosing.

| Construct | Measured behaviour | Verdict |
|---|---|---|
| Percentile `p95` of the corpus | Flags the top 5% of pairs **by construction**. On a clean 66-pair corpus it reported 4 conflicts. | Rejected — cannot ever report "no conflicts" |
| Tukey fence `Q3 + k·IQR` | Detection collapsed from 100% to 6% as `k` rose from 1.5 to 3.0: the contradictory pair inflates `Q3` and `IQR`, pushing the fence above itself | Rejected — classic masking; the outlier corrupts the statistic meant to detect it |
| **Median + k·MAD** | See below | **Accepted** |

The percentile failure is structural, not a tuning problem. For sorted similarities `s[0..M-1]`,
`numpy.percentile(..., 95)` evaluates at virtual index `0.95·(M-1) ≤ M-2`, so the result is at most
`s[M-1]` and strictly below it whenever the maximum is unique. At least one pair is therefore always
flagged. A percentile answers "which pairs rank highest"; a contradiction is an absolute question.

### Calibration of `k`

200 random corpora per cell, `N = 12` items (66 pairs), unit vectors, one near-collinear pair
injected for the detection column.

| dim | k = 3.0 | k = 4.0 | **k = 5.0** |
|---|---|---|---|
| 16 | detection 97%, FP 0.035 | detection 40% | detection 3% |
| 128 | FP 0.115 | detection 100%, FP 0.000 | **detection 100%, FP 0.000** |
| 384 | FP 0.210 | FP 0.020 | **detection 100%, FP 0.000** |
| 768 | FP 0.180 | FP 0.010 | **detection 100%, FP 0.000** |
| 1536 | FP 0.235 | FP 0.015 | **detection 100%, FP 0.000** |

At realistic embedding dimensionality (≥128), `k = 5.0` gives zero false positives on a clean corpus
*and* 100% detection of the injected pair. At `dim = 16` the estimator fails because the random
cosine spread is roughly `1/√d ≈ 0.25`, so the fence exceeds 1.0 — the top of the cosine range — and
nothing can cross it. The detector therefore refuses to derive a threshold below `MIN_DIMENSIONS = 128`
and returns `low_dimensionality`.

Minimum corpus, measured at dim 768, `k = 5.0`, 300 corpora per size: false positives per corpus are
0.087 at N=3, 0.007 at N=6, and 0.000 from N=7. `MIN_BACKGROUND_PAIRS = 15` (N ≥ 6) is the floor;
below it the detector returns `insufficient_corpus` rather than guessing.

## Consecuencias positivas

- The default threshold is backed by a reproducible measurement instead of a chosen constant.
- The detector can report "no conflicts", which a percentile-based one structurally cannot.
- Being a relative fence, it adapts to whatever embedding model the operator configures, rather than
  hardcoding a similarity value tied to one model's geometry.
- Existing memory is never mutated, so a false positive costs a row in `memory_conflicts` and an
  event — never data.
- Every response reports the observed score, the effective threshold, its source and the compared
  pair count, so the operator can recalibrate against their real model.

## Consecuencias negativas

- **The calibration used isotropic random vectors, not real text embeddings.** Real embeddings are
  anisotropic: they cluster, with a baseline cosine around 0.3–0.5. MAD adapts because it measures
  dispersion around the observed median rather than an absolute value, but this has **not** been
  validated against a real model, and that is the principal residual risk of this decision. It
  should be revalidated as soon as an `api` or `gateway` embedding provider is configured.
- Pair enumeration is `O(N²)` over the project's indexable embeddings. That is fine at the current
  corpus size and would need revisiting for large projects.
- `k` is still a number. It is measured rather than invented, and configurable, but a different
  embedding family could justify a different value.
- A corpus whose items are genuinely all near-duplicates has low dispersion and a tight fence, so it
  can report many conflicts. That is arguably correct behaviour, but it is worth knowing.

## Alternativas consideradas

### Percentile of the corpus distribution

Rejected on measurement — see the table above. It always fires, so the "unrelated memories produce
no false positive" requirement is unsatisfiable under it.

### `max(absolute_floor, percentile)`

Rejected. It works, but the floor is exactly the invented constant this decision set out to avoid,
and it carries the model dependence the relative fence removes.

### Reusing `index.search_embedding` scores

Rejected. Those are raw inner products on `float32` from a possibly unnormalized provider, ordered
by an unstable `argsort`. A threshold over that quantity would not be comparable across providers.

### A lexical fallback when embeddings are unavailable

Rejected explicitly. A silent lexical path would report conflicts that mean something different
from the semantic ones, under the same field names. Without embeddings the detector degrades and
says so.

## Impacto

- **Backend:** new `memory_retrieval/conflicts.py`; `MemoryRepository.list_conflict_candidates`,
  `record_memory_conflict` and `list_memory_conflicts`; `memory_conflicts` table in schema phase 69.
- **API:** `POST /api/v1/memory/conflicts/detect` (mutating, behind `require_write`) and
  `GET /api/v1/memory/conflicts`.
- **Datos:** append-only detections with a unique key per normalized pair, so repeated runs refresh
  a conflict instead of duplicating it. No memory item is modified.
