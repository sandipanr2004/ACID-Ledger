#!/usr/bin/env bash
# Step 03 - Create tables/indexes + insert sample financial data
# (recipe: "Create Database Tables and Indexes" + "Insert Sample Financial Data")
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
export LEDGER_DB="${LEDGER_DB:-data/ledger.db}"

bin/ledgerctl seed
bin/ledgerctl accounts
echo "✅ Sample financial data inserted"
