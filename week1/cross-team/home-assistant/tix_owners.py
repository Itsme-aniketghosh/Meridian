#!/usr/bin/env python3
"""#3 Stale owners for home-assistant/core.  Usage: python3 tix_owners.py [--users]
Needs: clone (HA_REPO), tix/merged_prs.jsonl from tix_pull.py merged, tix/issues.jsonl.
 - PR -> integrations from the squash-merge commit's files (homeassistant/components/<d>/, tests/components/<d>/).
 - Next-author test at integration level: prior 2025-04-01..2026-04-01, test 2026-04-01..2026-10-01 (by mergedAt).
 - Stale owners, bus factor, missing owners, wrong-team first responder on window issues.
 --users : also check codeowner accounts via REST users/<login> (cached in tix/users.json)."""
import json, os, re, subprocess, collections, statistics, sys, random, datetime as dt
SCR = "data"
REPO = os.environ.get("HA_REPO", SCR + "/repo"); TIX = os.environ.get("HA_TIX", SCR + "/tix")
SNAP = "75c314edf0013bff046106015ab6cfb0b8c6b452"
SWEEP = 10  # PRs touching more than this many integrations are treated as central sweeps
BOTRE = re.compile(r"\[bot\]$|^(dependabot|renovate|github-actions|home-assistant|copilot|issue-triage-workflows)", re.I)
def is_bot(login, typ=None): return (typ == "Bot") or not login or bool(BOTRE.search(login))
def git(*a): return subprocess.check_output(["git", "-C", REPO, *a], text=True)

def codeowners_at(date):
    c = git("rev-list", "-1", "--first-parent", f"--before={date}T00:00:00Z", SNAP).strip()
    paths = [l.split("\t")[1] for l in git("ls-tree", "-r", c, "homeassistant/components/").splitlines() if l.endswith("/manifest.json") and l.count("/") == 3 + 0]
    out = {}
    p = subprocess.run(["git", "-C", REPO, "cat-file", "--batch"], input="".join(f"{c}:{x}\n" for x in paths), capture_output=True, text=True).stdout
    i = 0; data = p
    for x in paths:
        hdr_end = data.index("\n", i); size = int(data[i:hdr_end].split()[2])
        blob = data[hdr_end + 1: hdr_end + 1 + size]; i = hdr_end + 1 + size + 1
        try: m = json.loads(blob)
        except Exception: continue
        out[x.split("/")[2]] = [o.lstrip("@").lower() for o in m.get("codeowners", []) if "/" not in o]
    return c, out

def commit_domains():
    log = git("log", SNAP, "--first-parent", "--since=2025-03-15", "--name-only", "--format=\x1e%H")
    res = {}
    for chunk in log.split("\x1e")[1:]:
        lines = chunk.strip().splitlines(); h = lines[0]; ds = set()
        for f in lines[1:]:
            m = re.match(r"(?:homeassistant|tests)/components/([^/]+)/", f)
            if m: ds.add(m.group(1))
        res[h] = ds
    return res

def main():
    prs = [json.loads(l) for l in open(f"{TIX}/merged_prs.jsonl")]
    prs = {p["number"]: p for p in prs}.values()
    cdom = commit_domains()
    c0, own_prior = codeowners_at("2025-04-01"); c1, own_t = codeowners_at("2026-04-01"); _, own_snap = codeowners_at("2026-10-01")
    print(f"codeowners commits: 2025-04-01={c0[:10]} 2026-04-01={c1[:10]}")
    rows = []
    unmapped = 0
    for p in prs:
        if not p.get("mergedAt") or p["mergedAt"] < "2025-04-01" or p["mergedAt"] >= "2026-10-01": continue
        oid = (p.get("mergeCommit") or {}).get("oid")
        if oid not in cdom: unmapped += 1; continue
        a = p.get("author") or {}
        rows.append(dict(n=p["number"], t=p["mergedAt"], a=(a.get("login") or "").lower(), bot=is_bot(a.get("login"), a.get("__typename")),
                         d=cdom[oid], rev={(r.get("author") or {}).get("login", "").lower() for r in p["reviews"]["nodes"] if r.get("author")}))
    rows.sort(key=lambda r: r["t"])
    nb = sum(r["bot"] for r in rows)
    print(f"merged PRs 2025-04..2026-10 with merge commit on dev: N={len(rows)} (unmapped {unmapped}); bot-authored {nb} ({nb/len(rows):.1%})")
    touching = [r for r in rows if r["d"]]; sweeps = [r for r in touching if len(r["d"]) > SWEEP]
    print(f"  touching >=1 integration: {len(touching)}; sweeps >{SWEEP} integrations: {len(sweeps)}")
    use = [r for r in touching if not r["bot"] and len(r["d"]) <= SWEEP]
    prior = [r for r in use if r["t"] < "2026-04-01"]; test = [r for r in use if r["t"] >= "2026-04-01"]
    by_p = collections.defaultdict(list); by_t = collections.defaultdict(list)
    for r in prior:
        for d in r["d"]: by_p[d].append(r)
    for r in test:
        for d in r["d"]: by_t[d].append(r)
    both = sorted(set(by_p) & set(by_t))
    hits = collections.Counter(); hits_all = collections.Counter(); nall = 0; skipped_noown = 0
    for d in both:
        top = collections.Counter(r["a"] for r in by_p[d]).most_common(1)[0][0]
        last = by_p[d][-1]["a"]; co = set(own_t.get(d, []))
        nxt = by_t[d][0]["a"]
        hits["owner"] += nxt == top; hits["last"] += nxt == last; hits["codeowners"] += nxt in co
        hits["co_has"] += bool(co); hits["co_hit_has"] += bool(co) and nxt in co
        lt = last
        for r in by_t[d]:
            nall += 1; hits_all["owner"] += r["a"] == top; hits_all["last"] += r["a"] == lt; hits_all["codeowners"] += r["a"] in co; lt = r["a"]
    N = len(both)
    print(f"\nNEXT-AUTHOR (first test PR per integration), N={N} integrations with >=1 human non-sweep PR in both windows")
    for k in ("owner", "last", "codeowners"): print(f"  {k:11s} hit {hits[k]}/{N} = {hits[k]/N:.1%}")
    print(f"  codeowners, only integrations with >=1 codeowner: {hits['co_hit_has']}/{hits['co_has']} = {hits['co_hit_has']/max(1,hits['co_has']):.1%}")
    print(f"ALL test PRs (rolling last toucher), N={nall} (PR,integration) pairs")
    for k in ("owner", "last", "codeowners"): print(f"  {k:11s} hit {hits_all[k]}/{nall} = {hits_all[k]/nall:.1%}")

    # stale: prior-12-month activity of listed codeowners (codeowners as of 2026-04-01; PRs incl. sweeps, humans)
    act = collections.defaultdict(set); touched = set()
    for r in [r for r in touching if r["t"] < "2026-04-01"]:
        for d in r["d"]:
            touched.add(d); act[d].add(r["a"]); act[d] |= r["rev"]
    with_co = [d for d in touched if own_t.get(d)]
    stale = [d for d in with_co if not (set(own_t[d]) & act[d])]
    print(f"\nSTALE (codeowners @2026-04-01 vs PR authors+reviewers 2025-04..2026-04)")
    print(f"  integrations touched by >=1 merged PR in prior 12m: {len(touched)}; with >=1 codeowner: {len(with_co)}")
    print(f"  NONE of the codeowners authored/reviewed: {len(stale)}/{len(with_co)} = {len(stale)/len(with_co):.1%}")
    touched_h = {d for r in prior for d in r["d"]}
    with_co_h = [d for d in touched_h if own_t.get(d)]; stale_h = [d for d in with_co_h if not (set(own_t[d]) & act[d])]
    print(f"  same, only integrations with a human non-sweep PR: {len(stale_h)}/{len(with_co_h)} = {len(stale_h)/max(1,len(with_co_h)):.1%}")
    alld = own_t
    noown = [d for d, o in alld.items() if not o]; one = [d for d, o in alld.items() if len(o) == 1]
    distinct = {x for o in alld.values() for x in o}
    print(f"  manifests @2026-04-01: {len(alld)}; no codeowners {len(noown)} ({len(noown)/len(alld):.1%}); exactly one {len(one)} ({len(one)/len(alld):.1%}); among owned {len(one)}/{len(alld)-len(noown)} = {len(one)/(len(alld)-len(noown)):.1%}")
    print(f"  distinct codeowners {len(distinct)}; snapshot: {len({x for o in own_snap.values() for x in o})} distinct, {sum(1 for o in own_snap.values() if not o)}/{len(own_snap)} no owner")
    active = {r["a"] for r in rows if r["t"] >= "2025-10-01"} | {x for r in rows if r["t"] >= "2025-10-01" for x in r["rev"]}
    active12 = {r["a"] for r in rows if r["t"] >= "2025-04-01" and r["t"] < "2026-04-01"} | {x for r in rows if r["t"] < "2026-04-01" for x in r["rev"]}
    inact = distinct - active12
    print(f"  codeowners with no merged PR authored/reviewed 2025-04..2026-04 (any integration): {len(inact)}/{len(distinct)} = {len(inact)/len(distinct):.1%}")
    json.dump(sorted(distinct), open(f"{TIX}/codeowners_20260401.json", "w"))
    if "--users" in sys.argv or os.path.exists(f"{TIX}/users.json"):
        cache = json.load(open(f"{TIX}/users.json")) if os.path.exists(f"{TIX}/users.json") else {}
        if "--users" in sys.argv:
            todo = [u for u in sorted(distinct) if u not in cache]
            for k in range(0, len(todo), 50):
                chunk = todo[k:k + 50]
                q = "query{" + " ".join(f'u{j}: repositoryOwner(login:"{u}"){{login __typename}}' for j, u in enumerate(chunk)) + "}"
                r = subprocess.run(["gh", "api", "graphql", "-f", f"query={q}"], capture_output=True, text=True)
                d = (json.loads(r.stdout or "{}").get("data") or {})
                if not d: print("user batch failed", r.stderr[:200]); continue
                for j, u in enumerate(chunk): cache[u] = (d[f"u{j}"]["__typename"] if d.get(f"u{j}") else "404")
            json.dump(cache, open(f"{TIX}/users.json", "w"))
        print(f"  codeowner account types: {collections.Counter(cache.get(u) for u in distinct)}")
        gone = [u for u in distinct if cache.get(u) == "404"]
        print(f"  codeowner accounts 404 (deleted/renamed): {len(gone)}/{len([u for u in distinct if u in cache])} checked: {sorted(gone)[:15]}")
        print(f"  integrations whose every codeowner is 404 or inactive: {sum(1 for d,o in alld.items() if o and all((cache.get(x)=='404') or (x in inact) for x in o))}/{len(alld)-len(noown)}")

    # WRONG TEAM: window issues labelled integration: X, first non-bot commenter (not the author)
    iss = {i["number"]: i for i in (json.loads(l) for l in open(f"{TIX}/issues.jsonl"))}.values()
    n = wrong = nolabel = noresp = 0; lag = []; co_lag = []
    for i in iss:
        doms = [l["name"].split(": ", 1)[1] for l in i["labels"]["nodes"] if l["name"].startswith("integration: ")]
        if not doms: nolabel += 1; continue
        co = {x for d in doms for x in own_prior.get(d, [])}
        if not co: continue
        au = ((i.get("author") or {}).get("login") or "").lower()
        cs = [c for c in i["comments"]["nodes"] if c.get("author") and not is_bot(c["author"]["login"], c["author"].get("__typename")) and c["author"]["login"].lower() != au]
        n += 1
        if not cs: noresp += 1; continue
        first = cs[0]
        if first["author"]["login"].lower() not in co: wrong += 1
        t0 = dt.datetime.fromisoformat(i["createdAt"].replace("Z", "+00:00"))
        lag.append((dt.datetime.fromisoformat(first["createdAt"].replace("Z", "+00:00")) - t0).total_seconds() / 3600)
        cc = [c for c in cs if c["author"]["login"].lower() in co]
        if cc: co_lag.append((dt.datetime.fromisoformat(cc[0]["createdAt"].replace("Z", "+00:00")) - t0).total_seconds() / 3600)
    print(f"\nWRONG TEAM (window issues with integration label whose integration has codeowners @2025-04-01): N={n} (no integration label: {nolabel})")
    print(f"  no human non-author comment in first 15 comments: {noresp} ({noresp/n:.1%})")
    print(f"  first human responder NOT a codeowner: {wrong}/{n-noresp} = {wrong/(n-noresp):.1%}")
    print(f"  median hours to first human response {statistics.median(lag):.1f}; issues with a codeowner comment {len(co_lag)}/{n} ({len(co_lag)/n:.1%}), median hours to first codeowner comment {statistics.median(co_lag):.1f}")

if __name__ == "__main__":
    main()
