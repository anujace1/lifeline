"""What-if engine: compares response options using formulas, not AI.
Which options appear depends on the delay reason, the length of the delay, stock cover, quantity and backup availability,
so different alerts get different option sets and different numbers."""

import math
import re
from datetime import timedelta
import config, risk

A = config.ASSUMPTIONS
# delay reason -> (option label, share of the delay it recovers, extra cost as a share of order value, note)
REASON_ACTIONS = {
    "Vehicle breakdown": (
        "Carrier sends a replacement vehicle",
        0.5,
        0.0,
        "Carrier arranges a backup vehicle; recovery cost is theirs if liable.",
    ),
    "Wrong location dispatch": (
        "Reroute to the correct hub",
        0.5,
        0.01,
        "Carrier transfers the load to the right hub; rerouting is charged to them if contractually liable.",
    ),
    "Product left behind in courier warehouse": (
        "Priority dispatch from the warehouse",
        0.6,
        0.01,
        "Carrier ships the left-behind goods on the next run at priority.",
    ),
    "Partner not answering the call": (
        "Escalate to the partner's manager",
        0.3,
        0.0,
        "Escalate through the manager contact; recovers time once someone takes ownership.",
    ),
    "Connecting local courier team issues": (
        "Own team collects at the hub",
        0.5,
        0.015,
        "Collect directly from the connecting hub to skip the failed handover.",
    ),
    "Delivery dispute and undelivered": (
        "Joint re-delivery with proof of delivery",
        0.4,
        0.01,
        "Re-deliver with the partner and record proof to settle the dispute.",
    ),
    "Damage before Delivering": (
        "Rush replacement shipment",
        0.5,
        0.04,
        "Ship replacement units now; claim the cost back from the carrier after validation.",
    ),
    "Rough product handling": (
        "Rush replacement shipment",
        0.5,
        0.04,
        "Ship replacement units now; claim the cost back from the carrier after validation.",
    ),
    "Miss handling of the product": (
        "Re-process at the hub with priority scan",
        0.4,
        0.005,
        "Carrier re-scans and re-routes the item on the next run.",
    ),
}
STOP = {
    "product",
    "partner",
    "courier",
    "delivery",
    "delivering",
    "issues",
    "thing",
    "other",
}


def _stems(t):
    return {
        w[:5] for w in re.findall(r"[a-z]+", t.lower()) if len(w) > 3 and w not in STOP
    }


def match_reason(reason):
    if not reason:
        return None
    for k in REASON_ACTIONS:
        if k.lower() == reason.lower():
            return k
    best, top = None, 0
    for k in REASON_ACTIONS:
        n = len(_stems(reason) & _stems(k))
        if n > top:
            best, top = k, n
    return best


def run_scenarios(order, part, inv, delay_days, cash_related=False, reason=None):
    arrival = risk.d(order["promised_date"]) + timedelta(days=delay_days)
    D = max(1, (arrival - config.TODAY).days)  # days until the delayed delivery lands
    S, _ = risk.stock_position(inv)
    u, rev, cover = inv["daily_use"], inv["revenue_per_day"], risk.cover_days(inv)
    value = order["qty"] * part["unit_cost"]
    out = []

    def add(key, label, ok, stop, extra, loss, delivery, note):
        out.append(
            {
                "key": key,
                "label": label,
                "feasible": ok,
                "note": note,
                "delivery": delivery,
                "stop_days": stop if ok else None,
                "extra_cost": round(extra) if ok else None,
                "revenue_loss": round(loss) if ok else None,
                "total": round(extra + loss) if ok else None,
            }
        )

    def shifted(
        key, label, recover, pct, note
    ):  # the goods arrive `recover` days earlier than the delayed date
        recover = max(1, min(recover, delay_days))
        stop_x = max(0, max(1, D - recover) - cover)
        add(
            key,
            label,
            True,
            stop_x,
            value * pct,
            stop_x * rev,
            "On time" if stop_x == 0 else "Slightly late",
            f"{note} (recovers about {recover} day(s))",
        )

    stop = max(0, D - cover)
    nothing_total = stop * rev
    add(
        "nothing",
        "Do nothing",
        True,
        stop,
        0,
        nothing_total,
        "Late" if stop else "On time",
        "Wait for the original partner." if stop else "Stock already covers the wait.",
    )

    rate = max(min(1.0, S / (u * D)) if u else 1.0, A["min_run_rate"])
    lasts = S / (u * rate) if u else 9999
    stop_s = max(0, math.ceil(D - lasts - 1e-9))
    loss_s = (1 - rate) * rev * min(D, lasts) + rev * stop_s
    useful = loss_s < nothing_total - 0.5
    add(
        "slow",
        "Run line slower",
        useful,
        stop_s,
        0,
        loss_s,
        "Slightly late" if stop_s else "On time",
        (
            f"Run at {round(rate * 100)}% rate until supply arrives."
            if useful
            else "No benefit: stock is too low (or not needed) at the minimum run rate."
        ),
    )

    if part["backup_supplier_id"]:
        days = part["backup_lead_time_days"] + part["backup_qualification_days"]
        stop_b = max(0, days - cover)
        add(
            "backup",
            "Switch to the backup partner",
            days < D,
            stop_b,
            value * part["backup_premium_pct"] / 100,
            stop_b * rev,
            "On time" if stop_b == 0 else "Slightly late",
            f"Backup delivers in about {days} day(s); confirm capacity first.",
        )
    else:
        add(
            "backup",
            "Switch to the backup partner",
            False,
            0,
            0,
            0,
            "n/a",
            "No qualified backup for this item (single-source).",
        )

    action = REASON_ACTIONS.get(match_reason(reason))
    if action and delay_days >= 2 and not cash_related:
        shifted(
            "carrier_fix",
            action[0],
            round(delay_days * action[1]),
            action[2],
            action[3],
        )

    if delay_days >= 3 and not cash_related:
        shifted(
            "express",
            "Expedite with express transport",
            round(delay_days * A["express_recovery_share"]),
            A["express_cost_pct"],
            "Pay for faster transport",
        )

    if delay_days >= 2 and u:
        dp = max(0, (risk.d(order["promised_date"]) - config.TODAY).days)
        stop_p = max(0, dp - cover) + max(
            0,
            math.ceil(
                D - (max(cover, dp) + A["partial_share"] * order["qty"] / u) - 1e-9
            ),
        )
        add(
            "partial",
            "Split shipment (part now, rest later)",
            True,
            stop_p,
            value * A["partial_fee_pct"],
            stop_p * rev,
            "On time" if stop_p == 0 else "Slightly late",
            f"Ask for {round(A['partial_share'] * 100)}% of the order on the original date and the rest when ready.",
        )

    if stop > 0:
        add(
            "resequence",
            "Do nothing + re-sequence production",
            True,
            stop,
            nothing_total * A["resequence_cost_pct"],
            nothing_total * (1 - A["resequence_save"]),
            "Late",
            f"Build other products during the stop; assumes {round(A['resequence_save'] * 100)}% of the lost output is recovered.",
        )

    stop_e = max(0, D - min(delay_days, A["early_pay_recovery_days"]) - cover)
    add(
        "early_pay",
        "Pay the partner early",
        cash_related,
        stop_e,
        value * A["early_pay_cost_pct"],
        stop_e * rev,
        "Short delay" if stop_e else "On time",
        f"Only if the delay is payment-related (assumes {A['early_pay_recovery_days']} days recovered).",
    )
    return out


def recommend(scs):
    ok = [s for s in scs if s["feasible"]]
    return min(ok, key=lambda s: (s["total"], s["stop_days"]))["key"] if ok else None
