"""The agent. It UNDERSTANDS commands and can call six tools; none of them posts
an entry or a loan. Changes are only staged with prepare_* (PIN needed to post)."""
import difflib
import re
from dataclasses import dataclass
from typing import Optional

from . import actions, credit, db, ledger
from .parser import parse_command
from .util import inr, now_or

_PERIOD_LABEL = {"today": "Aaj", "week": "Is hafte", "month": "Is mahine"}


@dataclass
class Reply:
    text: str
    action_id: Optional[str] = None
    ok: bool = True


# --------------------------------------------------------------- the 6 tools
def get_sales_summary(conn, shop_id, period="today", now=None):          # read-only
    return ledger.sales_summary(conn, shop_id, period, now)


def get_customer_balance(conn, shop_id, customer_id):                     # read-only
    return ledger.customer_balance(conn, shop_id, customer_id)


def list_overdue(conn, shop_id, now=None):                                # read-only
    return ledger.list_overdue(conn, shop_id, now)


def resolve_customer(conn, shop_id, text):
    """Map a spoken name to a saved customer, by code (difflib), never by the AI.
    Returns {'status': 'ok'|'ambiguous'|'not_found', 'customer', 'candidates'}."""
    tokens = [t for t in re.findall(r"[\w\u0900-\u097F\u0600-\u06FF]+", (text or "").lower())
              if len(t) >= 2]
    scored = []
    for c in ledger.list_customers(conn, shop_id):
        parts = c["name"].lower().split()
        score = 0.0
        for part in parts:
            best = max((difflib.SequenceMatcher(None, part, t).ratio() for t in tokens), default=0)
            if best >= 0.8:
                score += best
        if score > 0:
            scored.append((score, c))
    if not scored:
        return {"status": "not_found", "customer": None, "candidates": []}
    top = max(s for s, _ in scored)
    best = [c for s, c in scored if abs(s - top) < 1e-9]
    if len(best) == 1:
        return {"status": "ok", "customer": best[0], "candidates": best}
    return {"status": "ambiguous", "customer": None, "candidates": best}


def prepare_entry(conn, shop_id, customer_id, amount, entry_type, now=None):
    """Stage a credit_sale / payment / cash_sale. Posting needs a PIN."""
    payload = {"amount": amount}
    if entry_type != "cash_sale":
        payload["customer_id"] = customer_id
    return actions.prepare_action(conn, shop_id, entry_type, payload, now)


def prepare_loan(conn, shop_id, amount, now=None):
    """Stage a loan request after the credit-rule check. Posting needs a PIN."""
    return actions.prepare_action(conn, shop_id, "loan", {"amount": amount}, now)


# ------------------------------------------------------------------ dispatch
def _staged(res):
    if not res.ok:
        return Reply(res.reason, ok=False)
    return Reply(res.description["voice"], action_id=res.action_id)


def handle_command(conn, shop_id, text, now=None, use_llm=False):
    now = now_or(now)
    text = (text or "").strip()[:300]
    if not text:
        return Reply("Kuch boliye ya likhiye.", ok=False)

    parsed = None
    if use_llm:
        from . import llm
        parsed = llm.parse_with_claude(text)
    parsed = parsed or parse_command(text)
    intent, amount = parsed.intent, parsed.amount
    name_text = text if parsed.name_hint is None else parsed.name_hint

    if intent == "sales_query":
        s = get_sales_summary(conn, shop_id, parsed.period, now)
        return Reply(f"{_PERIOD_LABEL[parsed.period]} {inr(s['total'])} ki bikri, "
                     f"jis mein {inr(s['credit'])} udhaar hai.")

    if intent == "overdue_query":
        rows = list_overdue(conn, shop_id, now)
        if not rows:
            return Reply("Koi udhaar baaki nahi hai. 🎉")
        r = rows[0]
        return Reply(f"{r['name']} ka {inr(r['total'])}, {r['oldest_days']} din se baaki hai.")

    if intent == "ageing_report":
        rows = list_overdue(conn, shop_id, now)
        if not rows:
            return Reply("Koi udhaar baaki nahi hai. 🎉")
        return Reply("Baaki udhaar: " + "; ".join(
            f"{r['name']} {inr(r['total'])} ({r['oldest_days']} din)" for r in rows))

    if intent == "loan":
        if not amount:
            return Reply("Kitne rupaye ka loan chahiye?", ok=False)
        return _staged(prepare_loan(conn, shop_id, amount, now))

    if intent == "loan_repay":
        loan = credit.active_loan(conn, shop_id)
        if loan is None:
            return Reply("Koi active loan nahi hai.", ok=False)
        return _staged(actions.prepare_action(conn, shop_id, "loan_repay",
                                              {"loan_id": loan["id"]}, now))

    if intent == "cash_sale":
        if not amount:
            return Reply("Kitne rupaye ki bikri?", ok=False)
        return _staged(prepare_entry(conn, shop_id, None, amount, "cash_sale", now))

    if intent in ("credit_sale", "payment", "balance_query"):
        r = resolve_customer(conn, shop_id, name_text)
        if r["status"] == "not_found":
            return Reply("Customer nahi mila. Pehle Customers tab mein add karein.", ok=False)
        if r["status"] == "ambiguous":
            names = ", ".join(c["name"] for c in r["candidates"])
            return Reply(f"Kaun sa customer? ({names}). Poora naam bolein.", ok=False)
        cust = r["customer"]
        if intent == "balance_query":
            bal = get_customer_balance(conn, shop_id, cust["id"])
            return Reply(f"{cust['name']} ka {inr(bal)} baaki hai." if bal > 0
                         else f"{cust['name']} ka koi udhaar baaki nahi hai.")
        if not amount:
            return Reply("Kitne rupaye? Raqam bhi boliye.", ok=False)
        return _staged(prepare_entry(conn, shop_id, cust["id"], amount, intent, now))

    return Reply("Samajh nahi aaya. Aise bolein: 'Ali ne 500 ka udhaar liya', "
                 "'Ali ne 300 wapas diye', 'Aaj ki bikri kitni hui?', "
                 "'Mujhe 10000 chahiye'.", ok=False)
