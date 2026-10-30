# SQLite tables: who writes them, who reads them, what would break

The registry lives in `app/engine/schema_registry.py` and is re-checked by
`tests/test_schema_registry.py`. This document is the same data in prose for a
reviewer who is asking one question — *may I drop this table?* — and every row
below is asserted equal to the module, so the two cannot disagree.

## How this was measured

`ztockz.db` was opened **read-only** (`sqlite3.connect("file:ztockz.db?mode=ro",
uri=True)`) on 2026-10-30: 7,036,928 bytes, sha256
`44e12230037f309764157f0ea7ef8939117fbff6f35caf8e8c51a380a70593b3`, unchanged
after the reads. Row counts are a snapshot of use, not a contract — they answer
"has this table ever been written here?", which is what makes the difference
between a live-but-unused table and an abandoned one.

## The four statuses

* `written` — at least one `INSERT`/`UPDATE`/`DELETE` in `app/` names the table.
* `read-only` — something reads it and nothing in `app/` writes it: the rows
  arrive from elsewhere (Alembic, a script, another process), so `app/` holds a
  `SELECT` at most — for a table with an `external_owner`, none at all.
* `dead` — no SQL in `app/` reads it and none writes it, and nothing outside
  `app/` owns it. A leftover.
* `reserved` — declared by a migration for a feature that has not landed. No
  code touches it yet; dropping it would break a consumer that is not in this
  repository. **Nothing is `reserved` today**; the vocabulary keeps the case
  nameable when it appears.

A table that is written but never read — `instruments` is one — is still
`written`; its note records the asymmetry.

## The registry

<!-- registry:start -->
| Table | Status | Rows | Evidence | If dropped |
| --- | --- | --- | --- | --- |
| `alembic_version` | read-only | 1 | `scripts/fundamentals-snapshot.py:373` | `alembic upgrade` cannot tell head from an unknown revision and refuses to run; `scripts/fundamentals-snapshot.py` cannot check the schema it writes into. |
| `jobs` | written | 0 | `app/jobs/repository.py:179`<br>`app/jobs/repository.py:220`<br>`app/jobs/repository.py:205` | The request lifecycle (submit, poll, cancel, retry, retention) loses its only store: `POST /markets/enrich` cannot create a job and `GET /requests/{id}` returns 404 for everything. |
| `job_batches` | written | 0 | `app/jobs/repository.py:713`<br>`app/jobs/repository.py:753`<br>`app/jobs/repository.py:782` | A retried job restarts the whole universe instead of resuming at the batch that failed, and the partial rows already fetched are orphaned. |
| `quotes` | written | 0 | `app/jobs/quotes.py:107`<br>`app/jobs/quotes.py:145`<br>`app/jobs/quotes.py:126` | A submitted job can no longer carry the plan it was sold: `POST /markets/enrich` loses quote consumption, so single-use enforcement and the quote-to-job link disappear. |
| `job_artifacts` | written | 0 | `app/jobs/repository.py:574`<br>`app/jobs/repository.py:601` | The export manifest (path, sha256, row count) cannot be served, so every download has to regenerate the bundle and the retention sweeper loses its blast radius. |
| `verification_rejections` | written | 0 | `app/jobs/repository.py:542`<br>`app/jobs/repository.py:535` | Per-cell verifier provenance is lost: the export's `rejections_by_rule` count becomes 0 and a rejected cell looks like a cell that was never measured. |
| `observations` | dead | 0 | `app/models.py:165` | Nothing — it is measured dead. Keeping it costs a migration's worth of schema, a row in every `sqlite_master` scan, and this question in every audit. |
| `instruments` | written | 0 | `app/resolve/seed.py:48` | The next `POST /markets/resolve` seed fails at its `INSERT`; no read path breaks, because nothing `SELECT`s from this table today. |
| `instrument_aliases` | written | 0 | `app/resolve/seed.py:72` | Same as `instruments`: the seeder's second statement fails, and no reader exists to notice the rows are gone. |
| `provider_health` | written | 4 | `app/providers/health.py:56`<br>`app/providers/health.py:142` | Every restart forgets each provider's failure count and last verdict: `GET /providers` reports `unknown` and a breaker that was open comes back closed on deploy. |
| `screens` | written | 65 | `app/screen/repository.py:196`<br>`app/screen/repository.py:265`<br>`app/api/health.py:627` | `GET /screens`, `GET /screens/{id}` and the export have no snapshot to serve, and the health probe's `screens` count becomes an error instead of a number. |
| `screen_rows` | written | 1300 | `app/screen/repository.py:211`<br>`app/screen/repository.py:271`<br>`app/screen/repository.py:294` | A snapshot renders as a header with zero rows and the export bundle comes out empty, while `screens` still claims a total count. |
| `screen_facts` | written | 3831 | `app/screen/facts.py:448`<br>`app/screen/facts.py:491`<br>`app/screen/facts.py:474` | Selections lose `short_float`, `insider_own` and `inst_own`; the join degrades to `null` by design, so nothing raises — the cells just go blank. |
| `fundamentals_snapshots` | written | 3902 | `app/fundamentals/snapshot.py:890`<br>`app/fundamentals/snapshot.py:935`<br>`app/fundamentals/snapshot.py:919` | Selections lose `pe_forward`, `ps` and `payout_ratio`; again a silent `null` rather than an error, which is why the loss would go unnoticed. |
<!-- registry:end -->

## What the numbers say

Eight of the fourteen tables are empty, for three different reasons — which is the
whole point of separating status from row count:

* `jobs`, `job_batches`, `quotes`, `job_artifacts`, `verification_rejections`
  are **live and unexercised**: every one of them has a writer in `app/`, and
  zero rows means this database has never served a request.
* `instruments` and `instrument_aliases` are **write-only and unexercised**: the
  seeder only runs when `POST /markets/resolve` or `/markets/estimate` asks for a
  reference, and it has never run against this file.
* `observations` is **dead**: it has no writer and no reader anywhere in the
  tree.

## Migrations versus the live database

No discrepancy. `alembic upgrade head` creates thirteen tables across
`0001_init` (9: `jobs`, `job_batches`, `quotes`, `instruments`,
`instrument_aliases`, `observations`, `verification_rejections`,
`job_artifacts`, `provider_health`), `0002_screen` (2: `screens`,
`screen_rows`), `0003_fundamentals` (1) and `0005_screen_facts` (1);
`0004_field_sources` only adds the `fundamentals_snapshots.field_sources`
column. Alembic adds `alembic_version` itself, which makes fourteen, and the
live file has all fourteen. A future migration that creates a table without a
registry entry fails `test_every_table_in_the_schema_is_classified`; a table in
the registry that no migration creates fails the same test from the other side.

## What the scanner cannot see

`scan_table_usage` parses `app/**.py` with `ast` and reads string constants, not
raw text. That catches SQL built as a module constant (`app/resolve/seed.py`),
which a scan of `execute(...)` call sites would miss. It does **not** catch:

* SQL assembled at runtime — `"SELECT * FROM " + name`, `"... %s" % table`;
* an f-string placeholder — `f"DELETE FROM {table} WHERE job_id = :job_id"` in
  `app/jobs/repository.py` (the retention purge) names no table; the three
  tables it deletes are registered from the *literal* statements in the same
  module, which is why none of them is misclassified;
* a table name held in a variable and handed to `text()`;
* schema probes (`SELECT name FROM sqlite_master`, `PRAGMA table_info(x)`) —
  they ask whether a table exists, not what it holds, so they never count as a
  read;
* writes performed by Alembic (`alembic_version`) or by `scripts/`.

Where the scanner is blind, the registry says so with `external_owner` or with a
note, and its evidence is manual. The tests hold both halves to account: every
citation must exist and name its own table, every table the scanner sees must
carry the status the scanner derives, and no table may be called `dead` while
any SQL mentions it.

## Keeping it honest

```console
$ cd /home/ubuntu/ztockz && .venv/bin/pytest tests/test_schema_registry.py -o addopts="" -q
```

The suite fails when: a migration adds a table without an entry (coverage), a
citation's line moves or stops naming its table (grounding), a status stops
following from the scanner (agreement), SQL names a table outside the registry
(drift in either direction), or this document's block disagrees with `TABLES`.
