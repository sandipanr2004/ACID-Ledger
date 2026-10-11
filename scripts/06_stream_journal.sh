#!/usr/bin/env bash
# Step 06 - Journal streaming (recipe: "Set up Journal Streaming to Kinesis")
# Streams the journal as JSON lines - the local stand-in for a Kinesis stream.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
export LEDGER_DB="${LEDGER_DB:-data/ledger.db}"

bin/ledgerctl stream --output stream/journal.jsonl
echo "Streamed $(wc -l < stream/journal.jsonl | tr -d ' ') journal entries to stream/journal.jsonl"
head -1 stream/journal.jsonl
echo "✅ Journal streaming configured"
