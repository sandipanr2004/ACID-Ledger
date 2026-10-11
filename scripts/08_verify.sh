#!/usr/bin/env bash
# Step 08 - Verify data integrity (recipe: "Test Data Verification and Integrity")
# Checks: hash chain, journal-replay vs materialized state, Merkle digest
# against the digest pinned in step 05.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
export LEDGER_DB="${LEDGER_DB:-data/ledger.db}"

bin/ledgerctl verify --digest-file build/digest.json --json
echo "✅ Data verification completed"
