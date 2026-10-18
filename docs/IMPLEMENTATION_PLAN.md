# Implementation Plan — Bulk Firm Payment Service

> The *how/when* for [TECH_SPEC.md](TECH_SPEC.md) (the *what/why*). Requirements: [ASSIGNMENT.md](../ASSIGNMENT.md).
> Work top to bottom. Each step ends in a working, verifiable state and a suggested commit, so the commit history
> tells the story the reviewers asked for. If a step forces a design change, update TECH_SPEC.md in the same commit.
> Package layout and layering rules: TECH_SPEC §7.

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
Gotcha (hit during implementation): `pg_isready` over the Unix socket reports healthy while the init scripts are still
running, because the temporary init server listens on the socket only. The healthcheck uses `-h 127.0.0.1`.
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

Goal: `app/utils/money.py` with `parse_amount_to_cents(value: str) -> int`, no floats.

- [ ] Write `tests/unit/test_money.py` first, table-driven with `pytest.mark.parametrize`, using every accept/reject
      case in TECH_SPEC §5, plus the max (`"999999999999.99"`) and a 13-digit reject.
- [ ] Implement: `re.fullmatch(r"\d{1,12}(\.\d{1,2})?", value)`, split on `.`, `int(whole) * 100 +
      int(frac.ljust(2, "0"))`, reject `== 0`. Raise `ValueError` with a clear message (Pydantic turns it into a
      field error in Step 3). Reject non-`str` explicitly so `5` (int) never gets through.
- [ ] `format_cents(cents) -> str` helper for messages/logs (`1325125 -> "13251.25"`).

Verify: `pytest tests/unit/test_money.py -q`

Commit: `feat: exact dollar-string to cents parsing`

## Step 3 — Request/response schemas and error model

Goal: all validation from TECH_SPEC §5 runs before any DB access, and every error leaves in the §4.1 shape.

- [ ] `app/core/exceptions.py`: `DomainError(code, message, details=None)` base; subclasses `UnknownPayerFirm`,
      `UnknownPayeeFirm`, `SelfPayment`, `InsufficientFunds`; `TransientDbError` for the 503 path.
- [ ] `app/core/config.py`: `DatabaseSettings` (`database_url` **required, no default**, `db_pool_size`,
      `db_max_overflow`) and `ApiSettings` (`max_payments_per_request = 1000`), each with a cached getter.
      Split so request validation never needs database config. Connection strings live only in the environment
      (`.env`, compose `environment:`), never in code.
- [ ] `app/schemas/types.py` (shared `FirmUuid`, `Amount`, `Description`) and `app/schemas/bulk_payment.py` (Pydantic v2, `model_config = ConfigDict(extra="forbid")` on every model):
  - `PaymentIn`: `amount: StrictStr` → validator calls `parse_amount_to_cents`, keeps cents on the model
    (e.g. a computed/private `amount_cents`); `payee_firm_uuid: str` → validator `str(uuid.UUID(v))` (lowercase
    canonical); `description: StrictStr` → strip, length `1..1000`.
  - `BulkPaymentIn`: `payer_firm_uuid` (same UUID validator), `payments: list[PaymentIn]` with
    `min_length=1`; max length checked against settings in a model validator (so it stays configurable).
  - Model validator: any payee == payer → raise so the handler emits `self_payment` (simplest is to check it in
    the route/service and raise `SelfPayment`; it keeps the `code` distinct from `validation_error`).
  - Response models: `PaymentOut`, `BulkPaymentOut` matching §4.1.
- [ ] `app/api/error_handlers.py`, registered by `create_app()` in `app/main.py`:
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

- [ ] `app/core/database.py`: `create_engine(get_database_settings().database_url, pool_pre_ping=True, pool_size=..., max_overflow=...)`,
      built in the app factory (not at import time) so tests can point it at `payments_test`.
- [ ] `app/models/firm.py`, `app/models/payment.py`: SQLAlchemy Core `Table`s on one shared `MetaData`, mirroring
      `db/001_schema.sql` (the SQL file stays the DDL source of truth).
- [ ] `app/repositories/firm_repository.py`: `get_by_uuid(conn, uuid)` for now (Step 5 adds the locking queries).
- [ ] `app/schemas/firm.py`: `FirmOut(uuid, name, balance_cents)`.
- [ ] `app/api/dependencies.py`: `get_engine(request)` from `app.state`, `get_connection` (one per request, read-only use).
- [ ] `app/api/routes/health.py`: `GET /health` → `SELECT 1` → `{"status": "ok"}` (DB down → 503).
- [ ] `app/api/routes/firms.py`: `GET /firms/{uuid}` → `FirmOut` or 404.
- [ ] `app/main.py`: `create_app(settings=None)` builds the engine, stores it on `app.state`, includes the routers.
- [ ] `tests/integration/conftest.py`:
  - session fixture: engine on `TEST_DATABASE_URL` (read from the environment / `.env`, no default; fail with a
    clear message if unset); drop and recreate tables by running `db/001_schema.sql`.
  - autouse function fixture: `TRUNCATE payments, firms RESTART IDENTITY CASCADE` + run `db/002_seed.sql`.
  - `client` fixture: `TestClient(create_app(test_settings))`.
  - helper `balances(engine) -> dict[int, int]` and `payment_count(engine)`.

Verify: `uvicorn app.main:app --reload` → `curl localhost:8000/health`, `curl localhost:8000/firms/3f1c9a2e-7b4d-4c1e-9a55-2d8e6f0b7c41`;
`pytest` with a smoke test for both endpoints.

Commit: `feat: app factory, DB engine, health and firm lookup endpoints`

## Step 5 — Core service: the transaction (the important part)

Goal: `app/services/bulk_payment_service.py::create_bulk_payment(engine, request) -> result`, implementing
TECH_SPEC §6.2 exactly. No HTTP imports in services or repositories.

- [ ] Pre-transaction (pure Python, in the service): compute `total_cents`, the distinct payee uuids, and per-payee
      credit aggregation (`collections.Counter` / dict). Self-payment check → `SelfPayment`.
- [ ] `app/repositories/firm_repository.py`, each function takes an open connection and never commits:
  - `lock_by_uuids(conn, uuids)`: `SELECT id, uuid, balance_cents FROM firms WHERE uuid = ANY(:uuids) ORDER BY id
    FOR UPDATE` (bind the list as a Postgres array; with SQLAlchemy + psycopg a Python list works).
  - `debit(conn, firm_id, cents)`: `UPDATE firms SET balance_cents = balance_cents - :cents WHERE id = :id`.
  - `credit_many(conn, credits)`: one `UPDATE ... FROM unnest(:ids, :credits)` (or `VALUES`) statement.
- [ ] `app/repositories/payment_repository.py`: `insert_many(conn, rows) -> list[int]`, one multi-row `INSERT ...
      RETURNING id`. To guarantee request order, either use `INSERT ... SELECT ... FROM unnest(...) WITH ORDINALITY
      ... ORDER BY ordinality RETURNING id` or sort the returned ids (identity values are assigned in insert order
      within one statement). Pick one and comment why.
- [ ] Service transaction, inside `with engine.begin() as conn:` (one transaction, READ COMMITTED):
  1. `lock_by_uuids`.
  2. Map uuid → row. Missing payer → `UnknownPayerFirm`. Missing payees → `UnknownPayeeFirm(details=[...])`.
     `payer.balance_cents < total` → `InsufficientFunds`. Raising inside `begin()` rolls back.
  3. `insert_many`, 4. `debit`, 5. `credit_many`.
- [ ] Retry wrapper in the service (§6.4): catch `sqlalchemy.exc.OperationalError` / `DBAPIError`, inspect
      `err.orig.sqlstate in {"40P01", "40001"}`, up to 3 attempts with 10–50 ms jitter, then raise
      `TransientDbError`. Never retry `DomainError`.
- [ ] Return a plain dataclass result (payer uuid, total, list of `(id, payee_uuid, amount_cents, description)`).

Verify: `tests/integration/test_bulk_payment_service.py` calling `create_bulk_payment` directly with the test engine:
sample request → balances `3674875 / 170075 / 1405050`, 3 rows; insufficient → raises, nothing written;
exact balance → balance 0; unknown payee → raises, nothing written.

Commit: `feat: atomic bulk payment with ordered row locks`

## Step 6 — HTTP endpoint and API tests

Goal: `POST /bulk-payments` wired up, behaving per TECH_SPEC §4.1.

- [ ] `app/api/routes/bulk_payments.py` (sync `def`, included in `create_app()`): validate via `BulkPaymentIn`, call service, return 201 `BulkPaymentOut`.
- [ ] `tests/integration/test_bulk_payments_api.py`: the assignment sample end to end (status, body, balances via
      `GET /firms/{uuid}` *and* via SQL, 3 rows, request order of ids); insufficient funds → 422
      `insufficient_funds` and DB unchanged; exact-balance → 201.
- [ ] `tests/integration/test_validation.py`: one parametrized test per row of the §4.1 code table and each §5 rule →
      422 + expected `code`, and assert balances and payment count are unchanged.
- [ ] `scripts/sample_request.json`: the assignment's sample request, byte for byte.

Verify: `pytest -q`; manually `curl -i -X POST localhost:8000/bulk-payments -H 'Content-Type: application/json' -d @scripts/sample_request.json`.

Commit: `feat: POST /bulk-payments endpoint with API tests`

## Step 7 — Concurrency tests

Goal: prove TECH_SPEC §6 claims with real parallelism against real Postgres.

- [ ] `tests/integration/test_concurrency.py`. Call the **service function** from a `ThreadPoolExecutor` (each worker
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

Verify: `pytest tests/integration/test_concurrency.py -q` in < 10 s; run it 5–10 times in a row to check it isn't flaky.

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

- [ ] `Makefile`: `install` (venv, deps, and `cp -n .env.example .env`), `up`, `down`, `reset` (`down -v && up`), `test`, `lint`, `fmt`, `demo`, `concurrency-demo`.
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
