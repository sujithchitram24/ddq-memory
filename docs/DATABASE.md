# Database design and trade-offs

The schema lives in `app/models.py`; the migration is `migrations/versions/0001_initial_schema.py`.
For each decision: what was chosen, why, and the main alternative.

## Tables at a glance

```
organizations 1──* documents 1──* document_versions 1──* processing_runs 1──* text_chunks
                                        │  active_run_id ─────────┘        1──* extracted_tables 1──* financial_facts
                                        └──* jobs
organizations 1──* eval_runs 1──* eval_results
```

| Table | One row means | Key constraints |
|---|---|---|
| `organizations` | a tenant | `api_key_hash` UNIQUE |
| `documents` | a logical document ("Northwind DDQ") | CHECK format ∈ pdf/docx/xlsx |
| `document_versions` | one exact set of bytes | UNIQUE `(org_id, sha256)`, UNIQUE `(document_id, version_number)` |
| `jobs` | one unit of background work | partial UNIQUE `(version_id, processor_version, kind) WHERE status IN ('queued','running')` |
| `processing_runs` | one complete, committed extraction of a version | UNIQUE `(version_id, processor_version)` |
| `text_chunks` | one chunk (flat or prose, size 50 or 200) | UNIQUE `(run_id, representation, chunk_size, seq)`; GIN on `tsv` |
| `extracted_tables` | a table as extracted (raw grid as JSONB) | UNIQUE `(run_id, seq)` |
| `financial_facts` | one typed cell with provenance | GIN on `tsv`; index `(run_id, row_key)` |
| `eval_runs` / `eval_results` | an evaluation and its per-question scores | UNIQUE `(eval_run_id, question_id, strategy, chunk_size)` |

## Keys and integrity

- **UUID primary keys**, generated in the app: ids are safe to show in URLs (no
  guessable counters, no leaking "you are customer #12"), and we can know an id before
  inserting (useful for writing related rows in one flush). The cost: 16 bytes and random
  insert order in B-tree indexes. Fine at this scale; UUIDv7 (time-ordered) fixes the
  ordering cost later.
- **Foreign keys with `ON DELETE CASCADE`** from everything to its parent: deleting a
  document version deletes its runs, chunks, tables and facts. No orphans.
- **`active_run_id` is deliberately NOT a foreign key.** `processing_runs.version_id → document_versions`
  plus `document_versions.active_run_id → processing_runs` would be a circular FK. We
  write both in the same transaction instead. The trade-off: integrity
  is enforced by code in one place (`persist`), not by the database. Alternative: a
  `DEFERRABLE INITIALLY DEFERRED` FK, checked at commit. A reasonable next step.
- **CHECK constraints** on status/kind columns: we used `VARCHAR + CHECK` instead of
  Postgres `ENUM`, because adding an enum value is a migration with some locking
  subtleties, while editing a CHECK is a normal migration.

## Normalised vs JSONB, the core judgement call

- **Relational columns** for anything we filter, join, sort, constrain or index:
  `org_id`, `run_id`, `row_key`, labels, `value_numeric`, `currency`, `period`,
  `validation_status`.
- **JSONB** for things always read or written whole and with variable shape:
  `extracted_tables.rows` (the raw grid), `location` (page vs sheet+cell vs bbox),
  `quality_flags`, `footnotes`, `stats`, `eval config`.
- *Why not a `table_cells` table?* We already have one, in effect: `financial_facts` is
  the cell-level, queryable form. The JSONB grid is the **fidelity copy** used to
  re-normalise without re-parsing and to show "what the parser saw".
- *Why not one table per financial concept (fees, IRRs, commitments)?* DDQs have
  hundreds of concepts and every fund labels them differently. A generic `(row_label,
  column_label, value, unit, period)` fact plus provenance handles all of them; concept
  mapping (entity resolution: "Mgmt fee" = "Management fee") is a layer on top, not a
  schema per concept.

## Provenance: every fact can answer "where did you come from?"

`financial_facts` → `run_id` → `processing_runs.processor_version` + `version_id` →
`document_versions.sha256` + `storage_key`; plus the fact's own `location` (page/table/row/col
or `Sheet!C3`), `extraction_method` (`openpyxl/table`) and `validation_status`.

Reprocessing after a parser change writes a **new run**; old runs remain until cleaned
up, so an answer given last week can still be traced to the exact rows it used.

**Confidence:** we deliberately store `validation_status` (parsed / unparsed / missing)
and `quality_flags`, not a "confidence 0.87". A number like that from an LLM is not a
calibrated probability unless you've measured it against labelled data.

## Indexes, and the plans we actually saw

Query plans captured on the evaluation database (150 chunks, 319 facts):

```
EXPLAIN SELECT ... FROM financial_facts WHERE tsv @@ to_tsquery('english','management | fee | basis');
 Seq Scan on financial_facts  (cost=0.00..27.00 rows=182)
   Filter: (tsv @@ '''manag'' | ''fee'' | ''basi'''::tsquery)

SET enable_seqscan = off;   -- same query
 Bitmap Heap Scan on financial_facts
   ->  Bitmap Index Scan on ix_facts_tsv
```

Interpretation: with 319 rows reading the whole table is cheaper than using the index,
so Postgres *correctly* ignores it. The GIN index matters at thousands of rows. Don't
"fix" a seq scan on a tiny table. Note also the stemming: `management → manag`,
`basis → basi`.

The job-claim query uses the **partial unique index** `uq_jobs_active` (it only
contains queued/running rows) as a bitmap scan, so finished jobs never slow down
claiming, however many there are.

| Index | Serves |
|---|---|
| `ix_chunks_tsv` (GIN) | flat/prose full-text search |
| `ix_facts_tsv` (GIN) | structured search |
| `ix_facts_run_row` | "fetch every fact of this row" |
| `ix_chunks_run_repr_size` | restrict search to one run + representation + size |
| `uq_jobs_active` (partial) | idempotent enqueue AND the claim query |
| `ix_jobs_claim` | fallback for claim ordering at scale |
| `ix_documents_org_created` | `GET /documents` listing per tenant |

## Full-text vs vector search

We use Postgres FTS (lexical): exact terms such as "Net IRR" or "2024" matter in finance,
and it needs no embedding model. Weakness: synonyms ("carry" vs "carried interest").
Next step: **hybrid** retrieval, adding `pgvector` embeddings in the same database
(no separate vector DB until scale or latency demands it) and merging lexical and
vector results with reciprocal rank fusion.

## Transactions and isolation

- Default isolation is **READ COMMITTED**: each statement sees data committed before it
  started. That is enough here because correctness is enforced by **constraints** and
  **row locks**, not by reading-then-deciding:
  - duplicate uploads → unique `(org_id, sha256)`
  - duplicate jobs → partial unique index + `ON CONFLICT DO NOTHING`
  - duplicate runs → unique `(version_id, processor_version)`
  - concurrent claim → `FOR UPDATE SKIP LOCKED`
  - zombie writer → `UPDATE ... WHERE locked_by = :me` (fencing)
- **Short transactions**: claim, commit; parse with no transaction open; then write
  everything in one transaction. The QA path also ends its read transaction before
  calling an LLM (`answer_question` → `session.rollback()`).
  *Bug found while building this:* that rollback also discarded evaluation rows the
  runner had added but not committed yet. Fixed by collecting results and writing once.
  Lesson: a shared session plus hidden transaction side effects is a footgun.

## Connection pooling

`pool_size=5, max_overflow=5` per process; `pool_pre_ping` detects connections killed by
a failover. Budget: `(api_pods × 10) + (worker_pods × 10) < max_connections` (RDS
default depends on instance memory). With many pods, put **PgBouncer** or **RDS Proxy**
in front (transaction pooling).

## Migrations without downtime (expand / contract)

Rolling updates mean old and new code run *at the same time* against one schema. So:
1. **Expand**: add the new column (nullable) / new table. Old code ignores it.
2. Deploy code that writes both old and new; backfill in batches.
3. Deploy code that reads new only.
4. **Contract**: drop the old column in a later release.

Never rename a column in one step. Create indexes with `CREATE INDEX CONCURRENTLY` on
big tables. Migrations run as a separate Kubernetes Job *before* the rollout
(`k8s/migrate-job.yaml`).

## Multi-tenant isolation

Today: `org_id` on every tenant row and in every query; cross-tenant access returns
404 (`tests/test_tenancy.py`). One forgotten `WHERE` would leak data, so production adds
**Row-Level Security**: `ALTER TABLE financial_facts ENABLE ROW LEVEL SECURITY;
CREATE POLICY tenant ON financial_facts USING (org_id = current_setting('app.org_id')::uuid);`
with the app running `SET LOCAL app.org_id = ...` per transaction and connecting as a
role that is not the table owner. Very large tenants could later get their own
schema/database.

## Representative SQL

```sql
-- latest processed version of each requested document (DISTINCT ON)
SELECT DISTINCT ON (dv.document_id) dv.active_run_id, d.display_name
FROM document_versions dv JOIN documents d ON d.id = dv.document_id
WHERE dv.org_id = :org AND dv.active_run_id IS NOT NULL
ORDER BY dv.document_id, dv.version_number DESC;

-- best table ROWS for a question (rank facts, group by row)
SELECT run_id, table_id, row_key, max(ts_rank_cd(tsv, q)) AS score
FROM financial_facts, to_tsquery('english', :q) q
WHERE org_id = :org AND run_id = ANY(:runs) AND tsv @@ q
GROUP BY run_id, table_id, row_key ORDER BY score DESC LIMIT 3;

-- facts the parser could not type (review queue)
SELECT table_title, row_label, column_label, value_raw
FROM financial_facts WHERE validation_status = 'unparsed';

-- operational health: queue depth and oldest waiting job
SELECT status, count(*), min(created_at) FROM jobs GROUP BY status;
```
