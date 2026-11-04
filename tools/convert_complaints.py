"""Converts the delivery-delay complaints workbook into the CSV files Lifeline loads.
Usage (needs: pip install openpyxl):
    python tools/convert_complaints.py path/to/50_hypothetical_delivery_delay_complaints-1.xlsx
Real columns used: courier, consignment no., delay reason, delay days, status, impact, action taken.
Everything the workbook does not contain (items, quantities, dates, stock, costs, contacts, alternate
carriers) is SAMPLE data, generated deterministically so it stays consistent."""
import csv
import sys
from collections import OrderedDict, defaultdict
from datetime import date, timedelta
from pathlib import Path

import openpyxl

TODAY = date(2026, 10, 28)
OUT = Path(__file__).resolve().parent.parent / "data"

# ---- sample master data (not in the workbook) ----
CARRIERS = OrderedDict([  # name -> (id, region, phone, email, payment_terms)
    ("All India Cargo", ("S01", "India (domestic)", "+910000000001", "ops@allindiacargo.example", 30)),
    ("DHL", ("S02", "International", "+910000000002", "support@dhl-partner.example", 30)),
])
ALTERNATES = [  # sample alternate carriers (no complaints on file)
    ("S03", "Rapid Route Logistics", "India (domestic)", "+910000000003", "dispatch@rapidroute.example", 30, 88),
    ("S04", "Metro Freight Services", "India (metro lanes)", "+910000000004", "bookings@metrofreight.example", 45, 84),
]
# sku, name, category, transit_days, unit_cost, carrier, backup, premium_pct, backup_days, qualification_days,
# on_hand, reserved, reorder_point, daily_use, revenue_per_day
ITEMS = [
    ("CMP-001", "Silicon Wafer", "Wafer", 6, 40, "S01", "S03", 5, 3, 0, 1500, 1000, 700, 125, 1500),
    ("CMP-002", "Resistor Reel", "Passive", 4, 0.02, "S01", "S03", 4, 2, 0, 60000, 8000, 20000, 2500, 800),
    ("CMP-003", "Microcontroller", "Logic IC", 8, 2.5, "S01", "", 0, 0, 0, 9000, 2000, 3000, 700, 2500),
    ("CMP-004", "Power Management IC", "Analog IC", 5, 1.1, "S01", "S03", 6, 3, 2, 2800, 800, 900, 250, 1200),
    ("CMP-005", "Packaged Die", "Packaging", 5, 5.5, "S01", "S04", 5, 3, 0, 900, 100, 300, 100, 1800),
    ("CMP-006", "Capacitor Reel", "Passive", 7, 0.03, "S02", "S04", 4, 3, 0, 80000, 10000, 25000, 5000, 900),
    ("CMP-007", "Memory Module", "Memory", 6, 9.0, "S02", "S04", 6, 3, 1, 4000, 1000, 1200, 600, 3000),
    ("CMP-008", "Sensor Module", "Sensors", 9, 3.2, "S02", "", 0, 0, 0, 3500, 500, 1000, 250, 1400),
    ("CMP-009", "PCB Panel", "PCB", 5, 12, "S02", "S03", 5, 2, 0, 1200, 200, 400, 100, 2200),
    ("CMP-010", "Connector Kit", "Connectors", 6, 0.8, "S02", "S04", 4, 3, 0, 20000, 2000, 5000, 3000, 700),
]
STATUS_OFFSET = {"Re-delivery scheduled": 1, "Awaiting partner update": 3, "Pending investigation": 2, "Escalated to hub": 5}
EXTRA_OPEN = [("AIC-HYP-20261051", "CMP-001", 1), ("AIC-HYP-20261052", "CMP-004", 2),
              ("DHL-HYP-20261053", "CMP-007", 3), ("DHL-HYP-20261054", "CMP-010", 1)]  # (id, sku, days until promised)


def write(name, header, rows):
    with open(OUT / name, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(header)
        w.writerows(rows)
    print("wrote", name, len(rows), "rows")


def main(xlsx):
    ws = openpyxl.load_workbook(xlsx, data_only=True)["Delay Complaints"]
    data = [r for r in ws.iter_rows(min_row=2, values_only=True) if r[0]]
    has_actions = len(data[0]) > 9
    by_carrier = defaultdict(list)
    for r in data:
        by_carrier[r[1]].append(r)
    item_of = {c[0]: [i for i in ITEMS if i[5] == c[0]] for c in CARRIERS.values()}
    sku = {i[0]: i for i in ITEMS}

    # suppliers: reliability derived from the real delay data (100 - 5 x average delay days)
    sup_rows = []
    for name, (sid, region, phone, email, terms) in CARRIERS.items():
        avg = sum(r[6] for r in by_carrier[name]) / len(by_carrier[name])
        sup_rows.append([sid, name, region, phone, email, terms, round(100 - 5 * avg)])
    sup_rows += [list(a) for a in ALTERNATES]
    write("suppliers.csv", ["supplier_id", "name", "region", "phone", "email", "payment_terms_days", "base_reliability"], sup_rows)

    write("parts.csv", ["sku", "name", "category", "lead_time_days", "unit_cost", "primary_supplier_id", "backup_supplier_id",
                        "backup_premium_pct", "backup_lead_time_days", "backup_qualification_days"], [list(i[:10]) for i in ITEMS])
    write("inventory.csv", ["sku", "on_hand", "reserved", "reorder_point", "daily_use", "revenue_per_day"], [[i[0]] + list(i[10:]) for i in ITEMS])

    orders = []
    counters = defaultdict(int)
    for r in data:
        cid, courier, consignment, _, reason, _, delay, status, impact = r[:9]
        sid = CARRIERS[courier][0]
        k = counters[sid]
        counters[sid] += 1
        it = item_of[sid][k % 5]
        qty = max(10, round(it[13] * (5 + (k * 3) % 8), -1)) if it[13] >= 100 else 100
        if status == "Resolved after delay":
            promised = date(2026, 10, 10) + timedelta(days=k % 9)
            revised, st = promised + timedelta(days=delay), "Delivered"
        else:
            revised = TODAY + timedelta(days=STATUS_OFFSET[status] + k % 2)
            promised, st = revised - timedelta(days=delay), "Delayed"
        orders.append([consignment, sid, it[0], qty, (promised - timedelta(days=it[3])).isoformat(), promised.isoformat(), st,
                       revised.isoformat(), f"{reason} | {status} | {impact}"])
    for po, s, d in EXTRA_OPEN:
        it = sku[s]
        promised = TODAY + timedelta(days=d + 2)
        orders.append([po, it[5], s, round(it[13] * 8, -1), (promised - timedelta(days=it[3])).isoformat(), promised.isoformat(), "Open", "",
                       "Sample on-time order (added for demo)"])
    write("orders.csv", ["po", "supplier_id", "sku", "qty", "order_date", "promised_date", "status", "revised_date", "note"], orders)

    if has_actions:  # the company's own past actions per delay reason become the AI's playbook
        seen, rows = set(), []
        for r in data:
            if (r[4], r[9]) not in seen:
                seen.add((r[4], r[9]))
                rows.append([r[4], r[9]])
        write("playbook.csv", ["reason", "action"], rows)


if __name__ == "__main__":
    main(sys.argv[1])
