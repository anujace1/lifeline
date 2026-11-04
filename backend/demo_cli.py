"""Run the full pipeline in the terminal (no web server needed):  python demo_cli.py"""

import db, agent, llm

conn = db.connect()
db.init(conn, reseed=True)
tests = [
    (
        "whatsapp",
        "+910000000001",
        "AIC-HYP-20261051 will be 8 days late, vehicle breakdown",
    ),
    ("sms", "+910000000002", "DHL-HYP-20261053 delayed 5 days, mishandling at the hub"),
    ("email", "+910000000001", "Reconfirming delivery of AIC-HYP-20261052."),
    ("whatsapp", "+910000000002", "AIC-HYP-20261001 will be 3 days late"),
    ("whatsapp", "+911234567890", "Will be 3 days late"),
]
for ch, sender, text in tests:
    a = agent.ingest(conn, ch, sender, text)["alert"]
    print(f"\n[{ch}] {text}")
    if not a:
        print("  -> no action needed (logged only)")
        continue
    print(
        f"  -> alert #{a['id']} {a['kind']} | {a['severity']} | {a['status']} | confidence {a['confidence']}"
    )
    print("  ", a["explanation"])
first = conn.execute(
    "SELECT id FROM alerts WHERE kind='delay' ORDER BY id LIMIT 1"
).fetchone()["id"]
prep = agent.plan_prepare(conn, first)
plan = agent.plan_save(conn, first, llm.advise(prep["facts"], prep["fallback"]))
print(
    f"\nRESPONSE PLAN for alert #{first} (source: {plan['source']})\n ", plan["summary"]
)
for s in plan["actions"]:
    print(f"   - [{s['owner']} / {s['when']}] {s['step']}")
print("  message:", plan["supplier_message"])
print("\nASK:", agent.ask(conn, "What if DHL cannot deliver for 2 weeks?")["answer"])
