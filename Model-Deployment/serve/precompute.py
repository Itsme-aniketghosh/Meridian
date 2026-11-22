"""The block's history, replayed once into a state file the image ships (S2, Rachna): pushes score in milliseconds.

    python -m serve.precompute --block ../out/<run_id>/meridian.sqlite     # from Model-Deployment/: serve/history.pkl

The endpoint used to replay the whole block for every push (1.88 s on a laptop, against the 2 s p95 bar). This
replays it once (p9.precompute, on model/data.py's History: the same code as training's features) and writes the
state the push's features are read from. The image ships this file and serves with MERIDIAN_HISTORY, so it needs
neither the block nor GCS at start. Authors in the state are email hashes: the file holds no email.
"""
import argparse
import hashlib
import pickle
import sys
import time
from pathlib import Path

from serve import scorer as S


def build(block, out, run_id=None):
    t0 = time.perf_counter()
    repo, prior_fix_days = S.p9.model_repo()
    state = S.state_from_block(block, run_id, repo, prior_fix_days)
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, "wb") as f:
        pickle.dump(state, f, protocol=pickle.HIGHEST_PROTOCOL)
    return state, out, time.perf_counter() - t0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--block", required=True, type=Path)
    ap.add_argument("--run-id")
    ap.add_argument("--out", type=Path, default=Path(__file__).resolve().parent / "history.pkl")
    a = ap.parse_args(argv)
    state, out, took = build(a.block, a.out, a.run_id)
    md5 = hashlib.md5(out.read_bytes()).hexdigest()
    print(f"replayed {state['commits']:,} {state['repo']} commits of run {state['run_id']} to {state['at']} in "
          f"{took:.1f} s: {out} ({out.stat().st_size / 1e6:.1f} MB, md5 {md5})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
