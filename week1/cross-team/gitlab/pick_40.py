"""Seeded sample of 40 ever-blocked issues, excluding the 17 already checked. Writes sample_blocked_40.txt."""
import json, random, csv
lab=[json.loads(l) for l in open("labels_all.jsonl")]
blocked=sorted({r["iid"] for r in lab if r["events"] and any(e["label"]=="workflow::blocked" and e["action"]=="add" for e in r["events"])},key=int)
done={r["iid"] for r in csv.DictReader(open("hand_check_17.csv"))}
random.seed(2); pick=random.sample([i for i in blocked if i not in done],40)
open("sample_blocked_40.txt","w").write("\n".join(pick)); print(len(blocked),len(pick))
