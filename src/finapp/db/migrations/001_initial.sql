-- 001: core data model (see docs/PLAN.md, "Core data model").
-- Money is stored as integer minor units (cents) in the original currency.
-- Sign convention: negative = money out of the account, positive = money in.

CREATE TABLE institution (
    id          INTEGER PRIMARY KEY,
    name        TEXT NOT NULL,
    country     TEXT NOT NULL,              -- ISO 3166 alpha-2, e.g. 'US', 'CA'
    source      TEXT NOT NULL,              -- adapter that feeds it: 'csv', 'ofx', ...
    UNIQUE (name, country)
);

CREATE TABLE account (
    id              INTEGER PRIMARY KEY,
    institution_id  INTEGER NOT NULL REFERENCES institution(id),
    external_key    TEXT NOT NULL UNIQUE,   -- stable id from the source, e.g. 'csv:chase:checking'
    name            TEXT NOT NULL,
    type            TEXT NOT NULL CHECK (type IN
                    ('checking','savings','credit_card','investment','retirement','loan','cash','other')),
    currency        TEXT NOT NULL,          -- ISO 4217, e.g. 'USD', 'CAD'
    jurisdiction    TEXT NOT NULL,          -- country the account is held in
    is_foreign      INTEGER NOT NULL DEFAULT 0,  -- relative to tax residency (US): counts toward FBAR
    closed_at       TEXT,
    created_at      TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE sync_state (
    account_id       INTEGER PRIMARY KEY REFERENCES account(id),
    last_success_at  TEXT,
    last_attempt_at  TEXT,
    status           TEXT NOT NULL DEFAULT 'never' CHECK (status IN ('never','ok','error','stale')),
    error            TEXT,
    source           TEXT
);

CREATE TABLE category (
    id         INTEGER PRIMARY KEY,
    parent_id  INTEGER REFERENCES category(id),
    name       TEXT NOT NULL,
    UNIQUE (parent_id, name)
);

CREATE TABLE merchant (
    id                   INTEGER PRIMARY KEY,
    canonical_name       TEXT NOT NULL UNIQUE,
    mcc                  TEXT,
    default_category_id  INTEGER REFERENCES category(id)
);

CREATE TABLE txn (
    id               INTEGER PRIMARY KEY,
    account_id       INTEGER NOT NULL REFERENCES account(id),
    posted_at        TEXT NOT NULL,          -- ISO date YYYY-MM-DD
    pending          INTEGER NOT NULL DEFAULT 0,
    amount_minor     INTEGER NOT NULL,       -- cents, original currency, signed
    currency         TEXT NOT NULL,
    raw_descriptor   TEXT NOT NULL,
    merchant_id      INTEGER REFERENCES merchant(id),
    external_id      TEXT,                   -- source's own id (e.g. OFX FITID), informational
    source           TEXT NOT NULL,
    source_hash      TEXT NOT NULL UNIQUE,   -- content hash for cross-source dedup
    imported_at      TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX idx_txn_account_date ON txn(account_id, posted_at);

CREATE TABLE split (
    id              INTEGER PRIMARY KEY,
    txn_id          INTEGER NOT NULL REFERENCES txn(id) ON DELETE CASCADE,
    amount_minor    INTEGER NOT NULL,        -- splits of a txn sum to its amount
    category_id     INTEGER REFERENCES category(id),
    flow_type       TEXT NOT NULL CHECK (flow_type IN ('spend','income','transfer','investment','refund')),
    budget_include  INTEGER NOT NULL DEFAULT 1,
    confidence      REAL,                    -- categorization confidence, NULL = uncategorized
    note            TEXT,
    tags            TEXT NOT NULL DEFAULT '[]'  -- JSON array
);
CREATE INDEX idx_split_txn ON split(txn_id);

CREATE TABLE rule (
    id          INTEGER PRIMARY KEY,
    conditions  TEXT NOT NULL,               -- JSON: merchant, account, amount band, ...
    action      TEXT NOT NULL,               -- JSON: category, flow_type, budget_include
    confidence  REAL NOT NULL DEFAULT 1.0,
    origin      TEXT NOT NULL CHECK (origin IN ('user','learned')),
    created_at  TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE fx_rate (
    date    TEXT NOT NULL,                   -- YYYY-MM-DD
    base    TEXT NOT NULL,                   -- 1 unit of base ...
    quote   TEXT NOT NULL,                   -- ... = rate units of quote
    rate    TEXT NOT NULL,                   -- decimal string, exact
    source  TEXT NOT NULL,                   -- 'boc' (Bank of Canada), 'treasury', 'manual'
    PRIMARY KEY (date, base, quote, source)
);

CREATE TABLE budget (
    id            INTEGER PRIMARY KEY,
    period        TEXT NOT NULL,             -- 'YYYY-MM'
    category_id   INTEGER NOT NULL REFERENCES category(id),
    amount_minor  INTEGER NOT NULL,
    currency      TEXT NOT NULL,
    rollover      INTEGER NOT NULL DEFAULT 0,
    UNIQUE (period, category_id)
);

CREATE TABLE balance_snapshot (
    account_id     INTEGER NOT NULL REFERENCES account(id),
    date           TEXT NOT NULL,
    balance_minor  INTEGER NOT NULL,
    currency       TEXT NOT NULL,
    source         TEXT NOT NULL,
    PRIMARY KEY (account_id, date)
);
