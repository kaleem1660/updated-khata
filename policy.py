"""Policy engine: plain code that decides whether a prepared entry is allowed.
It runs when an entry is prepared AND again when it is confirmed."""
from dataclasses import dataclass, field

from . import credit, ledger
from .config import MAX_ENTRY_AMOUNT
from .util import inr, now_or, pct_of


@dataclass
class PolicyResult:
    ok: bool
    reason: str = ""
    requires_approval: bool = False
    info: dict = field(default_factory=dict)


def _fail(msg):
    return PolicyResult(False, msg)


def _amount(payload, cap=MAX_ENTRY_AMOUNT):
    a = payload.get("amount")
    if isinstance(a, bool) or not isinstance(a, int):
        return None, "Raqam theek nahi hai."
    if a <= 0:
        return None, "Raqam zero se zyada honi chahiye."
    if cap and a > cap:
        return None, f"Ek entry ki had {inr(cap)} hai."
    return a, None


def _customer(conn, shop, payload):
    cid = payload.get("customer_id")
    if isinstance(cid, bool) or not isinstance(cid, int):
        return None, "Customer theek nahi hai."
    row = conn.execute("SELECT id, name FROM customers WHERE id=? AND shop_id=?",
                       (cid, shop["id"])).fetchone()
    if row is None:
        return None, "Customer nahi mila."
    return row, None


def _credit_sale(conn, shop, payload, now):
    amt, err = _amount(payload)
    if err:
        return _fail(err)
    cust, err = _customer(conn, shop, payload)
    if err:
        return _fail(err)
    bal = ledger.customer_balance(conn, shop["id"], cust["id"])
    return PolicyResult(True, info={"customer": cust["name"], "amount": amt,
                                    "balance": bal, "new_balance": bal + amt})


def _payment(conn, shop, payload, now, online=False):
    amt, err = _amount(payload)
    if err:
        return _fail(err)
    cust, err = _customer(conn, shop, payload)
    if err:
        return _fail(err)
    bal = ledger.customer_balance(conn, shop["id"], cust["id"])
    if bal <= 0:
        return _fail(f"{cust['name']} ka koi udhaar baaki nahi hai.")
    if amt > bal:
        return _fail(f"{cust['name']} ka sirf {inr(bal)} baaki hai.")
    info = {"customer": cust["name"], "amount": amt, "balance": bal, "new_balance": bal - amt}
    if online:
        fee = pct_of(amt, shop["platform_fee_rate"])
        info.update(fee=fee, net=amt - fee)
    return PolicyResult(True, info=info)


def _online_payment(conn, shop, payload, now):
    return _payment(conn, shop, payload, now, online=True)


def _cash_sale(conn, shop, payload, now):
    amt, err = _amount(payload)
    if err:
        return _fail(err)
    return PolicyResult(True, info={"amount": amt})


def _loan(conn, shop, payload, now):
    amt, err = _amount(payload, cap=None)
    if err:
        return _fail(err)
    chk = credit.check_loan(conn, shop, amt, now)
    if not chk["ok"]:
        return _fail(chk["reason"])
    needs = amt > shop["approval_threshold"]
    if needs and not conn.execute("SELECT 1 FROM approvers WHERE shop_id=?",
                                  (shop["id"],)).fetchone():
        return _fail(f"{inr(shop['approval_threshold'])} se bade loan ke liye approver register karein.")
    return PolicyResult(True, requires_approval=needs, info=chk)


def _loan_repay(conn, shop, payload, now):
    loan = credit.active_loan(conn, shop["id"])
    if loan is None:
        return _fail("Koi active loan nahi hai.")
    if payload.get("loan_id") != loan["id"]:
        return _fail("Loan match nahi hua.")
    return PolicyResult(True, info={"loan_id": loan["id"], "amount": loan["total_due"],
                                    "total_due": loan["total_due"], "due_date": loan["due_date"]})


def _reversal(conn, shop, payload, now):
    eid = payload.get("entry_id")
    if isinstance(eid, bool) or not isinstance(eid, int):
        return _fail("Entry theek nahi hai.")
    e = conn.execute("SELECT * FROM journal_entries WHERE id=? AND shop_id=?",
                     (eid, shop["id"])).fetchone()
    if e is None:
        return _fail("Entry nahi mili.")
    if e["reverses_id"] is not None:
        return _fail("Reversal ko reverse nahi kar sakte.")
    if e["kind"] in ledger.LOAN_KINDS:
        return _fail("Loan ki entries reverse nahi ho sakti (prototype rule).")
    if conn.execute("SELECT 1 FROM journal_entries WHERE reverses_id=?", (eid,)).fetchone():
        return _fail("Yeh entry pehle hi reverse ho chuki hai.")
    amt = conn.execute("SELECT SUM(debit) AS s FROM journal_lines WHERE entry_id=?",
                       (eid,)).fetchone()["s"]
    return PolicyResult(True, info={"entry_id": eid, "description": e["description"], "amount": amt})


_CHECKS = {
    "credit_sale": _credit_sale, "payment": _payment, "online_payment": _online_payment,
    "cash_sale": _cash_sale, "loan": _loan, "loan_repay": _loan_repay, "reversal": _reversal,
}


def check(conn, shop_id, kind, payload, now=None):
    now = now_or(now)
    shop = conn.execute("SELECT * FROM shops WHERE id=?", (shop_id,)).fetchone()
    if shop is None:
        return _fail("Dukaan nahi mili.")
    fn = _CHECKS.get(kind)
    if fn is None or not isinstance(payload, dict):
        return _fail("Yeh request samajh nahi aayi.")
    return fn(conn, shop, payload, now)
