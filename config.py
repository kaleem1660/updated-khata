"""Central settings. All rates are prototype assumptions (simulated money)."""

DEFAULT_FEE_RATE = 0.03            # flat loan fee for a 30-day term
DEFAULT_LIMIT_PCT = 0.40           # loan limit = 40% of last 30 days of sales
DEFAULT_PLATFORM_FEE_RATE = 0.01   # fee on embedded (payment-request) collections
DEFAULT_APPROVAL_THRESHOLD = 5000  # loans above this also need an approver PIN

MIN_HISTORY_DAYS = 15              # days of recorded sales needed before lending
LOAN_TERM_DAYS = 30
SALES_WINDOW_DAYS = 30

PENDING_TTL_SECONDS = 120          # a prepared entry expires after 2 minutes
MAX_PIN_ATTEMPTS = 3               # account locks after 3 wrong PINs
MAX_ENTRY_AMOUNT = 100_000         # sanity cap for a single ledger entry (Rs)
REMINDER_AFTER_DAYS = 7            # remind customers whose oldest dues are older
