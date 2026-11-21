"""Embedded credit: a transparent rule-based working-capital loan (simulated).

Limit = limit_pct (40%) of the last 30 days of recorded sales. Needs >= 15 days
of history, one active loan at a time, and none while a repayment is overdue."""
from datetime import timedelta

from . import ledger
from .config import LOAN_TERM_DAYS, MIN_HISTORY_DAYS, SALES_WINDOW_DAYS
from .util import fmt_ts, inr, now_or, parse_ts, pct_of


def sales_last_days(conn, shop_id, days, now):
    row = conn.execute(
        "SELECT COALESCE(SUM(l.credit - l.debit),0) AS amt FROM journal_lines l"
        " JOIN journal_entries e ON e.id=l.entry_id JOIN accounts a ON a.id=l.account_id"
        " WHERE a.code='SALES' AND e.shop_id=? AND e.ts>? AND e.ts<=?",
        (shop_id, fmt_ts(now - timedelta(days=days)), fmt_ts(now)),
    ).fetchone()
    return int(row["amt"])


def history_days(conn, shop_id, now):
    row = conn.execute(
        "SELECT MIN(e.ts) AS first_ts FROM journal_entries e"
        " JOIN journal_lines l ON l.entry_id=e.id JOIN accounts a ON a.id=l.account_id"
        " WHERE e.shop_id=? AND a.code='SALES' AND l.credit>0", (shop_id,),
    ).fetchone()
    if row["first_ts"] is None:
        return 0
    return max((now - parse_ts(row["first_ts"])).days, 0)


def active_loan(conn, shop_id):
    return conn.execute(
        "SELECT * FROM loans WHERE shop_id=? AND status='active' ORDER BY id DESC LIMIT 1",
        (shop_id,),
    ).fetchone()


def is_overdue(loan, now):
    return loan is not None and now > parse_ts(loan["due_date"])


def loan_offer(conn, shop, now=None):
    """Everything the Loan screen needs, with a plain-language reason if not eligible."""
    now = now_or(now)
    sid = shop["id"]
    sales = sales_last_days(conn, sid, SALES_WINDOW_DAYS, now)
    limit = int(sales * shop["limit_pct"])
    days = history_days(conn, sid, now)
    loan = active_loan(conn, sid)
    reason = ""
    if days < MIN_HISTORY_DAYS:
        reason = f"Kam az kam {MIN_HISTORY_DAYS} din ki sales record chahiye (abhi {days} din)."
    elif is_overdue(loan, now):
        reason = "Purana loan overdue hai. Pehle usse chukayein."
    elif loan is not None:
        reason = "Ek waqt mein sirf ek loan ho sakta hai."
    elif limit <= 0:
        reason = "Pichle 30 din mein koi sales record nahi."
    return {"sales_30d": sales, "limit": limit, "history_days": days,
            "eligible": reason == "", "reason": reason, "active_loan": loan}


def check_loan(conn, shop, amount, now=None):
    now = now_or(now)
    offer = loan_offer(conn, shop, now)
    result = {"ok": False, "reason": "", "limit": offer["limit"], "amount": amount,
              "fee": 0, "total_due": 0, "due_date": None, "term_days": LOAN_TERM_DAYS}
    if isinstance(amount, bool) or not isinstance(amount, int) or amount <= 0:
        result["reason"] = "Loan ki raqam theek nahi hai."
        return result
    if not offer["eligible"]:
        result["reason"] = offer["reason"]
        return result
    if amount > offer["limit"]:
        result["reason"] = f"Aap ki limit {inr(offer['limit'])} hai. {inr(amount)} us se zyada hai."
        return result
    fee = pct_of(amount, shop["fee_rate"])
    result.update(ok=True, fee=fee, total_due=amount + fee,
                  due_date=fmt_ts(now + timedelta(days=LOAN_TERM_DAYS)))
    return result


def disburse_loan(conn, shop, amount, now=None, ref=None):
    now = now_or(now)
    chk = check_loan(conn, shop, amount, now)
    if not chk["ok"]:
        raise ValueError(chk["reason"])
    sid = shop["id"]
    cur = conn.execute(
        "INSERT INTO loans(shop_id, principal, fee, total_due, created_at, due_date)"
        " VALUES (?,?,?,?,?,?)",
        (sid, amount, chk["fee"], chk["total_due"], fmt_ts(now), chk["due_date"]),
    )
    ledger.post_entry(conn, sid, "loan", "Loan received",
                      [("CASH", amount, 0, None), ("LOAN_PAYABLE", 0, amount, None)], now, ref)
    if chk["fee"] > 0:
        ledger.post_entry(conn, sid, "loan_fee", "Loan fee charged",
                          [("LOAN_FEE_EXP", chk["fee"], 0, None),
                           ("LOAN_PAYABLE", 0, chk["fee"], None)], now, ref)
    return cur.lastrowid


def repay_loan(conn, shop_id, loan_id, now=None, ref=None):
    now = now_or(now)
    loan = conn.execute(
        "SELECT * FROM loans WHERE id=? AND shop_id=? AND status='active'", (loan_id, shop_id)
    ).fetchone()
    if loan is None:
        raise ValueError("Active loan nahi mila.")
    ledger.post_entry(conn, shop_id, "loan_repay", "Loan repaid",
                      [("LOAN_PAYABLE", loan["total_due"], 0, None),
                       ("CASH", 0, loan["total_due"], None)], now, ref)
    conn.execute("UPDATE loans SET status='repaid', repaid_at=? WHERE id=?",
                 (fmt_ts(now), loan_id))
    return loan["total_due"]
