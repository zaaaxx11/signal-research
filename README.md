# Market Enrichment Engine (MEE)

Contract-first market data enrichment service. Phase 0 = contracts, skeleton,
job lifecycle, fake provider, 92-column export contract.

**Current state: RED.** The skeleton declares the module surface with
`raise NotImplementedError`; `tests/` encodes the Fase-0 acceptance criteria.
Running the suite now fails on purpose. No business logic is implemented yet.

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
.venv/bin/python -m pytest                 # counts + failures
.venv/bin/python -m pytest -o addopts="" -q --tb=line -rf   # verbose RED evidence
```

Fase-0 gate coverage:

| Gate (02 §5) | Test |
|---|---|
| 1. migrations on a DB copy | not in this slice (implementation phase) |
| 2. `POST /markets/enrich` → 202 → `Success`, CSV header exactly 92 | `tests/test_api.py`, `tests/test_export_contract.py` |
| 3. lifecycle: transition, non-retryable failure, cancel, reap, attempts | `tests/test_job_lifecycle.py` |
| 3b. quote single-use CAS (409) / expiry (410) | `tests/test_quotes.py` |
| 4. `extra="forbid"` → 422 (knob, field, market kind) | `tests/test_contracts.py`, `tests/test_api.py` |
| 5. zero network in the suite | `tests/conftest.py`, `tests/test_no_network.py` |
| 6. import direction | `tests/test_import_direction.py` |

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
  main.py             # ASGI wiring (endpoints are stubs)
  api/                # markets, requests, providers, health
  jobs/               # ports (Protocol), errors, repository, quotes, service
  worker/runner.py    # process_job: the one execution path
  providers/          # base, registry, ladder, budget, http seam, adapters/fake
  enrich/             # orchestrator
  verify/             # pure rules + units
  export/writer.py    # csv/json/xlsx/parquet contract
  models.py, db.py    # ORM + async engine (stubs)
tests/
  spec/columns_v1.json
  fixtures/fake_universe.json
  conftest.py         # autouse no-network guard
  _import_rules.py    # AST dependency checker
  _support.py         # spec loader, ASGI client, polling helper
```

## Next step

Implement against the tests, in this order: `contracts.py` → `jobs/repository.py`
+ `jobs/quotes.py` → `api/*` + `providers/adapters/fake.py` → `export/writer.py`.
Do not weaken a test to make it pass; the tests are the contract.
