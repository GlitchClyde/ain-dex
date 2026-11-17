"""Generate a deliberately messy sales dataset for demos and tests."""
import csv
import random
from datetime import date, timedelta
from pathlib import Path

random.seed(42)
regions = ["North", "South", "East", "West"]
products = {"Router": 120.0, "Firewall": 450.0, "Switch": 80.0, "Sensor": 35.0}
segments = ["SMB", "Enterprise", "Gov"]
rows = []
start = date(2025, 1, 1)
for i in range(1, 301):
    d = start + timedelta(days=random.randint(0, 364))
    product = random.choice(list(products))
    units = random.randint(1, 20)
    price = products[product]
    revenue = units * price
    if i in (50, 120, 200):  # outliers
        revenue *= 25
    rows.append(
        {
            "order_id": f"ORD-{1000 + i}",
            "order_date": d.strftime("%Y-%m-%d") if i % 7 else d.strftime("%m/%d/%Y"),
            "region": random.choice(regions) if i % 40 else "",
            "product": product,
            "segment": random.choice(segments),
            "units": units,
            "unit_price": f"{price:.2f}",
            "revenue": f"${revenue:,.2f}" if i % 25 else "N/A",
        }
    )
rows += [rows[10], rows[11], rows[12]]  # exact duplicates
out = Path(__file__).resolve().parent.parent / "sample_data" / "sales_sample.csv"
with out.open("w", newline="", encoding="utf-8") as f:
    w = csv.DictWriter(f, fieldnames=list(rows[0]))
    w.writeheader()
    w.writerows(rows)
print(f"Wrote {out} ({len(rows)} rows)")
