from datetime import timedelta

from voicekhata import actions, credit, ledger
from .base import NOW, VKTestCase


class CreditRuleTests(VKTestCase):
    def test_worked_example_from_report(self):
        self.seed_sales(1000, 30)                         # Rs 30,000 in 30 days
        chk = credit.check_loan(self.conn, self.shop_row(), 10000, NOW)
        self.assertTrue(chk["ok"])
        self.assertEqual((chk["limit"], chk["fee"], chk["total_due"]), (12000, 300, 10300))

    def test_at_below_above_limit(self):
        self.seed_sales(1000, 30)
        s = self.shop_row()
        self.assertTrue(credit.check_loan(self.conn, s, 12000, NOW)["ok"])
        self.assertTrue(credit.check_loan(self.conn, s, 100, NOW)["ok"])
        self.assertFalse(credit.check_loan(self.conn, s, 12001, NOW)["ok"])
        self.assertFalse(credit.check_loan(self.conn, s, 15000, NOW)["ok"])

    def test_too_little_history(self):
        self.seed_sales(1000, 5)
        chk = credit.check_loan(self.conn, self.shop_row(), 500, NOW)
        self.assertFalse(chk["ok"])
        self.assertIn("din", chk["reason"])

    def test_invalid_amounts(self):
        self.seed_sales(1000, 30)
        for bad in (0, -5, None, "100", True, 1.5):
            self.assertFalse(credit.check_loan(self.conn, self.shop_row(), bad, NOW)["ok"])

    def _take_loan(self, amount=4000):
        with self.conn:
            credit.disburse_loan(self.conn, self.shop_row(), amount, NOW)

    def test_one_active_loan_only(self):
        self.seed_sales(1000, 30)
        self._take_loan()
        chk = credit.check_loan(self.conn, self.shop_row(), 1000, NOW)
        self.assertFalse(chk["ok"])

    def test_no_new_loan_while_overdue(self):
        self.seed_sales(1000, 30)
        self._take_loan()
        later = NOW + timedelta(days=31)
        chk = credit.check_loan(self.conn, self.shop_row(), 1000, later)
        self.assertFalse(chk["ok"])
        self.assertIn("overdue", chk["reason"])

    def test_disburse_and_repay_keep_books_balanced(self):
        self.seed_sales(1000, 30)
        self._take_loan(4000)
        loan = credit.active_loan(self.conn, self.shop)
        self.assertEqual((loan["fee"], loan["total_due"]), (120, 4120))
        with self.conn:
            credit.repay_loan(self.conn, self.shop, loan["id"], NOW)
        rows, d, c = ledger.trial_balance(self.conn, self.shop)
        self.assertEqual(d, c)
        payable = next(r for r in rows if r["code"] == "LOAN_PAYABLE")
        self.assertEqual(payable["debit"], payable["credit"])
        self.assertIsNone(credit.active_loan(self.conn, self.shop))

    def test_large_loan_needs_approver(self):
        self.seed_sales(1000, 30)
        res = actions.prepare_action(self.conn, self.shop, "loan", {"amount": 10000}, NOW)
        self.assertTrue(res.ok and res.requires_approval)
        r1 = actions.confirm_action(self.conn, self.shop, res.action_id, "1234", None, NOW)
        self.assertEqual(r1.code, "approver_needed")
        r2 = actions.confirm_action(self.conn, self.shop, res.action_id, "1234", "9999", NOW)
        self.assertEqual(r2.code, "approver_bad")
        r3 = actions.confirm_action(self.conn, self.shop, res.action_id, "1234", "4321", NOW)
        self.assertTrue(r3.ok)

    def test_small_loan_needs_no_approver(self):
        self.seed_sales(1000, 30)
        res = actions.prepare_action(self.conn, self.shop, "loan", {"amount": 3000}, NOW)
        self.assertTrue(res.ok and not res.requires_approval)
