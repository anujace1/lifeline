"""Deterministic risk maths. No AI here on purpose: numbers must be explainable."""

import math
from datetime import date, timedelta
import config


def d(s):
    return date.fromisoformat(s)


def stock_position(inv):
    avail = inv["on_hand"] - inv["reserved"]
    cov = avail / inv["daily_use"] if inv["daily_use"] else math.inf
    return avail, cov


def cover_days(inv):
    cov = stock_position(inv)[1]
    return 9999 if cov == math.inf else math.floor(cov)


def inventory_risk(inv, linked_order=None):
    avail, cov = stock_position(inv)
    if avail < inv["reorder_point"]:
        r = "Low stock"
    elif cov <= config.COVERAGE_THRESHOLD_DAYS:
        r = "At risk"
    else:
        r = "Healthy"
    if (
        linked_order
        and linked_order["status"] == "Delayed"
        and cov <= config.COVERAGE_THRESHOLD_DAYS + 2
    ):
        r = "At risk"
    return r


def exposure(order, inv, delay_days=0):
    """Will stock run out before the (delayed) delivery arrives?"""
    cover = cover_days(inv)
    arrival = d(order["promised_date"]) + timedelta(days=delay_days)
    stockout = config.TODAY + timedelta(days=cover)
    gap = max(0, (arrival - stockout).days)
    margin = (stockout - arrival).days
    sev = (
        "critical"
        if gap >= 3
        else "high" if gap > 0 else "medium" if margin <= 2 else "low"
    )
    return {
        "arrival_date": arrival.isoformat(),
        "stockout_date": stockout.isoformat(),
        "cover_days": cover,
        "gap_days": gap,
        "margin_days": margin,
        "revenue_at_risk": round(gap * inv["revenue_per_day"]),
        "severity": sev,
    }


def supplier_score(conn, sid):
    s = conn.execute(
        "SELECT base_reliability FROM suppliers WHERE supplier_id=?", (sid,)
    ).fetchone()
    delayed = conn.execute(
        "SELECT COUNT(*) FROM orders WHERE supplier_id=? AND status='Delayed'", (sid,)
    ).fetchone()[0]
    single = conn.execute(
        "SELECT COUNT(*) FROM parts WHERE primary_supplier_id=? AND backup_supplier_id IS NULL",
        (sid,),
    ).fetchone()[0]
    score = max(0, s["base_reliability"] - min(15, 2 * delayed) - 3 * single)
    label = "High risk" if score < 60 else "Watch" if score < 80 else "Reliable"
    return {
        "score": score,
        "label": label,
        "delayed_orders": delayed,
        "single_source_parts": single,
    }
