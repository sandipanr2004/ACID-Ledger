#!/usr/bin/env python3
"""Tiny stdlib-only web UI for the local ACID ledger.

Serves the dashboard (web/index.html + assets) and a JSON API:

  GET  /api/state     accounts, transactions, audit, journal tail, digest, verify
  GET  /api/journal   full journal (JSONL-equivalent, as JSON)
  GET  /api/verify    fresh integrity verification report
  POST /api/transfer  {from, to, amount, description?} -> atomic transfer
  POST /api/seed      load the recipe's sample data (idempotent)
  POST /api/tamper    DEMO ONLY: mutate a balance to showcase tamper detection

Zero dependencies: python3 standard library only (like the rest of the project).
"""

import argparse
import json
import os
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from ledger.core import Ledger, LedgerError  # noqa: E402

WEB_DIR = os.path.join(ROOT, "web")
CONTENT_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".js": "application/javascript; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".svg": "image/svg+xml",
}

DB_PATH = os.environ.get("LEDGER_DB", os.path.join(ROOT, "data", "ledger.db"))


def ledger_state(db_path, journal_limit=25):
    ledger = Ledger(db_path)
    try:
        journal = list(ledger.stream_entries())
        return {
            "ledger": db_path,
            "entryCount": len(journal),
            "accounts": ledger.list_accounts(),
            "transactions": ledger.list_transactions(),
            "auditEvents": ledger._rows("AuditLog"),
            "journal": list(reversed(journal))[:journal_limit],
            "digest": ledger.get_digest(),
            "verify": ledger.verify(),
        }
    finally:
        ledger.close()


class Handler(BaseHTTPRequestHandler):
    server_version = "LedgerWeb/1.0"
    db_path = DB_PATH  # overridden in main() from --db

    def log_message(self, fmt, *args):
        pass  # keep the console clean; errors still raise

    # -- helpers ------------------------------------------------------

    def _send(self, status, body, content_type):
        data = body if isinstance(body, bytes) else body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def _json(self, obj, status=200):
        self._send(status, json.dumps(obj, sort_keys=True),
                   "application/json; charset=utf-8")

    def _error(self, status, message):
        self._json({"error": message}, status)

    def _read_body(self):
        length = int(self.headers.get("Content-Length") or 0)
        if not length:
            return {}
        return json.loads(self.rfile.read(length).decode("utf-8") or "{}")

    # -- GET ----------------------------------------------------------

    def do_GET(self):
        path = self.path.split("?", 1)[0]
        try:
            if path in ("/", "/index.html"):
                return self._static("index.html")
            if path == "/api/state":
                return self._json(ledger_state(self.db_path))
            if path == "/api/journal":
                ledger = Ledger(self.db_path)
                try:
                    return self._json(ledger._entry_dicts())
                finally:
                    ledger.close()
            if path == "/api/verify":
                ledger = Ledger(self.db_path)
                try:
                    return self._json(ledger.verify())
                finally:
                    ledger.close()
            if path.startswith("/api/"):
                return self._error(404, "unknown API route: %s" % path)
            return self._static(path.lstrip("/"))
        except FileNotFoundError:
            return self._error(404, "not found")
        except Exception as exc:  # surface anything unexpected as JSON
            return self._error(500, "%s: %s" % (type(exc).__name__, exc))

    def _static(self, name):
        full = os.path.realpath(os.path.join(WEB_DIR, name))
        if not full.startswith(WEB_DIR + os.sep):
            return self._error(403, "forbidden")
        with open(full, "rb") as fh:
            ext = os.path.splitext(full)[1]
            self._send(200, fh.read(), CONTENT_TYPES.get(ext, "application/octet-stream"))

    # -- POST ---------------------------------------------------------

    def do_POST(self):
        path = self.path.split("?", 1)[0]
        try:
            body = self._read_body()
        except Exception:
            return self._error(400, "invalid JSON body")
        try:
            if path == "/api/transfer":
                return self._transfer(body)
            if path == "/api/seed":
                ledger = Ledger(self.db_path)
                try:
                    result = ledger.seed()
                    return self._json({"ok": True, "seeded": result is not None})
                finally:
                    ledger.close()
            if path == "/api/tamper":
                return self._tamper(body)
            return self._error(404, "unknown API route: %s" % path)
        except LedgerError as exc:
            return self._error(400, str(exc))
        except Exception as exc:
            return self._error(500, "%s: %s" % (type(exc).__name__, exc))

    def _transfer(self, body):
        from_id = body.get("from")
        to_id = body.get("to")
        amount = body.get("amount")
        if not from_id or not to_id or amount is None:
            return self._error(400, "'from', 'to' and 'amount' are required")
        try:
            amount = float(amount)
        except (TypeError, ValueError):
            return self._error(400, "'amount' must be a number")
        ledger = Ledger(self.db_path)
        try:
            result = ledger.transfer(from_id, to_id, amount,
                                     description=body.get("description") or "Transfer")
        finally:
            ledger.close()
        return self._json({"ok": True, "result": result})

    def _tamper(self, body):
        account = body.get("account")
        balance = body.get("balance")
        if account is None or balance is None:
            return self._error(400, "'account' and 'balance' are required")
        ledger = Ledger(self.db_path)
        try:
            doc = ledger.tamper_balance(account, balance)
        finally:
            ledger.close()
        return self._json({"ok": True, "account": account, "balance": doc["balance"],
                           "warning": "state now diverges from the journal - "
                                      "the dashboard will show TAMPERING DETECTED"})


def main():
    parser = argparse.ArgumentParser(description="Web UI for the local ACID ledger")
    parser.add_argument("--db", default=DB_PATH)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8787)
    args = parser.parse_args()

    Handler.db_path = os.path.abspath(args.db)

    server = ThreadingHTTPServer((args.host, args.port), Handler)
    print("Ledger web UI:  http://%s:%d   (db=%s)" % (args.host, args.port, Handler.db_path),
          flush=True)
    print("Open it in a browser. Ctrl+C to stop.", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
