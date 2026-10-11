#!/usr/bin/env bash
# Step 01 - Preparation (recipe: "Preparation" - env vars + resource names)
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

export LEDGER_DB="${LEDGER_DB:-data/ledger.db}"
mkdir -p data build stream exports

echo "LEDGER_DB=${LEDGER_DB}"
echo "✅ Environment prepared successfully"
