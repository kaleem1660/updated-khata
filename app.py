"""Voice Khata - Streamlit app.   Run:  streamlit run app.py
Simulated prototype: all money is made-up. The AI only prepares entries; a PIN posts them."""
import hashlib
import os
from datetime import datetime

import pandas as pd
import streamlit as st

from voicekhata import actions, agent, audit, credit, db, ledger, llm, reminders, seed, security, voice
from voicekhata.util import inr, now_or, parse_ts

st.set_page_config(page_title="Voice Khata", page_icon="🧾", layout="wide")

try:  # Streamlit Cloud: put ANTHROPIC_API_KEY in app secrets (optional)
    if "ANTHROPIC_API_KEY" in st.secrets and not os.getenv("ANTHROPIC_API_KEY"):
        os.environ["ANTHROPIC_API_KEY"] = st.secrets["ANTHROPIC_API_KEY"]
except Exception:
    pass


@st.cache_resource
def get_conn():
    conn = db.connect(os.getenv("VK_DB", "voice_khata.db"))
    db.init_db(conn)
    seed.seed_demo(conn)
    return conn


conn = get_conn()
S = st.session_state
for key, default in (("shop_id", None), ("chat", []), ("pending", None),
                     ("flash", None), ("speak", None), ("last_audio", None)):
    S.setdefault(key, default)


# ------------------------------------------------------------------ helpers
def flash(kind, text):
    S.flash = (kind, text)


def say(role, text):
    S.chat.append({"role": role, "text": text})


def stage(result, source_text=None):
    """Show the outcome of a prepare_* call: either a pending card or the reason."""
    if result.ok:
        S.pending = result.action_id
        say("assistant", result.description["voice"])
        S.speak = result.description["voice"]
    else:
        flash("error", result.reason)


def run_command(text, shop):
    say("user", text)
    reply = agent.handle_command(conn, shop["id"], text, now_or(), use_llm=S.get("use_llm", False))
    say("assistant", reply.text)
    S.speak = reply.text
    if reply.action_id:
        S.pending = reply.action_id
    st.rerun()


# -------------------------------------------------------------------- login
def login_view():
    st.title("🧾 Voice Khata")
    st.caption("Voice-first ledger with embedded payments, credit and reminders - simulated prototype.")
    shops = conn.execute("SELECT id, name FROM shops ORDER BY name").fetchall()
    with st.form("login"):
        choice = st.selectbox("Dukaan", [s["name"] for s in shops])
        pin = st.text_input("PIN", type="password", max_chars=6)
        go = st.form_submit_button("Login")
    if go:
        shop = next(s for s in shops if s["name"] == choice)
        ok, msg, _ = security.verify_pin(conn, "shops", shop["id"], pin)
        if ok:
            S.shop_id = shop["id"]
            st.rerun()
        else:
            st.error(msg)
    st.info("Demo login: **Demo Kirana** / PIN **1234** (approver PIN for big loans: **4321**).")
    with st.expander("Nayi dukaan register karein"):
        with st.form("register"):
            name = st.text_input("Dukaan ka naam")
            pin1 = st.text_input("PIN (4-6 digits)", type="password", max_chars=6)
            a_name = st.text_input("Approver ka naam (optional - bade loan ke liye)")
            a_pin = st.text_input("Approver PIN (optional)", type="password", max_chars=6)
            reg = st.form_submit_button("Register")
        if reg:
            try:
                db.create_shop(conn, name, pin1, a_name or None, a_pin or None)
                st.success("Dukaan ban gayi. Ab login karein.")
            except ValueError as exc:
                st.error(str(exc))


# ------------------------------------------------------------ pending card
def pending_card(shop):
    pid = S.pending
    if not pid:
        return
    row = actions.get_pending(conn, shop["id"], pid)
    if row is None or row["status"] != "pending":
        S.pending = None
        return
    now = now_or()
    secs = int((parse_ts(row["expires_at"]) - now).total_seconds())
    desc, res = actions.describe_pending(conn, shop["id"], row, now)
    with st.container(border=True):
        if desc is None:
            st.error(res.reason)
            if st.button("Band karein"):
                actions.cancel_action(conn, shop["id"], pid)
                S.pending = None
                st.rerun()
            return
        st.subheader(f"🔐 {desc['title']}")
        if desc["amount"] is not None:
            st.markdown(f"# {inr(desc['amount'])}")           # large digits for read-back
        for line in desc["lines"]:
            st.write(line)
        st.caption(f"Entry {max(secs, 0)} second mein expire hogi. Sirf PIN se post hoti hai.")
        with st.form("confirm_form"):
            pin = st.text_input("Aap ka PIN", type="password", max_chars=6)
            a_pin = None
            if res.requires_approval:
                a_pin = st.text_input("Approver ka PIN (bada loan)", type="password", max_chars=6)
            c1, c2 = st.columns(2)
            confirm = c1.form_submit_button("✅ Confirm")
            cancel = c2.form_submit_button("❌ Cancel")
        if cancel:
            actions.cancel_action(conn, shop["id"], pid)
            S.pending = None
            say("assistant", "Entry cancel kar di.")
            st.rerun()
        if confirm:
            out = actions.confirm_action(conn, shop["id"], pid, pin, a_pin, now_or())
            say("assistant", out.message)
            flash("success" if out.ok else "error", out.message)
            if out.done:
                S.pending = None
            if out.code == "locked":
                S.shop_id = None
            st.rerun()


# --------------------------------------------------------------------- tabs
def assistant_tab(shop):
    st.caption("Bolein ya likhein: *Ali ne paanch sau ka udhaar liya* · *Ali ne teen sau wapas diye* · "
               "*Aaj ki bikri kitni hui?* · *Kis ka udhaar sab se purana hai?* · *Mujhe 10000 chahiye*")
    audio = None
    if hasattr(st, "audio_input") and voice.stt_available():
        audio = st.audio_input("🎤 Microphone")
    if audio is not None:
        data = audio.getvalue()
        digest = hashlib.md5(data).hexdigest()
        if digest != S.last_audio:
            S.last_audio = digest
            with st.spinner("Sun raha hoon..."):
                text = voice.transcribe(data, S.get("stt_lang") or None)
            if text:
                run_command(text, shop)
            st.warning("Awaaz samajh nahi aayi. Dobara try karein ya type karein.")
    for m in S.chat[-30:]:
        with st.chat_message(m["role"]):
            st.text(m["text"])
    prompt = st.chat_input("Command likhein (Hindi / Hinglish / English)")
    if prompt:
        run_command(prompt, shop)


def customers_tab(shop):
    customers = ledger.list_customers(conn, shop["id"])
    st.subheader("Customers aur baaki udhaar")
    df = pd.DataFrame(customers, columns=["id", "name", "balance"]) if customers else pd.DataFrame()
    if not df.empty:
        st.dataframe(df.rename(columns={"name": "Customer", "balance": "Baaki (₹)"}).drop(columns="id"),
                     hide_index=True)
    with st.form("add_customer", clear_on_submit=True):
        name = st.text_input("Naya customer")
        if st.form_submit_button("Add"):
            try:
                db.add_customer(conn, shop["id"], name)
                st.rerun()
            except ValueError as exc:
                st.error(str(exc))
    st.divider()
    st.subheader("Quick entry (bina awaaz ke)")
    if not customers:
        st.info("Pehle customer add karein.")
        return
    with st.form("quick_entry"):
        kind = st.selectbox("Type", ["credit_sale", "payment", "cash_sale"],
                            format_func=lambda k: {"credit_sale": "Udhaar (credit sale)",
                                                   "payment": "Payment received",
                                                   "cash_sale": "Cash sale"}[k])
        who = st.selectbox("Customer", customers, format_func=lambda c: c["name"])
        amount = st.number_input("Raqam (₹)", min_value=1, step=50, value=500)
        go = st.form_submit_button("Prepare entry")
    if go:
        stage(agent.prepare_entry(conn, shop["id"], who["id"], int(amount), kind, now_or()))
        st.rerun()


def reports_tab(shop):
    now = now_or()
    st.subheader("Sales")
    cols = st.columns(3)
    for col, (label, period) in zip(cols, (("Aaj", "today"), ("Is hafte", "week"), ("Is mahine", "month"))):
        s = ledger.sales_summary(conn, shop["id"], period, now)
        col.metric(label, inr(s["total"]), f"cash {inr(s['cash'])} | udhaar {inr(s['credit'])}",
                   delta_color="off")
    st.subheader("Receivables ageing")
    rows = ledger.ageing(conn, shop["id"], now)
    if rows:
        df = pd.DataFrame(rows)[["name", "total", "b0_7", "b8_15", "b15_plus", "oldest_days"]]
        df.columns = ["Customer", "Total (₹)", "0-7 din", "8-15 din", "15+ din", "Sab se purana (din)"]
        st.dataframe(df, hide_index=True)
    else:
        st.success("Koi udhaar baaki nahi.")
    st.subheader("Simple profit view (is mahine)")
    p = ledger.profit_view(conn, shop["id"], "month", now)
    c = st.columns(3)
    c[0].metric("Sales", inr(p["sales"]))
    c[1].metric("Kharche + fees", inr(p["total_expenses"]))
    c[2].metric("Profit", inr(p["profit"]))
    st.subheader("Trial balance")
    tb, td, tc = ledger.trial_balance(conn, shop["id"])
    st.dataframe(pd.DataFrame(tb)[["code", "name", "debit", "credit"]], hide_index=True)
    st.write(f"Debits **{inr(td)}** = Credits **{inr(tc)}** → " + ("✅ Balanced" if td == tc else "❌ NOT balanced"))
    st.subheader("Ledger entries")
    entries = ledger.recent_entries(conn, shop["id"], 40)
    if entries:
        df = pd.DataFrame(entries)[["id", "ts", "kind", "description", "amount", "reversed"]]
        st.dataframe(df, hide_index=True)
        options = [e for e in entries if not e["reversed"] and e["reverses_id"] is None
                   and e["kind"] not in ledger.LOAN_KINDS]
        if options:
            with st.form("reverse"):
                pick = st.selectbox("Galat entry reverse karein", options,
                                    format_func=lambda e: f"#{e['id']} {e['description']} ({inr(e['amount'])})")
                if st.form_submit_button("Prepare reversal"):
                    stage(actions.prepare_action(conn, shop["id"], "reversal",
                                                 {"entry_id": pick["id"]}, now_or()))
                    st.rerun()
        st.caption("Ledger append-only hai: entry delete nahi hoti, sirf reversing entry banti hai.")


def loan_tab(shop):
    now = now_or()
    offer = credit.loan_offer(conn, shop, now)
    st.subheader("Working-capital loan (simulated)")
    st.caption(f"Limit = {int(shop['limit_pct'] * 100)}% of last 30 days sales · "
               f"fee {shop['fee_rate'] * 100:.0f}% flat for 30 days · one loan at a time.")
    c = st.columns(3)
    c[0].metric("Pichle 30 din ki sales", inr(offer["sales_30d"]))
    c[1].metric("Aap ki limit", inr(offer["limit"]))
    c[2].metric("Record history", f"{offer['history_days']} din")
    loan = offer["active_loan"]
    if loan is not None:
        st.warning(f"Active loan: {inr(loan['principal'])} + fee {inr(loan['fee'])} = "
                   f"{inr(loan['total_due'])}, due {loan['due_date'][:10]}"
                   + (" - **OVERDUE**" if credit.is_overdue(loan, now) else ""))
        if st.button("Loan wapas karein (prepare)"):
            stage(actions.prepare_action(conn, shop["id"], "loan_repay", {"loan_id": loan["id"]}, now))
            st.rerun()
    if not offer["eligible"]:
        if loan is None:
            st.info(offer["reason"])
        return
    with st.form("loan_form"):
        amount = st.number_input("Kitna chahiye (₹)", min_value=1, step=500,
                                 value=max(min(offer["limit"], 10000), 1))
        go = st.form_submit_button("Loan request (prepare)")
    if go:
        stage(agent.prepare_loan(conn, shop["id"], int(amount), now))
        st.rerun()
    st.caption(f"{inr(shop['approval_threshold'])} se bade loan ke liye approver ka PIN bhi lagega.")


def payments_tab(shop):
    now = now_or()
    st.subheader("Payment request (simulated embedded payment)")
    st.caption(f"Customer ko payment request bhejein. Simulated payment par {shop['platform_fee_rate'] * 100:.0f}% "
               "platform fee kat'ti hai; ledger mein gross, fee aur net teeno record hote hain.")
    owing = [c for c in ledger.list_customers(conn, shop["id"]) if c["balance"] > 0]
    if owing:
        with st.form("pay_request"):
            who = st.selectbox("Customer", owing, format_func=lambda c: f"{c['name']} (baaki {inr(c['balance'])})")
            amount = st.number_input("Raqam (₹)", min_value=1, step=50, value=min(who["balance"], 500))
            go = st.form_submit_button("Simulate: customer ne pay kiya")
        if go:
            stage(actions.prepare_action(conn, shop["id"], "online_payment",
                                         {"customer_id": who["id"], "amount": int(amount)}, now))
            st.rerun()
    else:
        st.info("Kisi ka udhaar baaki nahi.")
    st.subheader("Reminders (drafts - bheje nahi jaate)")
    items = reminders.build_reminders(conn, shop, now)
    if not items:
        st.success("Abhi koi reminder nahi.")
    for r in items:
        st.text(f"[{r['kind']}] {r['to']}: {r['message']}")


def audit_tab(shop):
    st.subheader("Audit log")
    rows = audit.recent(conn, shop["id"], 150)
    if rows:
        st.dataframe(pd.DataFrame([dict(r) for r in rows]), hide_index=True)


# --------------------------------------------------------------------- main
def main():
    if S.shop_id is None:
        login_view()
        return
    shop = db.get_shop(conn, S.shop_id)
    with st.sidebar:
        st.header(f"🧾 {shop['name']}")
        if st.button("Logout"):
            S.update(shop_id=None, pending=None, chat=[])
            st.rerun()
        st.divider()
        S.use_llm = st.toggle("Claude se samajhna (optional)", value=False, disabled=not llm.available(),
                              help="Needs `pip install anthropic` and ANTHROPIC_API_KEY. "
                                   "Claude only fills a form; code and PIN decide.")
        if voice.stt_available():
            S.stt_lang = st.selectbox("Bolne ki zubaan", ["hi", "ur", "en", "ta"])
        else:
            st.caption("Mic: `pip install faster-whisper` (optional).")
        S.tts = st.toggle("Jawab bolkar sunayein (gTTS)", value=False, disabled=not voice.tts_available())
        st.caption("Simulated prototype - paisa asli nahi hai.")

    if S.flash:
        kind, text = S.flash
        {"success": st.success, "error": st.error}.get(kind, st.info)(text)
        S.flash = None
    if S.speak and S.get("tts"):
        clip = voice.speak(S.speak)
        if clip:
            st.audio(clip, format="audio/mp3", autoplay=True)
    S.speak = None

    pending_card(shop)
    tabs = st.tabs(["💬 Assistant", "👥 Customers", "📊 Reports", "💰 Loan", "📲 Payments & Reminders", "🧾 Audit"])
    for tab, fn in zip(tabs, (assistant_tab, customers_tab, reports_tab, loan_tab, payments_tab, audit_tab)):
        with tab:
            fn(shop)


main()
