"""The endpoint's scorer (S2, Rachna): the champion, the same features as training, and nothing about a person out.

A request is scored one of two ways, both with the champion named by Model-Pipeline/registry/champion.json (its
model file's md5 checked at start; a mismatch refuses to start):

- a known commit, {"repo", "sha"}: its row of the feature table (Model-Pipeline/data/features.parquet, Contract 1),
  so its risk is the one the model was judged on (val's scores in reports/champion/champion_val_scores.csv)
- a new push, {"repo", "numstat", "subject", "author_email_hash"}: Data-Pipeline/meridian/p9.py's push_features on the
  base block, the push check's own code path, so serving can't drift from training. The block's history is read
  once at start (p9.load_history), not per request

The author comes in as sha256 of the lower-cased email and is matched against the block's aliases hashed the same
way, inside this process only: no email is ever received, logged or returned. A repo the model wasn't trained on
(model.toml [data] repo) is refused. The P9 verdict is the push check's: decision 15b's rule when adopted for this
champion, else lines × files above [push_check] defect_risk_threshold (P9 is LLM-free and stays lines × files).
"""
import bisect
import hashlib
import json
import sqlite3
import sys
import tomllib
from contextlib import closing
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]                   # the repo
DATA_PIPELINE = ROOT / "Data-Pipeline"
MODEL_PIPELINE = ROOT / "Model-Pipeline"
if str(DATA_PIPELINE) not in sys.path:
    sys.path.insert(0, str(DATA_PIPELINE))

from meridian import p9  # noqa: E402

DEFAULTS = {
    "champion": MODEL_PIPELINE / "registry" / "champion.json",
    "features": MODEL_PIPELINE / "data" / "features.parquet",
    "val_scores": MODEL_PIPELINE / "reports" / "champion" / "champion_val_scores.csv",
    "rule": MODEL_PIPELINE / "reports" / "p9_rule.json",
    "settings": DATA_PIPELINE / "settings.toml",
    "config": DATA_PIPELINE / "meridian.toml",
}
MAX_FILES = 5000           # a push with more changed files than this is refused (the request size cap is in app.py)


class BadRequest(ValueError):
    """400: the body isn't a scorable request."""


class WrongRepo(ValueError):
    """422: a repo the model wasn't trained on."""


def email_hash(email):
    """How a caller hashes an author: sha256 of the trimmed, lower-cased email, hex."""
    return hashlib.sha256(email.strip().lower().encode("utf-8")).hexdigest()


def _md5(path):
    return hashlib.md5(Path(path).read_bytes()).hexdigest()


class Scorer:
    def __init__(self, champion=None, features=None, val_scores=None, block=None, run_id=None, rule=None,
                 settings=None, config=None):
        import pandas as pd
        paths = {k: Path(v) for k, v in DEFAULTS.items()}
        given = dict(champion=champion, features=features, val_scores=val_scores, rule=rule, settings=settings,
                     config=config)
        paths.update({k: Path(v) for k, v in given.items() if v})
        model, record, why = p9.load_champion(paths["champion"])
        if model is None:
            raise SystemExit(f"won't start: {why or 'no champion.json at ' + str(paths['champion'])}")
        self.model, self.record = model, record
        self.version, self.md5 = str(record.get("version", "")), record["model_md5"]
        _, self.train = p9._model_modules()
        self.repo, self.prior_fix_days = p9.model_repo()

        table = pd.read_parquet(paths["features"]) if paths["features"].suffix == ".parquet" else \
            pd.read_csv(paths["features"])
        self.table = table.set_index("sha", drop=False)
        self.features_version = _md5(paths["features"])
        scores = pd.read_csv(paths["val_scores"])
        column = "forest" if "forest" in scores.columns else scores.columns[-1]
        self.val = sorted(scores[column].astype(float).tolist())

        push_cfg = tomllib.loads(paths["settings"].read_text(encoding="utf-8"))["push_check"]
        self.threshold = float(push_cfg["defect_risk_threshold"])
        self.rule, _ = p9.load_rule(paths["rule"], self.md5)
        cfg = tomllib.loads(paths["config"].read_text(encoding="utf-8"))
        ds = next((d for d in cfg["datasets"].values() if any(r["repo"] == self.repo for r in d.get("repos", []))),
                  {})
        generic = cfg.get("tickets", {}).get("key_regex_generic", r"\b[A-Z][A-Z0-9]+-\d+\b")
        self.key_regex = ds.get("key_regex") or generic
        self.block = None
        if block:                                                # pushes need the base block; known SHAs don't
            self._load_block(Path(block), run_id)

    # ---------- the block, once ----------
    def _load_block(self, path, run_id):
        with closing(sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)) as db:
            if run_id is None:
                rows = db.execute("SELECT run_id, snapshot_at FROM run").fetchall()
                if len(rows) != 1:
                    raise SystemExit(f"{path} holds {len(rows)} runs: name one with MERIDIAN_RUN_ID")
                run_id, at = rows[0]
            else:
                at = db.execute("SELECT snapshot_at FROM run WHERE run_id = ?", (run_id,)).fetchone()[0]
            self.history = p9.load_history(db, self.repo, run_id)
        alias = self.history["alias"]
        self.by_hash = {email_hash(e): e for e in alias["email"].dropna()}     # stays in this process
        self.block = {"path": str(path), "run_id": run_id, "at": at}

    # ---------- scoring ----------
    def percentile(self, risk):
        return bisect.bisect_right(self.val, risk) / len(self.val) if self.val else None

    def p9_verdict(self, risk, lines, files):
        lxf = lines * files
        if self.rule is not None:                                # 15b adopted for this champion: both must flag
            alert = risk >= float(self.rule["model_cutoff"]) and lxf >= float(self.rule["lxf_cutoff"])
            return {"rule": "model", "alert": bool(alert)}
        return {"rule": "lines_x_files", "alert": bool(lxf > self.threshold)}

    def score(self, body):
        """(response dict, log fields) for a request body (a dict). Raises BadRequest or WrongRepo."""
        if not isinstance(body, dict):
            raise BadRequest("the body must be a JSON object")
        repo = body.get("repo")
        if not isinstance(repo, str) or not repo:
            raise BadRequest("'repo' is required")
        if repo != self.repo:
            raise WrongRepo(f"the model only scores {self.repo}, the repo it was trained on")
        if "sha" in body:
            row, lines, files, kind = self._known(body["sha"])
        elif "numstat" in body:
            row, lines, files, kind = self._push(body)
        else:
            raise BadRequest("give either 'sha' (a commit in the block) or 'numstat' (a new push)")
        risk = float(self.train.score_with(self.model, row)[0])
        response = {"risk": risk, "percentile_val": self.percentile(risk), "p9": self.p9_verdict(risk, lines, files),
                    "model": {"version": self.version, "md5": self.md5}, "features_version": self.features_version}
        cols = self.train.input_columns(self.model)
        values = self.train.inputs_for(self.model, row).iloc[0]
        feats = {c: float(values[c]) for c in cols if not c.startswith("ticket_type=")}
        return response, {"kind": kind, "features": feats, "risk": risk}

    def _known(self, sha):
        if not isinstance(sha, str) or not sha:
            raise BadRequest("'sha' must be a commit SHA")
        if sha not in self.table.index:
            raise BadRequest(f"{sha[:12]} isn't in the feature table (Spark first-parent commits from 2015)")
        row = self.table.loc[[sha]]
        r = row.iloc[0]
        return row, int(r["la"]) + int(r["ld"]), int(r["nf"]), "sha"

    def _push(self, body):
        if self.block is None:
            raise BadRequest("this server has no base block, so it scores known SHAs only")
        numstat = body.get("numstat")
        if not isinstance(numstat, list) or not numstat:
            raise BadRequest("'numstat' must be a non-empty list of [added, deleted, path]")
        if len(numstat) > MAX_FILES:
            raise BadRequest(f"{len(numstat)} files: over the {MAX_FILES}-file cap")
        rows = []
        for item in numstat:
            if not (isinstance(item, list) and len(item) == 3 and isinstance(item[2], str) and item[2]):
                raise BadRequest("each numstat entry must be [added, deleted, path]")
            a, d, path = item
            for v in (a, d):                                    # None = a binary file, as git numstat's "-"
                if v is not None and (not isinstance(v, int) or isinstance(v, bool) or v < 0):
                    raise BadRequest("added and deleted must be counts (or null for a binary file)")
            rows.append((path, a, d))
        subject = body.get("subject", "")
        if not isinstance(subject, str):
            raise BadRequest("'subject' must be a string")
        h = body.get("author_email_hash")
        if h is not None and not (isinstance(h, str) and len(h) == 64 and all(c in "0123456789abcdef" for c in h)):
            raise BadRequest("'author_email_hash' must be the sha256 hex of the lower-cased email (never the email)")
        author = self.by_hash.get(h, h or "")                   # unknown: a new author, as the push check reads one
        row = p9.push_features(None, self.repo, self.block["run_id"], self.block["at"], rows, [("push", subject, "")],
                               author, self.key_regex, self.prior_fix_days, history=self.history)
        lines = sum((a or 0) + (d or 0) for _, a, d in rows if a is not None and d is not None)
        return row, lines, len(rows), "push"

    def health(self):
        return {"ok": True, "model": {"version": self.version, "md5": self.md5}}


def describe(s):
    return json.dumps({"model": s.version[:10], "md5": s.md5[:8], "repo": s.repo, "block": bool(s.block),
                       "features": s.features_version[:8], "val_scores": len(s.val)})
