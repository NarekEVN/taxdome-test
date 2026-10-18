#!/usr/bin/env bash
# Empty both tables and reload the assignment's seed, using the db container's own credentials.
set -euo pipefail
cd "$(dirname "$0")/.."

docker compose exec -T -e PGOPTIONS=--client-min-messages=warning db sh -c 'psql -v ON_ERROR_STOP=1 -q -o /dev/null -U "$POSTGRES_USER" -d "$POSTGRES_DB" \
  -c "TRUNCATE payments, firms RESTART IDENTITY CASCADE" \
  -f /docker-entrypoint-initdb.d/002_seed.sql'
