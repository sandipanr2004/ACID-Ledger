"""Test suite for the local ACID ledger.

Covers the guarantees the original QLDB recipe claims:
ACID atomicity, hash-chain integrity, tamper detection (state + journal),
Merkle proofs, digest pinning, temporal history, streaming and export.

Run:  python3 -m unittest discover -s tests -v
"""

import json
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ledger.core import InsufficientFunds, Ledger, LedgerError
from ledger.merkle import build_proof, merkle_root, verify_proof


class LedgerTestBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="ledger-test-")
        self.db_path = os.path.join(self.tmp, "ledger.db")
        self.ledger = Ledger(self.db_path)
        self.ledger.seed()

    def tearDown(self):
        self.ledger.close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def balances(self):
        return {row["document"]["accountId"]: row["document"]["balance"]
                for row in self.ledger.list_accounts()}


class TestSeedAndSchema(LedgerTestBase):
    def test_seed_creates_sample_data(self):
        self.assertEqual(self.balances(), {"ACC-001": 10000.00, "ACC-002": 25000.00})
        txns = self.ledger.list_transactions()
        self.assertEqual([t["key"] for t in txns], ["TXN-001", "TXN-002"])

    def test_seed_is_idempotent(self):
        self.assertIsNone(self.ledger.seed())

    def test_journal_has_entries_after_seed(self):
        n = self.ledger.conn.execute("SELECT COUNT(*) AS n FROM _journal").fetchone()["n"]
        self.assertEqual(n, 2)  # accounts tx + transactions tx


class TestAcidTransfer(LedgerTestBase):
    def test_transfer_moves_money_atomically(self):
        self.ledger.transfer("ACC-001", "ACC-002", 500.00, transaction_id="TXN-T1")
        self.assertEqual(self.balances(), {"ACC-001": 9500.00, "ACC-002": 25500.00})
        txn = [t for t in self.ledger.list_transactions() if t["key"] == "TXN-T1"]
        self.assertEqual(len(txn), 1)
        self.assertEqual(txn[0]["document"]["amount"], 500.00)

    def test_transfer_writes_audit_event(self):
        self.ledger.transfer("ACC-001", "ACC-002", 10.00)
        events = self.ledger._rows("AuditLog")
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["document"]["action"], "FUNDS_TRANSFER")

    def test_insufficient_funds_rejected_with_zero_writes(self):
        before = self.ledger.conn.execute("SELECT COUNT(*) AS n FROM _journal").fetchone()["n"]
        with self.assertRaises(InsufficientFunds):
            self.ledger.transfer("ACC-001", "ACC-002", 999999.00)
        after = self.ledger.conn.execute("SELECT COUNT(*) AS n FROM _journal").fetchone()["n"]
        self.assertEqual(before, after)
        self.assertEqual(self.balances(), {"ACC-001": 10000.00, "ACC-002": 25000.00})

    def test_unknown_account_rejected(self):
        with self.assertRaises(LedgerError):
            self.ledger.transfer("ACC-001", "ACC-999", 10.00)

    def test_negative_amount_rejected(self):
        with self.assertRaises(LedgerError):
            self.ledger.transfer("ACC-001", "ACC-002", -5.00)

    def test_failure_before_commit_rolls_back_everything(self):
        """Atomicity: journal entry AND partial state roll back together."""
        before = self.ledger.conn.execute("SELECT COUNT(*) AS n FROM _journal").fetchone()["n"]
        with self.assertRaises(LedgerError):
            self.ledger.transfer("ACC-002", "ACC-001", 1000.00, fail_before_commit=True)
        after = self.ledger.conn.execute("SELECT COUNT(*) AS n FROM _journal").fetchone()["n"]
        self.assertEqual(before, after)
        self.assertEqual(self.balances(), {"ACC-001": 10000.00, "ACC-002": 25000.00})
        self.assertTrue(self.ledger.verify()["ok"])


class TestHashChainAndDigest(LedgerTestBase):
    def test_chain_links_to_genesis(self):
        rows = self.ledger.conn.execute(
            "SELECT prev_hash FROM _journal ORDER BY seq").fetchall()
        self.assertEqual(rows[0]["prev_hash"], "0" * 64)

    def test_verify_passes_on_clean_ledger(self):
        report = self.ledger.verify()
        self.assertTrue(report["ok"], report)
        self.assertTrue(report["chain"]["ok"])
        self.assertTrue(report["state"]["ok"])

    def test_digest_stable_without_writes(self):
        d1 = self.ledger.get_digest()
        d2 = self.ledger.get_digest()
        self.assertEqual(d1["digest"], d2["digest"])
        self.assertEqual(d1["digestTipAddress"], d2["digestTipAddress"])

    def test_digest_changes_after_write(self):
        d1 = self.ledger.get_digest()
        self.ledger.transfer("ACC-001", "ACC-002", 1.00)
        d2 = self.ledger.get_digest()
        self.assertNotEqual(d1["digest"], d2["digest"])
        self.assertEqual(d2["digestTipAddress"]["seq"], d1["digestTipAddress"]["seq"] + 1)

    def test_digest_is_merkle_root_of_entries(self):
        hashes = [r["hash"] for r in self.ledger.conn.execute(
            "SELECT hash FROM _journal ORDER BY seq")]
        self.assertEqual(self.ledger.get_digest()["digest"], merkle_root(hashes))


class TestTamperDetection(LedgerTestBase):
    def test_state_tampering_detected(self):
        """Mutate a table row directly; state-vs-replay check must fail."""
        self.ledger.tamper_balance("ACC-001", 999999.00)
        report = self.ledger.verify()
        self.assertFalse(report["ok"])
        self.assertFalse(report["state"]["ok"])
        self.assertIn("ACC-001", report["state"]["tables"]["Accounts"]["mismatchedKeys"])
        # the hash chain itself is untouched - only state diverged
        self.assertTrue(report["chain"]["ok"])

    def test_journal_tampering_detected(self):
        """Edit a journal payload; recomputed hash must not match."""
        self.ledger.conn.execute("BEGIN IMMEDIATE")
        self.ledger.conn.execute(
            "UPDATE _journal SET payload = replace(payload, '500.0', '999.9') WHERE seq = 2")
        self.ledger.conn.execute("COMMIT")
        report = self.ledger.verify()
        self.assertFalse(report["ok"])
        self.assertFalse(report["chain"]["ok"])
        self.assertTrue(any("hash mismatch" in e for e in report["chain"]["errors"]))

    def test_full_chain_rewrite_caught_by_pinned_digest(self):
        """Rewrite the whole journal consistently; only the pinned digest
        (external anchor) can catch it."""
        digest = self.ledger.get_digest()
        # rewrite every payload consistently AND fix all hashes + links
        rows = self.ledger.conn.execute(
            "SELECT seq, prev_hash, payload, hash FROM _journal ORDER BY seq").fetchall()
        self.ledger.conn.execute("BEGIN IMMEDIATE")
        prev = "0" * 64
        import hashlib
        for row in rows:
            payload = row["payload"].replace("10000.0", "77777.0").replace(
                "25000.0", "88888.0")
            h = hashlib.sha256((prev + "\n" + payload).encode()).hexdigest()
            self.ledger.conn.execute(
                "UPDATE _journal SET prev_hash=?, payload=?, hash=? WHERE seq=?",
                (prev, payload, h, row["seq"]))
            prev = h
        self.ledger.conn.execute("COMMIT")
        report = self.ledger.verify(expected_digest=digest["digest"])
        self.assertFalse(report["ok"])
        self.assertFalse(report["digest"]["ok"])


class TestMerkleProofs(LedgerTestBase):
    def setUp(self):
        super().setUp()
        for i in range(5):  # ensure an odd-sized tree as well
            self.ledger.transfer("ACC-001", "ACC-002", 1.00 + i)

    def test_every_entry_has_valid_proof(self):
        hashes = [r["hash"] for r in self.ledger.conn.execute(
            "SELECT hash FROM _journal ORDER BY seq")]
        for seq in range(1, len(hashes) + 1):
            proof = self.ledger.proof_for_seq(seq)
            self.assertTrue(verify_proof(proof["leaf"], proof), "seq %d" % seq)
            self.assertEqual(proof["root"], merkle_root(hashes))

    def test_proof_rejects_wrong_leaf(self):
        proof = self.ledger.proof_for_seq(1)
        fake = "ab" * 32
        self.assertFalse(verify_proof(fake, proof))

    def test_proof_for_missing_seq_raises(self):
        with self.assertRaises(LedgerError):
            self.ledger.proof_for_seq(9999)

    def test_build_proof_out_of_range(self):
        with self.assertRaises(IndexError):
            build_proof(["aa"], 5)


class TestHistory(LedgerTestBase):
    def test_history_shows_all_versions(self):
        self.ledger.transfer("ACC-001", "ACC-002", 500.00)
        history = self.ledger.history("Accounts", "ACC-001")
        self.assertEqual(len(history), 2)  # INSERT + one UPDATE
        self.assertEqual(history[0]["action"], "INSERT")
        self.assertEqual(history[0]["document"]["balance"], 10000.00)
        self.assertEqual(history[1]["action"], "UPDATE")
        self.assertEqual(history[1]["document"]["balance"], 9500.00)
        self.assertEqual(history[0]["document"]["createdAt"], "2024-01-01T00:00:00Z")
        # every version carries tx metadata like QLDB's metadata.txTime/txId
        for entry in history:
            self.assertIn("txId", entry)
            self.assertIn("txTime", entry)

    def test_history_for_unknown_key_is_empty(self):
        self.assertEqual(self.ledger.history("Accounts", "ACC-XXX"), [])


class TestStreamAndExport(LedgerTestBase):
    def test_stream_yields_all_entries_in_order(self):
        entries = list(self.ledger.stream_entries())
        self.assertEqual([e["seq"] for e in entries], [1, 2])
        self.assertTrue(all("hash" in e and "payload" in e for e in entries))

    def test_stream_from_seq(self):
        entries = list(self.ledger.stream_entries(from_seq=2))
        self.assertEqual([e["seq"] for e in entries], [2])

    def test_export_writes_verifiable_bundle(self):
        path, count = self.ledger.export(self.tmp)
        self.assertEqual(count, 2)
        with open(path) as fh:
            bundle = json.load(fh)
        self.assertEqual(bundle["entryCount"], 2)
        self.assertEqual(bundle["digest"]["digest"], self.ledger.get_digest()["digest"])
        hashes = [e["hash"] for e in bundle["entries"]]
        self.assertEqual(merkle_root(hashes), bundle["digest"]["digest"])


class TestVerificationCli(unittest.TestCase):
    """End-to-end through the CLI entry point (like the recipe's validation)."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="ledger-cli-")
        self.db = os.path.join(self.tmp, "ledger.db")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def run_cli(self, *argv):
        from ledger.cli import main
        import io
        from contextlib import redirect_stdout, redirect_stderr
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = main(["--db", self.db] + list(argv))
        return code, out.getvalue(), err.getvalue()

    def test_full_flow_through_cli(self):
        code, _, _ = self.run_cli("init")
        self.assertEqual(code, 0)
        code, _, _ = self.run_cli("seed")
        self.assertEqual(code, 0)
        code, out, _ = self.run_cli("transfer", "ACC-001", "ACC-002", "25")
        self.assertEqual(code, 0)
        digest_file = os.path.join(self.tmp, "digest.json")
        code, _, _ = self.run_cli("digest", "-o", digest_file)
        self.assertEqual(code, 0)
        code, out, _ = self.run_cli("verify", "--digest-file", digest_file)
        self.assertEqual(code, 0)
        self.assertIn("VERIFIED", out)
        # insufficient funds -> non-zero exit, clear message
        code, _, err = self.run_cli("transfer", "ACC-001", "ACC-002", "99999999")
        self.assertEqual(code, 3)
        self.assertIn("TRANSFER REJECTED", err)


if __name__ == "__main__":
    unittest.main()
