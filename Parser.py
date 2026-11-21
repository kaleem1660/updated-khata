"""Rule-based command parser for Hindi / Hinglish / Urdu-roman / English.
Always available (no paid service). It only returns an intent + amount + period;
customers are matched separately, by code, in agent.resolve_customer."""
import re
from dataclasses import dataclass
from typing import Optional

INTENTS = ("credit_sale", "payment", "cash_sale", "sales_query", "balance_query",
           "overdue_query", "ageing_report", "loan", "loan_repay", "unknown")


@dataclass
class ParsedCommand:
    intent: str = "unknown"
    amount: Optional[int] = None
    period: str = "today"
    name_hint: Optional[str] = None      # None = scan the whole utterance for a customer


# ----------------------------------------------------------------- amounts
_WORDS = {
    "ek": 1, "do": 2, "teen": 3, "char": 4, "chaar": 4, "paanch": 5, "panch": 5, "chhe": 6,
    "chhah": 6, "che": 6, "saat": 7, "aath": 8, "nau": 9, "das": 10, "gyarah": 11,
    "barah": 12, "terah": 13, "chaudah": 14, "pandrah": 15, "solah": 16, "satrah": 17,
    "atharah": 18, "unnis": 19, "bees": 20, "pachchis": 25, "pachis": 25, "tees": 30,
    "chalis": 40, "pachas": 50, "pachaas": 50, "saath": 60, "sattar": 70, "assi": 80,
    "nabbe": 90,
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8,
    "nine": 9, "ten": 10, "eleven": 11, "twelve": 12, "thirteen": 13, "fourteen": 14,
    "fifteen": 15, "sixteen": 16, "seventeen": 17, "eighteen": 18, "nineteen": 19,
    "twenty": 20, "thirty": 30, "forty": 40, "fifty": 50, "sixty": 60, "seventy": 70,
    "eighty": 80, "ninety": 90,
}
_MULT = {"sau": 100, "hundred": 100, "hazaar": 1000, "hazar": 1000, "hajar": 1000,
         "thousand": 1000, "k": 1000, "lakh": 100000, "lakhs": 100000, "lac": 100000}
_DEVANAGARI_DIGITS = str.maketrans("०१२३४५६७८९", "0123456789")


def _normalise(text):
    text = text.lower().translate(_DEVANAGARI_DIGITS).replace("₹", " ")
    return re.sub(r"(?<=\d),(?=\d)", "", text)          # 10,000 -> 10000


def _eval_run(run):
    total = cur = 0
    for kind, val in run:
        if kind == "n":
            cur += val
        elif val == 100:
            cur = (cur or 1) * 100
        else:
            total += (cur or 1) * val
            cur = 0
    return total + cur


def extract_amount(text):
    """'paanch sau' -> 500, '10,000' -> 10000, 'do hazaar' -> 2000. None if absent.
    If digits are present they win, so a verb like 'de do' is never read as 2."""
    toks = re.findall(r"\d+(?:\.\d+)?|[^\W\d_]+", _normalise(text))
    has_digits = any(t[0].isdigit() for t in toks)
    run = []
    for t in toks + [None]:
        item = None
        if t is not None:
            if t[0].isdigit():
                item = ("n", int(float(t)))
            elif t in _MULT and run:
                item = ("m", _MULT[t])
            elif not has_digits and t in _WORDS:
                item = ("n", _WORDS[t])
        if item:
            run.append(item)
        else:
            if run:
                value = _eval_run(run)
                if value > 0:
                    return value
            run = []
    return None


# ----------------------------------------------------------------- intent
def _has(text, *stems):
    return any(s in text for s in stems)


_LOAN_REPAY = ("wapas", "vapas", "repay", "chuka", "return", "वापस")
_LOAN = ("loan", "karz", "qarz", "chahiye", "chahie", "chahiya", "need", "borrow", "चाहिए")
_OVERDUE = ("purana", "purane", "oldest", "overdue", "sab se late", "सबसे पुराना")
_AGEING = ("ageing", "aging", "report", "sab ka", "sabka", "all customers", "summary")
_SALE = ("bikri", "bikree", "sale", "becha", "kamai", "earning", "income", "बिक्री")
_QUESTION = ("kitn", "how much", "kya ", "what", "total", "?", "कितन")
_BALANCE = ("baaki", "baqi", "balance", "hisaab", "hisab", "owe", "बाकी", "हिसाब")
_PAY_STRONG = ("wapas", "vapas", "paid", "jama", "received", "receive", "mile", "mila",
               "return", "chuka", "payment", "वापस", "जमा")
_PAY_WEAK = ("diye", "de diya", "pay ")
_CREDIT = ("udhaar", "udhar", "credit", "उधार")
_CASH = ("cash", "nakad", "nakd", "becha", "sold")


def parse_command(text):
    t = _normalise(text or "")
    amount = extract_amount(t)
    question = _has(t, *_QUESTION)
    period = "week" if _has(t, "week", "hafta", "haft") else \
             "month" if _has(t, "month", "mahin", "mahina", "maheena") else "today"
    credit_word = _has(t, *_CREDIT)
    p = lambda intent: ParsedCommand(intent, amount, period)

    if _has(t, "loan", "karz", "qarz") and _has(t, *_LOAN_REPAY) and not _has(t, "chahiye"):
        return p("loan_repay")
    if _has(t, *_LOAN) and amount and not credit_word:
        return p("loan")
    if _has(t, *_OVERDUE):
        return p("overdue_query")
    if _has(t, *_AGEING):
        return p("ageing_report")
    if _has(t, *_SALE) and (amount is None or question):
        return p("sales_query")
    if amount and _has(t, *_CASH + _SALE) and not credit_word and not _has(t, *_PAY_STRONG):
        return p("cash_sale")
    if _has(t, *_BALANCE) and amount is None:
        return p("balance_query")
    if _has(t, *_PAY_STRONG):
        return p("payment")
    if credit_word:
        return p("credit_sale")
    if _has(t, *_PAY_WEAK):
        return p("payment")
    return p("unknown")
