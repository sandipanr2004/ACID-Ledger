#!/usr/bin/env bash
# Step 09 - Audit trail queries (recipe: "Create Audit Trail Query Functions")
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
export LEDGER_DB="${LEDGER_DB:-data/ledger.db}"

echo "--- SQL audit queries (queries/audit.sql) ---"
bin/ledgerctl query --file queries/audit.sql

echo ""
echo "--- temporal history of ACC-001 (like PartiQL history()) ---"
bin/ledgerctl history Accounts ACC-001

echo ""
echo "--- temporal history of TXN-A1 ---"
bin/ledgerctl history Transactions TXN-A1

echo ""
echo "--- Merkle inclusion proof for journal entry 1 ---"
bin/ledgerctl proof --seq 1 | head -20

echo "✅ Audit trail queries completed"
