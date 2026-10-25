"""What every part shares: the command line, the config, git, jsonl, and where the data lives. See CONTRACTS.md, "Every part runs as one command".

- Data (snapshots/, out/) goes under $MERIDIAN_DATA. Without it, under the repo root, where a laptop run keeps it.
- Logs go to stdout. A part that fails exits non-zero (SystemExit with the reason, or the exception).
- meridian.toml is what a part computes (hashed into the run_id). settings.toml is everything else (how and where it
  runs, alerts, data checks): never hashed.
- A cache (settings.toml [cache], off by default) keeps a part's results for inputs that didn't change, in
  snapshots/_cache/<part>/, never hashed into a run. On or off, a part writes the same bytes.
"""
import argparse, hashlib, json, os, subprocess, tomllib
from pathlib import Path

HERE = Path(__file__).resolve().parents[1]  # Data-Pipeline/
CONFIG = HERE / "meridian.toml"
SETTINGS = HERE / "settings.toml"
SCHEMA = HERE / "schema.sql"

# Settings in meridian.toml that the code follows but has no switch for. A changed value stops the run,
# rather than being silently ignored. Only keys present in the config are checked (fixtures leave most out).
FIXED = {
    ("run", "id_hash"): "sha256", ("run", "id_length"): 16, ("run", "time_format"): "%Y-%m-%dT%H:%M:%SZ",
    ("identity", "use_mailmap"): True, ("identity", "merge_on"): ["name", "email_local"],
    ("identity", "lowercase_emails"): True, ("identity", "use_author_not_committer"): True,
    ("identity", "coauthors_count"): False, ("ownership", "skip_merges"): True,
    ("ownership", "owner"): "team_share", ("ownership", "tie_break"): "most_recent_commit",
    ("tickets", "key_must_exist"): True, ("tickets", "key_sources"): ["subject", "body"],
    ("tickets", "fields_as_of_snapshot"): ["status", "resolution", "resolved_at"],
    ("szz", "blame_at"): "parent", ("szz", "skip_whitespace"): True, ("szz", "skip_comments"): True,
    ("scanner", "tree_sitter_python"): "pinned", ("resolver", "tool"): "jedi", ("resolver", "env"): "snapshot",
    ("graph", "imports_count_as_edges"): False,
}


def data_root():
    return Path(os.environ.get("MERIDIAN_DATA") or HERE.parent)


def check_fixed(cfg, name="meridian.toml"):
    bad = [f"[{s}] {k} = {cfg[s][k]!r}, the code does {v!r}" for (s, k), v in FIXED.items()
           if k in cfg.get(s, {}) and cfg[s][k] != v]
    if bad:
        raise SystemExit(f"{name} asks for what the code doesn't do: " + "; ".join(bad))


def load_config(path):
    cfg = tomllib.loads(Path(path).read_text(encoding="utf-8"))
    check_fixed(cfg, Path(path).name)
    return cfg


def load_settings(section, path=None):
    """One [section] of settings.toml ($MERIDIAN_SETTINGS, or path, points at another copy). Never changes a row."""
    return tomllib.loads(Path(path or os.environ.get("MERIDIAN_SETTINGS") or SETTINGS).read_text(encoding="utf-8")).get(section, {})


load_infra = load_settings  # the name collect and history use: settings.toml was infra.toml


def cache_on(name):
    """settings.toml [cache] <name>, except under MERIDIAN_NO_CACHE: the weekly rebuild must not reuse what it checks."""
    return bool(load_settings("cache").get(name, False)) and not os.environ.get("MERIDIAN_NO_CACHE")


def git(co, *args, text=True):
    """git in repo co, deaf to the user's global and system git config, so every machine gives the same output."""
    env = {**os.environ, "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1"}
    # safe.directory: the global config that usually trusts snapshot checkouts owned by another user is ignored here
    out = subprocess.run(["git", "-C", str(co), "-c", "core.quotePath=false", "-c", "safe.directory=*", *args],
                         check=True, capture_output=True, env=env).stdout
    return out.decode("utf-8", "replace") if text else out


def write_jsonl(path, rows, key=None):
    """One JSON object per line, '\\n' endings on every OS, sorted by key if given. Atomic: tmp then rename."""
    if key:
        rows = sorted(rows, key=lambda r: tuple(r[k] for k in key))
    tmp = path.with_name(path.name + ".tmp")
    try:
        with open(tmp, "w", encoding="utf-8", newline="\n") as f:
            for r in rows:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")
        tmp.replace(path)
    finally:
        tmp.unlink(missing_ok=True)  # a killed write must not leave people's data in a stray file


def read_jsonl(path):
    with open(path, encoding="utf-8", newline="") as f:  # split on '\n' only: U+2028 inside a value is not a line end
        return [json.loads(line) for line in f.read().split("\n") if line.strip()]


def blob_sha(data):
    """git's blob SHA of these bytes: what `git hash-object` gives for the file."""
    return hashlib.sha1(b"blob %d\0" % len(data) + data).hexdigest()


def cache_key(*parts):
    return hashlib.sha256(json.dumps(parts, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def source_hash(*files):
    """sha256 of these source files: an entry another version of the code made is never reused."""
    return hashlib.sha256(b"\0".join(Path(f).read_bytes() for f in files)).hexdigest()


class Cache:
    """{key: value} a part kept from its last run, in snapshots/_cache/<part>/<name>.json under the data root.
    get() hands back a value only if ok(value), and lists its key in hits. save() keeps only what this run used or
    made, so nothing stale piles up. Values keep their key order, so a cached row is written in the same bytes."""

    def __init__(self, part, name, root=None):
        self.path = Path(root or data_root()) / "snapshots" / "_cache" / part / f"{name.replace('/', '__')}.json"
        self.old = json.loads(self.path.read_text(encoding="utf-8")) if self.path.exists() else {}
        self.new, self.hits = {}, []

    def get(self, key, ok=lambda value: True):
        value = self.old.get(key)
        if value is None or not ok(value):
            return None
        self.hits.append(key)
        self.new[key] = value
        return value

    def put(self, key, value):
        self.new[key] = value

    def save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_name(self.path.name + ".tmp")
        try:
            tmp.write_text(json.dumps(self.new, ensure_ascii=False), encoding="utf-8")
            tmp.replace(self.path)
        finally:
            tmp.unlink(missing_ok=True)  # a killed write leaves the last good cache, never half a file


def handoff(cfg, run_id, root=None):
    """out/<run_id>/: one <table>.jsonl per table, loaded by the writer."""
    return Path(root or data_root()) / cfg["run"]["handoff_dir"].format(run_id=run_id)


def log(*a):
    print(*a, flush=True)


def parser(doc, run_id_help="the run to work on: snapshots/<run_id>/ must exist"):
    ap = argparse.ArgumentParser(description=doc.split("\n")[0])
    ap.add_argument("--run-id", required=True, help=run_id_help)
    ap.add_argument("--config", type=Path, default=CONFIG, help="meridian.toml")
    return ap
