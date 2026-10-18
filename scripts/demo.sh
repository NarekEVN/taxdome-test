#!/usr/bin/env bash
# Walk through the assignment's sample request against the running stack (through nginx) and check the result.
set -euo pipefail
cd "$(dirname "$0")/.."

BASE_URL="${BASE_URL:-http://localhost:8080}"
PINECREST=3f1c9a2e-7b4d-4c1e-9a55-2d8e6f0b7c41
LOPEZ=8b2e4c71-0d3a-4f6e-b1c9-5a7d2e9f4c10
NAIR=e5f18b3c-2a9d-4c07-8e6b-1d4a7f9c3b25
TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT

dollars() { printf '$%d.%02d' $(($1 / 100)) $(($1 % 100)); }
balance() { curl -sf "$BASE_URL/firms/$1" | python3 -c 'import json, sys; print(json.load(sys.stdin)["balance_cents"])'; }
show_balances() {
  for firm in "Pinecrest:$PINECREST" "Lopez:$LOPEZ" "Nair:$NAIR"; do
    printf '  %-10s %s\n' "${firm%%:*}" "$(dollars "$(balance "${firm#*:}")")"
  done
}
post() {
  curl -s -o "$TMP/body" -D "$TMP/headers" -w '%{http_code}' -X POST "$BASE_URL/bulk-payments" \
    -H 'Content-Type: application/json' -d "$1"
}
print_response() {
  local instance
  instance=$(tr -d '\r' < "$TMP/headers" | awk 'tolower($1) == "x-instance:" {print $2}')
  echo "  HTTP $1 (served by $instance)"
  python3 -m json.tool < "$TMP/body" | sed 's/^/  /'
}

echo "1. Reset the database to the assignment's seed"
scripts/reset_db.sh
show_balances

echo
echo "2. POST /bulk-payments with scripts/sample_request.json (Pinecrest pays \$13,251.25)"
status=$(post @scripts/sample_request.json)
print_response "$status"

echo
echo "3. Balances afterwards (expected: \$36,748.75 / \$1,700.75 / \$14,050.50)"
show_balances

echo
echo "4. A request Lopez can't cover (\$1,700.75 balance, \$5,000 total) is denied and changes nothing"
status=$(post "{\"payer_firm_uuid\": \"$LOPEZ\", \"payments\": [
  {\"amount\": \"2500\", \"payee_firm_uuid\": \"$NAIR\", \"description\": \"Too much\"},
  {\"amount\": \"2500\", \"payee_firm_uuid\": \"$PINECREST\", \"description\": \"Way too much\"}]}")
print_response "$status"
show_balances

echo
actual="$(balance $PINECREST) $(balance $LOPEZ) $(balance $NAIR)"
if [[ "$actual" == "3674875 170075 1405050" ]]; then
  echo "OK: balances match the assignment."
else
  echo "FAIL: expected 3674875 170075 1405050 cents, got $actual" >&2
  exit 1
fi
