import unittest

from voicekhata.parser import extract_amount, parse_command


class ParserTests(unittest.TestCase):
    CASES = [
        ("Ali ne paanch sau ka udhaar liya.", "credit_sale", 500),
        ("Ali ne teen sau wapas diye.", "payment", 300),
        ("Aaj ki bikri kitni hui?", "sales_query", None),
        ("Kis ka udhaar sab se purana hai?", "overdue_query", None),
        ("Mujhe maal ke liye ₹10,000 chahiye.", "loan", 10000),
        ("Ali took 500 on credit", "credit_sale", 500),
        ("Ali paid 300", "payment", 300),
        ("Ali ne 500 udhaar wapas kiya", "payment", 500),
        ("700 ka cash maal becha", "cash_sale", 700),
        ("Ali ka kitna baaki hai", "balance_query", None),
        ("loan wapas karna hai", "loan_repay", None),
        ("how much is the total sales this month", "sales_query", None),
        ("Ali ko udhaar de do 500", "credit_sale", 500),
        ("namaste", "unknown", None),
    ]

    def test_intents_and_amounts(self):
        for text, intent, amount in self.CASES:
            with self.subTest(text=text):
                p = parse_command(text)
                self.assertEqual((p.intent, p.amount), (intent, amount))

    def test_number_words(self):
        for text, value in [("do hazaar", 2000), ("ek lakh", 100000), ("five hundred", 500),
                            ("two thousand five hundred", 2500), ("5k", 5000),
                            ("paanch sau", 500), ("1,500", 1500), ("₹७००", 700)]:
            with self.subTest(text=text):
                self.assertEqual(extract_amount(text), value)

    def test_period(self):
        self.assertEqual(parse_command("is mahine ki sale kitni hui").period, "month")
        self.assertEqual(parse_command("is hafte ki bikri kitni hui").period, "week")
