"""Double-entry ledger. Every entry balances; corrections are reversing entries.
Functions here never commit: the caller wraps them in `with conn:`."""
from datetime import datetime, timedelta

from .util import fmt_ts, now_or, parse_ts, pct_of

LOAN_KINDS = ("loan", "loan_fee", "loan_repay")


# ---------------------------------------------------------------- core posting
def _account_ids(conn, shop_id):
    rows = conn.execute("SELECT id, code FROM accounts WHERE shop_id=?", (shop_id,)).fetchall()
    return {r["code"]: r["id"] for r in rows}


def _require_customer(conn, shop_id, customer_id):
    row = conn.execute(
        "SELECT id, name FROM customers WHERE id=? AND shop_id=?", (customer_id, shop_id)
    ).fetchone()
    if row is None:
        raise ValueError("Customer is dukaan ka nahi hai.")
    return row


def post_entry(conn, shop_id, kind, description, lines, now=None, ref=None, reverses_id=None):
    """lines: [(account_code, debit, credit, customer_id_or_None), ...]"""
    total_d = total_c = 0
    for _code, debit, credit, _cust in lines:
        for v in (debit, credit):
            if isinstance(v, bool) or not isinstance(v, int) or v < 0:
                raise ValueError("Amounts must be non-negative whole rupees.")
        if (debit > 0) == (credit > 0):
            raise ValueError("Each line needs exactly one of debit or credit.")
        total_d += debit
        total_c += credit
    if total_d != total_c or total_d <= 0:
        raise ValueError(f"Unbalanced entry: debits {total_d} != credits {total_c}")
    accounts = _account_ids(conn, shop_id)
    cur = conn.execute(
        "INSERT INTO journal_entries(shop_id, ts, kind, description, reverses_id, ref)"
        " VALUES (?,?,?,?,?,?)",
        (shop_id, fmt_ts(now_or(now)), kind, description, reverses_id, ref),
    )
    entry_id = cur.lastrowid
    for code, debit, credit, cust in lines:
        if cust is not None:
            _require_customer(conn, shop_id, cust)
        conn.execute(
            "INSERT INTO journal_lines(entry_id, account_id, customer_id, debit, credit)"
            " VALUES (?,?,?,?,?)",
            (entry_id, accounts[code], cust, debit, credit),
        )
    return entry_id


def record_cash_sale(conn, shop_id, amount, now=None, ref=None):
    return post_entry(conn, shop_id, "cash_sale", "Cash sale",
                      [("CASH", amount, 0, None), ("SALES", 0, amount, None)], now, ref)


def record_credit_sale(conn, shop_id, customer_id, amount, now=None, ref=None):
    cust = _require_customer(conn, shop_id, customer_id)
    return post_entry(conn, shop_id, "credit_sale", f"Credit sale to {cust['name']}",
                      [("RECEIVABLE", amount, 0, customer_id), ("SALES", 0, amount, None)], now, ref)


def record_payment(conn, shop_id, customer_id, amount, now=None, ref=None):
    cust = _require_customer(conn, shop_id, customer_id)
    return post_entry(conn, shop_id, "payment", f"Payment received from {cust['name']}",
                      [("CASH", amount, 0, None), ("RECEIVABLE", 0, amount, customer_id)], now, ref)


def record_online_payment(conn, shop_id, customer_id, gross, fee_rate, now=None, ref=None):
    """Embedded payment: gross recorded against the customer, fee as expense, net as cash."""
    cust = _require_customer(conn, shop_id, customer_id)
    fee = pct_of(gross, fee_rate)
    lines = [("CASH", gross - fee, 0, None)]
    if fee > 0:
        lines.append(("PLATFORM_FEE_EXP", fee, 0, None))
    lines.append(("RECEIVABLE", 0, gross, customer_id))
    return post_entry(conn, shop_id, "online_payment",
                      f"Payment request paid by {cust['name']} (fee {fee})", lines, now, ref)


def reverse_entry(conn, shop_id, entry_id, now=None, ref=None):
    entry = conn.execute(
        "SELECT * FROM journal_entries WHERE id=? AND shop_id=?", (entry_id, shop_id)
    ).fetchone()
    if entry is None:
        raise ValueError("Entry nahi mili.")
    if entry["reverses_id"] is not None:
        raise ValueError("Reversal ko reverse nahi kar sakte.")
    if conn.execute("SELECT 1 FROM journal_entries WHERE reverses_id=?", (entry_id,)).fetchone():
        raise ValueError("Yeh entry pehle hi reverse ho chuki hai.")
    codes = {r["id"]: r["code"] for r in conn.execute(
        "SELECT id, code FROM accounts WHERE shop_id=?", (shop_id,))}
    lines = conn.execute("SELECT * FROM journal_lines WHERE entry_id=?", (entry_id,)).fetchall()
    swapped = [(codes[l["account_id"]], l["credit"], l["debit"], l["customer_id"]) for l in lines]
    return post_entry(conn, shop_id, entry["kind"],
                      f"Reversal of #{entry_id}: {entry['description']}",
                      swapped, now, ref, reverses_id=entry_id)


# -------------------------------------------------------------------- queries
def customer_balance(conn, shop_id, customer_id):
    row = conn.execute(
        "SELECT COALESCE(SUM(l.debit) - SUM(l.credit), 0) AS bal FROM journal_lines l"
        " JOIN journal_entries e ON e.id=l.entry_id"
        " JOIN accounts a ON a.id=l.account_id"
        " WHERE e.shop_id=? AND a.code='RECEIVABLE' AND l.customer_id=?",
        (shop_id, customer_id),
    ).fetchone()
    return int(row["bal"])


def list_customers(conn, shop_id):
    rows = conn.execute(
        "SELECT c.id, c.name, COALESCE(SUM(r.debit) - SUM(r.credit), 0) AS balance"
        " FROM customers c LEFT JOIN ("
        "   SELECT l.customer_id, l.debit, l.credit FROM journal_lines l"
        "   JOIN accounts a ON a.id=l.account_id WHERE a.code='RECEIVABLE') r"
        " ON r.customer_id=c.id WHERE c.shop_id=? GROUP BY c.id ORDER BY c.name",
        (shop_id,),
    ).fetchall()
    return [dict(r) for r in rows]


def period_start(period, now):
    midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)
    if period == "today":
        return midnight
    if period == "week":
        return midnight - timedelta(days=6)
    if period == "month":
        return midnight.replace(day=1)
    return datetime(1970, 1, 1)


def sales_summary(conn, shop_id, period="today", now=None):
    now = now_or(now)
    rows = conn.execute(
        "SELECT e.kind, COALESCE(SUM(l.credit - l.debit), 0) AS amt FROM journal_lines l"
        " JOIN journal_entries e ON e.id=l.entry_id JOIN accounts a ON a.id=l.account_id"
        " WHERE a.code='SALES' AND e.shop_id=? AND e.ts>=? AND e.ts<=? GROUP BY e.kind",
        (shop_id, fmt_ts(period_start(period, now)), fmt_ts(now)),
    ).fetchall()
    by_kind = {r["kind"]: int(r["amt"]) for r in rows}
    cash, credit = by_kind.get("cash_sale", 0), by_kind.get("credit_sale", 0)
    return {"period": period, "total": cash + credit, "cash": cash, "credit": credit}


def ageing(conn, shop_id, now=None):
    """Receivables ageing. Payments are applied to the oldest charges first (FIFO)."""
    now = now_or(now)
    names = {c["id"]: c["name"] for c in list_customers(conn, shop_id)}
    rows = conn.execute(
        "SELECT l.customer_id, e.ts, l.debit, l.credit FROM journal_lines l"
        " JOIN journal_entries e ON e.id=l.entry_id JOIN accounts a ON a.id=l.account_id"
        " WHERE e.shop_id=? AND a.code='RECEIVABLE' ORDER BY e.ts, e.id, l.id",
        (shop_id,),
    ).fetchall()
    per = {}
    for r in rows:
        d = per.setdefault(r["customer_id"], {"charges": [], "credits": 0})
        if r["debit"]:
            d["charges"].append((parse_ts(r["ts"]), r["debit"]))
        d["credits"] += r["credit"]
    out = []
    for cid, d in per.items():
        remaining, open_items = d["credits"], []
        for ts, amt in d["charges"]:
            if remaining >= amt:
                remaining -= amt
            else:
                open_items.append((ts, amt - remaining))
                remaining = 0
        if not open_items:
            continue
        b = {"b0_7": 0, "b8_15": 0, "b15_plus": 0}
        oldest = 0
        for ts, amt in open_items:
            days = max((now - ts).days, 0)
            oldest = max(oldest, days)
            key = "b0_7" if days <= 7 else "b8_15" if days <= 15 else "b15_plus"
            b[key] += amt
        out.append({"customer_id": cid, "name": names.get(cid, "?"),
                    "total": sum(a for _, a in open_items), "oldest_days": oldest, **b})
    return sorted(out, key=lambda r: -r["oldest_days"])


def list_overdue(conn, shop_id, now=None):
    return ageing(conn, shop_id, now)


def trial_balance(conn, shop_id):
    rows = conn.execute(
        "SELECT a.code, a.name, a.type, COALESCE(SUM(l.debit),0) AS debit,"
        " COALESCE(SUM(l.credit),0) AS credit FROM accounts a"
        " LEFT JOIN journal_lines l ON l.account_id=a.id WHERE a.shop_id=?"
        " GROUP BY a.id ORDER BY a.id", (shop_id,),
    ).fetchall()
    rows = [dict(r) for r in rows]
    td, tc = sum(r["debit"] for r in rows), sum(r["credit"] for r in rows)
    return rows, td, tc


def profit_view(conn, shop_id, period="month", now=None):
    now = now_or(now)
    start, end = fmt_ts(period_start(period, now)), fmt_ts(now)
    rows = conn.execute(
        "SELECT a.code, a.name, a.type, COALESCE(SUM(l.debit - l.credit),0) AS net"
        " FROM journal_lines l JOIN journal_entries e ON e.id=l.entry_id"
        " JOIN accounts a ON a.id=l.account_id"
        " WHERE e.shop_id=? AND a.type IN ('income','expense') AND e.ts>=? AND e.ts<=?"
        " GROUP BY a.id", (shop_id, start, end),
    ).fetchall()
    sales = sum(-r["net"] for r in rows if r["type"] == "income")
    expenses = [{"name": r["name"], "amount": r["net"]} for r in rows if r["type"] == "expense"]
    total_exp = sum(e["amount"] for e in expenses)
    return {"sales": sales, "expenses": expenses, "total_expenses": total_exp,
            "profit": sales - total_exp}


def recent_entries(conn, shop_id, limit=50):
    rows = conn.execute(
        "SELECT e.id, e.ts, e.kind, e.description, e.reverses_id,"
        " (SELECT SUM(debit) FROM journal_lines WHERE entry_id=e.id) AS amount,"
        " EXISTS(SELECT 1 FROM journal_entries r WHERE r.reverses_id=e.id) AS reversed"
        " FROM journal_entries e WHERE e.shop_id=? ORDER BY e.id DESC LIMIT ?",
        (shop_id, limit),
    ).fetchall()
    return [dict(r) for r in rows]
