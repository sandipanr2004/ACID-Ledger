# ACID-Compliant Distributed Ledger with Cryptographic Verification

A **fully runnable, end-to-end** re-implementation of the
[mzazon/cloud-projects QLDB recipe](https://github.com/mzazon/cloud-projects/blob/main/aws/acid-compliant-distributed-databases/acid-compliant-distributed-databases.md)
— the same architecture, running **locally on SQLite with zero dependencies**
(Python 3.9+ standard library only).

> **Why local?** Amazon QLDB reached end-of-support on **July 31, 2025** — the
> recipe itself warns that new ledgers can't be created. This project keeps the
> recipe's guarantees fully intact and actually executable on your machine:
> ACID transactions, an append-only hash-chained journal, SHA-256 + Merkle
> digests, tamper detection, temporal audit queries, journal streaming and
> verifiable export.

## Quickstart

```bash
./demo.sh                              # full end-to-end run (~5 seconds)
bin/ledgerweb                          # live web dashboard on http://127.0.0.1:8787
python3 -m unittest discover -s tests -v   # 27 tests
scripts/10_cleanup.sh                  # remove generated artifacts
```

Requirements: `python3` (3.9+) and `bash`. Nothing else — no AWS account, no
pip installs.

## What the demo proves

1. **ACID transfers** — debit + credit + transaction record + audit event
   commit as one atomic journal entry.
2. **Atomic rollback** — a failure injected before commit rolls back journal
   *and* state together (see `--fail-before-commit`).
3. **Rejection without partial writes** — insufficient funds changes nothing.
4. **Cryptographic digest** — Merkle root over the journal + tip address
   (like `aws qldb get-digest`), stable when idle.
5. **Streaming** — the journal tails out as JSONL (Kinesis stand-in).
6. **Export** — a self-contained, digest-pinned JSON bundle (S3 stand-in).
7. **Tamper detection** — a *copy* of the ledger is corrupted directly in the
   table (bypassing the journal); `verify` catches it: `TAMPERING DETECTED`.
8. **Final verification** — the original ledger still verifies: `VERIFIED`.

## Architecture

```
            ┌────────────────────────── ledgerctl (CLI) ─────────────────────────┐
            │ init seed transfer accounts transactions digest proof verify      │
            │ history stream export query tamper                                 │
            └───────────────────────────────┬────────────────────────────────────┘
                                            │
   writes (BEGIN IMMEDIATE, one ACID tx)    │    reads / verification
                                            ▼
┌──────────────────────────────┐   ┌─────────────────────────────────────────┐
│  _journal  (append-only)     │   │  Materialized tables                    │
│  seq · txId · txTime         │──▶│  Accounts · Transactions · AuditLog     │
│  prevHash · payload · hash   │   │  (derived from journal, never diverges) │
└──────────────┬───────────────┘   └──────────────────┬──────────────────────┘
               │ SHA-256 hash chain                   │ replay + compare
               ▼                                      ▼
        Merkle tree root  ──── digest ────▶  verify: chain ✓ state ✓ digest ✓
```

Every logical write is **journal-first** (one hash-chained entry whose hash
commits to the previous entry's hash), then materialized into queryable
tables — inside the *same* SQLite transaction, so journal and state can never
diverge at commit time.

## Recipe → local implementation map

| Original QLDB recipe step | This project |
|---|---|
| Create QLDB ledger | `scripts/02_create_ledger.sh` → `ledgerctl init` |
| Create tables & indexes | schema in [ledger/db.py](ledger/db.py) (auto-created) |
| Insert sample financial data | `scripts/03_seed_data.sh` → `ledgerctl seed` |
| ACID transaction patterns | `scripts/04_transactions.sh` → `ledgerctl transfer` |
| Generate cryptographic digest | `scripts/05_digest.sh` → `ledgerctl digest` (Merkle root + tip) |
| Stream journal to Kinesis | `scripts/06_stream_journal.sh` → `ledgerctl stream` (JSONL) |
| Export journal to S3 | `scripts/07_export_journal.sh` → `ledgerctl export` (digest-pinned bundle) |
| Implement transaction verification | `ledgerctl verify` — chain + replay + digest |
| Monitor journal streaming | `stream/journal.jsonl`, entry counts in every command |
| Audit trail queries (`history()`) | `scripts/09_audit_queries.sh` → `ledgerctl history` + [queries/audit.sql](queries/audit.sql) |
| Test data verification & integrity | tamper-detection segment of [demo.sh](demo.sh) |
| Cleanup | `scripts/10_cleanup.sh` |

## Verification model

`ledgerctl verify [--digest-file build/digest.json]` runs three independent
checks and exits non-zero on any failure:

1. **Chain** — recompute every entry hash (`sha256(prev_hash + "\n" + payload)`)
   and check every link back to the genesis hash. Catches any journal edit.
2. **State** — replay all journal ops onto empty tables with the same
   strictness as the real write path, then diff against the materialized
   tables. Catches direct table mutation (the demo's tamper scenario).
3. **Digest** — recompute the Merkle root and compare with a digest pinned
   *externally* (`--digest-file`). This is the only check that catches an
   attacker who rewrites the entire journal consistently — which is exactly
   why QLDB digests are meant to be anchored outside the ledger. A test
   (`test_full_chain_rewrite_caught_by_pinned_digest`) proves this case.

`ledgerctl proof --seq N` produces a Merkle inclusion proof for any entry and
self-verifies it before printing.

## CLI reference

```
bin/ledgerctl [--db PATH] <command>

  init                                   create ledger + schema
  seed                                   load the recipe's sample data (idempotent)
  create-account --id --number --balance [--type --currency --customer]
  transfer FROM TO AMOUNT [--description] [--transaction-id]
                                         atomic transfer (exit 3 if rejected)
  accounts | transactions                list current state
  digest [-o FILE]                       Merkle-root digest + tip address
  proof --seq N                          Merkle inclusion proof for an entry
  verify [--digest-file FILE] [--json]   full verification (exit 2 if tampered)
  history TABLE KEY                      temporal query (like history())
  stream [--from-seq N] [--follow] [-o FILE]   journal as JSONL
  export [--out DIR]                     verifiable export bundle
  query -f FILE                          run SQL audit queries
  tamper --account ID --balance N        DEMO ONLY: corrupt state on purpose
```

`--db` defaults to `$LEDGER_DB` or `data/ledger.db`.

## Web dashboard (`bin/ledgerweb`)

A zero-dependency web UI over the same ledger (Python stdlib `http.server`):

- **Live state** — balances, transactions, audit events, and the journal tail,
  refreshed every ~1.2s
- **Transfer form** — sends real atomic transfers through `POST /api/transfer`
  (rejections like insufficient funds come back as HTTP 400 with the reason)
- **Hash chain, visualized** — each journal row shows `prevHash → hash`, so you
  can literally see consecutive entries linking
- **Verification panel** — chain / state-replay / Merkle digest checks run on
  every refresh; the header pill flips between VERIFIED and TAMPERING DETECTED
- **Tamper button** — corrupts ACC-001's balance on purpose so you can watch
  verification catch it live

API: `GET /api/state`, `GET /api/journal`, `GET /api/verify`,
`POST /api/transfer`, `POST /api/seed`, `POST /api/tamper`.
Flags: `bin/ledgerweb [--db PATH] [--host H] [--port P]` (default port 8787).

## Project layout

```
ledger/            core engine (stdlib only)
  db.py            SQLite schema: _journal + materialized tables
  core.py          ACID writes, hash chain, digest, verify, history, export
  merkle.py        Merkle root, inclusion proofs, proof verification
  cli.py           ledgerctl command-line interface
bin/ledgerctl      runnable CLI wrapper
bin/ledgerweb      web dashboard launcher
web/               stdlib HTTP server + dashboard (index.html, app.js, style.css)
scripts/01..10     one script per recipe step
demo.sh            automated end-to-end demo incl. tamper detection
queries/audit.sql  SQL audit-trail queries
tests/             27 tests (ACID, chain, tamper, proofs, CLI flow)
```

## Limitations (deliberate scope)

- **Single-process, single-machine** — SQLite gives real ACID (WAL mode,
  `BEGIN IMMEDIATE` serialization) but not distributed consensus. This mirrors
  the recipe's *logic*, not QLDB's multi-node replication.
- **Balances are floats** — matching the recipe's sample data; production
  ledgers should use integer minor units or `Decimal`.
- **Streaming is a file tail**, not a network service — swap
  `ledgerctl stream --follow` behind a Kinesis/Firehose producer if you want
  the real thing.
- **Pin your digest externally** — the ledger file plus a copied digest file
  on the same disk can be rewritten together; the digest's value comes from
  anchoring it somewhere the ledger can't reach (like QLDB's digest API
  called from a separate trust domain).

## Run it

```bash
./demo.sh
```
