# Ztockz

Contract-first market data enrichment service (formerly MEE). Phase 0 = contracts, schema,
job lifecycle, fake provider, 92-column export contract.

**Current state: GREEN.** `68 passed, 0 failed` (`evidence/phase0-green.txt`),
five consecutive identical runs. The suite in `tests/` is the contract and was
not modified: it went from `49 failed, 19 passed` at commit `16e6aae` to
`68 passed` with implementation only.

What exists today: the frozen vocabulary (`app/contracts.py`), the nine tables
plus Alembic `0001` (`app/models.py`, `alembic/`), the CAS job repository and
single-use quote store (`app/jobs/`), the deterministic fake provider reading
`tests/fixtures/fake_universe.json` (`app/providers/adapters/fake.py`), the
streaming export writer (`app/export/writer.py`), the in-process runner
(`app/worker/runner.py`) and the HTTP surface `POST /markets/enrich`,
`GET /requests/{id}`, `GET /requests/{id}/export/{fmt}`, `POST /requests/{id}/cancel`,
`POST /requests/{id}/retry`, `GET /providers`, `GET /health`.

Still stubs on purpose (later phases): `POST /markets/resolve|estimate` → 501,
`providers/{ladder,budget,http}.py`, `enrich/orchestrator.py`, `verify/*`.
`POST /markets/enrich` also answers 501 for a capability with no rung, a format
without a writer (`xlsx`, `parquet`) or a `quote_id` (Phase 1) — it refuses
before a job row exists rather than returning a `Success` that is not what was
asked for.

## Stack (pinned, no ranges)

Python **3.14.7**, Linux **aarch64**. Runtime pins live in `requirements.txt`
and `pyproject.toml` (05-framework §3.1). Install with wheels only:

```bash
python3.14 -m venv .venv
.venv/bin/pip install --only-binary=:all: -r requirements.txt
.venv/bin/pip install --only-binary=:all: "pytest==9.1.1" "pytest-asyncio==1.4.0" \
  "hypothesis==6.170.5" "time-machine==3.5.1" "openpyxl==3.1.5"
```

## Tests

```bash
.venv/bin/pytest                              # 68 passed
.venv/bin/pytest -o addopts="" -q --tb=line    # per-test lines
```

Fase-0 gate coverage:

| Gate (02 §5) | Where it is proven |
|---|---|
| 1. `alembic upgrade head` on a copy of a live DB; `PRAGMA table_info(jobs)` | `evidence/phase0-green.txt` gate 1 + gate 1b (models vs migration, no drift) |
| 2. `POST /markets/enrich` → 202 → `Success`, CSV header exactly 92 | `tests/test_api.py`, `tests/test_export_contract.py`, evidence gate 2 |
| 3. lifecycle: transition, non-retryable failure, cancel, reap, attempts | `tests/test_job_lifecycle.py` (12), evidence gate 3 |
| 3b. quote single-use CAS (409) / expiry (410) | `tests/test_quotes.py` (4), evidence gate 3 |
| 4. `extra="forbid"` → 422 (knob, field, market kind) | `tests/test_contracts.py`, `tests/test_api.py` |
| 5. zero network in the suite (and a probe proving the guard bites) | `tests/conftest.py`, `tests/test_no_network.py` |
| 6. import direction (AST, 9 synthetic violations) | `tests/test_import_direction.py` (11) |

## Database

Alembic is the only source of the schema — the app never calls
`Base.metadata.create_all`, because a second schema definition drifts silently.

```bash
alembic -x db=/tmp/copy.db upgrade head    # on a COPY, never on the live file
alembic -x db=/tmp/copy.db current
alembic downgrade base                     # drops the nine tables, reverse order
```

URL resolution in `alembic/env.py` (first hit wins): programmatic
`attributes["db_url"]` → `-x db=…` → `$MEE_DB_PATH` → `alembic.ini`.

SQLite runs with `journal_mode=WAL`, `busy_timeout=5000`, `foreign_keys=ON`
(SQLite defaults FKs **off**, which would make `ON DELETE CASCADE` a comment).

## Frozen 92-column spec

`tests/spec/columns_v1.json` is generated mechanically from
`briefs/market-engine/02-architecture.md` §3.8.1: 10 groups, 92 unique names,
ordered, plus `CORE_COLUMNS` and the `data_quality` enum.

`PLAN-market-enrichment-engine.md` §4 is an illustrative summary with different
names (`timezone`, `market_status`, `venue`, ...). It does **not** match the
frozen table; 02 §3.8.1 wins because it is the section that declares the exact
ordered names, group counts and the `CANONICAL_COLUMNS_V1` symbol.

## Layout

```
app/
  contracts.py        # the single vocabulary; imports no other app.* module
  config.py           # settings, no secrets
  auth.py             # sha256 api-key fingerprint + ApiError envelope
  runtime.py          # composition root: repository + in-process task spawner
  main.py             # ASGI wiring + one error envelope
  api/                # markets, requests, providers, health
  jobs/               # ports (Protocol), errors, repository (CAS), quotes, service
  worker/runner.py    # process_job: the one execution path
  providers/          # base, registry, ladder (Phase 2), budget (Phase 2), http seam
  providers/adapters/ # fake.py (fixture-driven, zero egress)
  enrich/             # quality.py (coverage counters), orchestrator (Phase 3)
  verify/             # pure rules + units (Phase 4)
  export/writer.py    # streaming csv/json; xlsx & parquet answer 501
  models.py, db.py    # nine tables + async engine (PRAGMAs, alembic upgrade)
alembic/
  env.py, versions/0001_init.py
tests/                # untouched contract suite
```

## Portable deployment

The engine has no host path in its code. Everything resolves from `MEE_*`
variables, then `MEE_DATA_DIR`, then `./data`:

| Variable | Default | Purpose |
|---|---|---|
| `MEE_DATA_DIR` | `./data` | root for cache, snapshot, backups, SQLite, exports |
| `MEE_ENGINE_CACHE_DIR` | `<data>/cache` | SEC document cache |
| `MEE_ENGINE_SNAPSHOT` | `<data>/engine-snapshot.json` | dataset loaded at startup |
| `MEE_BACKUP_DIR` | `<data>/backups` | `ops backup` destination |
| `MEE_DB_PATH` | `<data>/ztockz.db` | SQLite for jobs/exports |
| `MEE_SEC_RATE_LIMIT` | `10` | shared SEC requests/second (`0` disables) |
| `MEE_QUOTE_HUB` | `0` | start the free/premium QuoteHub loops |
| `MEE_QUOTE_SYMBOLS` | empty | comma list when no universe file is set |
| `MEE_QUOTE_UNIVERSE` | empty | JSON list / `{"symbols":[]}` / `{"rows":[{"name":...}]}` |
| `MEE_QUOTE_STORE_PATH` | `data/quotes.json` | atomic latest-value snapshot |
| `MEE_QUOTE_HISTORY_DIR` | empty | optional hourly snapshots; unset = no history |
| `MEE_DEPTH` | `0` | start the depth collector |
| `MEE_DEPTH_FINVIZ` | `1` | fetch Finviz; set `0` for the Finviz-off stack |
| `MEE_DEPTH_CHART` | `0` | derive performance/technical fields from Yahoo chart |
| `MEE_DEPTH_FINRA` | `1` | fetch FINRA consolidated short interest |
| `MEE_DEPTH_OFFICIAL` | `1` | fetch SSGA/NDX membership and Cboe optionable |
| `MEE_DEPTH_NEWS` | `0` | fetch Yahoo/Nasdaq RSS headlines |
| `MEE_DEPTH_SYMBOLS` | empty | depth universe when no file is set |
| `MEE_DEPTH_UNIVERSE_PATH` | empty | JSON list / `{"symbols":[]}` / `{"rows":[{"name":...}]}` |
| `MEE_DEPTH_STORE_PATH` | `data/depth.json` | atomic depth snapshot |
| `MEE_DEPTH_REFRESH_S` | `86400` | daily depth cadence |
| `MEE_DEPTH_CONCURRENCY` | `3` | per-source fetch concurrency |
| `MEE_QUOTE_FREE_REFRESH_S` | `900` | free cadence (15 minutes) |
| `MEE_QUOTE_PREMIUM_REFRESH_MIN_S` | `60` | premium cadence lower bound |
| `MEE_QUOTE_PREMIUM_REFRESH_MAX_S` | `120` | premium cadence upper bound |

### QuoteHub

`MEE_QUOTE_HUB=1` starts two background loops after startup: Yahoo fills the
free snapshot every 15 minutes, CNBC fills the premium snapshot at a random
1–2 minute cadence. `GET /market/hub?symbols=AAPL,MSFT` reads the in-memory
values without calling a provider; the response intentionally has no `source`
or `tier` field. The legacy `GET /market/quote` path remains the request-time
poller and its existing contract is unchanged.

The hub is opt-in and its public-mode gate is the same fail-closed licence
boundary as `/market/quote`; do not enable it on a public tunnel without a
licence decision.

### Depth collector

`MEE_DEPTH=1` starts a daily collector that asks Finviz, StockAnalysis and
Yahoo for every symbol, parses each source into the same field vocabulary, and
merges **per field**: the newest observation wins, and a source does not erase
a field another source owns. `GET /market/depth?symbols=AAPL,MSFT` reads the
snapshot; `source` and per-field timestamps are internal. The JSON is
`data/depth.json` by default and is written atomically once per cycle.

The depth store is separate from `quotes.json`: a slow fundamentals page must
never delay a price. Both collectors are opt-in so a bare checkout does not
start background scraping.



Docker is the reference runtime:

```bash
docker compose up -d --build           # engine on :8080, volume ztockz-data:/data
docker compose run --rm ops cache
docker compose run --rm ops backup --snapshot /data/engine-snapshot.json --keep 7
docker compose run --rm ops retention --max-age-days 30 --max-bytes 2000000000
docker compose run --rm ops smoke --base-url http://engine:8080
```

To serve a populated engine, put `engine-snapshot.json` in the volume before
starting, or build one inside the container:

```bash
docker compose run --rm engine python -m app.engine.bootstrap \
  --symbols AAPL,MSFT,NVDA --output /data/engine-snapshot.json
```

Without a snapshot the engine starts empty on purpose and `/engine/health` says
so.

Host systemd examples live in `deploy/`; they assume the app at `/opt/ztockz`
and data at `/var/lib/ztockz`, both configurable through the same variables.
They are an alternative to Docker, not a requirement.

### Multi-arch images

`.github/workflows/container.yml` builds **natively** on `ubuntu-latest`
(amd64) and `ubuntu-24.04-arm` (arm64), pushes one digest per platform, then
merges them into `ghcr.io/<owner>/<repo>:sha-<commit>` and `:latest`.

QEMU is intentionally not used: `python:3.14-slim` starts under qemu-user 8.2
but `import ssl` / `pip` segfault, so an emulated build is not evidence that the
image runs on the target. The arm64 image has been built and smoke-tested
locally (`ops smoke` true); amd64 is verified by the first CI run on a native
runner.

## Next step

Phase 1: `resolve/{intent,aliases,universe,estimator}.py`, `POST /markets/resolve`
and `POST /markets/estimate` (which creates the quote that `POST /markets/enrich`
already accepts as `quote_id`), then seed `instruments` for XNAS + XIDX.
Do not weaken a test to make it pass; the tests are the contract.
