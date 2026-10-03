"""Compare the three ways of finding 'blocked' on the seeded 1,000 sample."""
import json, re
notes={json.loads(l)["iid"]:json.loads(l) for l in open("notes.jsonl")}
labs={json.loads(l)["iid"]:json.loads(l) for l in open("labels.jsonl")}
sample=open("sample_1000.txt").read().split()
A=set(); B=set(); C=set()
for i in sample:
    body=[n["body"] for n in notes[i]["notes"]["nodes"]]
    if any(re.search(r"marked this (item|issue|task) as (blocked by|blocking)",x) for x in body): A.add(i)   # 1. block link, ever
    if any("status to **Blocked**" in x for x in body): B.add(i)                                           # 2a. Status field
    if any(e["label"]=="workflow::blocked" and e["action"]=="add" for e in labs[i]["events"]): C.add(i)    # 2b/3. label
S=B|C
print(f"1 block link ever        {len(A)}")
print(f"2 Status = Blocked       {len(B)}")
print(f"3 workflow::blocked      {len(C)}")
print(f"2 or 3 (marked blocked)  {len(S)}   overlap 2&3 {len(B&C)}")
print(f"link AND marked blocked  {len(A&S)}")
print(f"any of the three         {len(A|S)}")
