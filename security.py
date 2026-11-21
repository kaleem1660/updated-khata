"""PIN hashing, verification and lockout. Code decides; the AI never sees PINs."""
import hashlib
import hmac
import os
import re

from . import audit
from .config import MAX_PIN_ATTEMPTS

_ITERATIONS = 100_000
_TABLES = {"shops", "approvers"}


def validate_pin_format(pin):
    return isinstance(pin, str) and re.fullmatch(r"\d{4,6}", pin) is not None


def hash_pin(pin, salt=None):
    salt = salt or os.urandom(16).hex()
    digest = hashlib.pbkdf2_hmac("sha256", pin.encode(), bytes.fromhex(salt), _ITERATIONS)
    return digest.hex(), salt


def _matches(row, pin):
    candidate, _ = hash_pin(pin, row["pin_salt"])
    return hmac.compare_digest(candidate, row["pin_hash"])


def verify_pin(conn, table, row_id, pin):
    """Check a PIN. Returns (ok, message, locked). 3 wrong PINs lock the account."""
    if table not in _TABLES:
        raise ValueError("bad table")
    pin = pin if isinstance(pin, str) else ""
    row = conn.execute(f"SELECT * FROM {table} WHERE id=?", (row_id,)).fetchone()
    if row is None:
        return False, "Account nahi mila.", False
    shop_id = row_id if table == "shops" else row["shop_id"]
    if row["locked"]:
        return False, "Account lock hai (3 galat PIN). Admin se unlock karwayein.", True
    if validate_pin_format(pin) and _matches(row, pin):
        if row["failed_pins"]:
            with conn:
                conn.execute(f"UPDATE {table} SET failed_pins=0 WHERE id=?", (row_id,))
        return True, "ok", False
    failed = row["failed_pins"] + 1
    locked = failed >= MAX_PIN_ATTEMPTS
    with conn:
        conn.execute(
            f"UPDATE {table} SET failed_pins=?, locked=? WHERE id=?",
            (failed, int(locked), row_id),
        )
        audit.log(conn, shop_id, "account_locked" if locked else "pin_failed", f"{table}:{row_id}")
    if locked:
        return False, "Galat PIN. Account lock ho gaya.", True
    return False, f"Galat PIN. {MAX_PIN_ATTEMPTS - failed} koshish baaki hain.", False


def verify_approver(conn, shop_id, pin):
    """Approver PIN check for large loans. Returns (ok, message, locked)."""
    pin = pin if isinstance(pin, str) else ""
    rows = conn.execute("SELECT * FROM approvers WHERE shop_id=?", (shop_id,)).fetchall()
    if not rows:
        return False, "Is dukaan ka koi approver register nahi hai.", False
    active = [r for r in rows if not r["locked"]]
    if not active:
        return False, "Approver account lock hai.", True
    if validate_pin_format(pin):
        for r in active:
            if _matches(r, pin):
                if r["failed_pins"]:
                    with conn:
                        conn.execute("UPDATE approvers SET failed_pins=0 WHERE id=?", (r["id"],))
                return True, "ok", False
    with conn:
        for r in active:
            failed = r["failed_pins"] + 1
            conn.execute(
                "UPDATE approvers SET failed_pins=?, locked=? WHERE id=?",
                (failed, int(failed >= MAX_PIN_ATTEMPTS), r["id"]),
            )
        audit.log(conn, shop_id, "approver_pin_failed", "")
    return False, "Approver ka PIN galat hai.", False
