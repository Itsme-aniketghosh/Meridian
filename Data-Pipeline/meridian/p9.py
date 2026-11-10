"""P9 Defect risk with the champion model (step Q, Rachna). The push check calls `verdict` (meridian/pushcheck.py).

When Model-Pipeline/registry/champion.json names a champion, its model file is next to it, the file's md5 is the one
champion.json records, and the model loads, P9 scores the push with it: the push's Contract 1 features (Model-Pipeline
packets/README.md) computed from the base block the way model/data.py computes them, then the model's risk against
the threshold registry.py recorded on val (champion.json metrics.at_threshold). Otherwise P9 is lines × files above
[push_check] defect_risk_threshold, as before, and when a champion was named but couldn't be used the finding says why.

Templates only, no LLM, $0 a push. Read-only on the block, like the rest of the push check.

How the push becomes one commit (data.history, so the features can't drift from the training table):
- the base block's first-parent commits of the repo, in order, then the push as one more commit at the block's
  snapshot_at, with the push's numstat as its files (a binary file is 0 lines, as in training)
- the push's author is the person person_alias maps their email to; an email the block hasn't seen is a new person
- its ticket features read the push's keys as P8 does (the subject's, else the body's) against the block's tickets;
  is_minor is 1 when every commit's subject starts with [MINOR]
The model only scores the repo it was trained on (model.toml [data] repo); any other repo gets lines × files.

Shortcut: the push is dated at the block's snapshot_at, not its own author date, so age and the prior_fix_days window
are as of the block. A push days after the snapshot reads its files slightly younger than they are.
"""
import hashlib
import json
import re
import sys
import tomllib
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]                     # the repo
MODEL_PIPELINE = ROOT / "Model-Pipeline"


@dataclass
class Verdict:
    rule: str          # 'model' or 'lines_x_files'
    alert: bool
    text: str          # the P9 line, when alert
    why: str = None    # a champion was named but not used: why (said in the text when it alerts)


def _md5(path):
    return hashlib.md5(Path(path).read_bytes()).hexdigest()


def load_champion(path):
    """(model, champion record, None) when the champion can score; (None, None, why) when it was named but can't;
    (None, None, None) when there's no champion.json (no model shipped yet: today's rule, nothing to say)."""
    path = Path(path) if path else None
    if path is None or not path.is_file():
        return None, None, None
    try:
        record = json.loads(path.read_text(encoding="utf-8"))
        name, md5 = record["model_file"], record["model_md5"]
        float(record["metrics"]["at_threshold"]["threshold"])
    except (ValueError, KeyError, TypeError) as e:
        return None, None, f"champion.json doesn't read ({type(e).__name__})"
    model_path = path.parent / name
    if not model_path.is_file():
        return None, None, f"{name} isn't next to champion.json"
    if _md5(model_path) != md5:
        return None, None, f"{name}'s md5 isn't champion.json's"
    try:
        import joblib
        model = joblib.load(model_path)
    except Exception as e:      # a missing package (lightgbm), a corrupt file: never stops the push check
        return None, None, f"the model didn't load ({type(e).__name__})"
    return model, record, None


def _model_modules():
    """Model-Pipeline's data.py and train.py: the same feature code the model was trained with."""
    if str(MODEL_PIPELINE) not in sys.path:
        sys.path.append(str(MODEL_PIPELINE))   # last: both pipelines have a tests package
    from model import data, train
    return data, train


def push_features(db, repo, run_id, at, numstat, messages, author, key_regex, prior_fix_days):
    """The push as one Contract 1 row (a one-row DataFrame), from the block's history as data.history reads it."""
    import pandas as pd
    data, _ = _model_modules()
    q = lambda sql, *p: pd.read_sql(sql, db, params=[run_id, *p])
    commits = q("SELECT sha, parent_sha, seq, is_merge, authored_at, author_email, subject FROM main.git_commit "
                "WHERE run_id = ? AND repo = ? AND first_parent = 1 ORDER BY seq", repo)
    files = q("SELECT sha, path, old_path, added, deleted FROM main.commit_file WHERE run_id = ? AND repo = ?", repo)
    tickets = q("SELECT ct.sha, ct.key, t.effective_type, t.resolution, t.bulk_closed, t.components "
                "FROM main.commit_ticket ct JOIN main.ticket t ON t.run_id = ct.run_id AND t.key = ct.key "
                "WHERE ct.run_id = ? AND ct.repo = ?", repo)
    alias = q("SELECT email, person_id FROM main.person_alias WHERE run_id = ?")
    known_types = dict(db.execute("SELECT key, effective_type FROM main.ticket WHERE run_id = ?", (run_id,)).fetchall())

    subject = messages[0][1] if messages else ""
    push = pd.DataFrame([{"sha": "push", "parent_sha": None, "seq": len(commits), "is_merge": 0, "authored_at": at,
                          "author_email": author or "", "subject": subject}])
    push_files = pd.DataFrame([{"sha": "push", "path": p, "old_path": None, "added": a, "deleted": d}
                               for p, a, d in numstat])
    commits = pd.concat([commits, push], ignore_index=True)
    files = pd.concat([files, push_files], ignore_index=True)
    person = dict(zip(alias.email, alias.person_id))
    ids = {p: i for i, p in enumerate(sorted(set(person.values())))}
    who = {e: ids[p] for e, p in person.items()}
    for e in sorted(set(commits.author_email) - set(who)):    # an email the block hasn't seen: a new person each
        who[e] = len(ids) + len(who)
    rule, since = data.fix_rule()
    fixes = data.fix_commits(commits, tickets, rule, since)
    feats = data.history(commits, files, who, fixes, prior_fix_days)["push"]

    rx = re.compile(key_regex)
    keys = set()
    for _, s, body in messages:                                # as P8 reads keys: the subject's, else the body's
        keys |= set(rx.findall(s)) or set(rx.findall(body))
    keys = sorted(k for k in keys if k in known_types)
    row = dict(feats)
    row.update(fix=int(any(known_types[k] == "Bug" for k in keys)), has_ticket=int(bool(keys)),
               ticket_type=known_types[keys[0]] if keys else "none",
               is_minor=int(bool(messages) and all(data.MINOR.match(s) for _, s, _ in messages)))
    return pd.DataFrame([row])


def model_repo():
    cfg = tomllib.loads((MODEL_PIPELINE / "model.toml").read_text(encoding="utf-8"))
    return cfg["data"]["repo"], int(cfg["data"]["prior_fix_days"])


def verdict(db, repo, run_id, at, numstat, messages, author, key_regex, lines_x_files, threshold, champion):
    """P9 for the push. lines_x_files: (lines, files, lines × files) from p9_defect_risk; threshold: [push_check]
    defect_risk_threshold; champion: the champion.json path, or None for no model."""
    lines, files, risk = lines_x_files
    model, record, why = load_champion(champion)
    if model is not None:
        trained_on, prior_fix_days = model_repo()
        if repo != trained_on:
            model, why = None, None            # not this model's repo: today's rule, nothing went wrong
    if model is not None:
        try:
            _, train = _model_modules()
            row = push_features(db, repo, run_id, at, numstat, messages, author, key_regex, prior_fix_days)
            score = float(train.score_with(model, row)[0])
        except Exception as e:                 # never stops the push check: say so, use today's rule
            model, why = None, f"the model couldn't score this push ({type(e).__name__})"
    if model is not None:
        bar = float(record["metrics"]["at_threshold"]["threshold"])
        version = str(record.get("version", ""))[:10]
        kind = type(model[-1] if hasattr(model, "steps") else model).__name__
        text = (f"defect risk {score:.2f} from the champion model ({kind}, version {version}), over its threshold "
                f"{bar:.2f}; {lines} lines × {files} files")
        return Verdict("model", score > bar, text)
    alert = risk is not None and risk > threshold
    text = f"{lines} lines × {files} files = {risk}, over {threshold}"
    if why:
        text += f" (lines × files: {why})"
    return Verdict("lines_x_files", alert, text, why)
