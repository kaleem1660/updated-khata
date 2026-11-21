from .util import fmt_ts, now_or


def log(conn, shop_id, event, detail="", now=None):
    """Append one audit row. The caller owns the transaction (no commit here)."""
    conn.execute(
        "INSERT INTO audit_log(ts, shop_id, event, detail) VALUES (?,?,?,?)",
        (fmt_ts(now_or(now)), shop_id, event, str(detail)[:500]),
    )


def recent(conn, shop_id, limit=100):
    return conn.execute(
        "SELECT ts, event, detail FROM audit_log WHERE shop_id=? ORDER BY id DESC LIMIT ?",
        (shop_id, limit),
    ).fetchall()
