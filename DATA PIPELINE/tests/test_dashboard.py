"""The dashboard renders on the KB fixture, after graph: every planned query runs, none errors.

    python -m unittest tests.test_dashboard -v      # from DATA PIPELINE/
"""
import shutil, sqlite3, sys, tempfile, unittest
from contextlib import closing, redirect_stdout
from io import StringIO
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import meridian.graph as G
import meridian.writer as W
import tests.dashboard as D
import tests.test_kb as K
from meridian.common import SCHEMA, write_jsonl
from tests.test_graph import CONFIG


def fixture_block(root):
    """out/R2/ as the parts would write it, from the KB fixture: graph's own tables left for graph to compute."""
    out = root / "out" / "R2"
    out.mkdir(parents=True)
    with closing(K.build()) as db:
        db.row_factory = sqlite3.Row
        for name in W.tables(SCHEMA.read_text(encoding="utf-8")):
            if name not in G.KEYS:
                rows = [dict(r) for r in db.execute(f"SELECT * FROM {name}")]
                write_jsonl(out / f"{name}.jsonl", [r for r in rows if name == "call_site" or r["run_id"] == "R2"])
    return out


class Dashboard(unittest.TestCase):
    def test_renders_with_every_query_answered(self):
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, True)
        (tmp / "meridian.toml").write_text(CONFIG)
        fixture_block(tmp)
        with redirect_stdout(StringIO()) as log:
            G.main("R2", tmp / "meridian.toml", tmp)
            path = D.main("R2", tmp / "meridian.toml", tmp)
        page = path.read_text(encoding="utf-8")
        self.assertIn("Planned features", page)
        self.assertIn(" 0 error", log.getvalue())  # the doc's queries all run on a real block's layout


if __name__ == "__main__":
    unittest.main()
