"""Core ledger engine: ACID transactions over a hash-chained journal.

Mirrors the Amazon QLDB recipe step for step:

* journal-first writes, then materialize into queryable tables
* SHA-256 hash chain (each entry commits to the previous entry's hash)
* Merkle tree over journal entries -> ledger digest (like `get-digest`)
* state verification by replaying the journal and comparing with tables
* temporal `history()` queries straight off the journal
* journal streaming (JSONL, stands in for Kinesis) and export (stands in
  for the S3 journal export)
"""

import hashlib
import json
import uuid
from datetime import datetime, timezone

from . import db as dbm
from .merkle import build_proof, merkle_root

GENESIS = "0" * 64

# table name -> primary key column (must match the JSON document key)
TABLES = {
    "Accounts": "accountId",
    "Transactions": "transactionId",
    "AuditLog": "eventId",
}


class LedgerError(Exception):
    """Base error for ledger operations."""


class InsufficientFunds(LedgerError):
    """Raised when a transfer would overdraw an account (no writes happen)."""


class AccountNotFound(LedgerError):
    """Raised when an account referenced by a transaction does not exist."""


def now_iso():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def canon(obj):
    """Canonical JSON: stable key order and separators, so hashes are stable."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _entry_hash(prev_hash, payload_json):
    return hashlib.sha256((prev_hash + "\n" + payload_json).encode()).hexdigest()


class Ledger:
    def __init__(self, path):
        self.path = path
        self.conn = dbm.connect(path)
        dbm.init_schema(self.conn)

    def close(self):
        self.conn.close()

    # ------------------------------------------------------------------
    # write path (journal-first, single ACID transaction per logical write)
    # ------------------------------------------------------------------

    def _apply_op(self, cur, op):
        table = op.get("table")
        action = op.get("action")
        key = op.get("key")
        doc = op.get("document")
        if table not in TABLES:
            raise LedgerError("unknown table: %r" % (table,))
        if action not in ("INSERT", "UPDATE", "DELETE"):
            raise LedgerError("unknown action: %r" % (action,))
        pk = TABLES[table]
        if action == "INSERT":
            if not isinstance(doc, dict) or doc.get(pk) != key:
                raise LedgerError("INSERT document key mismatch for %s.%s" % (table, key))
            if cur.execute("SELECT 1 FROM %s WHERE %s=?" % (table, pk), (key,)).fetchone():
                raise LedgerError("INSERT: %s.%s already exists" % (table, key))
            cur.execute("INSERT INTO %s (%s, data) VALUES (?, ?)" % (table, pk),
                        (key, canon(doc)))
        elif action == "UPDATE":
            if not isinstance(doc, dict) or doc.get(pk) != key:
                raise LedgerError("UPDATE document key mismatch for %s.%s" % (table, key))
            cur.execute("UPDATE %s SET data=? WHERE %s=?" % (table, pk), (canon(doc), key))
            if cur.rowcount != 1:
                raise LedgerError("UPDATE: %s.%s does not exist" % (table, key))
        else:  # DELETE
            cur.execute("DELETE FROM %s WHERE %s=?" % (table, pk), (key,))
            if cur.rowcount != 1:
                raise LedgerError("DELETE: %s.%s does not exist" % (table, key))

    def _write_tx(self, ops_or_builder, fail_before_commit=False):
        """Append to the journal and materialize the ops in ONE ACID transaction.

        ops_or_builder is either a list of ops, or a callable(cur) -> ops that
        runs under the write lock (used by transfer so balance checks and
        reads happen inside the same locked transaction as the write).
        """
        cur = self.conn.cursor()
        cur.execute("BEGIN IMMEDIATE")
        try:
            ops = ops_or_builder(cur) if callable(ops_or_builder) else ops_or_builder
            row = cur.execute(
                "SELECT seq, hash FROM _journal ORDER BY seq DESC LIMIT 1"
            ).fetchone()
            next_seq = row["seq"] + 1 if row else 1
            prev_hash = row["hash"] if row else GENESIS
            tx_id = uuid.uuid4().hex
            tx_time = now_iso()
            payload = {"seq": next_seq, "txId": tx_id, "txTime": tx_time, "ops": ops}
            payload_json = canon(payload)
            h = _entry_hash(prev_hash, payload_json)
            cur.execute(
                "INSERT INTO _journal (seq, tx_id, tx_time, prev_hash, payload, hash)"
                " VALUES (?, ?, ?, ?, ?, ?)",
                (next_seq, tx_id, tx_time, prev_hash, payload_json, h),
            )
            for op in ops:
                self._apply_op(cur, op)
            if fail_before_commit:
                # demo/testing hook: proves journal + state roll back together
                raise LedgerError("simulated failure before commit")
            cur.execute("COMMIT")
        except Exception:
            cur.execute("ROLLBACK")
            raise
        result = {"seq": next_seq, "txId": tx_id, "txTime": tx_time, "hash": h}
        return result

    # ------------------------------------------------------------------
    # domain operations
    # ------------------------------------------------------------------

    def create_account(self, account_id, account_number, account_type,
                       balance, currency="USD", customer_id=None, status="ACTIVE"):
        doc = {
            "accountId": account_id,
            "accountNumber": account_number,
            "accountType": account_type,
            "balance": round(float(balance), 2),
            "currency": currency,
            "customerId": customer_id or "CUST-" + account_id.split("-")[-1],
            "createdAt": now_iso(),
            "status": status,
        }
        return self._write_tx([{
            "table": "Accounts", "action": "INSERT",
            "key": account_id, "document": doc,
        }])

    def seed(self):
        """Seed the sample financial data from the original recipe."""
        if self.conn.execute("SELECT COUNT(*) AS n FROM Accounts").fetchone()["n"]:
            return None  # already seeded
        self._write_tx([
            {"table": "Accounts", "action": "INSERT", "key": "ACC-001", "document": {
                "accountId": "ACC-001", "accountNumber": "1234567890",
                "accountType": "CHECKING", "balance": 10000.00, "currency": "USD",
                "customerId": "CUST-001", "createdAt": "2024-01-01T00:00:00Z",
                "status": "ACTIVE"}},
            {"table": "Accounts", "action": "INSERT", "key": "ACC-002", "document": {
                "accountId": "ACC-002", "accountNumber": "0987654321",
                "accountType": "SAVINGS", "balance": 25000.00, "currency": "USD",
                "customerId": "CUST-002", "createdAt": "2024-01-01T00:00:00Z",
                "status": "ACTIVE"}},
        ])
        return self._write_tx([
            {"table": "Transactions", "action": "INSERT", "key": "TXN-001", "document": {
                "transactionId": "TXN-001", "fromAccountId": "ACC-001",
                "toAccountId": "ACC-002", "amount": 500.00, "currency": "USD",
                "transactionType": "TRANSFER", "timestamp": "2024-01-15T10:30:00Z",
                "description": "Monthly transfer", "status": "COMPLETED"}},
            {"table": "Transactions", "action": "INSERT", "key": "TXN-002", "document": {
                "transactionId": "TXN-002", "fromAccountId": "ACC-002",
                "toAccountId": "ACC-001", "amount": 1000.00, "currency": "USD",
                "transactionType": "TRANSFER", "timestamp": "2024-01-20T14:15:00Z",
                "description": "Loan payment", "status": "COMPLETED"}},
        ])

    def transfer(self, from_id, to_id, amount, description="Transfer",
                 transaction_id=None, fail_before_commit=False):
        """Atomic funds transfer: debit + credit + record + audit in one tx.

        Balances are read, validated and updated under the same write lock as
        the journal append, so concurrent transfers cannot overdraw.
        """
        amount = round(float(amount), 2)
        if amount <= 0:
            raise LedgerError("amount must be positive")
        extras = {}

        def build(cur):
            rows = {}
            for acc_id in (from_id, to_id):
                row = cur.execute("SELECT data FROM Accounts WHERE accountId=?",
                                  (acc_id,)).fetchone()
                if row is None:
                    raise AccountNotFound("account %s does not exist" % acc_id)
                rows[acc_id] = json.loads(row["data"])
            if rows[from_id]["status"] != "ACTIVE":
                raise LedgerError("account %s is not ACTIVE" % from_id)
            if rows[to_id]["status"] != "ACTIVE":
                raise LedgerError("account %s is not ACTIVE" % to_id)
            if rows[from_id]["balance"] < amount:
                raise InsufficientFunds(
                    "account %s has %.2f, needs %.2f" % (from_id, rows[from_id]["balance"], amount))

            rows[from_id]["balance"] = round(rows[from_id]["balance"] - amount, 2)
            rows[to_id]["balance"] = round(rows[to_id]["balance"] + amount, 2)
            txn_id = transaction_id or ("TXN-" + uuid.uuid4().hex[:8].upper())
            txn_doc = {
                "transactionId": txn_id, "fromAccountId": from_id, "toAccountId": to_id,
                "amount": amount, "currency": rows[from_id]["currency"],
                "transactionType": "TRANSFER", "timestamp": now_iso(),
                "description": description, "status": "COMPLETED",
            }
            audit_doc = {
                "eventId": "EVT-" + uuid.uuid4().hex[:8].upper(),
                "transactionId": txn_id, "action": "FUNDS_TRANSFER",
                "fromAccountId": from_id, "toAccountId": to_id, "amount": amount,
                "timestamp": now_iso(),
            }
            extras["transactionId"] = txn_id
            return [
                {"table": "Accounts", "action": "UPDATE", "key": from_id, "document": rows[from_id]},
                {"table": "Accounts", "action": "UPDATE", "key": to_id, "document": rows[to_id]},
                {"table": "Transactions", "action": "INSERT", "key": txn_id, "document": txn_doc},
                {"table": "AuditLog", "action": "INSERT", "key": audit_doc["eventId"], "document": audit_doc},
            ]

        result = self._write_tx(build, fail_before_commit=fail_before_commit)
        result.update(extras)
        return result

    # ------------------------------------------------------------------
    # read path
    # ------------------------------------------------------------------

    def _rows(self, table):
        if table not in TABLES:
            raise LedgerError("unknown table: %r" % (table,))
        pk = TABLES[table]
        out = []
        for row in self.conn.execute("SELECT %s AS k, data FROM %s ORDER BY %s" % (pk, table, pk)):
            out.append({"key": row["k"], "document": json.loads(row["data"])})
        return out

    def list_accounts(self):
        return self._rows("Accounts")

    def list_transactions(self):
        return self._rows("Transactions")

    def history(self, table, key):
        """Temporal query: every version of `table.key` ever written, like
        QLDB's history() PartiQL function."""
        if table not in TABLES:
            raise LedgerError("unknown table: %r" % (table,))
        out = []
        for row in self.conn.execute(
                "SELECT seq, tx_id, tx_time, payload FROM _journal ORDER BY seq"):
            payload = json.loads(row["payload"])
            for op in payload["ops"]:
                if op["table"] == table and op["key"] == key:
                    out.append({
                        "seq": row["seq"], "txId": row["tx_id"], "txTime": row["tx_time"],
                        "action": op["action"], "document": op["document"],
                    })
        return out

    def _entry_dicts(self, from_seq=0):
        entries = []
        for row in self.conn.execute(
                "SELECT seq, tx_id, tx_time, prev_hash, payload, hash"
                " FROM _journal WHERE seq >= ? ORDER BY seq", (from_seq,)):
            entries.append({
                "seq": row["seq"], "txId": row["tx_id"], "txTime": row["tx_time"],
                "prevHash": row["prev_hash"], "payload": json.loads(row["payload"]),
                "hash": row["hash"],
            })
        return entries

    def stream_entries(self, from_seq=0):
        """Yield journal entries as dicts (JSONL -> stands in for Kinesis)."""
        for entry in self._entry_dicts(from_seq):
            yield entry

    def get_digest(self):
        """Ledger digest: Merkle root over all journal entries + tip address."""
        rows = self.conn.execute("SELECT seq, hash FROM _journal ORDER BY seq").fetchall()
        hashes = [r["hash"] for r in rows]
        return {
            "digest": merkle_root(hashes),
            "digestTipAddress": ({"seq": rows[-1]["seq"], "entryHash": rows[-1]["hash"]}
                                 if rows else None),
            "ledger": self.path,
            "entryCount": len(hashes),
            "retrievedAt": now_iso(),
        }

    def proof_for_seq(self, seq):
        """Merkle inclusion proof for the journal entry at `seq`."""
        rows = self.conn.execute("SELECT seq, hash FROM _journal ORDER BY seq").fetchall()
        hashes = [r["hash"] for r in rows]
        index = next((i for i, r in enumerate(rows) if r["seq"] == seq), None)
        if index is None:
            raise LedgerError("no journal entry with seq %s" % seq)
        proof = build_proof(hashes, index)
        proof["seq"] = seq
        return proof

    # ------------------------------------------------------------------
    # verification (the heart of the recipe)
    # ------------------------------------------------------------------

    def verify(self, expected_digest=None):
        """Full integrity verification:

        1. chain    - recompute every entry hash and check links to genesis
        2. state    - replay journal ops onto empty tables, compare with DB
        3. digest   - recompute Merkle root, compare with expected digest if
                      one was pinned externally (detects full-chain rewrites)
        """
        report = {
            "ledger": self.path,
            "chain": {"ok": True, "errors": [], "entries": 0},
            "state": {"ok": True, "errors": [], "tables": {}},
            "digest": {"ok": True, "errors": [], "root": None, "expected": expected_digest},
        }

        rows = self.conn.execute(
            "SELECT seq, tx_id, tx_time, prev_hash, payload, hash FROM _journal ORDER BY seq"
        ).fetchall()
        report["chain"]["entries"] = len(rows)

        # 1. hash chain ------------------------------------------------
        prev = GENESIS
        replay_state = {t: {} for t in TABLES}
        chain_ok = True
        for row in rows:
            if row["prev_hash"] != prev:
                chain_ok = False
                report["chain"]["errors"].append(
                    "entry %d: broken link (prev_hash does not match previous entry)" % row["seq"])
            recomputed = _entry_hash(row["prev_hash"], row["payload"])
            if recomputed != row["hash"]:
                chain_ok = False
                report["chain"]["errors"].append(
                    "entry %d: hash mismatch (journal entry was modified)" % row["seq"])
            prev = row["hash"]

            # 3b. replay (used by the state check below)
            try:
                payload = json.loads(row["payload"])
                for op in payload["ops"]:
                    self._apply_op_stateless(replay_state, op)
            except LedgerError as exc:
                report["state"]["ok"] = False
                report["state"]["errors"].append("replay of entry %d failed: %s" % (row["seq"], exc))
        report["chain"]["ok"] = chain_ok

        # 2. materialized state vs journal replay -----------------------
        for table in TABLES:
            pk = TABLES[table]
            db_state = {}
            for r in self.conn.execute("SELECT %s AS k, data FROM %s" % (pk, table)):
                db_state[r["k"]] = json.loads(r["data"])
            replayed = replay_state[table]
            mismatches = []
            for key in sorted(set(db_state) | set(replayed)):
                if db_state.get(key) != replayed.get(key):
                    mismatches.append(key)
            report["state"]["tables"][table] = {
                "rows": len(db_state), "mismatchedKeys": mismatches,
            }
            if mismatches:
                report["state"]["ok"] = False
                report["state"]["errors"].append(
                    "%s: %d row(s) diverge from the journal: %s"
                    % (table, len(mismatches), ", ".join(mismatches[:10])))

        # 3. digest ----------------------------------------------------
        root = merkle_root([r["hash"] for r in rows])
        report["digest"]["root"] = root
        if expected_digest is not None and expected_digest != root:
            report["digest"]["ok"] = False
            report["digest"]["errors"].append(
                "digest mismatch: ledger Merkle root differs from the pinned digest "
                "(the journal was rewritten)")

        report["ok"] = report["chain"]["ok"] and report["state"]["ok"] and report["digest"]["ok"]
        return report

    @staticmethod
    def _apply_op_stateless(state, op):
        """Apply an op to the replay state dict with the same rules as the DB."""
        table, action, key, doc = op["table"], op["action"], op["key"], op["document"]
        if table not in TABLES:
            raise LedgerError("unknown table %r" % table)
        pk = TABLES[table]
        bucket = state[table]
        if action == "INSERT":
            if key in bucket:
                raise LedgerError("replay: %s.%s already exists" % (table, key))
            if not isinstance(doc, dict) or doc.get(pk) != key:
                raise LedgerError("replay: key mismatch for %s.%s" % (table, key))
            bucket[key] = doc
        elif action == "UPDATE":
            if key not in bucket:
                raise LedgerError("replay: %s.%s does not exist" % (table, key))
            if not isinstance(doc, dict) or doc.get(pk) != key:
                raise LedgerError("replay: key mismatch for %s.%s" % (table, key))
            bucket[key] = doc
        elif action == "DELETE":
            if key not in bucket:
                raise LedgerError("replay: %s.%s does not exist" % (table, key))
            del bucket[key]
        else:
            raise LedgerError("replay: unknown action %r" % action)

    # ------------------------------------------------------------------
    # export (stands in for the QLDB -> S3 journal export)
    # ------------------------------------------------------------------

    def export(self, out_dir, prefix="journal-exports"):
        import os
        entries = self._entry_dicts()
        digest = self.get_digest()
        if entries:
            name = "%06d-%06d.json" % (entries[0]["seq"], entries[-1]["seq"])
        else:
            name = "empty.json"
        target = os.path.join(out_dir, prefix)
        os.makedirs(target, exist_ok=True)
        path = os.path.join(target, name)
        bundle = {
            "ledger": self.path,
            "inclusiveStartSeq": entries[0]["seq"] if entries else None,
            "exclusiveEndSeq": entries[-1]["seq"] + 1 if entries else None,
            "entryCount": len(entries),
            "digest": digest,
            "entries": entries,
            "exportedAt": now_iso(),
        }
        with open(path, "w") as fh:
            fh.write(json.dumps(bundle, indent=2, sort_keys=True))
        return path, len(entries)

    # ------------------------------------------------------------------
    # demo-only tamper tool (clearly marked; bypasses the journal)
    # ------------------------------------------------------------------

    def tamper_balance(self, account_id, new_balance):
        """DEMO ONLY: mutate an account balance directly in the materialized
        table, bypassing the journal. Used to prove verification detects it."""
        cur = self.conn.execute("SELECT data FROM Accounts WHERE accountId=?", (account_id,))
        row = cur.fetchone()
        if row is None:
            raise AccountNotFound("account %s does not exist" % account_id)
        doc = json.loads(row["data"])
        doc["balance"] = round(float(new_balance), 2)
        self.conn.execute("BEGIN IMMEDIATE")
        try:
            self.conn.execute("UPDATE Accounts SET data=? WHERE accountId=?",
                              (canon(doc), account_id))
            self.conn.execute("COMMIT")
        except Exception:
            self.conn.execute("ROLLBACK")
            raise
        return doc


def open_ledger(path):
    return Ledger(path)
