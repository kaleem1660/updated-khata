"""Embedded reminders (simulated: messages are drafted, not sent)."""
from . import credit, ledger
from .config import REMINDER_AFTER_DAYS
from .util import inr, now_or, parse_ts


def build_reminders(conn, shop, now=None):
    now = now_or(now)
    out = []
    for row in ledger.ageing(conn, shop["id"], now):
        if row["oldest_days"] > REMINDER_AFTER_DAYS:
            out.append({
                "kind": "customer", "to": row["name"],
                "message": (f"Salaam {row['name']}, aap ka {inr(row['total'])} udhaar "
                            f"{row['oldest_days']} din se baaki hai. Meherbani karke ada "
                            f"kar dein. - {shop['name']}"),
            })
    loan = credit.active_loan(conn, shop["id"])
    if loan is not None:
        due = parse_ts(loan["due_date"])
        days_left = (due - now).days
        if now > due:
            out.append({"kind": "loan", "to": "Shopkeeper",
                        "message": f"Loan overdue hai: {inr(loan['total_due'])} abhi chukayein."})
        elif days_left <= 3:
            out.append({"kind": "loan", "to": "Shopkeeper",
                        "message": f"Loan ki {inr(loan['total_due'])} ki qist {days_left} din "
                                   f"mein due hai ({loan['due_date'][:10]})."})
    return out
