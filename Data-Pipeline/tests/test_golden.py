"""Golden tests: the team run is c5118b019d642a9b (v1 was 70dd1dabfed544b8). Any change to an output's bytes fails here
until it is meant.

1. Everywhere (CI too): the KB fixture through graph and the writer gives exactly the bytes in golden/fixture.json,
   the sha256 of every jsonl and of every SQLite table's rows (not the .sqlite file: its header holds the SQLite
   version).
2. The team run, only with MERIDIAN_RUN_ID=c5118b019d642a9b and out/c5118b019d642a9b/ present: every jsonl has the
   md5 and size in golden/c5118b019d642a9b.json, which are dvc.lock's values from the pipeline image (run.jsonl left
   out: its image_digest differs per machine by design). Every mismatching file is listed, not just the first.

    python -m unittest tests.test_golden -v                 # from Data-Pipeline/
    python -m tests.test_golden --update                    # rewrites golden/fixture.json: only for a meant change

A team run's golden file is never regenerated: a new contract means a new team run_id and a new golden file.
golden/70dd1dabfed544b8.json stays as the v1 record. c5118b019d642a9b is the same snapshot (pinned SHAs and ticket
pulls) under the meridian.toml of the Oct 27 fixes (owner merge rule, join, SZZ reverts).
"""
import hashlib
import json
import os
import shutil
import sqlite3
import sys
import tempfile
import unittest
from contextlib import closing, redirect_stdout
from io import StringIO
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import meridian.graph as G
import meridian.writer as W
from meridian.common import CONFIG as RUN_CONFIG, HERE, SCHEMA, data_root, handoff, load_config
from tests.test_dashboard import fixture_block
from tests.test_graph import CONFIG

GOLDEN = HERE / "golden"
TEAM_RUN = "c5118b019d642a9b"


def fixture_hashes():
    """{"jsonl": {file: sha256}, "sqlite": {table: sha256 of its rows as JSON, every column sorted}} for the KB
    fixture."""
    tmp = Path(tempfile.mkdtemp())
    try:
        (tmp / "meridian.toml").write_text(CONFIG)
        out = fixture_block(tmp)
        with redirect_stdout(StringIO()):
            G.main("R2", tmp / "meridian.toml", tmp)
        W.write(out, tmp / "block.sqlite")
        jsonl = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(out.glob("*.jsonl"))}
        with closing(sqlite3.connect(tmp / "block.sqlite")) as db:
            rows = {}
            for t in sorted(W.tables(SCHEMA.read_text(encoding="utf-8"))):
                n = len(db.execute(f"SELECT * FROM {t} LIMIT 0").description)
                got = db.execute(f"SELECT * FROM {t} ORDER BY {', '.join(map(str, range(1, n + 1)))}").fetchall()
                rows[t] = hashlib.sha256(json.dumps(got, ensure_ascii=False).encode()).hexdigest()
        return {"jsonl": jsonl, "sqlite": rows}
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def mismatches(want, got):
    return [f"{k}: want {want.get(k)}, got {got.get(k)}"
            for k in sorted(set(want) | set(got)) if want.get(k) != got.get(k)]


class Fixture(unittest.TestCase):
    def test_same_bytes_as_golden(self):
        want = json.loads((GOLDEN / "fixture.json").read_text(encoding="utf-8"))
        got = fixture_hashes()
        bad = [f"{part} {m}" for part in ("jsonl", "sqlite") for m in mismatches(want[part], got[part])]
        self.assertEqual(bad, [], "fixture output changed; if meant: python -m tests.test_golden --update")


TEAM_OUT = handoff(load_config(RUN_CONFIG), TEAM_RUN, data_root())


@unittest.skipUnless(os.environ.get("MERIDIAN_RUN_ID") == TEAM_RUN and TEAM_OUT.is_dir(),
                     f"needs MERIDIAN_RUN_ID={TEAM_RUN} and {TEAM_OUT} (team data, never in CI)")
class TeamRun(unittest.TestCase):
    def test_every_output_as_frozen(self):
        want = json.loads((GOLDEN / f"{TEAM_RUN}.json").read_text(encoding="utf-8"))
        got = {}
        for name in want:
            p = TEAM_OUT / name
            if p.exists():
                with open(p, "rb") as f:  # streamed: commit_file.jsonl is ~190 MB
                    got[name] = {"md5": hashlib.file_digest(f, "md5").hexdigest(), "size": p.stat().st_size}
        self.assertEqual(mismatches(want, got), [])


if __name__ == "__main__":
    if "--update" in sys.argv:
        (GOLDEN / "fixture.json").write_text(json.dumps(fixture_hashes(), indent=1) + "\n",
                                             encoding="utf-8", newline="\n")
        print(f"wrote {GOLDEN / 'fixture.json'}")
    else:
        unittest.main()
