"""Charts for the quality appendix (appendix-quality.md): stats, missing values, anomalies, bias, tests.

    python report/figures_quality.py <dir with stats.json, anomalies.json, bias.json>   # from Data-Pipeline/
Reads report/tests_light.txt (the -v unittest output) for the test counts. Writes report/figures/*.png.
"""
import collections, json, re, sys
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

SRC, HERE = Path(sys.argv[1]), Path(__file__).parent
FIG = HERE / "figures"
FIG.mkdir(exist_ok=True)
BLUE, RED, GREY, INK = "#2a78d6", "#d03b3b", "#b4b2aa", "#52514e"   # reference palette: series 1, status critical
plt.rcParams.update({"font.size": 9, "axes.spines.top": False, "axes.spines.right": False, "axes.edgecolor": GREY,
                     "axes.labelcolor": INK, "xtick.color": INK, "ytick.color": INK, "figure.dpi": 130})
load = lambda f: json.loads((SRC / f).read_text(encoding="utf-8"))


def save(fig, name):
    fig.tight_layout()
    fig.savefig(FIG / name, bbox_inches="tight", facecolor="white")
    plt.close(fig)


def hbars(ax, labels, values, colors=BLUE, fmt="{:,}"):
    ax.barh(labels, values, color=colors, height=0.7)
    ax.invert_yaxis()
    for y, v in enumerate(values):
        ax.annotate(fmt.format(v), (v, y), xytext=(3, 0), textcoords="offset points", va="center", fontsize=7, color=INK)


# 1. rows per table (log scale; 0-row tables drawn at 1 and labelled 0)
tables = load("stats.json")["tables"]
rows = sorted(((t, v["rows"]) for t, v in tables.items()), key=lambda r: -r[1])
fig, ax = plt.subplots(figsize=(7, 6))
hbars(ax, [t for t, _ in rows], [max(n, 1) for _, n in rows])
for txt, (_, n) in zip(ax.texts, rows):
    txt.set_text(f"{n:,}")
ax.set_xscale("log")
ax.set_xlabel("rows (log scale)")
ax.set_title(f"Rows per table · {len(rows)} tables, {sum(n for _, n in rows):,} rows", loc="left")
save(fig, "rows_per_table.png")

# 2. missing share per column, where > 0
miss = sorted(((f"{t}.{c}", s["missing"] / v["rows"], s["missing"]) for t, v in tables.items()
               for c, s in v["columns"].items() if s.get("missing")), key=lambda r: -r[1])
fig, ax = plt.subplots(figsize=(7, 0.25 * len(miss) + 1))
hbars(ax, [c for c, *_ in miss], [m for _, m, _ in miss])
for txt, (_, m, n) in zip(ax.texts, miss):
    txt.set_text(f"{m:.1%} ({n:,})")
ax.set_xlim(0, 1.12)
ax.set_xlabel("share of rows missing (NULL or '')")
ax.set_title(f"Missing values · {len(miss)} columns with any", loc="left")
save(fig, "missing_share.png")

# 3. anomalies by severity and check
anom = load("anomalies.json")
# one bar per finding: its length is the rows it names (validate's message starts with the count)
rows_hit = [int(m.split()[0]) if m.split()[0].isdigit() else 1 for *_, m in anom]
labels = [f"{sev} · {check} · {where}" for sev, check, where, _ in anom] or ["none"]
fig, ax = plt.subplots(figsize=(6, 0.4 * len(labels) + 1.2))
hbars(ax, labels, rows_hit or [0], [RED if a[0] == "error" else "#fab219" for a in anom] or GREY)
errors = sum(a[0] == "error" for a in anom)
ax.set_xlabel("rows the finding names")
ax.set_title(f"validate anomalies · {errors} errors, {len(anom) - errors} warnings", loc="left")
save(fig, "anomalies.png")

# 4. bias: per-slice metric with its Wilson 95% interval vs overall, flag line at overall - max_gap; N >= min_n only
bias = load("bias.json")
gap, min_n = bias["max_gap"], bias["min_n"]
for rule in ("owner_rule", "ticket_mapping", "resolver"):
    parts = {k: v for k, v in bias[rule].items() if isinstance(v, dict) and "by_group" in v}
    keep = {k: sorted(((g, d) for g, d in v["by_group"].items() if d["n"] >= min_n), key=lambda r: r[0]) for k, v in parts.items()}
    fig, axes = plt.subplots(len(parts), 1, figsize=(7, sum(0.22 * len(g) + 1 for g in keep.values())),
                             gridspec_kw={"height_ratios": [len(g) + 3 for g in keep.values()]}, squeeze=False)
    for ax, (k, v) in zip(axes[:, 0], parts.items()):
        metric = next(m for m in v["overall"] if m != "n")
        overall = v["overall"][metric]
        g = keep[k]
        vals = [d[metric] for _, d in g]
        hbars(ax, [f"{s} (N={int(d['n']):,})" for s, d in g], vals,
              [RED if d.get("flagged", d[metric] < overall - gap) else BLUE for _, d in g], fmt="{:.2f}")
        ax.errorbar(vals, range(len(g)), xerr=[[d[metric] - d.get("ci_low", d[metric]) for _, d in g],
                                               [d.get("ci_high", d[metric]) - d[metric] for _, d in g]],
                    fmt="none", ecolor=INK, elinewidth=0.8, capsize=2)
        for txt, (_, d) in zip(ax.texts, g):   # labels clear the interval, not just the bar
            txt.xy = (d.get("ci_high", d[metric]), txt.xy[1])
        ax.axvline(overall, color=INK, lw=1)
        ax.axvline(overall - gap, color=RED, lw=1, ls="--")
        ax.set_xlim(0, 1.12)
        dropped = len(v["by_group"]) - len(g)
        ax.set_title(f"{k} · overall {overall:.3f} (N={int(v['overall']['n']):,}) · {len(v['flagged'])} of "
                     f"{v.get('tested', len(g))} flagged · {dropped} slices with N<{min_n} not shown", loc="left", fontsize=8)
    axes[-1, 0].set_xlabel(f"share per slice, whisker: Wilson 95% interval · solid: overall · dashed: overall − {gap} · "
                           f"red: flagged (whole interval below the dashed line)")
    save(fig, f"bias_{rule}.png")

# 5. owner-rule mitigation: each variant on the same slices
mit = bias["owner_rule"]["mitigations"]
names = list(mit)
fig, axes = plt.subplots(1, 4, figsize=(11, 2.8), sharey=True)
acc = "accuracy_assigned" if "accuracy_assigned" in next(iter(mit.values())) else "accuracy"
for ax, (key, title) in zip(axes, [(acc, "accuracy, next authors with a team (↑)" if acc != "accuracy" else "accuracy (↑)"),
                                   ("owned", "files owned share (↑)"),
                                   ("next_author_unassigned", "next author unassigned (↓)"),
                                   ("worst_module_gap", "worst module gap (↓)")]):
    vals = [m["files_owned"] / m["of"] if key == "owned" else m[key] for m in mit.values()]
    hbars(ax, names, vals, [RED if "730" in n else BLUE for n in names], fmt="{:.3f}")
    ax.set_title(title, fontsize=8, loc="left")
    ax.set_xlim(0, max(vals) * 1.3)
fig.suptitle("Owner-rule mitigations (red: recommended, teams from 730 days)", x=0.01, ha="left")
save(fig, "bias_mitigation.png")

# 6. tests per file, from the -v output
# tests print to stdout, so a result can land lines after its header: count headers, then mark skips and failures
log = (HERE / "tests_light.txt").read_text(encoding="utf-8", errors="replace")   # the console wrote it in cp1252
res = collections.defaultdict(collections.Counter)
for m in re.finditer(r"^test\w* \(tests\.(\w+)\.[\w.]+\)", log, re.M):
    res[m[1]]["ok"] += 1
for outcome, pat in (("skipped", r"^test\w* \(tests\.(\w+)\.[^)]+\) \.\.\. skipped"),
                     ("FAIL", r"^FAIL: \w+ \(tests\.(\w+)\."), ("ERROR", r"^ERROR: \w+ \(tests\.(\w+)\.")):
    for m in re.finditer(pat, log, re.M):
        res[m[1]]["ok"] -= 1
        res[m[1]][outcome] += 1
files = sorted(res, key=lambda f: -sum(res[f].values()))
fig, ax = plt.subplots(figsize=(7, 0.25 * len(files) + 1))
left = [0] * len(files)
for outcome, color in (("ok", BLUE), ("skipped", GREY), ("FAIL", RED), ("ERROR", "#8b1e1e")):
    vals = [res[f][outcome] for f in files]
    if any(vals):
        ax.barh(files, vals, left=left, color=color, height=0.7, label=outcome, edgecolor="white", linewidth=1)
        left = [a + b for a, b in zip(left, vals)]
ax.invert_yaxis()
for y, n in enumerate(left):
    ax.annotate(str(n), (n, y), xytext=(3, 0), textcoords="offset points", va="center", fontsize=7, color=INK)
total = collections.Counter()
for c in res.values():
    total.update(c)
ax.legend(frameon=False, loc="lower right")
ax.set_xlabel("tests")
ax.set_title(f"Tests per file (no team data) · {sum(total.values())} run · " +
             ", ".join(f"{n} {k}" for k, n in total.items()), loc="left")
save(fig, "tests_per_file.png")
print(f"wrote {len(list(FIG.glob('*.png')))} charts to {FIG}")
