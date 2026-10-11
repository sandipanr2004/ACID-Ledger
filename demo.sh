#!/usr/bin/env bash
# Automated end-to-end demo of the local ACID ledger.
# Runs every step of the recipe, then proves tamper detection on a COPY of
# the ledger (the original stays intact and verified).
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"
export LEDGER_DB="${LEDGER_DB:-data/ledger.db}"

banner() {
  echo ""
  echo "=================================================================="
  echo "== $1"
  echo "=================================================================="
}

for step in scripts/0*.sh scripts/10_cleanup.sh; do
  case "$step" in
    scripts/10_cleanup.sh) continue ;; # keep artifacts for inspection
    *) banner "$(basename "$step" .sh)"; bash "$step" ;;
  esac
done

banner "TAMPER DETECTION DEMO (on a copy - original ledger untouched)"
mkdir -p build
cp "${LEDGER_DB}" build/tampered.db
echo "--- mutating ACC-001 balance directly in the table (bypassing journal) ---"
bin/ledgerctl --db build/tampered.db tamper --account ACC-001 --balance 999999.00
echo "--- verifying the tampered copy (expect: TAMPERING DETECTED, exit 2) ---"
set +e
bin/ledgerctl --db build/tampered.db verify --digest-file build/digest.json
rc=$?
set -e
echo "(verify exit code: ${rc})"
if [ "$rc" -eq 0 ]; then
  echo "❌ FAILURE: tampering was NOT detected"
  exit 1
fi

banner "FINAL VERIFICATION OF THE ORIGINAL LEDGER"
bin/ledgerctl verify --digest-file build/digest.json

echo ""
echo "=================================================================="
echo " DEMO COMPLETE - everything ran end-to-end."
echo " Artifacts: ${LEDGER_DB}, build/digest.json, stream/journal.jsonl,"
echo "            exports/journal-exports/*.json"
echo " Run the test suite with:  python3 -m unittest discover -s tests -v"
echo " Clean up with:            scripts/10_cleanup.sh"
echo "=================================================================="
