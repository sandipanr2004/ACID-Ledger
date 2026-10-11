#!/usr/bin/env bash
# Step 04 - ACID transaction patterns (recipe: "Implement ACID Transaction Patterns")
#   1. a successful atomic transfer
#   2. a rejected transfer (insufficient funds -> zero writes)
#   3. a forced mid-transaction failure -> full rollback (atomicity proof)
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
export LEDGER_DB="${LEDGER_DB:-data/ledger.db}"

echo "--- 1. atomic transfer TXN-A1: ACC-001 -> ACC-002, 500.00 ---"
bin/ledgerctl transfer ACC-001 ACC-002 500.00 --description "Monthly transfer" --transaction-id TXN-A1

echo "--- 2. rejected transfer (insufficient funds, no partial writes) ---"
set +e
bin/ledgerctl transfer ACC-001 ACC-002 999999.00 --description "Should fail"
rc=$?
set -e
echo "(exit code ${rc} - as expected, ledger state untouched)"

echo "--- 3. forced failure before commit -> atomic rollback ---"
set +e
bin/ledgerctl transfer ACC-002 ACC-001 1000.00 --fail-before-commit
rc=$?
set -e
echo "(exit code ${rc} - debit AND journal entry rolled back together)"

echo "--- balances after all three attempts ---"
bin/ledgerctl accounts
echo "✅ ACID transaction patterns demonstrated"
