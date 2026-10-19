# Bulk firm payment service

A web service that takes one bulk payment request from a firm and applies it **all or nothing**. If the payer's
balance covers the sum of the payments, it stores every payment, debits the payer, credits each payee and returns
`201`. Otherwise nothing changes and it returns `422`. It is correct when several load-balanced instances run at once.

Python 3.11 · FastAPI · SQLAlchemy 2 Core + psycopg 3 · PostgreSQL 16 · nginx · pytest.

Detailed design: [docs/TECH_SPEC.md](docs/TECH_SPEC.md). Step-by-step build log: [docs/IMPLEMENTATION_PLAN.md](docs/IMPLEMENTATION_PLAN.md).

---

## How to run and verify

Prerequisites: Docker (with Compose v2), `make`, `curl`, Python 3.11 (for the tests and the demo scripts' JSON
formatting).

```bash
git clone https://github.com/NarekEVN/taxdome-test.git && cd taxdome-test
make install          # .venv + dev dependencies, and .env created from .env.example
make up               # Postgres + 2 app instances + nginx on http://localhost:8080
make demo             # the assignment's sample request through nginx, checks the balances
make concurrency-demo # 40 parallel payments across both instances, checks nothing overdraws
make test             # 154 tests: unit + integration against the real Postgres
```

`make` alone lists every target. `make reset` deletes the database volume and starts fresh.

### Expected result of `make demo`

| Firm                | Before     | After      |
|---------------------|------------|------------|
| Pinecrest CPA Group | $50,000.00 | $36,748.75 |
| Lopez Bookkeeping   | $500.00    | $1,700.75  |
| Nair Tax Services   | $2,000.00  | $14,050.50 |

Then a request Lopez can't cover gets `422 insufficient_funds`, and the balances stay the same. The script exits
non-zero if any balance is off.

### By hand

```bash
curl -i -X POST localhost:8080/bulk-payments -H 'Content-Type: application/json' -d @scripts/sample_request.json
curl localhost:8080/firms/3f1c9a2e-7b4d-4c1e-9a55-2d8e6f0b7c41     # balance_cents: 3674875
```

Every response carries `X-Instance: <host>:<pid>`, so you can see which instance and worker served it.

Safe retries: send an `Idempotency-Key` header and repeat the request. The repeat returns the same `201` body with
`Idempotent-Replayed: true` and doesn't pay again, even when it lands on the other instance:

```bash
curl -i -X POST localhost:8080/bulk-payments -H 'Content-Type: application/json' -H 'Idempotency-Key: demo-1' \
  -d @scripts/sample_request.json        # run twice: second response has Idempotent-Replayed: true
```

## The approach

### The hard part: concurrency across instances

Instances share nothing but the database, so the database is the only place that can coordinate them. No
in-process locks, no Redis. Each request is **one transaction** (READ COMMITTED) with **pessimistic row locks taken in a
fixed global order** ([bulk_payment_service.py](app/services/bulk_payment_service.py), [firm_repository.py](app/repositories/firm_repository.py)):

1. `SELECT id, uuid, balance_cents FROM firms WHERE uuid IN (payer, payees...) ORDER BY id FOR UPDATE`
2. Check that the payer and every payee exist, and that `balance >= total`. Otherwise raise and roll back → `422`.
3. Insert all payments in one statement, `RETURNING id`, in request order.
4. `UPDATE firms SET balance_cents = balance_cents - total WHERE id = payer`
5. Credit all payees in one `UPDATE ... FROM (VALUES ...)`, with duplicate payees summed first.
6. `COMMIT` → `201`.

Why this holds up:

| Race | What prevents it |
|------|------------------|
| Double spend / overdraft: two requests from one payer read the same balance | The payer row is locked before its balance is read. The second request waits, then sees the new balance. |
| Lost credit: two payers credit one firm | All writes are relative (`balance = balance ± x`) on rows we hold locks on. |
| Deadlock: A pays B while B pays A | Every transaction locks its rows in ascending `id` order in a single statement, so no wait cycle can form. |
| Partial write: crash halfway | Single transaction. |
| A logic bug anyway | `CHECK (balance_cents >= 0)` in the schema makes an overdraft impossible to commit. |

Deadlock (`40P01`) and serialization failure (`40001`) are retried up to 3 times with 10–50 ms jitter, as a safety net.
If they keep failing, the response is `503` with `Retry-After: 1`. Business rejections are never retried. Locks are per
firm row, so unrelated firms never wait on each other.

Alternatives considered (details in [TECH_SPEC §6.3](docs/TECH_SPEC.md)):
- **Conditional `UPDATE ... WHERE balance >= total`:** correct for the payer, but the payee rows still get locked
  in arbitrary order, so cycles can deadlock.
- **`SERIALIZABLE` with retries:** correct, but aborts more often under contention.
- **Optimistic version column:** needs a schema change and retry loops.
- **Advisory locks:** protect only the payer.
- **Redis or app-level locks:** extra infrastructure, and the database still has to be right.

### Layout

```
app/
  api/            routes/ (health, firms, bulk_payments), error_handlers, dependencies, middleware
  services/       bulk_payment_service: business rules, transaction boundary, retries
  repositories/   firm, payment, idempotency repositories: every SQL statement, take a connection, never commit
  models/         SQLAlchemy Core tables (db/001_schema.sql stays the DDL source of truth)
  schemas/        Pydantic request/response models and shared field types
  core/           config (env only), database engine, domain exceptions
  utils/          money: dollar string ⇄ integer cents
db/               000 test DB, 001 schema, 002 seed (run by Postgres on first start)
tests/unit/       no database
tests/integration/ real Postgres (payments_test), fresh seed for every test
scripts/          demo.sh, concurrency_demo.sh, reset_db.sh, sample_request.json
```

Dependencies only point down: routes → services → repositories → DB. Services know nothing about HTTP. They raise
domain errors, and `api/error_handlers.py` turns those into JSON.

### API

`POST /bulk-payments`: the body is exactly the assignment's format. `201` returns the created payments in request
order. The optional `Idempotency-Key` header makes retries safe (below).

```json
{ "payer_firm_uuid": "3f1c…", "total_amount_cents": 1325125,
  "payments": [{ "id": 1, "payee_firm_uuid": "e5f1…", "amount_cents": 625000, "description": "Overflow returns, August 2026" }, …] }
```

Every rejection has the same shape: `{"error": {"code": "...", "message": "...", "details": [...]}}`.

| Status | `code` | When |
|--------|--------|------|
| 422 | `validation_error` | Malformed JSON, wrong types, unknown or missing fields, bad amount or UUID, 0 or more than 1000 payments, empty description or one with control characters |
| 422 | `unknown_payer_firm` / `unknown_payee_firm` | UUID not in `firms` (`details` lists the unknown payees) |
| 422 | `self_payment` | A payee is the payer |
| 422 | `insufficient_funds` | Balance < sum of amounts: the denial the assignment asks for |
| 422 | `idempotency_key_reused` | This payer already used the `Idempotency-Key` with a different request |
| 503 | `service_unavailable` | Database unreachable, a busy row still locked after 5 s, a statement over 15 s, no free connection, or deadlock retries used up |
| 413 / 502 / 504 | `payload_too_large` / `bad_gateway` / `gateway_timeout` | From nginx: body over 2 MB, no instance answered, or no answer in 60 s. For 502/504 the outcome is unknown: retry with the same `Idempotency-Key`. |

Helper endpoints for verification: `GET /health` and `GET /firms/{uuid}` (balance lookup without psql).

**Idempotency-Key** (optional, 1–255 printable ASCII characters). The key is checked inside the payment
transaction, right after the payer row is locked:
- **Same key, same request:** the stored result is replayed with `Idempotent-Replayed: true`. Nothing is paid again,
  even if the balance has since run out.
- **Same key, different request:** `422 idempotency_key_reused`.

Other rules:
- **Keys are scoped per payer.**
- **Only successful requests are stored.** A rejected request can be retried with the same key.
- **No race between concurrent same-key requests:** they queue on the payer's lock, so the second one sees the first
  one's result.
- **"Same request" means the same after normalization:** `"300"` = `"300.00"`, and UUIDs are case-insensitive.

Details: [TECH_SPEC §6.6](docs/TECH_SPEC.md).

## Testing

154 tests. `make test` runs them in about 3 s against a real Postgres. SQLite would not do, because the row-locking
semantics are the point.

| File | What it proves |
|------|----------------|
| `unit/test_money.py` | Exact dollar → cents parsing, with no floats. Rejects `1.234`, `.5`, `1e3`, `5\n`, `"٣"` (non-ASCII digit), numbers instead of strings, zero, 13+ digits. |
| `unit/test_schemas.py`, `unit/test_error_handlers.py`, `unit/test_config.py` | Every validation rule; the error shape for every status; `DATABASE_URL` is required. |
| `integration/test_bulk_payment_service.py` | The assignment sample, ids in request order (200 payments), exact-balance payment, one cent short, unknown payer and payees, self-payment, the DB `CHECK` stopping a direct overdraft, retries on deadlock, giving up after 3 attempts, no retries for business errors. |
| `integration/test_bulk_payments_api.py`, `test_validation.py` | The same over HTTP: exact response bodies; every `422` code leaves the DB untouched; sending the sample 4 times gives 201, 201, 201, 422. |
| `integration/test_concurrency.py` | 20–50 threads, each on its own connection (= separate instances), released together by a barrier. **No overdraft:** $100 balance, 20 × $10 at once → exactly 10 succeed. **No deadlock:** A↔B cycles, 50 requests, all 201, zero retries. **Money is conserved:** random bulk requests → total unchanged, and each firm's balance matches its payment rows. **Lock order:** `EXPLAIN` of the real query shows `LockRows` above `Sort (id)`. |
| `integration/test_idempotency.py` | A repeat pays once; normalized-equal bodies count as the same; a different body → 422; keys are scoped per payer; replay works after funds run out; rejections aren't stored; **20 concurrent requests with the same key → 1 payment, 19 replays**; replay headers and key validation over HTTP. |
| `integration/test_models.py` | The Core table definitions match the real schema. |

**Do the concurrency tests catch anything?** I swapped the lock query for weakened versions:
- **Without `FOR UPDATE`, all three race tests fail.** Overdrafts are stopped only by the `CHECK` constraint, as
  500s. A↔B cycles deadlock and use up the retries.
- **Without `ORDER BY id`, the race tests still pass.** One statement over 3 rows scans in the same order every
  time. That's why the `EXPLAIN` test exists, and it does fail without the `ORDER BY`.

## Decisions and deviations

- **Money as `BIGINT` cents** instead of the assignment's `INTEGER`. A 32-bit int caps at about $21.4M, which a big
  practice's balance can pass.
- **Schema additions:** `CHECK (balance_cents >= 0)`, `CHECK (amount_cents > 0)`, `CHECK (payer <> payee)`, foreign
  keys, `UNIQUE (uuid)` and `payments.created_at`. They only add integrity and audit info; no column was changed or
  removed.
- **All input problems return `422`**, told apart by `code`. That's simpler for clients and matches FastAPI's default.
  The alternative would be 400 for malformed JSON and 404 for an unknown payer.
- **`POST /bulk-payments`:** a plural noun, hyphenated, per REST naming conventions.
- **Payment ids in request order** come from SQLAlchemy's `sort_by_parameter_order=True`. Postgres doesn't promise
  `RETURNING` order for multi-row inserts on its own.
- **No connection strings in code.** `DATABASE_URL` is a required setting with no default. Compose reads every
  credential from `.env`, with generic `postgres` fallbacks.
- **uvicorn `--workers 2`, not gunicorn.** gunicorn's `UvicornWorker` is deprecated, and uvicorn's own supervisor
  restarts dead workers.
- **Idempotency-Key stores successes only,** with no `response_status` column (every stored result is a `201`). A
  rejected request wrote nothing, so a retry should re-evaluate it rather than replay the rejection.
- **Database timeouts end every request before nginx gives up:** lock 5 s, statement 15 s, pool checkout 10 s, all
  under nginx's 60 s. A request either commits or fails with a clear 503; it never commits after the client was told
  504.
- **Dependencies are fully locked.** `requirements.lock` pins every runtime package, transitive ones included. It is
  generated by `make lock` in the same base image as production, and a unit test fails if it falls behind
  `requirements.txt`.
- **nginx never resends a POST** to the second instance once it reached the first, so a slow payment can't be
  applied twice.

## Issues I ran into

- **The Postgres healthcheck reported healthy before the seed had run.** While the init scripts run, Postgres runs a
  temporary server that listens only on the Unix socket, and `pg_isready` checked the socket. Fixed by checking over
  TCP (`-h 127.0.0.1`).
- **A code review turned up four defects, now fixed and covered by tests:**
  - **No database timeouts.** A request blocked on a locked row waited past nginx's 60 s timeout. The client got a
    504, and the payment committed afterwards anyway. Reproduced, then fixed with lock, statement and pool timeouts
    (now 503 after 5 s, and nothing commits later).
  - **A NUL character in a description caused a 500**, because Postgres `TEXT` can't store it. It is now a 422.
  - **nginx's default 1 MB body limit was below the API's own maximum request** (1000 payments × 1000 characters ≈
    1.1 MB), and its 413/502/504 pages were HTML. The limit is now 2 MB, and all three pages use the API's JSON error
    shape.
  - **Transitive dependencies weren't pinned.** A fresh build already resolved `starlette` 1.9.0, while the tested
    venv had 1.8.0. Fixed with `requirements.lock`.
- **Single-file bind mounts go stale.** After `nginx.conf` was replaced on the host, the nginx container still saw the
  old file length and refused to reload. Recreating the container (`docker compose up -d --force-recreate nginx`)
  fixes it.
- **Python's `\d` matches non-ASCII digits,** so `"٣"` passed a naive amount regex, and `$` lets a trailing `\n`
  through. Fixed with `[0-9]` and `fullmatch`.
- **The concurrency tests couldn't tell sorted from unsorted locking** (see Testing). Fixed by asserting on the query
  plan instead.
- **Dependencies moved under me:** Starlette's test client now wants `httpx2`, and `uvicorn.workers` is deprecated.
  I followed both.
- **Init scripts run only on an empty volume,** so changes to `db/*.sql` need `make reset`. Likewise, Postgres
  ignores changed `POSTGRES_*` values once the volume exists.
- **`cp -n` exits non-zero on macOS** when the target exists, which broke an idempotent `make install`. Replaced with
  `test -f .env || cp …`.
- **Right after startup, nginx sent the first few requests to one instance.** It didn't recur and nothing was logged.
  Later runs split about evenly.

## Assumptions

1. Balances start non-negative and can never go negative.
2. A firm can't pay itself.
3. Duplicate payees in one request are allowed (the sample pays Nair twice). Each entry is its own row; credits are
   summed.
4. `description` is required, 1–1000 characters after trimming, with no control characters other than tab, newline
   and CR.
5. `amount` must be a JSON string; JSON numbers are rejected to avoid float rounding. At most 2 decimals, at most 12
   integer digits, greater than 0.
6. UUIDs are matched case-insensitively (lowercased) and must be in the standard hyphenated form.
7. At most 1000 payments per request (`MAX_PAYMENTS_PER_REQUEST`).
8. Authentication is out of scope. In production the payer must come from the authenticated caller, never from the
   body.
9. USD only.

## Possible improvements

- **Expire idempotency keys** (e.g. after 24 h) with a cleanup job. They are kept forever today.
- **Authentication:** derive `payer_firm_uuid` from the caller's identity.
- **A `bulk_payments` batch table,** so the rows of one request are grouped for audit and reporting.
- **A double-entry ledger** (immutable entries, balances derived or cached) instead of mutable balances.
- **Hot-account contention:** many payers crediting one big firm queue up on its row. Options are sub-balances or
  applying credits asynchronously.
- **Operations:** Alembic migrations, structured logs with request IDs, metrics (latency, 422 rate, retry count), rate
  limiting.

## How I worked, and AI usage

- **Tools:** Claude Code (desktop app) for planning and implementation.
- **Workflow:** the tech spec and a step-by-step implementation plan came first (drafted with Claude Code, then
  reviewed and edited by me). Then I built one plan step per commit. Each step ended with its tests, lint and a manual check, and I reviewed and committed every step myself. The
  commit history follows the plan's steps.
- **Where I changed course on the AI's output:**
  - The endpoint path, renamed to follow REST conventions.
  - A full package split (api / services / repositories / models / schemas / core / utils) instead of flat modules.
  - No database URL in code, not even as a default.
  - No literal credentials in `docker-compose.yml`.
- **Logs:** the raw transcripts are kept private. [ai-logs/README.md](ai-logs/README.md) summarizes the sessions:
  what the AI did in each phase, where I steered it, and the problems it caught.

## Two quick questions

**How much time did you spend on this task?** 3h to 6h

**How proud are you of your work?** Good enough
