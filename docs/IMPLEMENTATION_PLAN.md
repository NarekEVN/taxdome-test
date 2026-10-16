# Implementation Plan — Bulk Firm Payment Service

> The *how/when* for [TECH_SPEC.md](TECH_SPEC.md) (the *what/why*). Requirements: [ASSIGNMENT.md](../ASSIGNMENT.md).
> Work top to bottom. Each step ends in a working, verifiable state and a suggested commit, so the commit history
> tells the story the reviewers asked for. If a step forces a design change, update TECH_SPEC.md in the same commit.

---

## Step 0 — Project scaffold

Goal: an empty but installable, lintable, testable Python project.

- [ ] Delete the PyCharm sample `main.py`.
- [ ] `.gitignore`: `.venv/`, `__pycache__/`, `.pytest_cache/`, `.ruff_cache/`, `.env`, `.idea/workspace.xml`
      (keep or drop the rest of `.idea/`, your call; most repos ignore all of it).
- [ ] Python 3.11 venv: `python3.11 -m venv .venv && source .venv/bin/activate`.
- [ ] `requirements.txt` (runtime, exact pins): `fastapi`, `uvicorn[standard]`, `gunicorn`, `sqlalchemy>=2`,
      `psycopg[binary]>=3`, `pydantic>=2`, `pydantic-settings`.
- [ ] `requirements-dev.txt`: `-r requirements.txt`, `pytest`, `httpx`, `ruff`.
- [ ] `pyproject.toml`: ruff config (line length, rule set `E,F,I,B,UP`), pytest config (`testpaths = ["tests"]`).
- [ ] Empty package `app/__init__.py`, `tests/__init__.py` (or none, with `pythonpath = ["."]` in pytest config).
- [ ] `ai-logs/` with a short `README.md` saying which tool (Claude Code) and that logs are raw exports. Export
      this and every later session there as you go; that's much easier than reconstructing them at the end.

Verify: `pip install -r requirements-dev.txt && ruff check . && pytest` (0 tests collected, exit code 5 is fine).

Commit: `chore: scaffold Python project (deps, ruff, pytest)`

## Step 1 — Database: compose, schema, seed

Goal: `docker compose up db` gives a Postgres 16 with the assignment's seed, plus an empty test database.

- [ ] `db/000_create_test_db.sql`: `CREATE DATABASE payments_test;`
- [ ] `db/001_schema.sql`, `db/002_seed.sql`: copy from TECH_SPEC §3 verbatim.
- [ ] `docker-compose.yml` with only the `db` service for now: `postgres:16`, env `POSTGRES_DB=payments`,
      user/password, volume `./db:/docker-entrypoint-initdb.d:ro`, named data volume, healthcheck `pg_isready`,
      port `5433:5432` (5433 avoids clashing with a local Postgres).
- [ ] `.env.example`: `DATABASE_URL=postgresql+psycopg://payments:payments@localhost:5433/payments` and
      `TEST_DATABASE_URL=...5433/payments_test`.

Gotcha: init scripts run **only on an empty data volume**. After editing SQL, run `docker compose down -v`.
Note: `000_create_test_db.sql` runs first (alphabetical), but `001`/`002` then run against `payments` only. The
test DB gets its schema from the test fixtures (Step 4), which is what we want.

Verify:
```
docker compose up -d db
docker compose exec db psql -U payments -d payments -c "SELECT id, name, balance_cents FROM firms ORDER BY id;"
docker compose exec db psql -U payments -l | grep payments_test
```

Commit: `feat(db): Postgres compose service, schema and seed`

## Step 2 — Money parsing (pure, test-first)

Goal: `app/money.py` with `parse_amount_to_cents(value: str) -> int`, no floats.

- [ ] Write `tests/test_money.py` first, table-driven with `pytest.mark.parametrize`, using every accept/reject
      case in TECH_SPEC §5, plus the max (`"999999999999.99"`) and a 13-digit reject.
- [ ] Implement: `re.fullmatch(r"\d{1,12}(\.\d{1,2})?", value)`, split on `.`, `int(whole) * 100 +
      int(frac.ljust(2, "0"))`, reject `== 0`. Raise `ValueError` with a clear message (Pydantic turns it into a
      field error in Step 3). Reject non-`str` explicitly so `5` (int) never gets through.
- [ ] `format_cents(cents) -> str` helper for messages/logs (`1325125 -> "13251.25"`).

Verify: `pytest tests/test_money.py -q`

Commit: `feat: exact dollar-string to cents parsing`

## Step 3 — Request/response schemas and error model

Goal: all validation from TECH_SPEC §5 runs before any DB access, and every error leaves in the §4.1 shape.

- [ ] `app/errors.py`: `DomainError(code, message, details=None)` base; subclasses `UnknownPayerFirm`,
      `UnknownPayeeFirm`, `SelfPayment`, `InsufficientFunds`; `TransientDbError` for the 503 path.
- [ ] `app/config.py`: `Settings(BaseSettings)` with `database_url`, `max_payments_per_request = 1000`,
      `db_pool_size`, `db_max_overflow`. One cached `get_settings()`.
- [ ] `app/schemas.py` (Pydantic v2, `model_config = ConfigDict(extra="forbid")` on every model):
  - `PaymentIn`: `amount: StrictStr` → validator calls `parse_amount_to_cents`, keeps cents on the model
    (e.g. a computed/private `amount_cents`); `payee_firm_uuid: str` → validator `str(uuid.UUID(v))` (lowercase
    canonical); `description: StrictStr` → strip, length `1..1000`.
  - `BulkPaymentIn`: `payer_firm_uuid` (same UUID validator), `payments: list[PaymentIn]` with
    `min_length=1`; max length checked against settings in a model validator (so it stays configurable).
  - Model validator: any payee == payer → raise so the handler emits `self_payment` (simplest is to check it in
    the route/service and raise `SelfPayment`; it keeps the `code` distinct from `validation_error`).
  - Response models: `PaymentOut`, `BulkPaymentOut` matching §4.1.
- [ ] Exception handlers (go in `app/main.py`, but can be written now):
  - `RequestValidationError` → 422 `{"error": {"code": "validation_error", "message": ..., "details": [{loc, msg}]}}`.
    This also covers malformed JSON.
  - `DomainError` → 422 with its code/message/details.
  - `TransientDbError` / `OperationalError` → 503 + `Retry-After: 1`.
  - Fallback `Exception` → 500 generic body, full traceback logged.

Verify: unit tests on the schemas directly (`BulkPaymentIn.model_validate({...})`) for: number amount, extra key,
bad UUID, empty list, 1001 items, blank description, uppercase UUID normalized.

Commit: `feat: request validation and error model`

## Step 4 — DB wiring, app skeleton, helper endpoints, test fixtures

Goal: a running app that talks to Postgres, and a test harness that gives each test a fresh seeded DB.

- [ ] `app/db.py`: `create_engine(settings.database_url, pool_pre_ping=True, pool_size=..., max_overflow=...)`,
      built in the app factory (not at import time) so tests can point it at `payments_test`.
- [ ] `app/main.py`: `create_app(settings=None)` factory, register handlers from Step 3, store engine on
      `app.state`. Routes:
  - `GET /health` → `SELECT 1` → `{"status": "ok"}` (DB down → 503).
  - `GET /firms/{uuid}` → `{"uuid", "name", "balance_cents"}` or 404.
- [ ] `tests/conftest.py`:
  - session fixture: engine on `TEST_DATABASE_URL`; drop and recreate tables by running `db/001_schema.sql`.
  - autouse function fixture: `TRUNCATE payments, firms RESTART IDENTITY CASCADE` + run `db/002_seed.sql`.
  - `client` fixture: `TestClient(create_app(test_settings))`.
  - helper `balances(engine) -> dict[int, int]` and `payment_count(engine)`.

Verify: `uvicorn app.main:app --reload` → `curl localhost:8000/health`, `curl localhost:8000/firms/3f1c9a2e-7b4d-4c1e-9a55-2d8e6f0b7c41`;
`pytest` with a smoke test for both endpoints.

Commit: `feat: app factory, DB engine, health and firm lookup endpoints`

## Step 5 — Core service: the transaction (the important part)

Goal: `app/service.py::create_bulk_payment(engine, request) -> result`, implementing TECH_SPEC §6.2 exactly.
No HTTP imports in this module.

- [ ] Pre-transaction (pure Python): compute `total_cents`, the distinct payee uuids, and per-payee credit
      aggregation (`collections.Counter` / dict). Self-payment check here if not done in Step 3.
- [ ] Inside `with engine.begin() as conn:` (one transaction, READ COMMITTED):
  1. `SELECT id, uuid, balance_cents FROM firms WHERE uuid = ANY(:uuids) ORDER BY id FOR UPDATE`
     (bind the list as a Postgres array; with SQLAlchemy `text()` + psycopg a Python list works).
  2. Map uuid → row. Missing payer → `UnknownPayerFirm`. Missing payees → `UnknownPayeeFirm(details=[...])`.
     `payer.balance_cents < total` → `InsufficientFunds`. Raising inside `begin()` rolls back.
  3. Multi-row `INSERT INTO payments (...) VALUES ... RETURNING id`. To guarantee request order, either use
     `INSERT ... SELECT ... FROM unnest(:payers, :payees, :amounts, :descs) WITH ORDINALITY ... ORDER BY ordinality
     RETURNING id` or sort the returned ids (identity values are assigned in insert order within one statement).
     Pick one and comment why.
  4. `UPDATE firms SET balance_cents = balance_cents - :total WHERE id = :payer_id`.
  5. Single credit `UPDATE ... FROM (VALUES ...)` (or `unnest(:ids, :credits)`, which avoids building SQL strings).
- [ ] Retry wrapper (§6.4): catch `sqlalchemy.exc.OperationalError` / `DBAPIError`, inspect
      `err.orig.sqlstate in {"40P01", "40001"}`, up to 3 attempts with 10–50 ms jitter, then raise
      `TransientDbError`. Never retry `DomainError`.
- [ ] Return a plain dataclass result (payer uuid, total, list of `(id, payee_uuid, amount_cents, description)`).

Verify: service-level tests calling `create_bulk_payment` directly with the test engine:
sample request → balances `3674875 / 170075 / 1405050`, 3 rows; insufficient → raises, nothing written;
exact balance → balance 0; unknown payee → raises, nothing written.

Commit: `feat: atomic bulk payment with ordered row locks`

## Step 6 — HTTP endpoint and API tests

Goal: `POST /bulk-payments` wired up, behaving per TECH_SPEC §4.1.

- [ ] Route (sync `def`): validate via `BulkPaymentIn`, call service, return 201 `BulkPaymentOut`.
- [ ] `tests/test_bulk_payments_api.py`: the assignment sample end to end (status, body, balances via
      `GET /firms/{uuid}` *and* via SQL, 3 rows, request order of ids); insufficient funds → 422
      `insufficient_funds` and DB unchanged; exact-balance → 201.
- [ ] `tests/test_validation.py`: one parametrized test per row of the §4.1 code table and each §5 rule →
      422 + expected `code`, and assert balances and payment count are unchanged.
- [ ] `scripts/sample_request.json`: the assignment's sample request, byte for byte.

Verify: `pytest -q`; manually `curl -i -X POST localhost:8000/bulk-payments -H 'Content-Type: application/json' -d @scripts/sample_request.json`.

Commit: `feat: POST /bulk-payments endpoint with API tests`

## Step 7 — Concurrency tests

Goal: prove TECH_SPEC §6 claims with real parallelism against real Postgres.

- [ ] `tests/test_concurrency.py`. Call the **service function** from a `ThreadPoolExecutor` (each worker
      checks out its own pooled connection, which is equivalent to a separate instance from Postgres' point of
      view). Engine `pool_size` ≥ number of workers. Start all workers on a `threading.Barrier`.
- [ ] Test 1, no overdraft: set a firm to 10000 cents; 20 parallel $10 payments → exactly 10 successes,
      10 `InsufficientFunds`, final balance 0, 10 rows.
- [ ] Test 2, no deadlock on cycles: A↔B, 50 mixed-direction requests (balances big enough) → all succeed, zero
      transient errors (also assert the retry counter stayed 0; expose it or log-capture it), balances match.
- [ ] Test 3, conservation: random bulk requests among the 3 seed firms (seeded `random.Random(42)`) →
      `SUM(balance_cents)` unchanged, no negatives, per-firm `initial + credits − debits == final` from `payments`.
- [ ] Sanity check the tests themselves: temporarily remove `FOR UPDATE` and confirm Test 1 fails (or the DB
      `CHECK` fires). Mention the result in the README; it's a strong signal to reviewers.

Verify: `pytest tests/test_concurrency.py -q` in < 10 s; run it 5–10 times in a row to check it isn't flaky.

Commit: `test: concurrency tests for overdraft, deadlock and conservation`

## Step 8 — Multi-instance deployment (compose + nginx)

Goal: the "multiple load-balanced instances" requirement runs locally.

- [ ] `Dockerfile`: `python:3.11-slim`, install `requirements.txt`, copy `app/`, non-root user,
      `CMD gunicorn app.main:app -k uvicorn.workers.UvicornWorker -w 2 -b 0.0.0.0:8000`.
- [ ] Compose: `app` service with `deploy.replicas: 2` (or `app1`/`app2`, which is explicit and works everywhere),
      `depends_on: db: condition: service_healthy`, `DATABASE_URL` pointing at `db:5432`.
- [ ] `nginx.conf`: `upstream app { server app1:8000; server app2:8000; }` (or Docker DNS for replicas),
      listen 8080. Add an `X-Instance` response header (hostname) from the app so the demo can show requests
      landing on different replicas.
- [ ] Mind the pool budget: replicas × workers × (pool_size + max_overflow) must stay under Postgres
      `max_connections` (default 100).

Verify: `docker compose up --build -d`, then `curl -i localhost:8080/health` a few times and check `X-Instance` alternates.

Commit: `feat: Dockerfile, 2 app replicas behind nginx`

## Step 9 — Demo scripts and Makefile

- [ ] `Makefile`: `install`, `up`, `down`, `reset` (`down -v && up`), `test`, `lint`, `fmt`, `demo`, `concurrency-demo`.
- [ ] `scripts/demo.sh`: reset DB, show balances, POST sample through nginx, show balances again, POST again to
      show the second request still succeeds (Pinecrest still has funds), then a deliberately oversized request → 422.
- [ ] `scripts/concurrency_demo.sh`: N parallel `curl`s through nginx (`xargs -P`), then print the count of 201/422
      and the final balances; the money total must be unchanged.

Verify: a fresh clone + `make up && make demo` works with no other steps (this is exactly what reviewers will do).

Commit: `chore: Makefile and demo scripts`

## Step 10 — README (written as a PR description)

Required by the assignment; reuse TECH_SPEC sections rather than rewriting them.

- [ ] Summary of the solution and approach (link TECH_SPEC §6 for the locking argument).
- [ ] How to run and verify: prerequisites, `make up`, `make test`, `make demo`, sample curl, expected balances table.
- [ ] Issues you ran into (keep notes in a scratch file *while* implementing, e.g. init scripts only on empty volume,
      insert-order guarantee, pool sizing).
- [ ] Assumptions (TECH_SPEC §10), schema deviations (§3), status-code decision (§4.1), alternatives (§6.3).
- [ ] Possible improvements (§9).
- [ ] AI usage: tools named, where each fit, link to `ai-logs/`.
- [ ] The two closing questions: time spent and how proud you are.

Commit: `docs: README`

## Step 11 — Final check, then stretch (optional)

- [ ] Fresh-clone test: `git clone` into `/tmp`, `make up && make test && make demo`.
- [ ] `ruff check . && ruff format --check .` clean.
- [ ] Every AI session exported into `ai-logs/`, unedited.
- [ ] Stretch, only if time allows: `Idempotency-Key` per TECH_SPEC §9 (separate commit, own tests: replay returns
      the same response, same key + different body → 422, concurrent same-key requests → one payment).

---

## Suggested timeline

| Block | Steps | Rough time |
|-------|-------|------------|
| Foundation | 0–2 | ~45 min |
| Validation + wiring | 3–4 | ~1 h |
| Core + API | 5–6 | ~1.5 h |
| Concurrency | 7 | ~45 min |
| Infra + demo | 8–9 | ~45 min |
| README + final check | 10–11 | ~45 min |

Steps 0→7 are strictly sequential. Steps 8–9 only depend on Step 6 and can happen in parallel with Step 7.
