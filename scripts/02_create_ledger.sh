#!/usr/bin/env bash
# Step 02 - Create the ledger (recipe: "Create QLDB Ledger with Encryption")
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
export LEDGER_DB="${LEDGER_DB:-data/ledger.db}"

bin/ledgerctl init
echo "✅ Ledger created and schema ready"
