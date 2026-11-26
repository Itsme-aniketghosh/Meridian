"""S10's fallback-rate slice report (Model-Deployment/PLAN.md §15, ↻4 Agents): on every refresh, how often the fact
gate sent the writer's LLM text back to the template, per repo, owner team and task. Owner: Vishwa. Run from
Model-Deployment/.

    python -m monitor.fallback --calls calls.jsonl --out fallback.json            # rows from meridian.agents --calls
    python -m monitor.fallback --db published/<run_id>.sqlite --run-id <run_id> --out fallback.json
                                                       # write the block's texts first (LLM off: the cache answers)

A person found the #6921 bug by reading this rate (41 of 68 texts fell back). This report finds the next one on its
own and posts to Slack. A person still makes the fix.

The rule is phase 2's, unchanged (Model-Pipeline/model/llm_bias.py, decision 9): one text per (agent, facts_hash),
which fell back when none of its attempts shipped. A slice is judged when N >= model.toml [bias] min_n, and flagged
when its whole Wilson interval is more than [bias] max_gap above the overall rate. A flagged slice listed in
[llm_bias] accepted (a fix tried and written down) is reported but doesn't post.

    {"run_id", "texts", "overall": {k, n, value}, "by": {repo|team|task: {group: {k, n, value, ci, judged, flagged}}},
     "flags": [...], "open_flags": [...], "posted": [channels], "note"}

Slack only when a flag is open, through Data-Pipeline's meridian.alert (event "warn", MERIDIAN_SLACK_WEBHOOK), with
anything that looks like an email removed. With --db, the writer runs with no LLM: the committed cache answers and a
miss ships the template, which is no LLM text and isn't counted (no key and no spend in the job). No texts: nothing
is judged and nothing posts. Exit 0 always: a report never fails the refresh.
"""
import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

import pandas as pd

from monitor import drift  # puts Model-Pipeline on the path

from model import llm_bias  # noqa: E402
from model.bias import bias_settings  # noqa: E402
from model.data import load_config  # noqa: E402

HERE = Path(__file__).resolve().parent
DEPLOY = HERE.parent
DATA_PIPELINE = DEPLOY.parent / "Data-Pipeline"
MODEL_TOML = drift.PIPELINE / "model.toml"
SHIP = "gemini-flash"   # the writer's shipped model (Model-Pipeline/GAPS.md gap 7), whose cache the job reads
EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
DIMS = ("repo", "team", "task")


def settings(model_toml=MODEL_TOML):
    """([bias]'s rule, [llm_bias] accepted) from model.toml."""
    cfg = load_config(model_toml)
    return bias_settings(cfg), list(cfg.get("llm_bias", {}).get("accepted", []))


def build(calls, b, accepted=(), run_id=None):
    """The report from the writer's logged rows (a DataFrame, or None). Pure: no I/O."""
    f = llm_bias.fallback(llm_bias.texts(calls), b)
    by = {d: f["by"][d]["groups"] for d in DIMS}
    flags = [{"slice": f"{d}={g}", "value": e["value"], "k": e["k"], "n": e["n"], "ci": e["ci"]}
             for d in DIMS for g, e in by[d].items() if e["flagged"]]
    acc = set(accepted)
    open_flags = [x for x in flags if f"fallback:{x['slice']}" not in acc]
    return {"run_id": run_id, "texts": f["texts"], "overall": f["by"]["repo"]["overall"], "by": by,
            "flags": flags, "open_flags": open_flags, "accepted": sorted(a for a in acc if a.startswith("fallback:")),
            "rule": {k: b[k] for k in ("min_n", "max_gap")},
            "note": f["note"] or (f"{sum(e['judged'] for d in DIMS for e in by[d].values())} slices judged "
                                  f"(N >= {b['min_n']})")}


def message(rep):
    """(subject, lines) for Slack: the open flags, worst first. Nothing that looks like an email."""
    o = rep["overall"]
    subject = (f"Meridian: LLM fallback rate flagged on {len(rep['open_flags'])} slice"
               f"{'s' if len(rep['open_flags']) != 1 else ''}" + (f" (block {rep['run_id']})" if rep["run_id"] else ""))
    lines = [f"overall {o['value']:.3f} ({o['k']} of {o['n']} LLM texts went back to the template)"]
    for x in sorted(rep["open_flags"], key=lambda x: -x["value"]):
        lines.append(f"{x['slice']}: {x['value']:.3f} ({x['k']} of {x['n']}, interval {x['ci'][0]:.3f}-"
                     f"{x['ci'][1]:.3f}), more than {rep['rule']['max_gap']} above overall")
    lines.append("a person reads the gate's misses for these texts and fixes the template, prompt or facts; "
                 "once tried, model.toml [llm_bias] accepted lists it")
    return EMAIL.sub("[removed]", subject), [EMAIL.sub("[removed]", x) for x in lines]


def send(subject, lines):
    """meridian.alert.send as a warning: the channels that took it. A broken channel never fails the report."""
    sys.path.insert(0, str(DATA_PIPELINE))
    try:
        from meridian import alert
        return alert.send(subject, lines, event="warn")
    except Exception as e:  # noqa: BLE001
        print(f"fallback: not sent ({type(e).__name__}: {e})", file=sys.stderr)
        return []


def post(rep, sender=send):
    """Posts when a flag is open; the channels, [] otherwise."""
    return sender(*message(rep)) if rep["open_flags"] else []


def write_texts(db, run_id, calls, python=sys.executable, model=SHIP):
    """The writer on the block with no LLM, its logged rows to calls (meridian.agents --calls). Replaces calls."""
    Path(calls).unlink(missing_ok=True)
    subprocess.run([python, "-m", "meridian.agents", "--run-id", run_id, "--db", str(db), "--quiet",
                    "--models", model, "--calls", str(calls)], cwd=DATA_PIPELINE, check=True,
                   stdout=sys.stderr)
    return calls


def read_calls(paths):
    rows = [pd.read_json(p, lines=True) for p in paths if Path(p).exists() and Path(p).stat().st_size]
    return pd.concat(rows, ignore_index=True) if rows else None


def run(calls=(), db=None, run_id=None, model_toml=MODEL_TOML, sender=send, work=None):
    b, accepted = settings(model_toml)
    paths = list(calls)
    if db is not None:
        paths.append(write_texts(db, run_id, Path(work or Path(db).parent) / f"calls_{run_id}.jsonl"))
    rep = build(read_calls(paths), b, accepted, run_id)
    rep["posted"] = post(rep, sender)
    return rep


def print_report(rep):
    o = rep["overall"]
    rate = f"{o['value']:.3f} ({o['k']} of {o['n']})" if o["value"] is not None else "n/a"
    print(f"fallback: {rep['texts']} LLM texts, overall {rate}; {rep['note']}")
    for x in rep["flags"]:
        state = "open" if x in rep["open_flags"] else "accepted"
        print(f"  FLAG {x['slice']} {x['value']:.3f} ({x['k']} of {x['n']}), {state}")
    print(f"posted: {', '.join(rep['posted']) or 'nothing'}")


def main(argv=None, sender=send):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--calls", nargs="*", default=[], type=Path, help="rows from meridian.agents --calls")
    ap.add_argument("--db", type=Path, help="a block: write its texts first, LLM off")
    ap.add_argument("--run-id", help="the block's run id (needed with --db)")
    ap.add_argument("--out", type=Path, help="write the report as JSON")
    a = ap.parse_args(argv)
    if a.db and not a.run_id:
        ap.error("--db needs --run-id")
    try:
        rep = run(a.calls, a.db, a.run_id, sender=sender, work=a.out.parent if a.out else None)
    except (subprocess.CalledProcessError, OSError, ValueError) as e:   # a report never fails the refresh
        rep = {"run_id": a.run_id, "texts": 0, "flags": [], "open_flags": [], "posted": [],
               "error": EMAIL.sub("[removed]", f"{type(e).__name__}: {e}")[:500]}
        print(f"fallback: not measured, {rep['error']}", file=sys.stderr)
    else:
        print_report(rep)
    if a.out:
        a.out.parent.mkdir(parents=True, exist_ok=True)
        a.out.write_text(json.dumps(rep, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
