import os
from datetime import date

DB_PATH = os.getenv("LIFELINE_DB", "lifeline.db")
# "Today" is fixed so the demo data stays consistent. Set LIFELINE_TODAY=real to use the real date.
_t = os.getenv("LIFELINE_TODAY", "2026-10-28")
TODAY = date.today() if _t == "real" else date.fromisoformat(_t)

LLM_PROVIDER = os.getenv("LIFELINE_LLM", "mock")  # mock | anthropic | sap
MODEL = os.getenv("LIFELINE_MODEL", "claude-sonnet-5-5")
MIN_CONFIDENCE = 0.6  # below this an alert is "needs_review"
COVERAGE_THRESHOLD_DAYS = 5  # inventory "At risk" below this many days of cover

# Every number the scenario engine assumes. Replace with real values as you learn them.
ASSUMPTIONS = {
    "min_run_rate": 0.4,  # slowest the line can run before it stops
    "early_pay_recovery_days": 3,  # days an early payment is assumed to win back
    "early_pay_cost_pct": 0.01,  # financing cost of paying early (1% of order value)
    "express_recovery_share": 0.5,  # share of the delay that express transport wins back
    "express_cost_pct": 0.03,  # express cost as a share of the order value
    "partial_share": 0.4,  # share of the order a partner can ship on the original date
    "partial_fee_pct": 0.005,  # handling cost of a split shipment
    "resequence_save": 0.3,  # share of lost output recovered by building other products
    "resequence_cost_pct": 0.05,  # changeover cost as a share of the lost revenue
}
