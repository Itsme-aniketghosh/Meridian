"""Gantt chart of one Airflow run from report/airflow_run.csv -> report/figures/airflow_gantt.png.
Usage (from Data-Pipeline/): python report/gantt.py [csv] [png]"""
import csv, sys
from datetime import datetime
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import Patch

HERE = Path(__file__).parent
src = Path(sys.argv[1]) if len(sys.argv) > 1 else HERE / "airflow_run.csv"
out = Path(sys.argv[2]) if len(sys.argv) > 2 else HERE / "figures" / "airflow_gantt.png"
BRANCH = {"history": "history branch", "owners": "history branch", "scan": "scan branch", "resolve": "scan branch"}
COLOR = {"serial": "#2a78d6", "history branch": "#eb6834", "scan branch": "#1baf7a"}   # categorical slots 1-3

rows = [r for r in csv.DictReader(open(src)) if r["start_date"]]
t = lambda s: datetime.fromisoformat(s)
t0 = min(t(r["start_date"]) for r in rows)
rows.sort(key=lambda r: t(r["start_date"]))

fig, ax = plt.subplots(figsize=(10, 0.45 * len(rows) + 1.4), dpi=150)
for i, r in enumerate(rows):
    start = (t(r["start_date"]) - t0).total_seconds()
    end = (t(r["end_date"]) - t0).total_seconds() if r["end_date"] else start
    group = BRANCH.get(r["task_id"], "serial")
    failed = r["state"] not in ("success", "skipped")
    ax.barh(i, max(end - start, 1), left=start, height=0.6, color=COLOR[group],
            hatch="//" if failed else None, edgecolor="white", linewidth=0)
    label = f"{end - start:,.0f} s" + ("" if r["state"] == "success" else f"  ({r['state']})")
    ax.text(end + 8, i, label, va="center", fontsize=8, color="#52514e")
ax.set_yticks(range(len(rows)), [r["task_id"] for r in rows], fontsize=9)
ax.invert_yaxis()
ax.set_xlabel("seconds since the first task started", color="#52514e")
wall = max((t(r["end_date"] or r["start_date"]) - t0).total_seconds() for r in rows)
ax.set_title(f"meridian_update, run {rows[0].get('meridian_run', '')}: wall time {wall:,.0f} s", loc="left", fontsize=11)
ax.set_xlim(0, wall * 1.12)
ax.grid(axis="x", color="#e5e4e0", linewidth=0.6)
ax.set_axisbelow(True)
for s in ("top", "right", "left"):
    ax.spines[s].set_visible(False)
ax.legend(handles=[Patch(color=c, label=g) for g, c in COLOR.items()], loc="lower right", frameon=False, fontsize=8)
fig.tight_layout()
out.parent.mkdir(parents=True, exist_ok=True)
fig.savefig(out)
print(out)
