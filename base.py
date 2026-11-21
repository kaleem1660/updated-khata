import unittest
from datetime import datetime

from voicekhata import db

NOW = datetime(2026, 11, 21, 12, 0, 0)


class VKTestCase(unittest.TestCase):
    """Fresh in-memory DB with two shops. Shop 1 has an approver (PIN 4321)."""

    def setUp(self):
        self.conn = db.connect()
        db.init_db(self.conn)
        self.shop = db.create_shop(self.conn, "Test Shop", "1234", "Family", "4321")
        self.other = db.create_shop(self.conn, "Other Shop", "5555")
        self.ali = db.add_customer(self.conn, self.shop, "Ali")
        self.raza = db.add_customer(self.conn, self.shop, "Raza")
        self.zainab = db.add_customer(self.conn, self.other, "Zainab")

    def shop_row(self, sid=None):
        return db.get_shop(self.conn, sid or self.shop)

    def seed_sales(self, per_day=1000, days=30):
        from voicekhata import ledger
        from datetime import timedelta
        with self.conn:
            for d in range(days - 1, -1, -1):
                ledger.record_cash_sale(self.conn, self.shop, per_day, NOW - timedelta(days=d, hours=1))

    def entry_count(self):
        return self.conn.execute("SELECT COUNT(*) AS n FROM journal_entries").fetchone()["n"]
