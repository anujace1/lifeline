"""Lifeline API.  Run:  cd backend && uvicorn main:app --reload   then open http://localhost:8000"""

import base64
import hmac
import os
import threading
from pathlib import Path
from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.concurrency import run_in_threadpool
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
import agent, config, db, jobs, llm, risk
from channels import twilio_sig

app = FastAPI(title="Lifeline")
conn = db.connect()
db.init(conn)
LOCK = (
    threading.Lock()
)  # SQLite + one shared connection: serialise writes (fine for a prototype)


@app.middleware("http")
async def basic_auth(request: Request, call_next):
    """If LIFELINE_PASSWORD is set, the dashboard and API ask for it (any username). Webhooks and /api/health stay open."""
    pw, path = os.getenv("LIFELINE_PASSWORD"), request.url.path
    if not pw or path.startswith("/webhooks/") or path == "/api/health":
        return await call_next(request)
    header = request.headers.get("Authorization", "")
    if header.startswith("Basic "):
        try:
            if hmac.compare_digest(
                base64.b64decode(header[6:]).decode().partition(":")[2], pw
            ):
                return await call_next(request)
        except Exception:
            pass
    return Response(
        "Login required",
        status_code=401,
        headers={"WWW-Authenticate": 'Basic realm="Lifeline"'},
    )


def rows(sql, *args):
    return [dict(r) for r in conn.execute(sql, args)]


class Inbound(BaseModel):
    channel: str = "whatsapp"
    sender: str
    text: str


class Decision(BaseModel):
    option: str
    approve: bool = True
    actor: str = "planner"


class Ask(BaseModel):
    question: str


@app.get("/api/health")
def health():
    return {"ok": True, "llm": config.LLM_PROVIDER, "today": config.TODAY.isoformat()}


@app.get("/api/summary")
def summary():
    open_alerts = rows(
        "SELECT * FROM alerts WHERE status IN ('pending_approval','needs_review')"
    )
    inv = [risk.inventory_risk(i) for i in conn.execute("SELECT * FROM inventory")]
    return {
        "open_alerts": len(open_alerts),
        "critical": sum(a["severity"] == "critical" for a in open_alerts),
        "delayed_orders": conn.execute(
            "SELECT COUNT(*) FROM orders WHERE status='Delayed'"
        ).fetchone()[0],
        "at_risk_items": sum(r != "Healthy" for r in inv),
        "messages": conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0],
    }


@app.get("/api/orders")
def orders():
    return rows(
        "SELECT o.*, s.name AS supplier, p.name AS part FROM orders o JOIN suppliers s USING(supplier_id) JOIN parts p USING(sku) ORDER BY o.promised_date"
    )


@app.get("/api/suppliers")
def suppliers():
    return [
        {**r, **risk.supplier_score(conn, r["supplier_id"])}
        for r in rows("SELECT * FROM suppliers")
    ]


@app.get("/api/inventory")
def inventory():
    out = []
    for i in conn.execute(
        "SELECT i.*, p.name FROM inventory i JOIN parts p USING(sku)"
    ):
        o = conn.execute(
            "SELECT * FROM orders WHERE sku=? AND status!='Delivered' ORDER BY promised_date",
            (i["sku"],),
        ).fetchone()
        avail, cov = risk.stock_position(i)
        out.append(
            {
                **dict(i),
                "available": avail,
                "cover_days": risk.cover_days(i),
                "po": o["po"] if o else None,
                "risk": risk.inventory_risk(i, o),
            }
        )
    return out


@app.get("/api/messages")
def messages():
    return rows(
        "SELECT m.*, s.name AS supplier FROM messages m LEFT JOIN suppliers s USING(supplier_id) ORDER BY m.id DESC LIMIT 50"
    )


@app.get("/api/alerts")
def alerts():
    return [
        agent.alert_dict(r)
        for r in conn.execute("SELECT * FROM alerts ORDER BY id DESC")
    ]


@app.get("/api/alerts/{aid}")
def alert(aid: int):
    r = conn.execute("SELECT * FROM alerts WHERE id=?", (aid,)).fetchone()
    if not r:
        raise HTTPException(404, "alert not found")
    return agent.alert_dict(r)


@app.post("/api/ingest")
def ingest(m: Inbound):
    with LOCK:
        return agent.ingest(conn, m.channel, m.sender, m.text)


@app.post("/api/alerts/{aid}/decision")
def decision(aid: int, d: Decision):
    with LOCK:
        a = agent.decide(conn, aid, d.option, d.approve, d.actor)
    if not a:
        raise HTTPException(409, "alert missing or already decided")
    return a


class PlanReq(BaseModel):
    refresh: bool = False


@app.post("/api/alerts/{aid}/plan")
def plan(aid: int, r: PlanReq):
    """Starts (or joins) a background job that drafts the plan; the dashboard polls /api/jobs/<id>. Cached plans return at once."""
    with LOCK:
        prep = agent.plan_prepare(conn, aid, r.refresh)
    if prep is None:
        raise HTTPException(404, "alert not found")
    if "cached" in prep:
        return {"state": "done", "result": prep["cached"]}

    def work():
        result = llm.advise(
            prep["facts"], prep["fallback"]
        )  # slow part runs outside the lock
        with LOCK:
            return agent.plan_save(conn, aid, result)

    return {"state": "working", "job": jobs.start(work, key=f"plan-{aid}")}


@app.post("/api/ask")
def ask(q: Ask):
    """Facts are collected under the lock; the (slow) AI answer runs as a background job."""
    with LOCK:
        prep = agent.ask_prepare(conn, q.question)
    return {
        "state": "working",
        "job": jobs.start(
            lambda: llm.answer(q.question, prep["facts"], prep["fallback"])
        ),
    }


@app.get("/api/jobs/{jid}")
def job(jid: str):
    return jobs.status(jid)


@app.post("/api/reset")
def reset():
    with LOCK:
        db.init(conn, reseed=True)
    return {"ok": True}


@app.get("/api/audit")
def audit_log():
    return rows("SELECT * FROM audit ORDER BY id DESC LIMIT 100")


# Real channels: point Twilio (WhatsApp/SMS) or an email forwarder at these. Add signature checks before going live.
@app.post("/webhooks/twilio")
async def twilio(request: Request):
    form = dict((await request.form()).items())
    token, base = os.getenv("TWILIO_AUTH_TOKEN"), os.getenv("LIFELINE_PUBLIC_URL")
    if token and base:  # signature check is on only when both are set
        url = base.rstrip("/") + "/webhooks/twilio"
        if not twilio_sig.valid(
            token, url, form, request.headers.get("X-Twilio-Signature", "")
        ):
            raise HTTPException(403, "invalid Twilio signature")
    sender = form.get("From", "")
    if not sender:
        raise HTTPException(400, "missing From")
    await run_in_threadpool(
        ingest,
        Inbound(
            channel="whatsapp" if sender.startswith("whatsapp:") else "sms",
            sender=sender,
            text=form.get("Body", ""),
        ),
    )
    return Response(
        content="<Response></Response>", media_type="application/xml"
    )  # empty TwiML = received, no auto-reply


@app.post("/webhooks/email")
def email_in(payload: dict):
    return ingest(
        Inbound(
            channel="email",
            sender=payload["from"],
            text=f"{payload.get('subject', '')}\n{payload.get('text', '')}",
        )
    )


app.mount(
    "/",
    StaticFiles(
        directory=Path(__file__).resolve().parent.parent / "frontend", html=True
    ),
    name="ui",
)
