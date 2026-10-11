#!/usr/bin/env bash
# Step 07 - Export journal (recipe: "Export Journal Data to S3")
# Exports a self-contained, digest-pinned JSON bundle - the local stand-in
# for `aws qldb export-journal-to-s3`.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
export LEDGER_DB="${LEDGER_DB:-data/ledger.db}"

bin/ledgerctl export --out exports
ls -la exports/journal-exports/
echo "✅ Journal export completed"
