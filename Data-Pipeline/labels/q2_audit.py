"""GAPS.md gap 6 (Chaitali, decision 11): how noisy are the model's Q2 positive labels? An LLM audits, a person checks.

The forest finds 0 of the 51 `size_bucket=Q2` positives in val. SZZ blames the lines a fix changed, so a small commit
may be blamed for a bug it didn't cause. For each (blamed commit, fix) pair, a model reads the fix's ticket key,
message and diff on the blamed file, and the blamed commit's message and diff on that file, and answers whether the
commit introduced that bug. 20 of the Q2 commits are judged by a person, blind. The result is a label-noise estimate
for the bias section: it never changes a label and never becomes training data (GAPS.md).

    python labels/q2_audit.py sheet       # labels/q2_audit_hand_check_20.csv: the blind sheet, human_verdict empty
    python labels/q2_audit.py run --model claude-opus-5-5 [--cache-only]   # the audit, through meridian.llm
    python labels/q2_audit.py summary     # Model-Pipeline/reports/q2_label_audit.json, from the cache and the sheet

Run from Data-Pipeline/. Reads Model-Pipeline/data/features.parquet (val rows only, 2023), the v1 block's
bug_inducing.jsonl and the Spark checkout. Only public Apache code and Jira keys are sent: lines that name a person
(Signed-off-by, Authored-by, "Closes #n from user/branch") and every email address and @handle are removed first.
"""
import argparse
import csv
import json
import random
import re
import sys
from contextlib import nullcontext
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))  # Data-Pipeline/, for meridian

from meridian.common import data_root, git, repo_checkout  # noqa: E402
from meridian.llm_cache import Cache, canonical, key  # noqa: E402

ROOT = HERE.parents[1]
FEATURES = ROOT / "Model-Pipeline" / "data" / "features.parquet"
BLOCK = "70dd1dabfed544b8"   # v1: its bug_inducing table (c5118's differs by 1%, Utkarsh)
SNAPSHOT = "c5118b019d642a9b"
REPO = "apache/spark"
SHEET = HERE / "q2_audit_hand_check_20.csv"
OUT = ROOT / "Model-Pipeline" / "reports" / "q2_label_audit.json"
PV = "q2-audit-1"            # the prompt version: part of every cache key
AGENT = "label_audit"
VERDICTS = ("yes", "no", "unclear")
MAX_DIFF_LINES = 200         # per diff, so one prompt stays near 6,000 tokens (GAPS.md, cost)
MAX_DIFF_CHARS = 8000        # ... and per diff in characters, for files with very long lines
HAND_N, Q4_N, SEED = 20, 50, 1
AGREE_MIN = 15               # of 20: below this the model's answers aren't used (decision 11)

PERSON_LINE = re.compile(r"^\s*(signed-off-by|authored-by|co-authored-by|lead-authored-by|author|reviewed-by|"
                         r"tested-by|reported-by):", re.I)
CLOSES = re.compile(r"(Closes #\d+) from \S+")
EMAIL = re.compile(r"[\w.+\-]+@[\w\-]+(?:\.[\w\-]+)+")
HANDLE = re.compile(r"(?<![\w/])@[A-Za-z0-9][\w\-]*")

SYSTEM = """You audit one label from SZZ, an algorithm that marks a commit as bug-inducing when a later bug fix changed
lines that commit wrote. SZZ is often wrong: the blamed lines may only have been moved, reformatted or touched in
passing, or the bug may come from elsewhere.

Below are the fix (its Jira ticket key, message and diff on one file) and the commit SZZ blamed (its message and diff
on the same file). Decide: did the blamed commit introduce the bug that the fix repairs?

Answer with JSON only: {"verdict": "yes" | "no" | "unclear", "reason": "<one sentence>"}
- yes: the blamed commit's change is the cause, or part of it
- no: the blamed lines aren't the cause (moved, reformatted, unrelated, or the bug predates them)
- unclear: these diffs aren't enough to tell

Facts:
"""


def scrub(text):
    """The text with every line naming a person dropped, and emails and @handles removed."""
    lines = [CLOSES.sub(r"\1", ln) for ln in text.splitlines() if not PERSON_LINE.match(ln)]
    return HANDLE.sub("@…", EMAIL.sub("<email>", "\n".join(lines))).strip()


def capped(diff, n=MAX_DIFF_LINES, chars=MAX_DIFF_CHARS):
    lines = diff.splitlines()
    out = "\n".join(lines[:n] + ([f"... ({len(lines) - n} more lines)"] if len(lines) > n else []))
    return out if len(out) <= chars else out[:chars] + f"\n... ({len(out) - chars} more characters)"


def picks(features, inducing):
    """(q2, q4): the val positives audited, as sorted sha lists. Q2: all of them; Q4: Q4_N drawn with SEED."""
    pos = features[(features["split"] == "val") & (features["label"] == 1)]
    q2 = sorted(pos.loc[pos["size_bucket"] == "Q2", "sha"])
    q4_all = sorted(pos.loc[pos["size_bucket"] == "Q4", "sha"])
    q4 = sorted(random.Random(SEED).sample(q4_all, min(Q4_N, len(q4_all))))
    missing = [s for s in q2 + q4 if s not in set(inducing["inducing_sha"])]
    if missing:
        raise ValueError(f"{len(missing)} audited commits have no bug_inducing row, e.g. {missing[0]}")
    return q2, q4


def hand_sample(q2):
    """The 20 Q2 commits a person judges: drawn with SEED from the sorted 51."""
    return sorted(random.Random(SEED).sample(q2, HAND_N))


def facts_for(co, row):
    """One pair's facts, every string scrubbed: what the model sees."""
    def show(sha, *args):
        return git(co, "show", "--no-color", *args, sha)
    return {"ticket": row["key"], "path": row["path"],
            "fix_message": scrub(show(row["fix_sha"], "-s", "--format=%B")),
            "fix_diff": scrub(capped(git(co, "show", "--no-color", "--format=", row["fix_sha"], "--", row["path"]))),
            "blamed_message": scrub(show(row["inducing_sha"], "-s", "--format=%B")),
            "blamed_diff": scrub(capped(git(co, "show", "--no-color", "--format=", row["inducing_sha"], "--",
                                            row["path"])))}


def items(features, inducing, co):
    """Every pair to audit: {bucket, sha, fix_sha, path, facts}, in a fixed order."""
    q2, q4 = picks(features, inducing)
    out = []
    for bucket, shas in (("Q2", q2), ("Q4", q4)):
        for sha in shas:
            rows = inducing[inducing["inducing_sha"] == sha].sort_values(["fix_sha", "path"])
            for _, r in rows.drop_duplicates(["fix_sha"]).iterrows():  # one file per fix: the first blamed path
                out.append({"bucket": bucket, "sha": sha, "fix_sha": r["fix_sha"], "path": r["path"],
                            "facts": facts_for(co, r)})
    return out


def prompt(facts):
    return SYSTEM + canonical(facts)


def parse(reply):
    """(verdict, reason), or ("unclear", why) for a reply that isn't the JSON asked for."""
    m = re.search(r"\{.*\}", reply or "", re.S)
    try:
        d = json.loads(m.group(0)) if m else None
    except json.JSONDecodeError:
        d = None
    if not isinstance(d, dict) or d.get("verdict") not in VERDICTS:
        return "unclear", "not the JSON asked for"
    return d["verdict"], str(d.get("reason", ""))


def audit(pairs, llm, model, cache, log=None, cache_only=False):
    """Each pair's verdict, from the cache or one call. Returns [{bucket, sha, fix_sha, verdict, reason}]."""
    out = []
    for p in pairs:
        k = key(model, PV, p["facts"])
        entry, hit = cache.get(k), True
        if entry is None:
            if cache_only or llm is None:
                raise LookupError(f"no cached answer for {p['sha'][:10]} / {p['fix_sha'][:10]} and no LLM")
            r, hit = llm(model, prompt(p["facts"])), False
            entry = {"model": model, "prompt_version": PV, "reply": r["reply"], "tokens_in": r["tokens_in"],
                     "tokens_out": r["tokens_out"], "cost_usd": r["cost_usd"]}
            cache.put(k, entry)
        verdict, reason = parse(entry["reply"])
        if log is not None:
            log(agent=AGENT, model=model, prompt_version=PV, tokens_in=0 if hit else entry["tokens_in"],
                tokens_out=0 if hit else entry["tokens_out"], cost_usd=0.0 if hit else entry["cost_usd"],
                cache_hit=hit, gate_pass=None, task="q2_audit", repo=REPO, facts_hash=k)
        out.append({"bucket": p["bucket"], "sha": p["sha"], "fix_sha": p["fix_sha"], "verdict": verdict,
                    "reason": reason, "cost_usd": 0.0 if hit else entry["cost_usd"], "made_usd": entry["cost_usd"]})
    return out


def per_commit(answers):
    """{sha: verdict}: yes if any pair is yes, else no if any is no, else unclear."""
    by = {}
    for a in answers:
        by.setdefault(a["sha"], []).append(a["verdict"])
    return {s: "yes" if "yes" in v else "no" if "no" in v else "unclear" for s, v in by.items()}


def share(verdicts):
    """The share of no among yes and no, with its Wilson 95% interval and every count."""
    from scipy.stats import binomtest
    yes, no, unclear = (sum(v == x for v in verdicts) for x in VERDICTS)
    n = yes + no
    if n == 0:
        return {"no": no, "yes": yes, "unclear": unclear, "n": 0, "noise": None, "ci": None}
    ci = binomtest(no, n).proportion_ci(method="wilson")
    return {"no": no, "yes": yes, "unclear": unclear, "n": n, "noise": no / n, "ci": [ci.low, ci.high]}


def summary(answers, hand, model):
    """The report: each bucket's noise share, the hand check's, and whether the model's answers are used."""
    commits = per_commit(answers)
    bucket = {a["sha"]: a["bucket"] for a in answers}
    judged = {h["sha"]: h["human_verdict"] for h in hand if h["human_verdict"] in VERDICTS}
    agree = sum(commits.get(s) == v and v != "unclear" for s, v in judged.items())
    used = len(judged) == HAND_N and agree >= AGREE_MIN
    q2 = share([v for s, v in commits.items() if bucket[s] == "Q2"])
    q4 = share([v for s, v in commits.items() if bucket[s] == "Q4"])
    apart = bool(used and q2["ci"] and q4["ci"] and q2["ci"][0] > q4["ci"][1])
    return {"model": model, "prompt_version": PV, "block": BLOCK, "split": "val",
            "Q2": q2, "Q4": q4, "hand": {"judged": len(judged), "of": HAND_N, "agree_with_model": agree,
                                         "share": share(list(judged.values()))},
            "model_used": used, "agree_min": AGREE_MIN, "q2_noisier_than_q4": apart,
            # what the answers cost to make, cached or not
            "cost_usd": round(sum(a.get("made_usd", a["cost_usd"]) for a in answers), 4),
            "note": "an estimate for the bias section: never a label, never training data (GAPS.md gap 6)"}


def write_sheet(hand_shas, inducing, path=SHEET):
    """The blind hand-check sheet: public links only, the model's answer not on it."""
    rows = []
    for i, sha in enumerate(hand_shas, 1):
        r = inducing[inducing["inducing_sha"] == sha].sort_values(["fix_sha", "path"]).iloc[0]
        rows.append({"order": i, "key": r["key"], "repo": REPO, "sha": sha, "fix_sha": r["fix_sha"],
                     "path": r["path"], "commit_url": f"https://github.com/{REPO}/commit/{sha}",
                     "fix_url": f"https://github.com/{REPO}/commit/{r['fix_sha']}",
                     "ticket_url": f"https://issues.apache.org/jira/browse/{r['key']}",
                     "human_verdict": "", "note": "", "checked_by": ""})
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]), lineterminator="\n")
        w.writeheader()
        w.writerows(rows)
    return path


def load():
    import pandas as pd
    features = pd.read_parquet(FEATURES, columns=["sha", "split", "label", "size_bucket"])
    inducing = pd.read_json(data_root() / "out" / BLOCK / "bug_inducing.jsonl", lines=True)
    inducing = inducing[inducing["repo"] == REPO]
    co = repo_checkout(data_root() / "snapshots" / SNAPSHOT, REPO)
    return features, inducing, co


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("step", choices=("sheet", "run", "summary"))
    p.add_argument("--model", default="claude-opus-5-5", help="decision 11: claude-opus-5-5, else gemini-pro")
    p.add_argument("--cache-only", action="store_true", help="answer from the cache, never call")
    p.add_argument("--track", action="store_true", help="log every answer to MLflow meridian-agents")
    a = p.parse_args(argv)
    features, inducing, co = load()
    q2, _ = picks(features, inducing)
    if a.step == "sheet":
        print(f"wrote {write_sheet(hand_sample(q2), inducing)}: {HAND_N} of {len(q2)} Q2 commits, blind")
        return 0
    pairs = items(features, inducing, co)
    llm = None
    if a.step == "run" and not a.cache_only:
        from meridian.llm import Client  # Vishwa's client (gap 1)
        llm = Client()
    log, tracked = None, nullcontext()
    if a.track:
        sys.path.insert(0, str(ROOT / "Model-Pipeline"))  # Contract 3: product code reaches model.tracking this way
        from model import tracking
        tracked = tracking.start_run(AGENT, owner="chaitali", experiment=tracking.AGENTS, block_run_id=BLOCK)
        log = tracking.log_llm_call
    with tracked:
        answers = audit(pairs, llm, a.model, Cache(), log, cache_only=a.cache_only or a.step == "summary")
    hand = []
    if SHEET.exists():
        with open(SHEET, newline="", encoding="utf-8") as f:
            hand = list(csv.DictReader(f))
    rep = summary(answers, hand, a.model)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(rep, indent=2) + "\n", encoding="utf-8", newline="\n")
    print(json.dumps({k: rep[k] for k in ("Q2", "Q4", "hand", "model_used", "cost_usd")}, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
