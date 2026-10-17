"""Seed the SQLite DB with a clean, healthy demo world (owner: P1, FR-1).

    python -m data.seed          # or: make seed

Everything is deterministic (fixed RNG seed) and timestamps are relative to "now",
so the demo looks the same whenever you reset it.

Demo orders ORD-1001…ORD-1004 start HEALTHY. Scenarios in services/scenarios.py break them:
    sc1          ORD-1001  phantom stock in WH-A          (inventory)
    sc2          ORD-1002  supplier PO slips 6 days        (supplier / deadline)
    sc3_expired  ORD-1003  card declined: expired card     (payment, low risk)
    sc3_fraud    ORD-1003  card declined: fraud signals    (payment, high risk)
    sc4          ORD-1004  non-stackable promos stacked    (pricing)
    sc5          ORD-1001  phantom stock + tight deadline   (inventory + deadline)
"""
from __future__ import annotations

import json
import random
import sys
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from services.common import DB_PATH, connect, days_from_now, iso, utcnow  # noqa: E402
from services.logic import price_lines  # noqa: E402

SCHEMA = Path(__file__).with_name("schema.sql")
RNG_SEED = 42

WAREHOUSES = [
    # id, name, city, extra cost per unit when an order is fulfilled from here
    ("WH-A", "Bengaluru Hub", "Bengaluru", 40.0),
    ("WH-B", "Mumbai Hub", "Mumbai", 60.0),
    ("WH-C", "Delhi Hub", "Delhi", 90.0),
]

PRODUCTS = [
    # sku, name, category, base_price, unit_cost, margin_floor
    ("SKU-HDPH", "Wireless Headphones", "electronics", 7999, 4800, 0.15),
    ("SKU-SPKR", "Bluetooth Speaker", "electronics", 4499, 2700, 0.15),
    ("SKU-WTCH", "Smartwatch", "electronics", 12999, 8200, 0.15),
    ("SKU-KBRD", "Mechanical Keyboard", "electronics", 5499, 3300, 0.15),
    ("SKU-MOUS", "Wireless Mouse", "electronics", 1499, 800, 0.15),
    ("SKU-MNTR", "27-inch 4K Monitor", "electronics", 18999, 13000, 0.12),
    ("SKU-CHAR", "Ergonomic Chair", "furniture", 14999, 9000, 0.15),
    ("SKU-DESK", "Standing Desk", "furniture", 29999, 19500, 0.15),
    ("SKU-LAMP", "LED Desk Lamp", "home", 2499, 1200, 0.20),
    ("SKU-BOTL", "Steel Bottle", "home", 899, 350, 0.20),
    ("SKU-BAG", "Laptop Backpack", "accessories", 3299, 1700, 0.20),
    ("SKU-CABL", "USB-C Cable", "accessories", 699, 180, 0.20),
]

CUSTOMERS = [
    ("CUST-01", "Ananya Iyer", "VIP", "Bengaluru"),
    ("CUST-02", "Rohan Mehta", "VIP", "Mumbai"),
    ("CUST-03", "Kavya Reddy", "VIP", "Hyderabad"),
    ("CUST-04", "Arjun Nair", "Gold", "Kochi"),
    ("CUST-05", "Priya Sharma", "Gold", "Delhi"),
    ("CUST-06", "Vikram Singh", "Gold", "Jaipur"),
    ("CUST-07", "Meera Joshi", "Gold", "Pune"),
    ("CUST-08", "Aditya Rao", "Gold", "Bengaluru"),
    ("CUST-09", "Sneha Kulkarni", "Gold", "Pune"),
    ("CUST-10", "Rahul Verma", "Standard", "Lucknow"),
    ("CUST-11", "Divya Menon", "Standard", "Chennai"),
    ("CUST-12", "Karan Malhotra", "Standard", "Chandigarh"),
    ("CUST-13", "Ishita Das", "Standard", "Kolkata"),
    ("CUST-14", "Nikhil Gupta", "Standard", "Indore"),
    ("CUST-15", "Pooja Patel", "Standard", "Ahmedabad"),
    ("CUST-16", "Siddharth Bose", "Standard", "Kolkata"),
    ("CUST-17", "Neha Agarwal", "Standard", "Delhi"),
    ("CUST-18", "Varun Pillai", "Standard", "Thiruvananthapuram"),
    ("CUST-19", "Tanvi Desai", "Standard", "Surat"),
    ("CUST-20", "Aman Khanna", "Standard", "Bengaluru"),
]

SUPPLIERS = [
    ("SUP-1", "FurniCraft Industries", "Pune", 0.91),
    ("SUP-2", "Voltline Electronics", "Chennai", 0.95),
    ("SUP-3", "OfficeWorks Supply", "Bengaluru", 0.97),
    ("SUP-4", "Northstar Distributors", "Delhi", 0.88),
]

# supplier, sku, unit_cost, lead_time_days, available_qty
CATALOG = [
    ("SUP-1", "SKU-DESK", 19500, 3, 40),
    ("SUP-1", "SKU-CHAR", 9000, 3, 60),
    ("SUP-3", "SKU-DESK", 21500, 2, 8),     # SC-2 alternate: dearer, faster
    ("SUP-3", "SKU-CHAR", 9600, 2, 25),
    ("SUP-3", "SKU-LAMP", 1250, 2, 200),
    ("SUP-2", "SKU-HDPH", 4800, 4, 300),
    ("SUP-2", "SKU-SPKR", 2700, 4, 300),
    ("SUP-2", "SKU-WTCH", 8200, 5, 150),
    ("SUP-2", "SKU-KBRD", 3300, 4, 200),
    ("SUP-2", "SKU-MOUS", 800, 3, 500),
    ("SUP-2", "SKU-MNTR", 13000, 6, 80),
    ("SUP-4", "SKU-HDPH", 5100, 3, 120),
    ("SUP-4", "SKU-MNTR", 13600, 4, 30),
    ("SUP-4", "SKU-DESK", 22800, 6, 10),    # SC-2 second alternate: dearest, too slow
    ("SUP-4", "SKU-BOTL", 350, 2, 900),
    ("SUP-4", "SKU-BAG", 1700, 3, 400),
    ("SUP-4", "SKU-CABL", 180, 2, 2000),
]

PROMOTIONS = [
    ("FEST20", "Festive sale 20% off", 0.20, 1, "ALL"),
    ("WELCOME5", "Welcome back 5% off", 0.05, 1, "ALL"),
    ("NEWUSER15", "First order 15% off — cannot be combined", 0.15, 0, "ALL"),
    ("CLEAR30", "Accessories clearance 30% off — cannot be combined", 0.30, 0, "accessories"),
]

CARRIERS = [
    ("STANDARD", "Standard Ground", 4, 0.0),
    ("EXPRESS", "Express Air", 2, 350.0),
    ("NEXT_DAY", "Next-Day Priority", 1, 900.0),
]

# Starting stock: sku -> {warehouse: on_hand}. Reserved is computed from open orders.
STOCK = {
    "SKU-HDPH": {"WH-A": 5, "WH-B": 12, "WH-C": 1},   # SC-1: WH-A will turn out to be phantom
    "SKU-SPKR": {"WH-A": 30, "WH-B": 22, "WH-C": 15},
    "SKU-WTCH": {"WH-A": 14, "WH-B": 9, "WH-C": 6},
    "SKU-KBRD": {"WH-A": 25, "WH-B": 18, "WH-C": 12},
    "SKU-MOUS": {"WH-A": 80, "WH-B": 60, "WH-C": 45},
    "SKU-MNTR": {"WH-A": 8, "WH-B": 11, "WH-C": 4},
    "SKU-CHAR": {"WH-A": 6, "WH-B": 9, "WH-C": 3},
    "SKU-DESK": {"WH-A": 0, "WH-B": 0, "WH-C": 0},    # made to order — always via supplier PO
    "SKU-LAMP": {"WH-A": 40, "WH-B": 35, "WH-C": 20},
    "SKU-BOTL": {"WH-A": 150, "WH-B": 120, "WH-C": 90},
    "SKU-BAG": {"WH-A": 45, "WH-B": 30, "WH-C": 25},
    "SKU-CABL": {"WH-A": 300, "WH-B": 250, "WH-C": 200},
}

OPEN_STATUSES = ("paid", "allocated", "picking", "awaiting_stock")

# Past resolved exceptions (for find_similar_cases + metrics history).
# domain, severity, symptom, root_cause, action_type, params, cost, resolved_by, minutes, feedback
HISTORY = [
    ("inventory", "medium", "Pick failed: item not found at bin; system shows stock",
     "Phantom stock: cycle-count drift at source warehouse", "reallocate_stock",
     {"from": "WH-A", "to": "WH-B"}, 120, "agent", 0.6, None),
    ("inventory", "low", "Pick failed: item not found at bin; system shows stock",
     "Phantom stock: units mis-shelved in returns area", "reallocate_stock",
     {"from": "WH-C", "to": "WH-A"}, 40, "agent", 0.5, None),
    ("inventory", "high", "Oversell: reserved exceeds on-hand",
     "Reservation held by cancelled order not released", "reallocate_stock",
     {"from": "WH-B", "to": "WH-A"}, 80, "agent", 0.7, None),
    ("supplier", "high", "Supplier PO delayed; delivery SLA at risk",
     "Supplier production delay", "switch_supplier",
     {"supplier_id": "SUP-3"}, 2000, "Ops: Farhan", 14.0, "Approved — customer is Gold tier"),
    ("supplier", "medium", "Supplier PO delayed; delivery SLA at risk",
     "Supplier partial shipment", "expedite_shipping",
     {"carrier": "EXPRESS"}, 350, "agent", 0.8, None),
    ("payment", "low", "Payment declined",
     "Card expired; customer has not updated card", "notify_customer",
     {"template": "update_payment_method"}, 0, "agent", 0.4, None),
    ("payment", "low", "Payment gateway timeout",
     "Transient gateway timeout", "retry_payment", {}, 0, "agent", 0.3, None),
    ("payment", "critical", "Payment declined",
     "High fraud score, IP/billing country mismatch, rapid retries", "hold_order",
     {"reason": "fraud_review"}, 0, "Risk: Leela", 22.0, "Confirmed fraud — order cancelled"),
    ("pricing", "high", "Order total below margin floor",
     "Non-stackable promo codes stacked by checkout bug", "apply_price_correction",
     {"rule": "best_single_valid_promo"}, 0, "Ops: Farhan", 9.0, "Approved; customer accepted corrected price"),
    ("pricing", "medium", "Order total below margin floor",
     "Stale price list after supplier cost increase", "hold_order",
     {"reason": "pricing_review"}, 0, "Ops: Farhan", 31.0, "Escalated to category manager"),
    ("deadline", "medium", "Promised delivery date at risk",
     "Warehouse backlog delayed packing", "expedite_shipping",
     {"carrier": "EXPRESS"}, 350, "agent", 0.5, None),
    ("deadline", "high", "Promised delivery date at risk",
     "Carrier hub disruption", "expedite_shipping",
     {"carrier": "NEXT_DAY"}, 900, "Ops: Farhan", 11.0, "Approved — VIP customer"),
]


def _reset_schema(conn):
    tables = [r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")]
    conn.execute("PRAGMA foreign_keys = OFF")
    for t in tables:
        conn.execute(f'DROP TABLE IF EXISTS "{t}"')
    conn.executescript(SCHEMA.read_text())
    conn.execute("PRAGMA foreign_keys = ON")


def _insert_order(conn, oid, cust, status, wh, items, codes, created_days_ago, promised_in_days,
                  carrier="STANDARD", history=None):
    lines = [(sku, qty) for sku, qty in items]
    price = price_lines(conn, lines, codes)
    total = price["price_as_applied"]
    created_days_ago = max(created_days_ago, 0.05)
    created = days_from_now(-created_days_ago)
    conn.execute(
        "INSERT INTO orders(id, customer_id, status, warehouse_id, promo_codes, subtotal, discount, total, carrier,"
        " created_at, promised_date) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        (oid, cust, status, wh, json.dumps(codes), price["subtotal"],
         round(price["subtotal"] - total, 2), total, carrier, created, days_from_now(promised_in_days)),
    )
    for sku, qty in items:
        base = conn.execute("SELECT base_price FROM products WHERE sku=?", (sku,)).fetchone()[0]
        conn.execute("INSERT INTO order_items VALUES (?,?,?,?)", (oid, sku, qty, base))
    for i, st in enumerate(history or ["placed", "paid", status]):
        conn.execute("INSERT INTO order_status_history(order_id, status, note, ts) VALUES (?,?,?,?)",
                     (oid, st, None, days_from_now(-created_days_ago + i * 0.02)))
    return total


def _captured_payment(conn, oid, amount, days_ago, rng):
    conn.execute("INSERT INTO payment_methods VALUES (?,?,?,?,?,?)",
                 (oid, "card", f"{rng.randint(1000, 9999)}", f"{utcnow().year + rng.randint(1, 4)}-{rng.randint(1, 12):02d}", "IN", "IN"))
    conn.execute("INSERT INTO payment_attempts(order_id, attempt_no, amount, status, decline_code, fraud_score, ts)"
                 " VALUES (?,?,?,?,?,?,?)",
                 (oid, 1, amount, "captured", None, round(rng.uniform(0.01, 0.12), 2), days_from_now(-days_ago + 0.01)))


def build(db_path: Path = DB_PATH) -> dict:
    """Rebuild the demo world. Built in a temporary file first and then copied over the live database in one
    step (SQLite backup), so a dashboard or n8n request during "Reset demo" sees the old data or the new data,
    never half-built tables."""
    import sqlite3
    db_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = db_path.with_name(db_path.name + ".building")
    tmp.unlink(missing_ok=True)
    try:
        counts = _build_into(tmp)
        src = sqlite3.connect(tmp)
        dst = sqlite3.connect(db_path, timeout=30)
        try:
            src.backup(dst)
        finally:
            src.close()
            dst.close()
        return counts
    finally:
        tmp.unlink(missing_ok=True)


def _build_into(db_path: Path) -> dict:
    rng = random.Random(RNG_SEED)
    conn = _connect_path(db_path)
    try:
        _reset_schema(conn)
        conn.executemany("INSERT INTO warehouses VALUES (?,?,?,?)", WAREHOUSES)
        conn.executemany("INSERT INTO products VALUES (?,?,?,?,?,?)", PRODUCTS)
        conn.executemany("INSERT INTO customers VALUES (?,?,?,?,?,?)",
                         [(cid, n, n.split()[0].lower() + "@example.com", t, c, "IN") for cid, n, t, c in CUSTOMERS])
        conn.executemany("INSERT INTO suppliers VALUES (?,?,?,?,?)",
                         [(sid, n, c, r, f"orders@{sid.lower()}.example.com") for sid, n, c, r in SUPPLIERS])
        conn.executemany("INSERT INTO supplier_catalog VALUES (?,?,?,?,?)", CATALOG)
        conn.executemany("INSERT INTO promotions(code, description, pct_off, stackable, applies_to) VALUES (?,?,?,?,?)", PROMOTIONS)
        conn.executemany("INSERT INTO carriers VALUES (?,?,?,?)", CARRIERS)
        for sku, by_wh in STOCK.items():
            for wh, qty in by_wh.items():
                conn.execute("INSERT INTO inventory VALUES (?,?,?,?,?,?)",
                             (sku, wh, qty, 0, qty, days_from_now(-rng.uniform(0.1, 1.5))))

        # ── Demo orders (healthy) ──
        # ORD-1001 · SC-1 · Standard customer, 2 headphones allocated to WH-A
        t = _insert_order(conn, "ORD-1001", "CUST-11", "allocated", "WH-A", [("SKU-HDPH", 2)], [], 0, 5,
                          history=["placed", "paid", "allocated"])
        _captured_payment(conn, "ORD-1001", t, 0, rng)
        # ORD-1002 · SC-2 · Gold customer, standing desk made to order via PO-2001 from SUP-1
        t = _insert_order(conn, "ORD-1002", "CUST-07", "awaiting_stock", "WH-A", [("SKU-DESK", 1)], [], 1, 6,
                          history=["placed", "paid", "awaiting_stock"])
        _captured_payment(conn, "ORD-1002", t, 1, rng)
        conn.execute("INSERT INTO purchase_orders VALUES (?,?,?,?,?,?,?,?,?,?)",
                     ("PO-2001", "SKU-DESK", "SUP-1", 1, 19500, "ORD-1002",
                      days_from_now(0.3), days_from_now(1), "open", None))
        # ORD-1003 · SC-3 · Gold customer, smartwatch, payment authorisation pending
        t = _insert_order(conn, "ORD-1003", "CUST-05", "payment_pending", None, [("SKU-WTCH", 1)], [], 0, 6,
                          history=["placed", "payment_pending"])
        conn.execute("INSERT INTO payment_methods VALUES (?,?,?,?,?,?)",
                     ("ORD-1003", "card", "4417", f"{utcnow().year + 2}-08", "IN", "IN"))
        conn.execute("INSERT INTO payment_attempts(order_id, attempt_no, amount, status, decline_code, fraud_score, ts)"
                     " VALUES (?,?,?,?,?,?,?)", ("ORD-1003", 1, t, "pending", None, 0.04, days_from_now(-0.01)))
        # ORD-1004 · SC-4 · Standard customer, monitor with one valid promo (FEST20)
        t = _insert_order(conn, "ORD-1004", "CUST-14", "paid", None, [("SKU-MNTR", 1)], ["FEST20"], 0, 6,
                          history=["placed", "paid"])
        _captured_payment(conn, "ORD-1004", t, 0, rng)

        # ── Background orders ORD-1005 … ORD-1050 (healthy) ──
        skus = [p[0] for p in PRODUCTS if p[0] != "SKU-DESK"]
        statuses = (["delivered"] * 18 + ["shipped"] * 10 + ["packed"] * 5 + ["picking"] * 4
                    + ["allocated"] * 5 + ["paid"] * 4)
        rng.shuffle(statuses)
        delivered_ids = []
        for i, status in enumerate(statuses):
            oid = f"ORD-{1005 + i}"
            cust = rng.choice(CUSTOMERS)[0]
            items = {}
            for sku in rng.sample(skus, rng.choice([1, 1, 1, 2, 2, 3])):
                items[sku] = rng.choice([1, 1, 1, 2])
            codes = rng.choice([[], [], [], ["FEST20"], ["WELCOME5"], ["FEST20", "WELCOME5"]])
            # keep background orders valid: drop codes that would breach a margin floor
            if price_lines(conn, list(items.items()), codes)["violations"]:
                codes = []
            if status == "delivered":
                ago = rng.uniform(4, 12)
                promised = -ago + 5
            elif status == "shipped":
                ago = rng.uniform(1.5, 4)
                promised = rng.uniform(1, 3)
            else:
                ago = rng.uniform(0.05, 1.2)
                promised = rng.uniform(5, 8)
            wh = None if status == "paid" else rng.choice(["WH-A", "WH-B", "WH-C"])
            flow = ["placed", "paid", "allocated", "picking", "packed", "shipped", "delivered"]
            hist = flow[: flow.index(status) + 1]
            total = _insert_order(conn, oid, cust, status, wh, list(items.items()), codes, ago, promised,
                                  history=hist)
            _captured_payment(conn, oid, total, ago, rng)
            if status == "delivered":
                delivered_ids.append(oid)

        # ── Reservations from open orders (keeps inventory consistent) ──
        for oid, wh in conn.execute("SELECT id, warehouse_id FROM orders WHERE status IN (?,?,?) AND warehouse_id IS NOT NULL",
                                    ("allocated", "picking", "packed")).fetchall():
            for sku, qty in conn.execute("SELECT sku, qty FROM order_items WHERE order_id=?", (oid,)).fetchall():
                conn.execute("UPDATE inventory SET reserved = MIN(on_hand, reserved + ?) WHERE sku=? AND warehouse_id=?",
                             (qty, sku, wh))
        # make sure WH-B has visible headroom for the SC-1 reallocation
        conn.execute("UPDATE inventory SET reserved=MIN(reserved,2) WHERE sku='SKU-HDPH' AND warehouse_id='WH-B'")
        conn.execute("UPDATE inventory SET reserved=2 WHERE sku='SKU-HDPH' AND warehouse_id='WH-A'")
        conn.execute("UPDATE inventory SET reserved=0 WHERE sku='SKU-HDPH' AND warehouse_id='WH-C'")

        # ── A few received POs so the supplier system isn't empty ──
        for n, (sup, sku, cost) in enumerate([("SUP-2", "SKU-HDPH", 4800), ("SUP-2", "SKU-MNTR", 13000),
                                              ("SUP-4", "SKU-CABL", 180), ("SUP-1", "SKU-CHAR", 9000)]):
            conn.execute("INSERT INTO purchase_orders VALUES (?,?,?,?,?,?,?,?,?,?)",
                         (f"PO-19{n:02d}", sku, sup, rng.choice([20, 40, 60]), cost, None,
                          days_from_now(-8 + n), days_from_now(-5 + n), "received", None))

        # ── Resolved exception history (FR-8, FR-19) ──
        rng.shuffle(delivered_ids)
        for (dom, sev, symptom, cause, action, params, cost, by, mins, fb), oid in zip(HISTORY, delivered_ids):
            created = days_from_now(-rng.uniform(2, 9))
            cur = conn.execute(
                "INSERT INTO exceptions(order_id, domain, severity, status, symptom, root_cause, confidence, created_at, resolved_at, origin)"
                " VALUES (?,?,?,?,?,?,?,?,?,'history')",
                (oid, dom, sev, "auto_resolved" if by == "agent" else "resolved", symptom, cause,
                 round(rng.uniform(0.78, 0.96), 2), created, created))
            exc_id = cur.lastrowid
            cur = conn.execute(
                "INSERT INTO proposed_actions(exception_id, rank, action_type, params_json, cost, risk, rationale,"
                " requires_approval, status) VALUES (?,?,?,?,?,?,?,?,?)",
                (exc_id, 1, action, json.dumps(params), cost, "low" if by == "agent" else "medium",
                 f"Resolved '{cause.lower()}'", int(by != "agent"), "executed"))
            conn.execute("INSERT INTO resolutions VALUES (?,?,?,?,?,?)",
                         (exc_id, cur.lastrowid, "success",
                          fb, by, mins))
        conn.commit()
        counts = {t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
                  for t in ("orders", "customers", "warehouses", "suppliers", "inventory", "exceptions")}
        return counts
    finally:
        conn.close()


def _connect_path(path: Path):
    import sqlite3
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    return conn


if __name__ == "__main__":
    print("Seeded", DB_PATH, build())
