"""The single Lifeline agent. One inbox, one pipeline:
message -> understand (AI) -> match to records -> calculate (code) -> alert -> human decides.
"""

import json
import re
from datetime import datetime
import config, llm, risk, scenarios


def now():
    return datetime.now().isoformat(timespec="seconds")


def audit(conn, actor, action, detail):
    conn.execute(
        "INSERT INTO audit(ts,actor,action,detail) VALUES (?,?,?,?)",
        (now(), actor, action, detail),
    )


def alert_dict(r):
    a = dict(r)
    for k in ("exposure", "scenarios", "plan"):
        a[k] = json.loads(a[k]) if a.get(k) else None
    return a


def identify_supplier(conn, sender):
    digits = re.sub(r"\D", "", sender)[-10:]
    for s in conn.execute("SELECT * FROM suppliers"):
        if (digits and re.sub(r"\D", "", s["phone"] or "")[-10:] == digits) or (
            s["email"] and s["email"].lower() in sender.lower()
        ):
            return s
    return None


def ingest(conn, channel, sender, text):
    """Single entry point for every channel (simulator, Twilio, email...)."""
    cur = conn.execute(
        "INSERT INTO messages(channel,sender,text,received_at) VALUES (?,?,?,?)",
        (channel, sender, text, now()),
    )
    mid = cur.lastrowid
    audit(conn, "system", "message_received", f"{channel} from {sender}")
    alert = process_message(conn, mid)
    conn.commit()
    return {"message_id": mid, "alert": alert}


def _make_alert(conn, mid, **f):
    f.setdefault("created_at", now())
    for k in ("exposure", "scenarios"):
        if f.get(k) is not None:
            f[k] = json.dumps(f[k])
    cols = ["message_id"] + list(f)
    cur = conn.execute(
        f"INSERT INTO alerts({','.join(cols)}) VALUES ({','.join('?' * len(cols))})",
        [mid] + list(f.values()),
    )
    audit(
        conn,
        "agent",
        "alert_created",
        f"alert {cur.lastrowid} ({f.get('kind')}, {f.get('status')})",
    )
    return alert_dict(
        conn.execute("SELECT * FROM alerts WHERE id=?", (cur.lastrowid,)).fetchone()
    )


def process_message(conn, mid):
    msg = conn.execute("SELECT * FROM messages WHERE id=?", (mid,)).fetchone()
    sup = identify_supplier(conn, msg["sender"])
    parts = {r["name"]: r["sku"] for r in conn.execute("SELECT name, sku FROM parts")}
    ex = llm.extract(msg["text"], parts)
    conn.execute(
        "UPDATE messages SET supplier_id=?, extracted=? WHERE id=?",
        (sup["supplier_id"] if sup else None, json.dumps(ex), mid),
    )
    if ex["intent"] == "other":
        return None
    if not sup:
        return _make_alert(
            conn,
            mid,
            kind="unknown_sender",
            severity="medium",
            status="needs_review",
            confidence=ex["confidence"],
            reason=ex["reason"],
            delay_days=ex["delay_days"],
            explanation=f"Message from unrecognised sender {msg['sender']}. Match it to a supplier before acting.",
        )
    orders = conn.execute(
        "SELECT * FROM orders WHERE supplier_id=? AND status!='Delivered' ORDER BY promised_date",
        (sup["supplier_id"],),
    ).fetchall()
    if not orders:
        return _make_alert(
            conn,
            mid,
            kind="no_open_order",
            supplier_id=sup["supplier_id"],
            severity="low",
            status="needs_review",
            confidence=ex["confidence"],
            explanation=f"{sup['name']} wrote, but there are no open orders to match.",
        )
    pick = next((o for o in orders if o["po"] == ex["po_hint"]), None) or next(
        (o for o in orders if o["sku"] == ex["sku_hint"]), None
    )
    if pick is None and (
        ex["po_hint"] or ex["sku_hint"]
    ):  # the message names an order this sender doesn't have: never guess
        hint = ex["po_hint"] or ex["sku_hint"]
        other = (
            conn.execute(
                "SELECT s.name FROM orders o JOIN suppliers s USING(supplier_id) WHERE o.po=?",
                (ex["po_hint"],),
            ).fetchone()
            if ex["po_hint"]
            else None
        )
        where = (
            f" (it belongs to {other['name']})"
            if other
            else " and no such open order was found"
        )
        return _make_alert(
            conn,
            mid,
            kind="order_mismatch",
            supplier_id=sup["supplier_id"],
            severity="medium",
            status="needs_review",
            confidence=ex["confidence"],
            delay_days=ex["delay_days"],
            explanation=f"{sup['name']} mentions {hint}, which is not one of their open orders{where}. Check the sender or the order number before acting.",
        )
    ambiguous = pick is None and len(orders) > 1
    pick = pick or orders[0]
    conf = round(max(0.0, ex["confidence"] - (0.2 if ambiguous else 0)), 2)
    days = ex["delay_days"]
    if days is None and ex.get(
        "new_date"
    ):  # supplier gave a new date instead of "N days late"
        days = max(0, (risk.d(ex["new_date"]) - risk.d(pick["promised_date"])).days)
    base = dict(
        supplier_id=sup["supplier_id"],
        po=pick["po"],
        sku=pick["sku"],
        reason=ex["reason"],
        confidence=conf,
        delay_days=days,
    )
    part = conn.execute("SELECT * FROM parts WHERE sku=?", (pick["sku"],)).fetchone()
    inv = conn.execute("SELECT * FROM inventory WHERE sku=?", (pick["sku"],)).fetchone()

    if ex["intent"] == "cashflow":
        return _make_alert(
            conn,
            mid,
            kind="cashflow",
            severity="medium",
            status="needs_review",
            **base,
            explanation=f"{sup['name']} raised a payment/cash-flow concern (open order {pick['po']}). Review payment status; "
            f"stock cover for {part['name']} is {risk.cover_days(inv)} days.",
        )
    days = days or 0
    exp = risk.exposure(pick, inv, days)
    cash = "payment" in (ex["reason"] or "") or "cash" in (ex["reason"] or "")
    scs = scenarios.run_scenarios(pick, part, inv, days, cash, ex["reason"])
    rec = scenarios.recommend(scs)
    rec_s = next((s for s in scs if s["key"] == rec), None)
    status = (
        "needs_review"
        if conf < config.MIN_CONFIDENCE
        else (
            "info"
            if exp["gap_days"] == 0 and exp["severity"] == "low"
            else "pending_approval"
        )
    )
    facts = {
        "supplier": sup["name"],
        "po": pick["po"],
        "part": part["name"],
        "delay_days": days,
        "reason": ex["reason"],
        "stock_cover_days": exp["cover_days"],
        "stockout_date": exp["stockout_date"],
        "new_arrival": exp["arrival_date"],
        "gap_days": exp["gap_days"],
        "revenue_at_risk": exp["revenue_at_risk"],
        "confidence": conf,
        "recommended": rec_s["label"] if rec_s else None,
        "recommended_total_impact": rec_s["total"] if rec_s else None,
    }
    tmpl = f"{sup['name']} reports {pick['po']} ({part['name']}) is " + (
        f"{days} days late" if days else "delayed (length not stated)"
    ) + (
        f" ({ex['reason']})" if ex["reason"] else ""
    ) + f". Stock covers {exp['cover_days']} days, so supply would run out on {exp['stockout_date']}, " f"{exp['gap_days']} day(s) before the new arrival ({exp['arrival_date']}); revenue at risk ${exp['revenue_at_risk']:,}. " + (
        f"Lowest-impact option: {rec_s['label']} (est. ${rec_s['total']:,}). "
        if rec_s
        else ""
    ) + (
        "Confidence is low: please confirm the details with the supplier."
        if status == "needs_review"
        else "A person must approve any action."
    )
    if conf >= config.MIN_CONFIDENCE:
        conn.execute(
            "UPDATE orders SET status='Delayed', revised_date=?, note=? WHERE po=?",
            (
                exp["arrival_date"],
                f"{days}d delay via message {mid}: {ex['reason']}",
                pick["po"],
            ),
        )
    return _make_alert(
        conn,
        mid,
        kind="delay",
        severity=exp["severity"],
        status=status,
        exposure=exp,
        scenarios=scs,
        recommended=rec,
        explanation=llm.explain(facts, tmpl),
        **base,
    )


def decide(conn, alert_id, option, approve, actor):
    a = conn.execute("SELECT * FROM alerts WHERE id=?", (alert_id,)).fetchone()
    if not a or a["status"] not in ("pending_approval", "needs_review"):
        return None
    conn.execute(
        "UPDATE alerts SET status=?, decided_option=?, decided_by=?, decided_at=? WHERE id=?",
        ("approved" if approve else "rejected", option, actor, now(), alert_id),
    )
    audit(
        conn,
        actor,
        "approved" if approve else "rejected",
        f"alert {alert_id}, option {option}",
    )
    conn.commit()
    return alert_dict(
        conn.execute("SELECT * FROM alerts WHERE id=?", (alert_id,)).fetchone()
    )


def _find_supplier(conn, q):
    stop = {"all", "ltd", "pvt", "co", "inc", "the", "and"}

    def keys(name):
        return {name.lower()} | {
            w
            for w in re.findall(r"[a-z]+", name.lower())
            if len(w) >= 3 and w not in stop
        }

    return next(
        (
            s
            for s in conn.execute("SELECT * FROM suppliers")
            if any(re.search(rf"\b{re.escape(k)}\b", q) for k in keys(s["name"]))
        ),
        None,
    )


def _order_view(conn, o, delay=None):
    part = conn.execute("SELECT * FROM parts WHERE sku=?", (o["sku"],)).fetchone()
    inv = conn.execute("SELECT * FROM inventory WHERE sku=?", (o["sku"],)).fetchone()
    if delay is None:
        delay = (
            (risk.d(o["revised_date"]) - risk.d(o["promised_date"])).days
            if o["revised_date"]
            else 0
        )
    exp = risk.exposure(o, inv, delay)
    note = o["note"] or ""
    reason = note.split(" | ")[0] if " | " in note else note
    scs = scenarios.run_scenarios(o, part, inv, delay, False, reason)
    best = next((s for s in scs if s["key"] == scenarios.recommend(scs)), None)
    return {
        "order": o["po"],
        "item": part["name"],
        "status": o["status"],
        "delay_days": delay,
        "reason": reason[:70],
        "stock_cover_days": exp["cover_days"],
        "gap_days": exp["gap_days"],
        "revenue_at_risk": exp["revenue_at_risk"],
        "severity": exp["severity"],
        "best_option": best["label"] if best else None,
        "best_option_total_cost": best["total"] if best else None,
    }


def ask_prepare(conn, question):
    """Collects verified facts relevant to the question. The AI (or the rules) only words the answer from these."""
    q = question.lower()
    sup = _find_supplier(conn, q)
    days = llm._parse_days(q) or 7
    open_orders = conn.execute(
        "SELECT * FROM orders WHERE status!='Delivered'"
    ).fetchall()
    views = sorted(
        (_order_view(conn, o) for o in open_orders),
        key=lambda v: (-v["gap_days"], v["stock_cover_days"]),
    )
    waiting = [
        alert_dict(r)
        for r in conn.execute(
            "SELECT * FROM alerts WHERE status IN ('pending_approval','needs_review') ORDER BY id DESC LIMIT 5"
        )
    ]
    facts = {
        "today": config.TODAY.isoformat(),
        "open_orders": len(open_orders),
        "delayed_orders": sum(o["status"] == "Delayed" for o in open_orders),
        "suppliers": [
            {"name": r["name"], **risk.supplier_score(conn, r["supplier_id"])}
            for r in conn.execute("SELECT * FROM suppliers")
        ],
        "most_exposed_orders": views[:6],
        "alerts_waiting_for_decision": [
            {
                "alert": a["id"],
                "order": a["po"],
                "type": a["kind"],
                "severity": a["severity"],
                "recommended": a["recommended"],
            }
            for a in waiting
        ],
    }
    top_text = None
    if sup:
        rows = [
            _order_view(conn, o, days)
            for o in open_orders
            if o["supplier_id"] == sup["supplier_id"]
        ]
        rows.sort(key=lambda v: (-v["gap_days"], v["stock_cover_days"]))
        facts["what_if"] = {
            "supplier": sup["name"],
            "assumed_delay_days": days,
            "most_exposed_orders": rows[:4],
        }
        if rows:
            w = rows[0]
            top_text = (
                f"If {sup['name']} slips {days} days, the most exposed order is {w['order']} ({w['item']}): stock covers {w['stock_cover_days']} days, "
                f"leaving a {w['gap_days']}-day gap and ${w['revenue_at_risk']:,} revenue at risk. "
                + (
                    f"Lowest-impact option: {w['best_option']} (est. ${w['best_option_total_cost']:,}). "
                    if w["best_option"]
                    else ""
                )
                + "A planner decides."
            )
        else:
            top_text = f"{sup['name']} has no open orders."
    ref = llm.ORDER_RE.search(question)
    if ref and not top_text:
        o = conn.execute(
            "SELECT * FROM orders WHERE po=?", (llm._norm_order(ref.group(0)),)
        ).fetchone()
        if o:
            v = _order_view(conn, o)
            facts["order_in_question"] = v
            top_text = (
                f"{v['order']} ({v['item']}) is {v['status']} with a {v['delay_days']}-day delay. Stock covers {v['stock_cover_days']} days, gap {v['gap_days']} day(s), "
                f"${v['revenue_at_risk']:,} at risk. "
                + (
                    f"Lowest-impact option: {v['best_option']} (est. ${v['best_option_total_cost']:,}). "
                    if v["best_option"]
                    else ""
                )
            )
    if not top_text:
        w = views[0] if views else None
        top_text = (
            f"{len(waiting)} alert(s) are waiting for a decision. "
            + (
                f"The most exposed order is {w['order']} ({w['item']}): stock covers "
                f"{w['stock_cover_days']} days, gap {w['gap_days']} day(s), ${w['revenue_at_risk']:,} at risk. "
                if w
                else ""
            )
            + "Ask about a specific partner or order for options."
        )
    return {"facts": facts, "fallback": top_text}


def ask(conn, question):
    prep = ask_prepare(conn, question)
    return llm.answer(question, prep["facts"], prep["fallback"])


# ---------------------------------------------------------------- AI response plans
STOP_WORDS = {
    "product",
    "partner",
    "courier",
    "delivery",
    "delivering",
    "issues",
    "thing",
    "other",
}


def playbook_for(conn, reason):
    """The company's own past actions for this kind of problem (data/playbook.csv)."""
    if not reason:
        return []
    rows = conn.execute("SELECT reason, action FROM playbook").fetchall()
    exact = [r["action"] for r in rows if r["reason"].lower() == reason.lower()]
    if exact:
        return exact
    stems = lambda t: {
        w[:5]
        for w in re.findall(r"[a-z]+", t.lower())
        if len(w) > 3 and w not in STOP_WORDS
    }
    want, best, top = stems(reason), None, 0
    for name in {r["reason"] for r in rows}:
        score = len(want & stems(name))
        if score > top:
            best, top = name, score
    return [r["action"] for r in rows if r["reason"] == best] if best else []


def _plan_context(conn, a):
    sup = (
        conn.execute(
            "SELECT * FROM suppliers WHERE supplier_id=?", (a["supplier_id"],)
        ).fetchone()
        if a["supplier_id"]
        else None
    )
    order = (
        conn.execute("SELECT * FROM orders WHERE po=?", (a["po"],)).fetchone()
        if a["po"]
        else None
    )
    part = (
        conn.execute("SELECT * FROM parts WHERE sku=?", (a["sku"],)).fetchone()
        if a["sku"]
        else None
    )
    exp = json.loads(a["exposure"]) if a["exposure"] else None
    scs = json.loads(a["scenarios"]) if a["scenarios"] else []
    rec = next((s for s in scs if s["key"] == a["recommended"]), None)
    facts = {
        "alert_type": a["kind"],
        "severity": a["severity"],
        "confidence": a["confidence"],
        "supplier": sup["name"] if sup else None,
        "supplier_score": (
            risk.supplier_score(conn, sup["supplier_id"]) if sup else None
        ),
        "order": a["po"],
        "item": part["name"] if part else None,
        "quantity": order["qty"] if order else None,
        "delay_days": a["delay_days"],
        "reason": a["reason"],
    }
    if exp:
        facts.update(
            {
                "stock_cover_days": exp["cover_days"],
                "stockout_date": exp["stockout_date"],
                "new_arrival": exp["arrival_date"],
                "gap_days": exp["gap_days"],
                "revenue_at_risk": exp["revenue_at_risk"],
            }
        )
    facts["options"] = [
        {
            "option": s["label"],
            "feasible": s["feasible"],
            "stop_days": s["stop_days"],
            "total_cost": s["total"],
        }
        for s in scs
    ]
    facts["recommended_option"] = rec["label"] if rec else None
    facts["policy_options"] = playbook_for(conn, a["reason"])[:5]
    return facts, sup, part, rec, exp


def _template_plan(facts, rec, exp):
    sup, po, item = facts["supplier"] or "the sender", facts["order"], facts["item"]
    kind = facts["alert_type"]
    if kind == "unknown_sender":
        return {
            "summary": "The message came from a number or address that is not in the supplier list, so nothing was matched.",
            "actions": [
                {
                    "step": "Identify who sent the message before acting on it.",
                    "owner": "Planner",
                    "when": "now",
                },
                {
                    "step": "If it is a real partner contact, add the number to the supplier list.",
                    "owner": "Data admin",
                    "when": "today",
                },
            ],
            "supplier_message": "Hello, we received your message but could not match it to a partner record. Please confirm your company name and the consignment or order number.",
            "questions": [
                "Which company are you writing from?",
                "Which order or consignment does this concern?",
            ],
        }
    if kind in ("order_mismatch", "no_open_order"):
        return {
            "summary": f"The message from {sup} does not match an open order, so no risk was calculated.",
            "actions": [
                {
                    "step": "Check the order number in the message against the open orders.",
                    "owner": "Planner",
                    "when": "now",
                },
                {
                    "step": "Ask the partner to confirm the correct order or consignment number.",
                    "owner": "Logistics coordinator",
                    "when": "today",
                },
            ],
            "supplier_message": f"Hello {sup}, we could not find the order mentioned in your message among your open orders. Please confirm the exact order or consignment number.",
            "questions": [
                "What is the exact order or consignment number?",
                "Is the delay about a different shipment?",
            ],
        }
    if kind == "cashflow":
        return {
            "summary": f"{sup} raised a payment concern on {po}.",
            "actions": [
                {
                    "step": f"Check the payment status of {po} and any open invoices.",
                    "owner": "Finance",
                    "when": "today",
                },
                {
                    "step": "Ask the partner to confirm in writing what is outstanding.",
                    "owner": "Procurement",
                    "when": "today",
                },
            ],
            "supplier_message": f"Hello {sup}, we are checking the payment status for {po} and will confirm shortly. Please send the invoice details you are referring to.",
            "questions": [
                "Which invoice is outstanding?",
                "Will this affect the delivery date?",
            ],
        }
    days = f" by {facts['delay_days']} days" if facts.get("delay_days") else ""
    why = f" ({facts['reason']})" if facts.get("reason") else ""
    summary = f"{sup} reports {po} ({item}) is delayed{days}{why}."
    if exp:
        summary += f" Stock covers {exp['cover_days']} days; the gap before the new arrival is {exp['gap_days']} day(s)."
    actions = []
    if rec:
        actions.append(
            {
                "step": f"Decide on the recommended option: {rec['label']} (estimated total impact ${rec['total']:,}).",
                "owner": "Planner",
                "when": "today",
            }
        )
    actions.append(
        {
            "step": f"Ask {sup} for a firm revised delivery date and the cause of the delay.",
            "owner": "Logistics coordinator",
            "when": "now",
        }
    )
    if exp and exp["gap_days"] > 0:
        actions.append(
            {
                "step": f"Check whether production can be re-sequenced to cover the {exp['gap_days']}-day gap.",
                "owner": "Production planner",
                "when": "today",
            }
        )
    for text in facts["policy_options"][: 5 - len(actions)]:
        actions.append({"step": text, "owner": "Procurement", "when": "this week"})
    message = (
        f"Hello {sup}, we have noted that {po} ({item}) is delayed{days}{why}. Please confirm the earliest firm delivery date, "
        f"whether a partial shipment is possible, and what is being done to recover."
    )
    if exp:
        message += (
            f" Our stock covers {exp['cover_days']} days, so this is time-critical."
        )
    return {
        "summary": summary,
        "actions": actions[:5],
        "supplier_message": message,
        "questions": [
            "What is the earliest firm delivery date?",
            "Can part of the shipment be delivered sooner?",
            "What caused the delay and how will it be fixed?",
        ],
    }


def plan_prepare(conn, alert_id, refresh=False):
    a = conn.execute("SELECT * FROM alerts WHERE id=?", (alert_id,)).fetchone()
    if not a:
        return None
    if a["plan"] and not refresh:
        return {"cached": json.loads(a["plan"])}
    facts, sup, part, rec, exp = _plan_context(conn, a)
    return {"facts": facts, "fallback": _template_plan(facts, rec, exp)}


def plan_save(conn, alert_id, plan):
    plan["generated_at"] = now()
    conn.execute("UPDATE alerts SET plan=? WHERE id=?", (json.dumps(plan), alert_id))
    audit(conn, "agent", "plan_generated", f"alert {alert_id} ({plan.get('source')})")
    conn.commit()
    return plan
