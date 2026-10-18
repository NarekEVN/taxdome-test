# AI usage summary

**Tool:** Claude Code (desktop app), as a pair programmer for planning, implementation, tests and documentation.

The raw transcripts are kept private. This is a summary of how the work went.

## How the sessions were run

- The work followed [docs/TECH_SPEC.md](../docs/TECH_SPEC.md) (what and why) and
  [docs/IMPLEMENTATION_PLAN.md](../docs/IMPLEMENTATION_PLAN.md) (how and when).
- It was built one plan step at a time. For each step, the AI proposed and wrote the code and tests and ran them
  (unit, integration against real Postgres, lint, and a manual check against the running app). Then it reported what
  changed.
- I reviewed every step and asked for changes where needed. I committed each step myself, so the commit history
  follows the plan's steps.

## What the AI did

| Phase | Work |
|-------|------|
| Spec and plan | Renamed the endpoint to `POST /bulk-payments` to follow REST naming. Turned the spec and the assignment into a step-by-step implementation plan. |
| Steps 0–3 | Project scaffold and pinned dependencies; Postgres in compose with schema and seed; exact dollar → cents parsing; request validation and a uniform error format. |
| Steps 4–6 | App factory, database engine, Core table models, repositories, the locking transaction with retries, and the HTTP endpoint, each with integration tests. |
| Step 7 | Concurrency tests (overdraft, deadlock cycles, money conservation). Weakened the lock query to confirm the tests catch races, then added a query-plan test where they didn't. |
| Steps 8–9 | Dockerfile, two app instances behind nginx, Makefile and self-checking demo scripts. |
| Step 10 | README written as a PR description. |
| Steps 11–12 | Fresh-clone check of the whole workflow. Stretch goal: `Idempotency-Key` header (stored with the payments in the same transaction, scoped per payer, race-free through the payer row lock), with tests for concurrent same-key requests. |

## Where I steered or corrected it

- **Structure:** asked for separate packages (api / services / repositories / models / schemas / core / utils)
  instead of flat modules.
- **Configuration:** rejected a default database URL in code, then literal credentials in `docker-compose.yml`. All
  values now come from `.env`, with generic placeholder defaults in compose.
- **Style:** trimmed explanatory comments and module docstrings to keep the code lean.
- **Review gates:** asked for plans and decisions before implementation, and checked each step before moving on.

## Where the AI caught problems itself

- **Postgres healthcheck race:** it reported healthy before the seed had run.
- **Amount-parsing edge cases:** non-ASCII digits and trailing newlines passing a naive regex.
- **Deprecated libraries:** `httpx` → `httpx2` for the test client; `uvicorn.workers` → uvicorn's built-in
  `--workers`.
- **Payment ids in request order:** Postgres doesn't guarantee `RETURNING` order for multi-row inserts.
- **Lock ordering:** the concurrency tests couldn't detect a missing `ORDER BY id`, so a query-plan assertion was
  added.
