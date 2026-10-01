"""Score test-bisection results against the Fonte bug-inducing-commit labels."""
import csv, glob, json, collections

labels = {(r["pid"], r["bid"]): r["bic_d4j_v3"] for r in csv.DictReader(open("defects4j_bic_labels.csv"))}
res = {}
for f in glob.glob("res/*.json"):
    try:
        j = json.loads(open(f).read().strip().splitlines()[-1])
    except Exception:
        continue
    res[(j["pid"], str(j["bid"]))] = j

status = collections.Counter()
per_pid = collections.defaultdict(collections.Counter)
rows = []
for key, truth in sorted(labels.items()):
    j = res.get(key)
    if not j:
        continue
    s = j["status"]
    if s == "found":
        s = "found_match" if j["bic"] == truth else "found_mismatch"
    status[s] += 1
    per_pid[key[0]][s] += 1
    rows.append({"pid": key[0], "bid": key[1], "status": s, "bisect_bic": j.get("bic", ""),
                 "fonte_bic": truth, "steps": j.get("steps", ""), "secs": j.get("secs", "")})

if rows:
    w = csv.DictWriter(open("results/bisect_vs_fonte.csv", "w", newline=""), fieldnames=list(rows[0]))
    w.writeheader(); w.writerows(rows)
done = sum(status.values())
found = status["found_match"] + status["found_mismatch"]
print(f"done {done}/{len(labels)}  " + "  ".join(f"{k}={v}" for k, v in status.most_common()))
if found:
    print(f"agreement when bisection returns an answer: {status['found_match']}/{found} = {status['found_match']/found:.0%}")
print(f"coverage (answer returned): {found}/{done}")
for p, c in sorted(per_pid.items()):
    print(f"  {p:12} " + " ".join(f"{k}={v}" for k, v in c.most_common()))
