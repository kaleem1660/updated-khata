"""Prepare -> confirm flow. The AI/agent can only PREPARE. Posting happens here,
once, after a PIN check, and only if the policy engine still agrees."""
import json
import uuid
from dataclasses import dataclass
from datetime import timedelta
from typing import Optional

from . import audit, credit, ledger, policy, security
from .config import PENDING_TTL_SECONDS
from .util import fmt_ts, inr, now_or, parse_ts


@dataclass
class PrepareResult:
    ok: bool
    reason: str = ""
    action_id: Optional[str] = None
    description: Optional[dict] = None
    requires_approval: bool = False


@dataclass
class ConfirmResult:
    ok: bool
    message: str
    done: bool          # True = this action is finished (success or dead); False = retry PIN
    code: str


def describe(kind, info):
    """Human-readable summary + the sentence the app reads out before the PIN."""
    name = info.get("customer", "")
    a = info.get("amount")
    if kind == "credit_sale":
        return {"title": "Udhaar entry", "amount": a,
                "lines": [f"Customer: {name}", f"Naya baaki: {inr(info['new_balance'])}"],
                "voice": f"{name}, {inr(a)} udhaar. PIN se confirm karein."}
    if kind == "payment":
        return {"title": "Payment received", "amount": a,
                "lines": [f"Customer: {name}", f"Ab baaki: {inr(info['new_balance'])}"],
                "voice": f"{name} se {inr(a)} mile. Ab {inr(info['new_balance'])} baaki hain. "
                         "PIN se confirm karein."}
    if kind == "online_payment":
        return {"title": "Payment request paid (simulated)", "amount": a,
                "lines": [f"Customer: {name}", f"Platform fee: {inr(info['fee'])}",
                          f"Net cash: {inr(info['net'])}", f"Ab baaki: {inr(info['new_balance'])}"],
                "voice": f"{name} ne {inr(a)} online diye. Fee {inr(info['fee'])}. "
                         "PIN se confirm karein."}
    if kind == "cash_sale":
        return {"title": "Cash sale", "amount": a, "lines": ["Cash mein bikri"],
                "voice": f"{inr(a)} ki cash bikri. PIN se confirm karein."}
    if kind == "loan":
        extra = ["Approver PIN bhi chahiye"] if info.get("approval") else []
        return {"title": "Working-capital loan", "amount": a,
                "lines": [f"Limit: {inr(info['limit'])}", f"Fee: {inr(info['fee'])}",
                          f"{info['term_days']} din mein wapas: {inr(info['total_due'])}"] + extra,
                "voice": f"Aap ki limit {inr(info['limit'])} hai. {info['term_days']} din mein "
                         f"{inr(info['total_due'])} wapas dene honge. PIN se confirm karein."}
    if kind == "loan_repay":
        return {"title": "Loan repayment", "amount": a,
                "lines": [f"Due date: {info['due_date'][:10]}"],
                "voice": f"Loan ke {inr(a)} wapas karne hain. PIN se confirm karein."}
    if kind == "reversal":
        return {"title": "Reverse entry", "amount": a,
                "lines": [f"#{info['entry_id']}: {info['description']}"],
                "voice": f"Entry number {info['entry_id']} ko reverse karna hai. PIN se confirm karein."}
    return {"title": kind, "amount": a, "lines": [], "voice": "PIN se confirm karein."}


def prepare_action(conn, shop_id, kind, payload, now=None):
    now = now_or(now)
    res = policy.check(conn, shop_id, kind, payload, now)
    if not res.ok:
        with conn:
            audit.log(conn, shop_id, "prepare_rejected", f"{kind}: {res.reason}", now)
        return PrepareResult(False, res.reason)
    info = dict(res.info, approval=res.requires_approval)
    action_id = uuid.uuid4().hex[:12]
    with conn:
        conn.execute("UPDATE pending_actions SET status='superseded'"
                     " WHERE shop_id=? AND status='pending'", (shop_id,))
        conn.execute(
            "INSERT INTO pending_actions(id, shop_id, kind, payload, created_at, expires_at)"
            " VALUES (?,?,?,?,?,?)",
            (action_id, shop_id, kind, json.dumps(payload), fmt_ts(now),
             fmt_ts(now + timedelta(seconds=PENDING_TTL_SECONDS))),
        )
        audit.log(conn, shop_id, "prepared", f"{action_id} {kind} {json.dumps(payload)}", now)
    return PrepareResult(True, action_id=action_id, description=describe(kind, info),
                         requires_approval=res.requires_approval)


def get_pending(conn, shop_id, action_id):
    """Shop-scoped lookup: another shop's action id simply is not found."""
    return conn.execute("SELECT * FROM pending_actions WHERE id=? AND shop_id=?",
                        (action_id, shop_id)).fetchone()


def describe_pending(conn, shop_id, row, now=None):
    res = policy.check(conn, shop_id, row["kind"], json.loads(row["payload"]), now)
    if not res.ok:
        return None, res
    return describe(row["kind"], dict(res.info, approval=res.requires_approval)), res


def cancel_action(conn, shop_id, action_id, now=None):
    with conn:
        cur = conn.execute("UPDATE pending_actions SET status='cancelled'"
                           " WHERE id=? AND shop_id=? AND status='pending'", (action_id, shop_id))
        if cur.rowcount:
            audit.log(conn, shop_id, "cancelled", action_id, now)
    return bool(cur.rowcount)


def _execute(conn, shop, kind, p, now, action_id):
    sid = shop["id"]
    if kind == "credit_sale":
        ledger.record_credit_sale(conn, sid, p["customer_id"], p["amount"], now, action_id)
    elif kind == "payment":
        ledger.record_payment(conn, sid, p["customer_id"], p["amount"], now, action_id)
    elif kind == "online_payment":
        ledger.record_online_payment(conn, sid, p["customer_id"], p["amount"],
                                     shop["platform_fee_rate"], now, action_id)
    elif kind == "cash_sale":
        ledger.record_cash_sale(conn, sid, p["amount"], now, action_id)
    elif kind == "loan":
        credit.disburse_loan(conn, shop, p["amount"], now, action_id)
    elif kind == "loan_repay":
        credit.repay_loan(conn, sid, p["loan_id"], now, action_id)
    elif kind == "reversal":
        ledger.reverse_entry(conn, sid, p["entry_id"], now, action_id)
    else:
        raise ValueError("unknown kind")


def _close(conn, shop_id, action_id, status, now, note=""):
    with conn:
        conn.execute("UPDATE pending_actions SET status=? WHERE id=? AND status='pending'",
                     (status, action_id))
        audit.log(conn, shop_id, status, f"{action_id} {note}", now)


def confirm_action(conn, shop_id, action_id, pin, approver_pin=None, now=None):
    now = now_or(now)
    row = get_pending(conn, shop_id, action_id)
    if row is None:
        with conn:
            audit.log(conn, shop_id, "confirm_denied", f"unknown action {action_id}", now)
        return ConfirmResult(False, "Yeh entry nahi mili.", True, "not_found")
    if row["status"] != "pending":
        with conn:
            audit.log(conn, shop_id, "confirm_denied", f"{action_id} already {row['status']}", now)
        return ConfirmResult(False, "Yeh entry pehle hi use ho chuki hai ya band hai.", True,
                             "not_pending")
    if now > parse_ts(row["expires_at"]):
        _close(conn, shop_id, action_id, "expired", now)
        return ConfirmResult(False, "Entry ka waqt khatam ho gaya (2 minute). Dobara bolein.", True,
                             "expired")

    ok, msg, locked = security.verify_pin(conn, "shops", shop_id, pin)
    if not ok:
        return ConfirmResult(False, msg, locked, "locked" if locked else "bad_pin")

    kind, payload = row["kind"], json.loads(row["payload"])
    res = policy.check(conn, shop_id, kind, payload, now)       # re-check at confirm time
    if not res.ok:
        _close(conn, shop_id, action_id, "rejected", now, res.reason)
        return ConfirmResult(False, res.reason, True, "policy")

    if res.requires_approval:
        if not approver_pin:
            return ConfirmResult(False, "Bade loan ke liye approver ka PIN bhi chahiye.", False,
                                 "approver_needed")
        a_ok, a_msg, a_locked = security.verify_approver(conn, shop_id, approver_pin)
        if not a_ok:
            return ConfirmResult(False, a_msg, a_locked, "approver_bad")

    shop = conn.execute("SELECT * FROM shops WHERE id=?", (shop_id,)).fetchone()
    try:
        with conn:
            cur = conn.execute("UPDATE pending_actions SET status='confirmed'"
                               " WHERE id=? AND shop_id=? AND status='pending'",
                               (action_id, shop_id))
            if cur.rowcount != 1:                                  # one-shot guard
                raise RuntimeError("already used")
            _execute(conn, shop, kind, payload, now, action_id)
            audit.log(conn, shop_id, "confirmed", f"{action_id} {kind} {json.dumps(payload)}", now)
    except Exception as exc:                                       # rolled back by `with conn`
        _close(conn, shop_id, action_id, "failed", now, str(exc))
        return ConfirmResult(False, f"Entry post nahi hui: {exc}", True, "error")
    return ConfirmResult(True, "Entry post ho gayi. ✅", True, "ok")
