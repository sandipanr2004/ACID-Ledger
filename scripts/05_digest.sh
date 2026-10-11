#!/usr/bin/env bash
# Step 05 - Generate cryptographic digest (recipe: "Generate Cryptographic Digest")
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
export LEDGER_DB="${LEDGER_DB:-data/ledger.db}"

bin/ledgerctl digest --output build/digest.json
cat build/digest.json
echo "✅ Cryptographic digest generated (pin build/digest.json externally)"
