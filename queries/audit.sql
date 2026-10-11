-- Audit trail queries (recipe: "Create Audit Trail Query Functions")
-- Run with: bin/ledgerctl query -f queries/audit.sql
-- Document fields live in the `data` JSON column; use json_extract.
-- Note: temporal queries (history) are available via `ledgerctl history <table> <key>`
-- because they read the journal, not the materialized tables.

-- All transactions involving ACC-001
SELECT transactionId,
       json_extract(data, '$.fromAccountId') AS from_account,
       json_extract(data, '$.toAccountId')   AS to_account,
       json_extract(data, '$.amount')        AS amount,
       json_extract(data, '$.timestamp')     AS timestamp
FROM Transactions
WHERE json_extract(data, '$.fromAccountId') = 'ACC-001'
   OR json_extract(data, '$.toAccountId') = 'ACC-001'
ORDER BY transactionId;

-- Transactions above 400.00 (amount threshold for compliance review)
SELECT transactionId,
       json_extract(data, '$.amount')   AS amount,
       json_extract(data, '$.status')   AS status,
       json_extract(data, '$.timestamp') AS timestamp
FROM Transactions
WHERE json_extract(data, '$.amount') > 400.0
ORDER BY amount DESC;

-- Current balances of all accounts
SELECT accountId,
       json_extract(data, '$.balance')  AS balance,
       json_extract(data, '$.currency') AS currency,
       json_extract(data, '$.status')   AS status
FROM Accounts
ORDER BY accountId;

-- Every audit event (the immutable record of all funds movements)
SELECT eventId,
       json_extract(data, '$.action')       AS action,
       json_extract(data, '$.fromAccountId') AS from_account,
       json_extract(data, '$.toAccountId')  AS to_account,
       json_extract(data, '$.amount')       AS amount
FROM AuditLog
ORDER BY eventId;

-- Journal metadata: every entry with its hash chain position
SELECT seq, tx_id, tx_time,
       substr(prev_hash, 1, 16) AS prev_hash_prefix,
       substr(hash, 1, 16)      AS hash_prefix
FROM _journal
ORDER BY seq;
