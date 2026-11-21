# 🧾 Voice Khata

Voice-first ledger with embedded finance (payments, credit, reminders) for small shopkeepers.
**Simulated prototype - all money is made-up.** Python + Streamlit + SQLite.

> The AI understands, code decides: the agent can only *prepare* an entry; a PIN and rule-based
> policy checks decide what is actually posted.

## Quick start

```bash
git clone https://github.com/<your-username>/voice-khata.git
cd voice-khata
python -m venv .venv
source .venv/bin/activate            # Windows: .venv\Scripts\activate
pip install -r requirements.txt
streamlit run app.py
```

Login: **Demo Kirana** / PIN **1234** (approver PIN for large loans: **4321**).
Demo data (5 customers, 30 days of sales) is created automatically on first run.

Try typing (Hindi / Hinglish / English):

| Say | Result |
| --- | --- |
| `Ali ne paanch sau ka udhaar liya` | prepares ₹500 credit sale → confirm with PIN |
| `Ali ne teen sau wapas diye` | prepares payment received |
| `Aaj ki bikri kitni hui?` | today's sales, cash vs credit |
| `Kis ka udhaar sab se purana hai?` | oldest unpaid customer |
| `Mujhe maal ke liye 10000 chahiye` | loan check → approver + your PIN |

## Run the tests

```bash
pip install -r requirements-dev.txt
pytest -q            # or: python -m unittest discover -s tests -t .
```

Tests cover ledger correctness (balanced books, reversals, append-only), the credit rule
(at/below/above limit, history, overdue, one active loan), command parsing, and the safety
attacks (replayed confirmation, PIN lockout, expiry, other shop's data, invalid amounts,
hostile customer names).

## Project layout

```
app.py                 Streamlit UI (login, assistant, customers, reports, loan, payments, audit)
voicekhata/
  db.py                schema, append-only triggers, shops/customers
  ledger.py            double-entry posting, reversals, reports (sales, ageing, trial balance)
  credit.py            limit rule (40% of 30-day sales), 3% fee, disburse / repay
  policy.py            policy engine (runs at prepare AND at confirm)
  actions.py           prepare → confirm flow: PIN, 2-min expiry, one-shot, audit log
  security.py          PBKDF2 PIN hashing, 3-strike lockout, approver check
  parser.py            rule-based Hindi/Hinglish/English parser (amount words, intents)
  agent.py             the 6 agent tools + command handling (none posts anything)
  llm.py               OPTIONAL Claude tool-calling parser (falls back to rules)
  voice.py             OPTIONAL faster-whisper (STT) and gTTS (spoken replies)
  reminders.py         overdue-customer and loan-due reminder drafts
  seed.py              demo data
tests/                 unittest/pytest suite
scripts/unlock.py      admin: unlock a shop after 3 wrong PINs
```

## Optional upgrades

```bash
pip install -r requirements-optional.txt
export ANTHROPIC_API_KEY=sk-ant-...   # enables the "Claude se samajhna" toggle
```

* **Microphone:** installing `faster-whisper` shows a mic widget (needs Streamlit ≥ 1.40).
  First use downloads the Whisper model. Choose `hi`/`ur`/`en`/`ta` in the sidebar.
* **Spoken replies:** `gTTS` (needs internet).
* **Claude mode:** Claude sees only the spoken sentence, fills a small `interpret_command`
  form, and cannot post anything. Customers are matched by code, not by the AI.
  Model can be changed with `ANTHROPIC_MODEL`.

## Put it on GitHub

```bash
git init
git add .
git commit -m "Voice Khata: voice-first ledger with embedded finance"
git branch -M main
git remote add origin https://github.com/<your-username>/voice-khata.git
git push -u origin main
```

(Create the empty repo first on github.com → New repository, without README/.gitignore.)
GitHub Actions (`.github/workflows/tests.yml`) then runs the tests on every push.

## Deploy free on Streamlit Community Cloud

1. Push the repo to GitHub (above).
2. Go to <https://share.streamlit.io> → **New app** → pick the repo, branch `main`, main file `app.py`.
3. (Optional) *Advanced settings → Secrets*: `ANTHROPIC_API_KEY = "sk-ant-..."`.

Notes: Cloud storage is ephemeral, so the demo database resets when the app restarts. Install the
heavy optional packages (`faster-whisper`) only locally; keep `requirements.txt` as is for Cloud.

## Prototype assumptions (change in `voicekhata/config.py`)

Loan fee 3% flat / 30 days · limit 40% of last 30 days of sales · ≥ 15 days of history · one active
loan · loans over ₹5,000 need an approver PIN · platform fee 1% on payment requests · entries
expire after 2 minutes · lock after 3 wrong PINs · amounts are whole rupees (fees rounded half up).

## Known limitations

Lending and payments are simulated; real lending needs a licence and regulated partner. Sales are
entered by the shopkeeper, so a real system must verify them. The app uses one shared SQLite file
and is meant for a single-user demo, not production traffic.
