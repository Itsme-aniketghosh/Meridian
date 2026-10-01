"""50-link hand check: does each commit's ticket key point to the right Jira ticket?

Sample: the commit -> ticket links of the last 1,000 first-parent commits, in git log order
(newest first, keys sorted), then random.sample(links, 50) with SEED.

  python3 links_check.py          if links_hand_check_50.csv exists: check it matches the sample, print the tally
                                  else: write it with an empty `correct` column to fill in by hand
"""
import csv, random, sqlite3, sys
from pathlib import Path

HERE = Path(__file__).parent
CSV = HERE / "links_hand_check_50.csv"
SEED, N = 7, 50

db = sqlite3.connect(HERE / "data" / "spark_jira.sqlite")  # from build.py
last = [sha for (sha,) in db.execute("select sha from commits order by seq desc limit 1000")]
keys = {}
for sha, key in db.execute("select sha, key from commit_ticket where sha in (%s)" % ",".join("?" * len(last)), last):
    keys.setdefault(sha, []).append(key)
links = [(sha, k) for sha in last for k in sorted(keys.get(sha, []))]
random.seed(SEED)
sample = random.sample(links, N)

if not CSV.exists():
    with open(CSV, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["sha", "key", "commit_subject", "ticket_summary", "correct", "note"])
        for sha, k in sorted(sample, key=lambda x: x[1]):
            subj, = db.execute("select subject from commits where sha = ?", (sha,)).fetchone()
            summ, = db.execute("select summary from tickets where key = ?", (k,)).fetchone()
            w.writerow([sha, k, subj, summ.strip(), "", ""])
    sys.exit(f"wrote {CSV.name} with {N} rows. Fill in `correct` (y/n) and `note`, then rerun")

rows = list(csv.DictReader(open(CSV)))
assert {(r["sha"], r["key"]) for r in rows} == set(sample), "CSV doesn't match the seeded sample"
blank = [r["key"] for r in rows if r["correct"] not in ("y", "n")]
assert not blank, f"no verdict for {blank}"
ok = sum(r["correct"] == "y" for r in rows)
notes = [r for r in rows if r["note"]]
# exact (Clopper-Pearson) 95% lower bound; for ok == N it is (0.025) ** (1 / N)
lo = 0.025 ** (1 / N) if ok == N else None
print(f"{ok}/{N} correct" + (f", 95% lower bound {lo:.3f}" if lo else "") + f"; {len(notes)} with a note:")
for r in notes:
    print(f"   {r['key']} {r['sha'][:10]}  {r['note']}")
