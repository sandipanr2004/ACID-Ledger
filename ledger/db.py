"""SQLite connection + schema for the local ledger.

Tables
------
_journal       append-only, hash-chained transaction log (the QLDB journal)
Accounts       materialized current state of accounts
Transactions   materialized current state of transactions
AuditLog       materialized current state of audit events

Every write to a materialized table happens inside the same SQLite
transaction that appends to _journal, so journal and state can never
diverge at commit time (journal-first, then materialize).
"""

import os
import sqlite3

SCHEMA = """
CREATE TABLE IF NOT EXISTS _journal (
    seq       INTEGER PRIMARY KEY,
    tx_id     TEXT NOT NULL UNIQUE,
    tx_time   TEXT NOT NULL,
    prev_hash TEXT NOT NULL,
    payload   TEXT NOT NULL,
    hash      TEXT NOT NULL UNIQUE
);

CREATE TABLE IF NOT EXISTS Accounts (
    accountId TEXT PRIMARY KEY,
    data      TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS Transactions (
    transactionId TEXT PRIMARY KEY,
    data          TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS AuditLog (
    eventId TEXT PRIMARY KEY,
    data    TEXT NOT NULL
);
"""


def connect(path):
    """Open (and create parent dirs for) the ledger database.

    isolation_level=None puts sqlite3 in autocommit mode so transactions are
    managed explicitly with BEGIN IMMEDIATE / COMMIT / ROLLBACK.
    """
    parent = os.path.dirname(os.path.abspath(path))
    if parent:
        os.makedirs(parent, exist_ok=True)
    conn = sqlite3.connect(path, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def init_schema(conn):
    conn.executescript(SCHEMA)
