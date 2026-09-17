# ADR-002: Closed taxonomy for `memory_items.kind`

## Estado: Aceptada

## Contexto

`memory_items.kind` is declared `TEXT NOT NULL` with no constraint
(`local_control_center/shared/migrations.py:239`). The HTTP contract accepted any string and
defaulted to `note`, so the column carried no contract at all: two writers could disagree on
spelling and nothing would notice.

The taxonomy was supposed to be derived from what the system actually stores. It could not be,
and that shaped this decision:

- **The table is empty.** A sweep of 1607 SQLite files across the repository and `~/.claude`,
  including the operational database at `~/.claude/local-control-center/platform.sqlite`, returned
  zero rows in `memory_items`. `SELECT kind, COUNT(*) FROM memory_items GROUP BY kind` yields the
  empty set. There are no production values to preserve and no data to migrate.
- **`kind='lesson'` is a read contract, not live data.** `threads/memory_recall.py:133` filters
  `WHERE kind = 'lesson'` to build the thread memory panel, and `threads/contracts.py:424` documents
  it. No production code path writes that value — only `tests_py/test_thread_memory_recall.py`. The
  panel's other source, `project_lessons`, has no writer either; the lessons that production really
  records live in `self_improvement_lessons`, which the panel does not read.
- **The only production writer** of `memory_items` is `POST /api/v1/memory`
  (`memory_retrieval/commands.py:35`), which writes `body.get("kind", "note")`.

So the evidence supports exactly two values. Anything else would be an aspirational list with no
consumer.

## Decisión

Declare `MemoryKind = Literal["note", "lesson"]` in `memory_retrieval/models.py` and apply it to
both `MemoryCreateRequest.kind` (the write contract) and `MemoryItemRecord.kind` (the read
contract). This mirrors `ArtifactKind` in `evidence/models.py:16`, which is likewise applied to
both the ingest request (`:153`) and the record (`:214`).

Validate at the Pydantic boundary only. **No CHECK constraint in SQLite.** SQLite cannot add a
CHECK to an existing table through `ALTER TABLE`; it would require a full table rebuild
(rename/create/copy/drop) on a table carrying two indexes, and the single writer already validates
at the edge. If an internal writer that bypasses the API is ever introduced, the constraint belongs
there, at that writer.

Migration phase 68 (`init_phase68_schema`) normalizes any out-of-set `kind` to `note` and preserves
the original under `metadata.legacyKind`. It never deletes a row. On a database seeded by any prior
version, every row remains readable through the typed output model.

## Consecuencias positivas

- `kind` becomes a contract instead of free text: an unknown value fails with 422 at the edge.
- Pre-existing rows stay readable and keep their original classification in metadata.
- The thread memory panel's `kind='lesson'` query keeps working unchanged, and
  `tests_py/test_thread_memory_recall.py` passes without any change to its business logic.
- Adding a value later is additive and cheap: extend the `Literal`, add a migration phase if old
  rows need remapping.

## Consecuencias negativas

- Typing the **output** model is fail-closed. `control_plane/models.py:80` embeds
  `list[MemoryItemRecord]` in `OverviewResponse`, and `control_plane/overview.py:171` feeds it rows
  unfiltered by kind. A row with an out-of-set `kind` would therefore fail response validation and
  return 500 for the whole overview, not just for the memory list. Phase 68 plus edge validation
  guarantee no such row exists today, but a future writer inserting through raw SQL would break
  overview. This is the same failure mode the `ArtifactKind` precedent already carries; consistency
  with the repository was preferred over a lenient read model.
- Without a CHECK, the database itself does not enforce the taxonomy.
- The taxonomy is narrow by construction. A consumer that needs a third class must add it
  explicitly rather than inventing a value at the call site — which is the intent, but it is
  friction.

## Alternativas consideradas

### A five-value taxonomy (`note`, `lesson`, `decision`, `constraint`, `fact`)

Rejected. Three of the five would have had no reader anywhere in the codebase. A taxonomy whose
values nothing consumes is documentation pretending to be a contract, and it would have had to be
migrated again once real consumers appeared and disagreed with the guess.

### CHECK constraint in SQLite in addition to Pydantic

Rejected for now. The cost is a full table rebuild for defense against a writer that does not
exist. The precedent for CHECK in this schema (phase 54, quota tables) applies it at table creation
time, not retrofitted.

### Strict on write, lenient on read (`MemoryItemRecord.kind: str`)

Rejected for inconsistency with `ArtifactKind`, which types the record. It would have removed the
overview failure mode noted above, at the cost of two different conventions for the same problem in
one codebase. Revisit if a non-API writer is ever introduced.

## Impacto

- **Backend:** `MemoryKind`/`DEFAULT_MEMORY_KIND` in `memory_retrieval/models.py`; migration phase
  68 and `CURRENT_SCHEMA_VERSION = 68` in `shared/migrations.py`.
- **Frontend:** the generated client's `MemoryItemRecord.kind` narrows from `string` to the union.
  `MemoryPage.tsx` only reads `item.kind` as display text, so no UI change is required.
- **Datos:** no rows added or removed; out-of-set kinds normalized with the original retained in
  `metadata.legacyKind`.
