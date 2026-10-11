#!/usr/bin/env bash
# Step 10 - Cleanup (recipe: "Cleanup")
# Removes generated artifacts only: the local ledger DB, exports, streams.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

rm -rf data build stream exports
echo "✅ Ledger data and generated artifacts cleaned up"
