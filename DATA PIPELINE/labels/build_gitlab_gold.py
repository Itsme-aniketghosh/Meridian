"""GitLab gold set · step 1 · D6: the two week-1 lists of cross-team blocks, merged, and the 57 stall reads. See CSV-POLICY.md.

    python3 "DATA PIPELINE/labels/build_gitlab_gold.py" <gold-set dir> <out/<run_id> dir>

<gold-set dir> holds the week-1 sheets, which stay local (they name people), and optionally extra_names.txt:
usernames the model quoted that aren't in the GitLab pull, one per line, so they are scrubbed too.
  xt_cross_team_blocks.csv    Aniket's link pass, one row per "blocked by" link
  cross_hand_check_116.csv    Vishwa's targeted read; 25 confirmed
  hand_check_17.csv, hand_check_40.csv   Vishwa's 57 stall reads; the 5 cross_team = y make Vishwa's 30
<out/<run_id> dir> is a team run's handoff folder: GitLab ticket and ticket_link fill the teams and dates the sheets lack.

Writes, next to this script:
  gitlab_cross_team_blocks.csv  one row per (blocked_ref, blocker_ref). A pair in both lists -> source 'both', the
                                stronger verdict and its evidence. human_verdict stays blank until the spot-check
  gitlab_stall_reasons.csv      one row per stall read
Over the week-1 verdicts: same team on both sides -> no; a blocked-by link removed while the blocker was open -> no;
then gitlab_spot_check_model.csv, a second model read of sampled pairs, wins where it disagrees.
Scrubbed: no people columns, emails -> [email], @handles -> @user, any GitLab username in the run -> user, quotes <= 200.
"""
import csv, gzip, hashlib, json, re, sys
from pathlib import Path

GL = "gitlab-org/gitlab"
HERE = Path(__file__).resolve().parent
REF = re.compile(r"(?:[\w.-]+/)*[\w.-]+#\d+|(?<![\w/#])#\d+")
RANK = ["wait", "planned", "partial", "unclear", "no", "no_evidence"]  # strongest first
XT_VERDICT = {"no evidence": "no_evidence"}
VP_VERDICT = {"confirmed": "wait", "possible": "unclear", "no": "no", "not-a-block": "no"}


def read_csv(path):
    with open(path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def read_jsonl(path):
    with open(path, encoding="utf-8") as f:
        return [json.loads(l) for l in f if l.strip()]


def full_ref(r, known=None):
    """#123 -> gitlab-org/gitlab#123. A short project (design.gitlab.com#1148) takes the full path seen in the links;
    one with no full path seen is a gitlab-org project (gitlab-runner#38654 -> gitlab-org/gitlab-runner#38654)."""
    if r.startswith("#"):
        return f"{GL}{r}"
    r = (known or {}).get(r, r)
    return r if "/" in r.split("#")[0] else f"gitlab-org/{r}"


def scrubber(usernames, always=()):
    """usernames that are English words (design, grant) are left alone: they'd mangle quotes. always: scrubbed anyway."""
    words = set()
    dict_file = Path("/usr/share/dict/words")
    if dict_file.exists():
        words = {w.strip().lower() for w in dict_file.read_text().splitlines()}
    names = {u for u in usernames if len(u) >= 4 and u.lower() not in words} | set(always)
    names = sorted(names, key=len, reverse=True)
    rx = re.compile(r"(?<![\w@./-])(" + "|".join(map(re.escape, names)) + r")(?![\w/-])") if names else None

    def scrub(text, limit=None):
        text = re.sub(r"[\w.+-]+@[\w-]+\.[\w.-]+", "[email]", text or "")
        text = re.sub(r"(?<![\w@])@(?!user\b)[A-Za-z0-9_][\w.-]*", "@user", text)
        if rx:
            text = rx.sub("user", text)
        text = " ".join(text.split())
        return text if not limit or len(text) <= limit else text[: limit - 1] + "…"
    return scrub


def group(components):
    gs = [c[len("group::"):] for c in json.loads(components or "[]") if c.startswith("group::")]
    return gs[0] if gs else ""


def main(src, run):
    src, run = Path(src), Path(run)
    tickets = {t["key"]: t for t in read_jsonl(run / "ticket.jsonl") if t["tracker"] == "gitlab"}
    links = [l for l in read_jsonl(run / "ticket_link.jsonl") if l["link_type"] == "blocked_by"]
    xt = read_csv(src / "xt_cross_team_blocks.csv")
    vp = read_csv(src / "cross_hand_check_116.csv")
    stalls = [dict(r, group=r.get("group", "")) for f in ("hand_check_17.csv", "hand_check_40.csv") for r in read_csv(src / f)]
    raw = run.parent.parent / "snapshots" / run.name / "gitlab" / "issues.jsonl.gz"
    users = set(re.findall(r'"username":\s*"([^"]+)"', gzip.decompress(raw.read_bytes()).decode())) if raw.exists() else set()
    users |= {t[k] for t in tickets.values() for k in ("assignee", "reporter") if t.get(k)} | {r["added_by"] for r in xt if r["added_by"]}
    extra = src / "extra_names.txt"  # names the model read live, not in the pull: kept local, one per line
    scrub = scrubber(users, {l.strip() for l in extra.read_text().splitlines() if l.strip()} if extra.exists() else ())
    team = lambda ref: group(tickets[ref]["components"]) if ref in tickets else ""
    closed = lambda ref: (tickets[ref]["resolved_at"] or "")[:10] if ref in tickets else ""
    added = lambda a, b: ((links.get((a, b)) or {}).get("added_at") or "")[:10]
    url = lambda iid: f"https://gitlab.com/{GL}/-/issues/{iid}"
    full = {l["other_key"] for l in links} | {r["blocker"] for r in xt}
    tails = {}
    for f in full:
        tails.setdefault(f.split("/")[-1], set()).add(f)
    known = {t: next(iter(fs)) for t, fs in tails.items() if len(fs) == 1 and "/" in next(iter(fs))}
    links = {(l["key"], full_ref(l["other_key"], known)): l for l in links}

    def ref_for(a, x):
        """A bare #1996 in a note means the issue a's own blocked-by link points at, if one ends that way."""
        linked = [b for (k, b) in links if k == a and b.endswith(x)] if x.startswith("#") else []
        return linked[0] if len(linked) == 1 else full_ref(x, known)

    rows = {}

    def put(r):
        key = (r["blocked_ref"], r["blocker_ref"])
        if key not in rows:
            rows[key] = r
            return
        old = rows[key]
        win = r if RANK.index(r["verdict"]) < RANK.index(old["verdict"]) else old
        lose = old if win is r else r
        rows[key] = {k: win[k] or lose[k] for k in win} | {"source": "both"}

    for r in xt:
        a, b = f"{GL}#{r['x']}", full_ref(r["blocker"], known)
        put({"blocked_ref": a, "blocker_ref": b, "waiting_team": r["x_group"] or team(a), "blocking_team": r["blocker_group"] or team(b),
             "link_added_at": r["link_added"][:10] or added(a, b), "blocker_closed_at": r["blocker_closed"][:10] or closed(b),
             "verdict": XT_VERDICT.get(r["verdict"], r["verdict"]), "source": "link_pass", "evidence_url": url(r["x"]),
             "evidence_quote": scrub(r["evidence"], 200), "checked_by": "model", "human_verdict": ""})

    def vishwa(a, refs, own, other, verdict, link, evidence):
        for b in refs or [""]:
            put({"blocked_ref": a, "blocker_ref": b, "waiting_team": team(a) or own, "blocking_team": team(b) or other,
                 "link_added_at": added(a, b), "blocker_closed_at": closed(b), "verdict": verdict, "source": "targeted_read",
                 "evidence_url": link, "evidence_quote": scrub(evidence, 200), "checked_by": "model", "human_verdict": ""})

    for r in vp:
        a = f"{GL}#{r['iid']}"
        other = scrub(r["waited_on_team"]).split(" (")[0]
        vishwa(a, [ref_for(a, x) for x in REF.findall(r["waited_on_ref"])], scrub(r["own_team"]), other,
               VP_VERDICT[r["verdict"]], r["url"], r["evidence"])
    for r in stalls:  # the 5 from the random read that count toward Vishwa's 30
        if r["cross_team"] != "y":
            continue
        a = f"{GL}#{r['iid']}"
        refs = sorted(b for (k, b) in links if k == a) or [ref_for(a, x) for x in REF.findall(r["reason"])]
        vishwa(a, refs, r["group"].removeprefix("group::"), "", "wait", r["url"], r["reason"])

    blocks = sorted(rows.values(), key=lambda r: (r["blocked_ref"], r["blocker_ref"]))

    # Rules from the data, over the model's verdicts. A model spot-check found 4 of 10 waits wrong for these reasons
    ruled = {"same team": 0, "link swapped": 0}
    for r in blocks:
        if r["verdict"] not in ("wait", "planned", "partial"):
            continue
        link = links.get((r["blocked_ref"], r["blocker_ref"]))
        if r["waiting_team"] and r["waiting_team"] == r["blocking_team"]:
            r["verdict"], why = "no", "same team"           # not cross-team
        elif link and link["removed_at"] and (not r["blocker_closed_at"] or link["removed_at"][:10] < r["blocker_closed_at"]):
            r["verdict"], why = "no", "link swapped"        # unlinked while the blocker was still open
        else:
            continue
        ruled[why] += 1
    recheck = HERE / "gitlab_spot_check_model.csv"  # a second, stricter model read of sampled pairs wins over the first
    if recheck.exists():
        again = {(r["blocked_ref"], r["blocker_ref"]): r["recheck_verdict"] for r in read_csv(recheck)}
        for r in blocks:
            if (k := (r["blocked_ref"], r["blocker_ref"])) in again and again[k] != r["verdict"]:
                r["verdict"] = again[k]
                ruled["model recheck"] = ruled.get("model recheck", 0) + 1
    if (HERE / "gitlab_cross_team_blocks.csv").exists():  # a rebuild keeps the spot-check
        done = {(r["blocked_ref"], r["blocker_ref"]): r["human_verdict"] for r in read_csv(HERE / "gitlab_cross_team_blocks.csv")}
        for r in blocks:
            r["human_verdict"] = done.get((r["blocked_ref"], r["blocker_ref"]), "")
    stall_rows = []
    for r in stalls:
        a = f"{GL}#{r['iid']}"
        still = not re.match(r"\d{4}-\d{2}-\d{2}", r["blocked_to"])
        stall_rows.append({"ref": a, "team": r["group"].removeprefix("group::") or team(a) or "unknown",
                           "blocked_from": r["blocked_from"], "blocked_to": "" if still else r["blocked_to"],
                           "ended_by": "open" if still else ("status" if r.get("source") == "status" else "label"),
                           "reason": r["reason_category"], "cross_team": int(r["cross_team"] == "y"),
                           "evidence_url": r["url"], "evidence_quote": scrub(r["reason"], 200), "checked_by": "model"})
    stall_rows.sort(key=lambda r: r["ref"])

    for name, out in (("gitlab_cross_team_blocks.csv", blocks), ("gitlab_stall_reasons.csv", stall_rows)):
        with open(HERE / name, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=list(out[0]), lineterminator="\n")
            w.writeheader()
            w.writerows(out)

    pos = [r for r in blocks if r["verdict"] in ("wait", "planned")]
    by = lambda rs, k: {v: sum(r[k] == v for r in rs) for v in sorted({r[k] for r in rs})}
    print(f"gitlab_cross_team_blocks.csv  {len(blocks)} pairs · verdicts {by(blocks, 'verdict')} · sources {by(blocks, 'source')}")
    print(f"  verdicts changed by rule: {ruled}")
    print(f"  wait + planned: {len(pos)} pairs on {len({r['blocked_ref'] for r in pos})} distinct waiting issues (need >= 38)")
    print(f"  no blocker ref: {sum(not r['blocker_ref'] for r in blocks)} · no blocking team: {sum(not r['blocking_team'] for r in blocks)}")
    print(f"gitlab_stall_reasons.csv  {len(stall_rows)} stalls · reasons {by(stall_rows, 'reason')} · ended_by {by(stall_rows, 'ended_by')}")

    # The spot-check: 10 wait, 5 planned, 5 partial, one per waiting issue, picked by hash so anyone gets the same 20
    # A wait or planned case holds if the person finds a wait or planned; a partial one if they find any real dependency
    holds = {"wait": {"wait", "planned"}, "planned": {"wait", "planned"}, "partial": {"wait", "planned", "partial"}}
    print("\nspot-check (fill human_verdict in gitlab_cross_team_blocks.csv):")
    for verdict, n in (("wait", 10), ("planned", 5), ("partial", 5)):
        seen, picked = set(), []
        for r in sorted((r for r in blocks if r["verdict"] == verdict and r["blocker_ref"]),
                        key=lambda r: hashlib.sha256(f"{r['blocked_ref']}\x1f{r['blocker_ref']}".encode()).hexdigest()):
            if r["blocked_ref"] not in seen and len(picked) < n:
                seen.add(r["blocked_ref"])
                picked.append(r)
        held = [r for r in picked if r["human_verdict"]]
        print(f"  {verdict}: {len(held)} of {len(picked)} checked, {sum(r['human_verdict'] in holds[verdict] for r in held)} hold")
        for r in picked:
            print(f"    {r['human_verdict'] or '____':12} {r['blocked_ref']} waits on {r['blocker_ref']}  {r['evidence_url']}")


if __name__ == "__main__":
    if len(sys.argv) != 3:
        sys.exit(__doc__)
    main(*sys.argv[1:])
