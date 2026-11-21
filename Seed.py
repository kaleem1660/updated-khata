"""Demo data (made-up). Creates two shops so shop isolation can be demonstrated."""
import random
from datetime import timedelta

from . import db, ledger
from .util import now_or

DEMO_SHOP, DEMO_PIN, APPROVER_PIN = "Demo Kirana", "1234", "4321"


def seed_demo(conn, now=None):
    if conn.execute("SELECT COUNT(*) AS n FROM shops").fetchone()["n"]:
        return
    now = now_or(now)
    sid = db.create_shop(conn, DEMO_SHOP, DEMO_PIN, "Family Member", APPROVER_PIN)
    ids = {n: db.add_customer(conn, sid, n) for n in ("Ali", "Raza", "Sara", "Bilal", "Meena")}
    rng = random.Random(42)
    with conn:
        for d in range(29, -1, -1):
            day = (now - timedelta(days=d)).replace(hour=11, minute=0, second=0)
            ledger.record_cash_sale(conn, sid, rng.randrange(6, 14) * 100, day)
            if d % 3 == 0:
                name = rng.choice(["Ali", "Sara", "Bilal", "Meena"])
                amt = rng.randrange(2, 9) * 100
                ledger.record_credit_sale(conn, sid, ids[name], amt, day)
                pay_day = day + timedelta(days=rng.randrange(3, 10), hours=2)
                if rng.random() < 0.6 and pay_day <= now:
                    ledger.record_payment(conn, sid, ids[name], amt, pay_day)
        # Raza: one old, unpaid Rs 900 credit sale (24 days old)
        ledger.record_credit_sale(conn, sid, ids["Raza"], 900,
                                  (now - timedelta(days=25)).replace(hour=15, minute=0, second=0))
    other = db.create_shop(conn, "Other Shop", "5555")
    db.add_customer(conn, other, "Zainab")
