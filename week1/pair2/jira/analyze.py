"""Week-1 checks on the raw SPARK Jira pull (jira/data/raw, from pull.py).

- ticket mix: types, resolutions, empty descriptions
- bulk closes: a resolution set by one person on >= BULK tickets within one hour
- Duplicate labels: bulk-closed share, and whether the duplicated ticket is linked
- links: which link types exist at all
- "open at time t": is the changelog complete enough to rebuild status at a past date
- pull cost: from pull_log.jsonl
"""
import collections, gzip, json, statistics
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).parent
RAW = HERE / "data" / "raw"
BULK = 20  # resolutions by one person in one hour


def ts(s):
    return datetime.strptime(s[:19], "%Y-%m-%dT%H:%M:%S")


def issues():
    for p in sorted(RAW.glob("page_*.json.gz")):
        yield from json.loads(gzip.decompress(p.read_bytes()))["issues"]


def main():
    rows, resolves = [], []  # resolves: (author, hour, key, resolution) from changelog
    for it in issues():
        f, cl = it["fields"], it.get("changelog") or {}
        hist = cl.get("histories", [])
        status_moves = [(h["created"], i) for h in hist for i in h["items"] if i["field"] == "status"]
        for h in hist:
            for i in h["items"]:
                if i["field"] == "resolution" and i.get("toString"):
                    who = (h.get("author") or {}).get("name", "?")
                    resolves.append((who, h["created"][:13], it["key"], i["toString"]))
        rows.append({
            "key": it["key"], "type": f["issuetype"]["name"], "sub": f["issuetype"]["subtask"],
            "res": (f["resolution"] or {}).get("name"), "status": f["status"]["name"],
            "created": f["created"], "resolved": f.get("resolutiondate"),
            "labels": f.get("labels", []),
            "desc_empty": not (f.get("description") or "").strip(),
            "links": [(l["type"]["name"], "out" if "outwardIssue" in l else "in") for l in f.get("issuelinks", [])],
            "cl_total": cl.get("total", 0), "cl_shown": len(hist), "status_moves": len(status_moves),
        })
    n = len(rows)
    by = {r["key"]: r for r in rows}
    print(f"tickets {n}  open {sum(r['res'] is None for r in rows)}")
    print("types", collections.Counter(r["type"] for r in rows).most_common(10))
    print("resolutions", collections.Counter(r["res"] for r in rows).most_common(10))
    top = [r for r in rows if not r["sub"]]
    print(f"top-level with empty description {100 * sum(r['desc_empty'] for r in top) / len(top):.0f}%  "
          f"(Bug {100 * sum(r['desc_empty'] for r in top if r['type'] == 'Bug') / max(1, sum(r['type'] == 'Bug' for r in top)):.0f}%)")

    # bulk closes: group the LAST resolution event per ticket by (author, hour)
    last = {}
    for who, hour, key, res in resolves:
        last[key] = (who, hour, res)
    batch = collections.Counter((w, h) for w, h, _ in last.values())
    timed = {k for k, (w, h, _) in last.items() if batch[(w, h)] >= BULK}
    labelled = {r["key"] for r in rows if "bulk-closed" in r["labels"]}
    bulk = timed | labelled
    print(f"\nbulk-closed: by timing (>= {BULK} by one person in one hour) {len(timed)}, by 'bulk-closed' label {len(labelled)}, "
          f"both {len(timed & labelled)}, union {len(bulk)}")
    print("  biggest batches", batch.most_common(5))
    for res in ("Incomplete", "Duplicate", "Cannot Reproduce", "Won't Fix", "Fixed"):
        ks = [r["key"] for r in rows if r["res"] == res]
        b = sum(k in bulk for k in ks)
        print(f"  {res:17} {len(ks):6}  bulk {b:6} ({100 * b / max(1, len(ks)):.0f}%)  real {len(ks) - b}")

    dups = [r for r in rows if r["res"] == "Duplicate"]
    linked = [r for r in dups if any(t == "Duplicate" for t, _ in r["links"])]
    usable = [r for r in linked if r["key"] not in bulk]
    print(f"\nDuplicate: {len(dups)}, with a Duplicate link {len(linked)} ({100 * len(linked) / max(1, len(dups)):.0f}%), "
          f"linked and not bulk {len(usable)}")

    lt = collections.Counter(t for r in rows for t, d in r["links"] if d == "out")
    print("\nlink types (outward)", lt.most_common())
    ever = sum(bool(r["links"]) for r in rows)
    blocks = sum(any(t == "Blocker" or t == "Blocks" for t, _ in r["links"]) for r in rows)
    print(f"tickets with any link {ever} ({100 * ever / n:.0f}%), with a block link {blocks}")

    trunc = [r for r in rows if r["cl_total"] > r["cl_shown"]]
    resolved = [r for r in rows if r["resolved"]]
    no_move = [r for r in resolved if r["status_moves"] == 0]
    print(f"\nchangelog truncated (needs a per-issue fetch) {len(trunc)}  max total {max((r['cl_total'] for r in rows), default=0)}")
    print(f"resolved tickets with NO status change in changelog {len(no_move)} ({100 * len(no_move) / max(1, len(resolved)):.1f}%)")
    print("  of those, by created year", collections.Counter(r["created"][:4] for r in no_move).most_common(6))

    log = [json.loads(l) for l in open(RAW / "pull_log.jsonl")]
    ok = [l for l in log if "secs" in l]
    errs = [l for l in log if "error" in l]
    if ok:
        print(f"\npull: {len(ok)} pages, {sum(l['n'] for l in ok)} tickets, "
              f"{(ok[-1]['t'] - ok[0]['t'] + ok[0]['secs']) / 60:.1f} min wall, median page {statistics.median(l['secs'] for l in ok):.1f}s, "
              f"p95 {sorted(l['secs'] for l in ok)[int(.95 * len(ok))]:.1f}s, {sum(l['bytes'] for l in ok) / 1e6:.0f} MB raw, "
              f"errors {len(errs)}, http codes {collections.Counter(l.get('code') for l in errs)}")
    (HERE / "results").mkdir(exist_ok=True)
    json.dump(rows, open(HERE / "results" / "jira_rows.json", "w"))


if __name__ == "__main__":
    main()
