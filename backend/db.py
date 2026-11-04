import csv
import sqlite3
from pathlib import Path
import config

DATA = Path(__file__).resolve().parent.parent / "data"
TABLES = ("suppliers", "parts", "orders", "inventory", "playbook")
SCHEMA = """
CREATE TABLE IF NOT EXISTS suppliers(supplier_id TEXT PRIMARY KEY, name TEXT, region TEXT, phone TEXT,
  email TEXT, payment_terms_days INTEGER, base_reliability INTEGER);
CREATE TABLE IF NOT EXISTS parts(sku TEXT PRIMARY KEY, name TEXT, category TEXT, lead_time_days INTEGER,
  unit_cost REAL, primary_supplier_id TEXT, backup_supplier_id TEXT, backup_premium_pct REAL,
  backup_lead_time_days INTEGER, backup_qualification_days INTEGER);
CREATE TABLE IF NOT EXISTS orders(po TEXT PRIMARY KEY, supplier_id TEXT, sku TEXT, qty INTEGER,
  order_date TEXT, promised_date TEXT, status TEXT, revised_date TEXT, note TEXT);
CREATE TABLE IF NOT EXISTS inventory(sku TEXT PRIMARY KEY, on_hand INTEGER, reserved INTEGER,
  reorder_point INTEGER, daily_use REAL, revenue_per_day REAL);
CREATE TABLE IF NOT EXISTS playbook(reason TEXT, action TEXT);
CREATE TABLE IF NOT EXISTS messages(id INTEGER PRIMARY KEY AUTOINCREMENT, channel TEXT, sender TEXT,
  text TEXT, received_at TEXT, supplier_id TEXT, extracted TEXT);
CREATE TABLE IF NOT EXISTS alerts(id INTEGER PRIMARY KEY AUTOINCREMENT, message_id INTEGER, kind TEXT,
  po TEXT, sku TEXT, supplier_id TEXT, delay_days INTEGER, reason TEXT, confidence REAL, severity TEXT,
  status TEXT, exposure TEXT, scenarios TEXT, recommended TEXT, explanation TEXT, created_at TEXT,
  decided_option TEXT, decided_by TEXT, decided_at TEXT, plan TEXT);
CREATE TABLE IF NOT EXISTS audit(id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT, actor TEXT, action TEXT, detail TEXT);
"""


def connect():
    conn = sqlite3.connect(config.DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def init(conn, reseed=False):
    if reseed:
        for t in TABLES + ("messages", "alerts", "audit"):
            conn.execute(f"DROP TABLE IF EXISTS {t}")
    conn.executescript(SCHEMA)
    if "plan" not in [r[1] for r in conn.execute("PRAGMA table_info(alerts)")]:  # upgrade an older database file
        conn.execute("ALTER TABLE alerts ADD COLUMN plan TEXT")
    if not conn.execute("SELECT COUNT(*) FROM suppliers").fetchone()[0]:
        seed(conn)
    elif not conn.execute("SELECT COUNT(*) FROM playbook").fetchone()[0]:
        seed(conn, ("playbook",))


def seed(conn, tables=TABLES):
    """Load data/*.csv. Replace those files with your real data (same column names)."""
    for t in tables:
        if not (DATA / f"{t}.csv").exists():
            continue
        with open(DATA / f"{t}.csv", newline="", encoding="utf-8-sig") as f:  # utf-8-sig tolerates the invisible mark Excel/Notepad can add
            rows = list(csv.DictReader(f))
        if not rows:
            continue
        cols = [c.strip() for c in rows[0].keys()]
        rows = [{k.strip(): (v.strip() if isinstance(v, str) else v) for k, v in r.items()} for r in rows]
        conn.executemany(
            f"INSERT INTO {t} ({','.join(cols)}) VALUES ({','.join('?' * len(cols))})",
            [tuple(r[c] if r[c] != "" else None for c in cols) for r in rows],
        )
    conn.commit()
