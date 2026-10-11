"""ledgerctl - command line interface for the local ACID ledger.

Maps 1:1 onto the commands in the original Amazon QLDB recipe:

  init               create ledger (+ tables/indexes)      -> create-ledger
  seed               load sample financial data
  transfer           ACID funds transfer                  -> PartiQL tx
  accounts           list accounts                        -> SELECT
  transactions       list transactions                    -> SELECT
  digest             cryptographic digest                 -> get-digest
  proof              Merkle inclusion proof for an entry
  verify             full integrity verification
  history            temporal query                       -> history()
  stream             journal as JSONL (Kinesis stand-in)
  export             journal export bundle (S3 stand-in)  -> export-journal-to-s3
  query              run a PartiQL-style SQL file
  tamper             DEMO ONLY: corrupt state to prove detection
"""

import argparse
import json
import os
import sys
import time

from .core import Ledger, LedgerError
from .merkle import verify_proof

DEFAULT_DB = os.environ.get("LEDGER_DB", "data/ledger.db")


def _open(args):
    return Ledger(args.db)


def _print_json(obj):
    print(json.dumps(obj, indent=2, sort_keys=True))


# ---------------------------------------------------------------------------
# commands
# ---------------------------------------------------------------------------

def cmd_init(args):
    ledger = _open(args)
    ledger.close()
    print("Ledger initialized at %s" % args.db)
    print("Tables: _journal (hash-chained), Accounts, Transactions, AuditLog")


def cmd_seed(args):
    ledger = _open(args)
    result = ledger.seed()
    if result is None:
        print("Ledger already contains data; nothing to seed.")
    else:
        print("Sample financial data seeded (journal entry %d, txId %s)"
              % (result["seq"], result["txId"]))
    ledger.close()


def cmd_create_account(args):
    ledger = _open(args)
    result = ledger.create_account(args.id, args.number, args.type, args.balance,
                                   currency=args.currency, customer_id=args.customer)
    print("Account %s created (journal entry %d)" % (args.id, result["seq"]))
    ledger.close()


def cmd_transfer(args):
    ledger = _open(args)
    try:
        result = ledger.transfer(args.from_id, args.to_id, args.amount,
                                 description=args.description,
                                 transaction_id=args.transaction_id,
                                 fail_before_commit=args.fail_before_commit)
    except LedgerError as exc:
        print("TRANSFER REJECTED: %s" % exc, file=sys.stderr)
        ledger.close()
        return 3
    print("Transfer %s: %s -> %s, %.2f (journal entry %d, txId %s)"
          % (result["transactionId"], args.from_id, args.to_id, args.amount,
             result["seq"], result["txId"]))
    ledger.close()
    return 0


def cmd_accounts(args):
    ledger = _open(args)
    for row in ledger.list_accounts():
        d = row["document"]
        print("%s  %s  %s  balance=%10.2f %s  status=%s"
              % (d["accountId"], d["accountType"], d["accountNumber"],
                 d["balance"], d["currency"], d["status"]))
    ledger.close()


def cmd_transactions(args):
    ledger = _open(args)
    for row in ledger.list_transactions():
        d = row["document"]
        print("%s  %s -> %s  %.2f %s  %s  [%s]"
              % (d["transactionId"], d["fromAccountId"], d["toAccountId"],
                 d["amount"], d["currency"], d["timestamp"], d["status"]))
    ledger.close()


def cmd_digest(args):
    ledger = _open(args)
    digest = ledger.get_digest()
    ledger.close()
    if args.output:
        with open(args.output, "w") as fh:
            fh.write(json.dumps(digest, indent=2, sort_keys=True))
        print("Digest written to %s" % args.output)
    else:
        _print_json(digest)


def cmd_proof(args):
    ledger = _open(args)
    try:
        proof = ledger.proof_for_seq(args.seq)
    except LedgerError as exc:
        print("ERROR: %s" % exc, file=sys.stderr)
        return 1
    ledger.close()
    if not verify_proof(proof["leaf"], proof):
        print("ERROR: generated proof failed self-verification", file=sys.stderr)
        return 1
    _print_json(proof)
    return 0


def cmd_verify(args):
    ledger = _open(args)
    expected = None
    if args.digest_file:
        with open(args.digest_file) as fh:
            expected = json.load(fh)["digest"]
    report = ledger.verify(expected_digest=expected)
    ledger.close()
    if args.json:
        _print_json(report)
    else:
        print("Verifying ledger: %s" % report["ledger"])
        print("  chain : %s (%d entries)" % ("OK" if report["chain"]["ok"] else "FAILED",
                                             report["chain"]["entries"]))
        print("  state : %s (%d tables)" % ("OK" if report["state"]["ok"] else "FAILED",
                                            len(report["state"]["tables"])))
        print("  digest: %s (root %s...)"
              % ("OK" if report["digest"]["ok"] else "FAILED",
                 (report["digest"]["root"] or "")[:16]))
        for section in ("chain", "state", "digest"):
            for err in report[section]["errors"]:
                print("    ! %s" % err)
        print("RESULT: %s" % ("VERIFIED" if report["ok"] else "TAMPERING DETECTED"))
    return 0 if report["ok"] else 2


def cmd_history(args):
    ledger = _open(args)
    entries = ledger.history(args.table, args.key)
    ledger.close()
    if not entries:
        print("No history for %s.%s" % (args.table, args.key))
        return 0
    for e in entries:
        print("seq=%-4d tx=%s  time=%s  %-6s  %s"
              % (e["seq"], e["txId"][:8], e["txTime"], e["action"],
                 json.dumps(e["document"], sort_keys=True)))
    return 0


def cmd_stream(args):
    ledger = _open(args)
    out = open(args.output, "w") if args.output else sys.stdout
    last_seq = args.from_seq - 1
    try:
        while True:
            new = 0
            for entry in ledger.stream_entries(from_seq=last_seq + 1):
                out.write(json.dumps(entry, sort_keys=True) + "\n")
                out.flush()
                last_seq = entry["seq"]
                new += 1
            if not args.follow:
                break
            if new == 0:
                time.sleep(args.interval)
    except KeyboardInterrupt:
        pass
    finally:
        ledger.close()
        if args.output:
            out.close()
    return 0


def cmd_export(args):
    ledger = _open(args)
    path, count = ledger.export(args.out, prefix=args.prefix)
    ledger.close()
    print("Exported %d journal entries to %s" % (count, path))
    return 0


def cmd_query(args):
    ledger = _open(args)
    with open(args.file) as fh:
        script = fh.read()
    # drop full-line comments and blank lines first, then split on ";"
    clean_lines = [ln for ln in script.splitlines()
                   if ln.strip() and not ln.strip().startswith("--")]
    statements = [s.strip() for s in "\n".join(clean_lines).split(";") if s.strip()]
    for stmt in statements:
        print("-- %s" % " ".join(stmt.split())[:100])
        try:
            cur = ledger.conn.execute(stmt)
            rows = cur.fetchall()
            for row in rows:
                print(json.dumps({k: row[k] for k in row.keys()}, sort_keys=True))
            print("(%d rows)" % len(rows))
        except Exception as exc:
            print("ERROR: %s" % exc, file=sys.stderr)
            ledger.close()
            return 1
    ledger.close()
    return 0


def cmd_tamper(args):
    ledger = _open(args)
    doc = ledger.tamper_balance(args.account, args.balance)
    ledger.close()
    print("DEMO: %s balance mutated to %.2f directly in the table (bypassing the journal)"
          % (args.account, doc["balance"]))
    print("Run `ledgerctl verify` to see detection in action.")
    return 0


# ---------------------------------------------------------------------------
# parser
# ---------------------------------------------------------------------------

def build_parser():
    p = argparse.ArgumentParser(prog="ledgerctl",
                                description="Local ACID ledger with cryptographic verification")
    p.add_argument("--db", default=DEFAULT_DB,
                   help="ledger database path (default: %s, env LEDGER_DB)" % DEFAULT_DB)
    sub = p.add_subparsers(dest="command", required=True)

    sub.add_parser("init", help="create the ledger and schema").set_defaults(fn=cmd_init)
    sub.add_parser("seed", help="load sample financial data").set_defaults(fn=cmd_seed)

    c = sub.add_parser("create-account", help="create a new account")
    c.add_argument("--id", required=True)
    c.add_argument("--number", required=True)
    c.add_argument("--type", default="CHECKING", choices=["CHECKING", "SAVINGS"])
    c.add_argument("--balance", type=float, required=True)
    c.add_argument("--currency", default="USD")
    c.add_argument("--customer")
    c.set_defaults(fn=cmd_create_account)

    c = sub.add_parser("transfer", help="atomic funds transfer")
    c.add_argument("from_id")
    c.add_argument("to_id")
    c.add_argument("amount", type=float)
    c.add_argument("--description", default="Transfer")
    c.add_argument("--transaction-id", dest="transaction_id")
    c.add_argument("--fail-before-commit", action="store_true",
                   help="DEMO: force a failure after writes to prove rollback")
    c.set_defaults(fn=cmd_transfer)

    sub.add_parser("accounts", help="list accounts").set_defaults(fn=cmd_accounts)
    sub.add_parser("transactions", help="list transactions").set_defaults(fn=cmd_transactions)

    c = sub.add_parser("digest", help="compute the cryptographic ledger digest")
    c.add_argument("--output", "-o", help="write digest JSON to a file (pin it externally!)")
    c.set_defaults(fn=cmd_digest)

    c = sub.add_parser("proof", help="Merkle inclusion proof for a journal entry")
    c.add_argument("--seq", type=int, required=True)
    c.set_defaults(fn=cmd_proof)

    c = sub.add_parser("verify", help="verify chain, state and digest")
    c.add_argument("--digest-file", help="pinned digest JSON to check against")
    c.add_argument("--json", action="store_true", help="print full JSON report")
    c.set_defaults(fn=cmd_verify)

    c = sub.add_parser("history", help="temporal query for one row (like PartiQL history())")
    c.add_argument("table", choices=["Accounts", "Transactions", "AuditLog"])
    c.add_argument("key")
    c.set_defaults(fn=cmd_history)

    c = sub.add_parser("stream", help="stream the journal as JSONL (Kinesis stand-in)")
    c.add_argument("--from-seq", type=int, default=1)
    c.add_argument("--output", "-o")
    c.add_argument("--follow", action="store_true", help="keep polling for new entries")
    c.add_argument("--interval", type=float, default=0.5)
    c.set_defaults(fn=cmd_stream)

    c = sub.add_parser("export", help="export the journal as a verifiable bundle (S3 stand-in)")
    c.add_argument("--out", default="exports")
    c.add_argument("--prefix", default="journal-exports")
    c.set_defaults(fn=cmd_export)

    c = sub.add_parser("query", help="run a SQL file against the ledger")
    c.add_argument("--file", "-f", required=True)
    c.set_defaults(fn=cmd_query)

    c = sub.add_parser("tamper", help="DEMO ONLY: corrupt a balance to prove detection")
    c.add_argument("--account", required=True)
    c.add_argument("--balance", type=float, required=True)
    c.set_defaults(fn=cmd_tamper)

    return p


def main(argv=None):
    args = build_parser().parse_args(argv)
    try:
        code = args.fn(args)
        return code if code is not None else 0
    except FileNotFoundError as exc:
        print("ERROR: %s" % exc, file=sys.stderr)
        return 1
    except LedgerError as exc:
        print("ERROR: %s" % exc, file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
