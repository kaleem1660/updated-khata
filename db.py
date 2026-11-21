"""SQLite schema. journal_entries, journal_lines and audit_log are append-only
(enforced by triggers), so fixes are reversing entries, never edits or deletes."""
import re
import sqlite3

from . import audit
from .config import (
    DEFAULT_APPROVAL_THRESHOLD, DEFAULT_FEE_RATE, DEFAULT_LIMIT_PCT,
    DEFAULT_PLATFORM_FEE_RATE,
)
from .security import hash_pin, validate_pin_format

CHART_OF_ACCOUNTS = [
    ("CASH", "Cash", "asset"),
    ("RECEIVABLE", "Customer Receivables (udhaar)", "asset"),
    ("LOAN_PAYABLE", "Loan Payable", "liability"),
    ("SALES", "Sales", "income"),
    ("LOAN_FEE_EXP", "Loan Fee Expense", "expense"),
    ("PLATFORM_FEE_EXP", "Platform Fee Expense", "expense"),
]

SCHEMA = """
CREATE TABLE IF NOT EXISTS shops(
  id INTEGER PRIMARY KEY, name TEXT UNIQUE NOT NULL,
  pin_hash TEXT NOT NULL, pin_salt TEXT NOT NULL,
  failed_pins INTEGER NOT NULL DEFAULT 0, locked INTEGER NOT NULL DEFAULT 0,
  fee_rate REAL NOT NULL, limit_pct REAL NOT NULL,
  platform_fee_rate REAL NOT NULL, approval_threshold INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS customers(
  id INTEGER PRIMARY KEY, shop_id INTEGER NOT NULL REFERENCES shops(id), name TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS accounts(
  id INTEGER PRIMARY KEY, shop_id INTEGER NOT NULL REFERENCES shops(id),
  code TEXT NOT NULL, name TEXT NOT NULL, type TEXT NOT NULL, UNIQUE(shop_id, code));
CREATE TABLE IF NOT EXISTS journal_entries(
  id INTEGER PRIMARY KEY, shop_id INTEGER NOT NULL REFERENCES shops(id),
  ts TEXT NOT NULL, kind TEXT NOT NULL, description TEXT NOT NULL,
  reverses_id INTEGER REFERENCES journal_entries(id), ref TEXT);
CREATE TABLE IF NOT EXISTS journal_lines(
  id INTEGER PRIMARY KEY, entry_id INTEGER NOT NULL REFERENCES journal_entries(id),
  account_id INTEGER NOT NULL REFERENCES accounts(id),
  customer_id INTEGER REFERENCES customers(id),
  debit INTEGER NOT NULL DEFAULT 0 CHECK(debit >= 0),
  credit INTEGER NOT NULL DEFAULT 0 CHECK(credit >= 0));
CREATE TABLE IF NOT EXISTS loans(
  id INTEGER PRIMARY KEY, shop_id INTEGER NOT NULL REFERENCES shops(id),
  principal INTEGER NOT NULL, fee INTEGER NOT NULL, total_due INTEGER NOT NULL,
  created_at TEXT NOT NULL, due_date TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'active', repaid_at TEXT);
CREATE TABLE IF NOT EXISTS pending_actions(
  id TEXT PRIMARY KEY, shop_id INTEGER NOT NULL REFERENCES shops(id),
  kind TEXT NOT NULL, payload TEXT NOT NULL,
  created_at TEXT NOT NULL, expires_at TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'pending');
CREATE TABLE IF NOT EXISTS approvers(
  id INTEGER PRIMARY KEY, shop_id INTEGER NOT NULL REFERENCES shops(id),
  name TEXT NOT NULL, pin_hash TEXT NOT NULL, pin_salt TEXT NOT NULL,
  failed_pins INTEGER NOT NULL DEFAULT 0, locked INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS audit_log(
  id INTEGER PRIMARY KEY, ts TEXT NOT NULL, shop_id INTEGER,
  event TEXT NOT NULL, detail TEXT);
"""

TRIGGERS = [
    f"CREATE TRIGGER IF NOT EXISTS {t}_no_{op.lower()} BEFORE {op} ON {t} "
    f"BEGIN SELECT RAISE(ABORT, '{t} is append-only'); END;"
    for t in ("journal_entries", "journal_lines", "audit_log")
    for op in ("UPDATE", "DELETE")
]


def connect(path=":memory:"):
    conn = sqlite3.connect(path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db(conn):
    with conn:
        conn.executescript(SCHEMA)
        for stmt in TRIGGERS:
            conn.execute(stmt)


_NAME_RE = re.compile(r"[\w\u0900-\u097F\u0600-\u06FF .'\-]{1,40}")


def clean_name(name):
    """Names are data, never instructions: allow only plain name characters."""
    name = name or ""
    if re.search(r"[\x00-\x1f\x7f]", name):              # newlines/tabs/control chars
        raise ValueError("Naam mein ghalat characters hain.")
    name = re.sub(r" +", " ", name.strip())
    if not _NAME_RE.fullmatch(name) or "_" in name:
        raise ValueError("Naam sirf huroof, numbers, space, . ' - ho sakta hai (max 40).")
    return name


def create_shop(conn, name, pin, approver_name=None, approver_pin=None):
    name = clean_name(name)
    if not validate_pin_format(pin):
        raise ValueError("PIN 4 se 6 digits ka hona chahiye.")
    if approver_pin and not validate_pin_format(approver_pin):
        raise ValueError("Approver PIN 4 se 6 digits ka hona chahiye.")
    pin_hash, salt = hash_pin(pin)
    try:
        with conn:
            cur = conn.execute(
                "INSERT INTO shops(name, pin_hash, pin_salt, fee_rate, limit_pct,"
                " platform_fee_rate, approval_threshold) VALUES (?,?,?,?,?,?,?)",
                (name, pin_hash, salt, DEFAULT_FEE_RATE, DEFAULT_LIMIT_PCT,
                 DEFAULT_PLATFORM_FEE_RATE, DEFAULT_APPROVAL_THRESHOLD),
            )
            shop_id = cur.lastrowid
            for code, acc_name, acc_type in CHART_OF_ACCOUNTS:
                conn.execute(
                    "INSERT INTO accounts(shop_id, code, name, type) VALUES (?,?,?,?)",
                    (shop_id, code, acc_name, acc_type),
                )
            if approver_name and approver_pin:
                a_hash, a_salt = hash_pin(approver_pin)
                conn.execute(
                    "INSERT INTO approvers(shop_id, name, pin_hash, pin_salt) VALUES (?,?,?,?)",
                    (shop_id, clean_name(approver_name), a_hash, a_salt),
                )
            audit.log(conn, shop_id, "shop_created", name)
    except sqlite3.IntegrityError:
        raise ValueError("Is naam ki dukaan pehle se maujood hai.")
    return shop_id


def add_customer(conn, shop_id, name):
    name = clean_name(name)
    with conn:
        cur = conn.execute("INSERT INTO customers(shop_id, name) VALUES (?,?)", (shop_id, name))
        audit.log(conn, shop_id, "customer_added", name)
    return cur.lastrowid


def get_shop(conn, shop_id):
    return conn.execute("SELECT * FROM shops WHERE id=?", (shop_id,)).fetchone()


def unlock_shop(conn, name):
    """Admin helper (see scripts/unlock.py). Not reachable from the app UI."""
    with conn:
        conn.execute("UPDATE shops SET locked=0, failed_pins=0 WHERE name=?", (name,))
