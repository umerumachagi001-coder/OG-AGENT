-- Order Exception Agent — SQLite schema (owner: P1)
-- Mock "source systems" first, then the agent/dashboard tables from docs/ARCHITECTURE.md.
-- All timestamps are ISO-8601 UTC strings. Money is INR (float, 2dp).

PRAGMA foreign_keys = ON;

-- ───────────── Reference data ─────────────
CREATE TABLE warehouses (
    id          TEXT PRIMARY KEY,          -- WH-A
    name        TEXT NOT NULL,
    city        TEXT NOT NULL,
    transfer_cost_per_unit REAL NOT NULL   -- cost to move one unit out of this warehouse
);

CREATE TABLE products (
    sku          TEXT PRIMARY KEY,
    name         TEXT NOT NULL,
    category     TEXT NOT NULL,
    base_price   REAL NOT NULL,
    unit_cost    REAL NOT NULL,
    margin_floor_pct REAL NOT NULL         -- minimum allowed margin on sale price, e.g. 0.15
);

CREATE TABLE customers (
    id        TEXT PRIMARY KEY,
    name      TEXT NOT NULL,
    email     TEXT NOT NULL,
    tier      TEXT NOT NULL CHECK (tier IN ('VIP','Gold','Standard')),
    city      TEXT NOT NULL,
    country   TEXT NOT NULL DEFAULT 'IN'
);

-- ───────────── Inventory system ─────────────
CREATE TABLE inventory (
    sku           TEXT NOT NULL REFERENCES products(sku),
    warehouse_id  TEXT NOT NULL REFERENCES warehouses(id),
    on_hand       INTEGER NOT NULL,        -- what the system of record believes
    reserved      INTEGER NOT NULL,
    last_scan_qty INTEGER NOT NULL,        -- what the last physical scan found
    last_scan_at  TEXT NOT NULL,
    PRIMARY KEY (sku, warehouse_id)
);

-- ───────────── Supplier system ─────────────
CREATE TABLE suppliers (
    id               TEXT PRIMARY KEY,
    name             TEXT NOT NULL,
    city             TEXT NOT NULL,
    reliability      REAL NOT NULL,        -- on-time ratio 0–1
    contact_email    TEXT NOT NULL
);

CREATE TABLE supplier_catalog (
    supplier_id     TEXT NOT NULL REFERENCES suppliers(id),
    sku             TEXT NOT NULL REFERENCES products(sku),
    unit_cost       REAL NOT NULL,
    lead_time_days  INTEGER NOT NULL,
    available_qty   INTEGER NOT NULL,
    PRIMARY KEY (supplier_id, sku)
);

CREATE TABLE purchase_orders (
    id                  TEXT PRIMARY KEY,  -- PO-2001
    sku                 TEXT NOT NULL REFERENCES products(sku),
    supplier_id         TEXT NOT NULL REFERENCES suppliers(id),
    qty                 INTEGER NOT NULL,
    unit_cost           REAL NOT NULL,
    for_order_id        TEXT,              -- back-to-back PO for a specific customer order
    promised_ship_date  TEXT NOT NULL,
    expected_arrival    TEXT NOT NULL,
    status              TEXT NOT NULL,     -- open | shipped | delayed | cancelled | received
    delay_reason        TEXT
);

-- ───────────── Order system ─────────────
CREATE TABLE orders (
    id              TEXT PRIMARY KEY,      -- ORD-1001
    customer_id     TEXT NOT NULL REFERENCES customers(id),
    status          TEXT NOT NULL,         -- see services/common.py ORDER_STATUSES
    warehouse_id    TEXT REFERENCES warehouses(id),
    promo_codes     TEXT NOT NULL DEFAULT '[]',  -- JSON array
    subtotal        REAL NOT NULL,         -- list price × qty
    discount        REAL NOT NULL DEFAULT 0,
    total           REAL NOT NULL,         -- what the customer is charged
    carrier         TEXT NOT NULL DEFAULT 'STANDARD',
    created_at      TEXT NOT NULL,
    promised_date   TEXT NOT NULL
);

CREATE TABLE order_items (
    order_id    TEXT NOT NULL REFERENCES orders(id),
    sku         TEXT NOT NULL REFERENCES products(sku),
    qty         INTEGER NOT NULL,
    unit_price  REAL NOT NULL,             -- list price at time of order
    PRIMARY KEY (order_id, sku)
);

CREATE TABLE order_status_history (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    order_id  TEXT NOT NULL REFERENCES orders(id),
    status    TEXT NOT NULL,
    note      TEXT,
    ts        TEXT NOT NULL
);

-- ───────────── Payment system ─────────────
CREATE TABLE payment_methods (
    order_id        TEXT PRIMARY KEY REFERENCES orders(id),
    method          TEXT NOT NULL,         -- card | upi | netbanking
    card_last4      TEXT,
    card_expiry     TEXT,                  -- YYYY-MM
    billing_country TEXT NOT NULL DEFAULT 'IN',
    ip_country      TEXT NOT NULL DEFAULT 'IN'
);

CREATE TABLE payment_attempts (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    order_id     TEXT NOT NULL REFERENCES orders(id),
    attempt_no   INTEGER NOT NULL,
    amount       REAL NOT NULL,
    status       TEXT NOT NULL,            -- captured | declined | timeout | pending
    decline_code TEXT,                     -- expired_card | do_not_honor | insufficient_funds | suspected_fraud | gateway_timeout
    fraud_score  REAL NOT NULL,            -- 0–1 from the gateway's risk engine
    ts           TEXT NOT NULL
);

-- ───────────── Pricing system ─────────────
CREATE TABLE promotions (
    code        TEXT PRIMARY KEY,
    description TEXT NOT NULL,
    pct_off     REAL NOT NULL,             -- 0.20 = 20 %
    stackable   INTEGER NOT NULL,          -- 1 if allowed with other codes
    applies_to  TEXT NOT NULL DEFAULT 'ALL', -- category or ALL
    active      INTEGER NOT NULL DEFAULT 1
);

-- ───────────── Shipping / SLA ─────────────
CREATE TABLE carriers (
    id          TEXT PRIMARY KEY,          -- STANDARD | EXPRESS | NEXT_DAY
    name        TEXT NOT NULL,
    transit_days INTEGER NOT NULL,
    base_cost   REAL NOT NULL
);

-- ───────────── Agent / dashboard tables (docs/ARCHITECTURE.md) ─────────────
CREATE TABLE exceptions (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    order_id     TEXT NOT NULL REFERENCES orders(id),
    domain       TEXT NOT NULL CHECK (domain IN ('inventory','supplier','payment','pricing','deadline')),
    severity     TEXT NOT NULL CHECK (severity IN ('low','medium','high','critical')),
    status       TEXT NOT NULL DEFAULT 'open',  -- open | investigating | awaiting_approval | auto_resolved | resolved | rejected | failed
    symptom      TEXT,                          -- what the detector saw
    root_cause   TEXT,
    confidence   REAL,
    created_at   TEXT NOT NULL,
    resolved_at  TEXT
);

CREATE TABLE investigation_trail (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    exception_id  INTEGER NOT NULL REFERENCES exceptions(id),
    step_no       INTEGER NOT NULL,
    type          TEXT NOT NULL CHECK (type IN ('thought','tool_call','tool_result','decision')),
    content       TEXT NOT NULL,                -- JSON or text
    ts            TEXT NOT NULL
);

CREATE TABLE proposed_actions (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    exception_id      INTEGER NOT NULL REFERENCES exceptions(id),
    rank              INTEGER NOT NULL,
    action_type       TEXT NOT NULL,
    params_json       TEXT NOT NULL,
    cost              REAL NOT NULL DEFAULT 0,
    risk              TEXT NOT NULL DEFAULT 'low',  -- low | medium | high
    rationale         TEXT,
    requires_approval INTEGER NOT NULL DEFAULT 0,
    status            TEXT NOT NULL DEFAULT 'proposed' -- proposed | approved | rejected | executed | failed
);

CREATE TABLE resolutions (
    exception_id    INTEGER PRIMARY KEY REFERENCES exceptions(id),
    action_id       INTEGER REFERENCES proposed_actions(id),
    outcome         TEXT NOT NULL,              -- success | failed | rejected
    human_feedback  TEXT,
    resolved_by     TEXT NOT NULL,              -- agent | <human name>
    minutes_to_resolve REAL
);

-- Audit log of every action endpoint call (NFR-3).
CREATE TABLE action_log (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    action_type TEXT NOT NULL,
    params_json TEXT NOT NULL,
    result_json TEXT NOT NULL,
    ok          INTEGER NOT NULL,
    cost        REAL NOT NULL DEFAULT 0,
    ts          TEXT NOT NULL
);

-- Customer notifications drafted/sent (FR-14).
CREATE TABLE notifications (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    order_id  TEXT NOT NULL REFERENCES orders(id),
    channel   TEXT NOT NULL DEFAULT 'email',
    message   TEXT NOT NULL,
    ts        TEXT NOT NULL
);

CREATE INDEX idx_orders_status ON orders(status);
CREATE INDEX idx_exceptions_status ON exceptions(status);
CREATE INDEX idx_trail_exc ON investigation_trail(exception_id, step_no);
CREATE INDEX idx_pay_order ON payment_attempts(order_id);
