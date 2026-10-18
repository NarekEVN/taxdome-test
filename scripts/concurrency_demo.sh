#!/usr/bin/env bash
# Fire REQUESTS parallel payments from Lopez ($500) through nginx at both app instances.
# With $25 each, exactly 20 can succeed however the requests interleave; everything else must be denied.
set -euo pipefail
cd "$(dirname "$0")/.."

BASE_URL="${BASE_URL:-http://localhost:8080}"
REQUESTS="${REQUESTS:-40}"
LOPEZ=8b2e4c71-0d3a-4f6e-b1c9-5a7d2e9f4c10
NAIR=e5f18b3c-2a9d-4c07-8e6b-1d4a7f9c3b25
BODY="{\"payer_firm_uuid\": \"$LOPEZ\", \"payments\": [{\"amount\": \"25\", \"payee_firm_uuid\": \"$NAIR\", \"description\": \"Concurrency demo\"}]}"

sql() { docker compose exec -T db sh -c "psql -At -U \"\$POSTGRES_USER\" -d \"\$POSTGRES_DB\" -c \"$1\""; }

scripts/reset_db.sh
echo "Sending $REQUESTS parallel \$25 payments from Lopez (balance \$500) to Nair via $BASE_URL ..."

results=$(seq "$REQUESTS" | xargs -P "$REQUESTS" -I{} curl -s -o /dev/null -D - -X POST "$BASE_URL/bulk-payments" \
  -H 'Content-Type: application/json' -d "$BODY" \
  | tr -d '\r' | awk '/^HTTP/ {status = $2} tolower($1) == "x-instance:" {split($2, a, ":"); print a[1], status}')

echo
echo "Responses by instance and status:"
sort <<< "$results" | uniq -c | sed 's/^/  /'

created=$(grep -c ' 201$' <<< "$results" || true)
denied=$(grep -c ' 422$' <<< "$results" || true)
lopez=$(sql "SELECT balance_cents FROM firms WHERE uuid = '$LOPEZ'")
rows=$(sql "SELECT count(*) FROM payments")
total=$(sql "SELECT sum(balance_cents) FROM firms")

echo
echo "201 Created: $created   422 Denied: $denied   payment rows: $rows   Lopez balance: $lopez cents"
echo "Money in the system: $total cents (seed total: 5250000)"

expected_created=$((REQUESTS < 20 ? REQUESTS : 20))
if [[ $created -eq $expected_created && $denied -eq $((REQUESTS - expected_created)) && $rows -eq $created \
      && $lopez -eq $((50000 - created * 2500)) && $total -eq 5250000 ]]; then
  echo "OK: no overdraft, no lost or duplicated payments."
else
  echo "FAIL: results don't add up." >&2
  exit 1
fi
