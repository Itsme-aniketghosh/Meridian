"""What every part shares: the command line, the config, and where the data lives. See CONTRACTS.md, "Every part runs as one command".

- Data (snapshots/, out/) goes under $MERIDIAN_DATA. Without it, under the repo root, where a laptop run keeps it.
- Logs go to stdout. A part that fails exits non-zero (SystemExit with the reason, or the exception).
"""
import argparse, os, tomllib
from pathlib import Path

HERE = Path(__file__).resolve().parents[1]  # DATA PIPELINE/
CONFIG = HERE / "meridian.toml"
SCHEMA = HERE / "schema.sql"


def data_root():
    return Path(os.environ.get("MERIDIAN_DATA") or HERE.parent)


def load_config(path):
    return tomllib.loads(Path(path).read_text())


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
