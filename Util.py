from datetime import datetime
from decimal import Decimal, ROUND_HALF_UP

TS_FMT = "%Y-%m-%d %H:%M:%S"


def now_or(now=None):
    return now if now is not None else datetime.now().replace(microsecond=0)


def fmt_ts(dt):
    return dt.strftime(TS_FMT)


def parse_ts(s):
    return datetime.strptime(s, TS_FMT)


def pct_of(amount, rate):
    """Whole-rupee percentage, rounded half up (avoids float surprises)."""
    value = Decimal(int(amount)) * Decimal(str(rate))
    return int(value.quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def inr(n):
    """Format whole rupees with Indian digit grouping: 100000 -> ₹1,00,000."""
    n = int(n)
    sign = "-" if n < 0 else ""
    s = str(abs(n))
    if len(s) > 3:
        head, tail = s[:-3], s[-3:]
        parts = []
        while len(head) > 2:
            parts.insert(0, head[-2:])
            head = head[:-2]
        if head:
            parts.insert(0, head)
        s = ",".join(parts + [tail])
    return f"{sign}₹{s}"
