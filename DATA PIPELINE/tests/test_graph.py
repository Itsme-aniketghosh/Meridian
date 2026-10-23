"""Graph tests: the KB fixture's hand-filled blocked_by, blast_radius and chain rows, now computed by meridian.graph
from that fixture's inputs; then one fixture per rule (depth, owners, unknown, max depth, cycles, each edge once).

    python -m unittest tests.test_graph -v      # from DATA PIPELINE/
"""
import json, shutil, sqlite3, sys, tempfile, textwrap, unittest
from contextlib import closing, redirect_stdout
from io import StringIO
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import meridian.graph as G
import meridian.writer as W
import tests.test_kb as K
from meridian.common import write_jsonl

CONFIG = textwrap.dedent("""\
    [run]
    handoff_dir = "out/{run_id}"

    [graph]
    blocked_by_max_depth = 3
    blocked_by_needs_different_owners = true
    imports_count_as_edges = false
    blast_radius_max_depth = 3

    [rules]
    done_statuses = ["Done", "Closed", "Resolved"]
    """)


class KnowledgeBase(unittest.TestCase):
    """The KB proof filled graph's tables by hand. Graph must give exactly those rows from the same inputs."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp())
        (cls.tmp / "meridian.toml").write_text(CONFIG)
        cls.out = cls.tmp / "out" / "R2"
        cls.out.mkdir(parents=True)
        with closing(K.build()) as db:
            db.row_factory = sqlite3.Row
            for name in G.NEED:
                write_jsonl(cls.out / f"{name}.jsonl", [dict(r) for r in db.execute(f"SELECT * FROM {name}")])
            cls.want = {name: sorted((dict(r) for r in db.execute(f"SELECT * FROM {name}")), key=lambda r: [r[k] for k in G.KEYS[name]])
                        for name in G.KEYS}
        with redirect_stdout(StringIO()):
            cls.got = dict(zip(G.KEYS, G.main("R2", cls.tmp / "meridian.toml", cls.tmp)))

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def same(self, name):
        self.assertEqual(sorted(self.got[name], key=lambda r: [r[k] for k in G.KEYS[name]]), self.want[name])

    def test_blocked_by(self):
        self.same("blocked_by")

    def test_blast_radius(self):
        self.same("blast_radius")

    def test_chain(self):
        self.same("chain")

    def test_loads_with_the_writer(self):
        block = self.tmp / "block" / "R2"
        block.mkdir(parents=True)
        for name in G.KEYS:
            shutil.copy(self.out / f"{name}.jsonl", block)
        W.write(block, self.tmp / "block.sqlite")
        with closing(sqlite3.connect(self.tmp / "block.sqlite")) as db:
            self.assertEqual([db.execute(f"SELECT count(*) FROM {n}").fetchone()[0] for n in G.KEYS], [1, 1, 7])

    def test_rerun_gives_the_same_bytes(self):
        before = {p.name: p.read_bytes() for p in self.out.glob("*.jsonl")}
        with redirect_stdout(StringIO()):
            G.main("R2", self.tmp / "meridian.toml", self.tmp)
        self.assertEqual(before, {p.name: p.read_bytes() for p in self.out.glob("*.jsonl")})


def site(sid, path, scope, team, detection="ast_resolved", line=1):
    return dict(call_site_id=sid, repo="r", path=path, line=line, col=4, scope=scope, symbol="s", detection=detection,
                owner_team=team, status="present")


def edge(caller_path, caller_scope, line, callee_path, callee_scope):
    return dict(repo="r", caller_path=caller_path, caller_scope=caller_scope, line=line, col=4, callee_repo="r",
                callee_path=callee_path, callee_scope=callee_scope)


def recv(s, fn):
    return dict(repo="r", path=s["path"], line=s["line"], col=4, symbol="s", detection=s["detection"], receiver_from=fn)


class BlockedBy(unittest.TestCase):
    # A's receiver comes from make(); make() calls build(), build() calls deep(): B sits in each
    A = site("A", "app.py", "main", "web")
    IN_MAKE, IN_BUILD, IN_DEEP = site("B1", "lib.py", "make", "core"), site("B2", "lib.py", "build", "core"), site("B3", "lib.py", "deep", "core")
    EDGES = [edge("lib.py", "make", 5, "lib.py", "build"), edge("lib.py", "build", 9, "lib.py", "deep")]

    def run_rule(self, sites, a=None, max_depth=3, different=True):
        a = a or self.A
        rows, _ = G.build_blocked_by("R", [a, *sites], [recv(a, "r:lib.py:make")], self.EDGES, max_depth, different)
        return {(r["blocker_id"], r["depth"], r["status"]) for r in rows}

    def test_inside_f_and_within_its_calls(self):
        self.assertEqual(self.run_rule([self.IN_MAKE, self.IN_BUILD, self.IN_DEEP]),
                         {("B1", 1, "resolved"), ("B2", 2, "resolved"), ("B3", 3, "resolved")})

    def test_max_depth(self):
        self.assertEqual(self.run_rule([self.IN_MAKE, self.IN_BUILD, self.IN_DEEP], max_depth=2),
                         {("B1", 1, "resolved"), ("B2", 2, "resolved")})

    def test_same_or_unowned_owner_is_no_block(self):
        self.assertEqual(self.run_rule([site("B1", "lib.py", "make", "web"), site("B2", "lib.py", "build", "unowned")]), set())
        self.assertEqual(self.run_rule([site("B1", "lib.py", "make", "web")], different=False), {("B1", 1, "resolved")})

    def test_unresolved_site_makes_it_unknown(self):
        self.assertEqual(self.run_rule([site("B1", "lib.py", "make", "core", "ast_unresolved")]), {("B1", 1, "unknown")})
        self.assertEqual(self.run_rule([self.IN_MAKE], a=site("A", "app.py", "main", "web", "ast_unresolved")), {("B1", 1, "unknown")})

    def test_no_receiver_no_block(self):
        rows, _ = G.build_blocked_by("R", [self.A, self.IN_MAKE], [], self.EDGES, 3, True)
        self.assertEqual(rows, [])


class BlastRadius(unittest.TestCase):
    def test_each_edge_once_at_its_shortest_depth(self):  # f calls the symbol; g calls f; f calls g back
        res = [dict(repo="r", path="a.py", line=1, col=4, symbol="s", detection="ast_resolved")]
        edges = [edge("a.py", "f", 1, "home.py", "s"), edge("b.py", "g", 2, "a.py", "f"), edge("a.py", "f", 3, "b.py", "g")]
        row, = G.build_blast_radius("R", [site("S", "a.py", "f", "t")], res, edges, 5)
        self.assertEqual((row["direct_sites"], row["invocations"], row["files"]), (1, 3, 2))
        self.assertEqual(json.loads(row["by_depth"]), {"1": 1, "2": 1, "3": 1, "4": 0, "5": 0})


class Chain(unittest.TestCase):
    def test_cycle_is_flagged_only_on_its_edges(self):  # X waits on Y, Y on X; Z waits on X
        sites = {s["call_site_id"]: s for s in [site(i, "p.py", "f", "t") for i in ("a", "b", "c", "d", "e")]}
        blocked = [dict(call_site_id="a", blocker_id="b"), dict(call_site_id="c", blocker_id="d"), dict(call_site_id="e", blocker_id="a")]
        tmap = [dict(key=k, target_kind="call_site", target=s) for k, s in [("X", "a"), ("Y", "b"), ("Y", "c"), ("X", "d"), ("Z", "e")]]
        tickets = [dict(key=k, status="Done") for k in "XYZ"]
        rows = G.build_chain("R", sites, list(sites.values()), blocked, tmap, tickets, {"Done"})
        got = {(r["from_key"], r["to_key"]): r["in_cycle"] for r in rows if r["type"] == "depends_on"}
        self.assertEqual(got, {("X", "Y"): 1, ("Y", "X"): 1, ("Z", "X"): 0})
        self.assertFalse([r for r in rows if r["type"] == "collides"])  # all Done: no collisions


if __name__ == "__main__":
    unittest.main()
