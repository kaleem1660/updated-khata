import sqlite3
from datetime import timedelta

from voicekhata import ledger
from .base import NOW, VKTestCase


class LedgerTests(VKTestCase):
    def test_credit_sale_and_payment_balance(self):
        with self.conn:
            ledger.record_credit_sale(self.conn, self.shop, self.ali, 500, NOW)
            ledger.record_payment(self.conn, self.shop, self.ali, 300, NOW)
        self.assertEqual(ledger.customer_balance(self.conn, self.shop, self.ali), 200)

    def test_trial_balance_always_zero(self):
        with self.conn:
            ledger.record_cash_sale(self.conn, self.shop, 700, NOW)
            ledger.record_credit_sale(self.conn, self.shop, self.ali, 500, NOW)
            ledger.record_online_payment(self.conn, self.shop, self.ali, 300, 0.01, NOW)
        _, debit, credit = ledger.trial_balance(self.conn, self.shop)
        self.assertEqual(debit, credit)
        self.assertGreater(debit, 0)

    def test_unbalanced_entry_rejected(self):
        with self.assertRaises(ValueError):
            with self.conn:
                ledger.post_entry(self.conn, self.shop, "x", "bad",
                                  [("CASH", 100, 0, None), ("SALES", 0, 90, None)], NOW)
        self.assertEqual(self.entry_count(), 0)

    def test_online_payment_records_gross_fee_net(self):
        with self.conn:
            ledger.record_credit_sale(self.conn, self.shop, self.ali, 1000, NOW)
            ledger.record_online_payment(self.conn, self.shop, self.ali, 1000, 0.01, NOW)
        rows = {r["code"]: r for r in ledger.trial_balance(self.conn, self.shop)[0]}
        self.assertEqual(rows["PLATFORM_FEE_EXP"]["debit"], 10)
        self.assertEqual(rows["CASH"]["debit"], 990)
        self.assertEqual(ledger.customer_balance(self.conn, self.shop, self.ali), 0)

    def test_reversal_restores_balance_and_cannot_repeat(self):
        with self.conn:
            eid = ledger.record_credit_sale(self.conn, self.shop, self.ali, 500, NOW)
            ledger.reverse_entry(self.conn, self.shop, eid, NOW)
        self.assertEqual(ledger.customer_balance(self.conn, self.shop, self.ali), 0)
        with self.assertRaises(ValueError):
            with self.conn:
                ledger.reverse_entry(self.conn, self.shop, eid, NOW)
        self.assertEqual(ledger.sales_summary(self.conn, self.shop, "today", NOW)["total"], 0)

    def test_ledger_is_append_only(self):
        with self.conn:
            ledger.record_cash_sale(self.conn, self.shop, 100, NOW)
        for sql in ("UPDATE journal_lines SET debit=1", "DELETE FROM journal_entries",
                    "DELETE FROM audit_log", "UPDATE journal_entries SET kind='x'"):
            with self.assertRaises(sqlite3.DatabaseError):
                self.conn.execute(sql)

    def test_sales_summary_split(self):
        with self.conn:
            ledger.record_cash_sale(self.conn, self.shop, 700, NOW)
            ledger.record_credit_sale(self.conn, self.shop, self.ali, 500, NOW)
        s = ledger.sales_summary(self.conn, self.shop, "today", NOW)
        self.assertEqual((s["total"], s["cash"], s["credit"]), (1200, 700, 500))

    def test_ageing_buckets_fifo(self):
        with self.conn:
            ledger.record_credit_sale(self.conn, self.shop, self.ali, 400, NOW - timedelta(days=20))
            ledger.record_credit_sale(self.conn, self.shop, self.ali, 300, NOW - timedelta(days=10))
            ledger.record_credit_sale(self.conn, self.shop, self.ali, 200, NOW - timedelta(days=2))
            ledger.record_payment(self.conn, self.shop, self.ali, 500, NOW - timedelta(days=1))
        row = ledger.ageing(self.conn, self.shop, NOW)[0]
        # 500 paid clears the 400 (20d) and 100 of the 300 (10d)
        self.assertEqual((row["b15_plus"], row["b8_15"], row["b0_7"]), (0, 200, 200))
        self.assertEqual(row["total"], 400)

    def test_customer_of_other_shop_rejected(self):
        with self.assertRaises(ValueError):
            with self.conn:
                ledger.record_credit_sale(self.conn, self.shop, self.zainab, 100, NOW)
