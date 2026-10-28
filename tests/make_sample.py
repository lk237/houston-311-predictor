"""Create a small SYNTHETIC file in the exact format of the city's 311 extracts.

Same banner, same header row, same pipe separators and time zones as the real
files, but made-up values. Use it only to check that the code runs end to end.
Never report results from it.

Usage:
    python tests/make_sample.py
    python src/load_db.py --files data/sample/houston_311_sample.txt
"""
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
rng = np.random.default_rng(7)
N = 6000

HEADER = ("365 Case Number|Case Number|Incident Address|Latitude|Longitude|Status|Created Date Local|"
          "Closed Date|Title|Incident Case Type|SLA Time|Resolve By Time|Service Area|Council District|"
          "Key Map|Department|Division|AVA Case Type|State Code|State Code Name|SLA Start Time|X|Y|"
          "Incident Street|Incident City|Incident State|Zip Code|TaxID|Created Date UTC|"
          "Customer SuperNeighborhood|Management District|Garbage Route|Garbage Day|SWM Quadrant|"
          "Recycling Route|Recycling Day|Recycling Quadrant|Recycling Areas|Heavy Trash Day|"
          "Heavy Trash Quadrant|Queue|ETJ|SLA Name|Channel|Extract Date|Latest Case Notes|"
          "Sample Case Conflicts Notes|Description|Resolution Notes")
cols = HEADER.split("|")

types = {  # title: (case type, department, division, SLA days)
    "Water Leak": ("Water", "Public Works", "Water Operations", 10),
    "Pothole": ("Street Condition", "Public Works", "Transportation Drainage Operations", 30),
    "Missed Garbage Pickup": ("Garbage", "Solid Waste Management", "Collections", 5),
    "Street Light Out": ("Street Lights", "Public Works", "Traffic Operations", 14),
    "Nuisance On Property": ("Nuisance", "Neighborhoods", "Inspections", 30),
    "Dead Animal Collection": ("Animals", "Solid Waste Management", "Collections", 2),
    "Drainage": ("Drainage", "Public Works", "Transportation Drainage Operations", 30),
    "Heavy Trash": ("Garbage", "Solid Waste Management", "Collections", 10),
}
titles = rng.choice(list(types), N, p=[.15, .14, .2, .1, .1, .08, .08, .15])
created_utc = pd.Timestamp("2025-01-01 06:00") + pd.to_timedelta(rng.uniform(0, 365, N), unit="D")
sla_days = np.array([types[t][3] for t in titles])
district = rng.choice(list("ABCDEFGHIJK"), N)
channel = rng.choice(["Phone", "SCF", "Email", "In Person"], N, p=[.65, .25, .08, .02])
slow = np.isin(district, list("BDH")) * 0.6 + (channel == "Phone") * 0.2
days = rng.gamma(2.0, sla_days / 2.0 * (1 + slow))
closed_utc = created_utc + pd.to_timedelta(days, unit="D")
is_open = rng.random(N) < 0.08


def local(ts):  # UTC -> Houston local time, formatted like the city file
    return ts.tz_localize("UTC").tz_convert("America/Chicago").tz_localize(None)


rows = []
for i in range(N):
    r = dict.fromkeys(cols, "")
    t = titles[i]
    r.update({
        "365 Case Number": f"{2500000000 + i}", "Case Number": f"{2500000000 + i}",
        "Status": "Routed" if is_open[i] else "Service Completed",
        "Created Date Local": f"{local(created_utc[i]):%Y-%m-%d %H:%M:%S}.0000000",
        "Created Date UTC": f"{created_utc[i]:%Y-%m-%d %H:%M:%S}.0000000",
        "SLA Start Time": f"{created_utc[i]:%Y-%m-%d %H:%M:%S}.0000000",
        "Closed Date": "" if is_open[i] else f"{local(closed_utc[i]):%Y-%m-%d %H:%M:%S}.000",
        "Resolve By Time": f"{created_utc[i] + pd.Timedelta(days=int(sla_days[i])):%Y-%m-%d %H:%M:%S}.000",
        "SLA Time": f"{sla_days[i]} Calendar Days",
        "Title": t, "Incident Case Type": types[t][0], "Department": types[t][1], "Division": types[t][2],
        "Council District": district[i], "Channel": channel[i],
        "Customer SuperNeighborhood": "SAMPLE", "Description": "synthetic | row with a pipe in free text",
    })
    rows.append("|".join(r[c] for c in cols))

out = ROOT / "data" / "sample" / "houston_311_sample.txt"
out.parent.mkdir(parents=True, exist_ok=True)
banner = ["-" * 44, "----- 311 Public Data D365 - SAMPLE ----", "-" * 44,
          "Extract date-time: [synthetic]", "-" * 44]
out.write_text("\n".join(banner + [HEADER] + rows) + "\n")
print(f"Wrote {N} synthetic rows to {out}")
