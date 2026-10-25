"""Change report tests: a hand-made two-run block (acme/shop) where four commits landed between R1 and R2, batched
into one run. Each change must come out under the commit that caused it.

    python -m unittest tests.test_report -v      # from Data-Pipeline/
"""
import contextlib, io, json, shutil, sys, tempfile, unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import meridian.report as RP
from meridian.common import write_jsonl

REPO = "acme/shop"
C1, C2, C3, C4 = ("1" * 40, "start"), ("2" * 40, "SHOP-1 a new call"), ("3" * 40, "drop the old call"), ("4" * 40, "move k")
COMMITS = [dict(run_id="R2", repo=REPO, sha=sha, parent_sha=None, seq=i, first_parent=1, is_merge=0,
                authored_at=f"2026-0{i + 1}-01T00:00:00Z", author_email="ann@example.com", committer_email="ann@example.com",
                subject=subject) for i, (sha, subject) in enumerate((C1, C2, C3, C4))]
R1, R2 = ("R1", "2026-01-15T00:00:00Z"), ("R2", "2026-05-15T00:00:00Z")


def site(sid, run, path, line, scope, eff, status="present"):
    return dict(call_site_id=sid, run_id=run[0], repo=REPO, path=path, line=line, col=4, scope=scope, symbol="ugettext",
                role="call", detection="ast_resolved", junk="none", in_except=0, owner_team="checkout", bug_fixes=0,
                status=status, effective_date="2026-01-01T00:00:00Z", effective_sha=eff[0], known_at=run[1])


def edge(run, path, scope, line, intro):
    return dict(run_id=run[0], repo=REPO, caller_path=path, caller_scope=scope, line=line, col=11,
                callee_path="lib/translation.py", callee_repo=REPO, callee_scope="ugettext", introduced_sha=intro[0])


def blast(run, invocations, by_depth):
    return dict(run_id=run[0], repo=REPO, symbol="ugettext", direct_sites=3, invocations=invocations, files=3,
                by_depth=json.dumps(by_depth))


SITES_R1 = [site("S1", R1, "a.py", 3, "f", C1), site("S2", R1, "a.py", 5, "g", C1), site("S3", R1, "b.py", 2, "k", C1)]
SITES_R2 = SITES_R1 + [  # append-only: R1's versions stay, R2 writes what changed
    site("S2", R2, "a.py", 5, "g", C3, "removed"),            # removed by commit 3
    site("S3", R2, "b.py", 4, "k", C4),                       # moved by commit 4
    site("S4", R2, "a.py", 7, "h", C2)]                       # added by commit 2
EDGES_R1 = [edge(R1, "a.py", "f", 3, C1), edge(R1, "a.py", "g", 5, C1), edge(R1, "b.py", "k", 2, C1), edge(R1, "c.py", "m", 10, C1)]
EDGES_R2 = [edge(R2, "a.py", "f", 3, C1),
            edge(R2, "a.py", "h", 7, C2),                     # new call: commit 2
            edge(R2, "b.py", "k", 4, C4),                     # line rewritten by commit 4: old edge removed, this one added
            edge(R2, "c.py", "m", 12, C1)]                    # only moved (lines inserted above): neither


class TwoRuns(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp())
        cls.config = cls.tmp / "meridian.toml"
        cls.config.write_text('[run]\nhandoff_dir = "out/{run_id}"\n')
        for run, sites, edges, br in ((R1, SITES_R1, EDGES_R1, blast(R1, 4, {"1": 4, "2": 0, "3": 0})),
                                      (R2, SITES_R2, EDGES_R2, blast(R2, 4, {"1": 3, "2": 1, "3": 0}))):
            out = cls.tmp / "out" / run[0]
            out.mkdir(parents=True)
            tables = {"run": [dict(run_id=run[0], snapshot_at=run[1])], "git_commit": COMMITS, "call_site": sites,
                      "call_edge": edges, "blast_radius": [br]}
            for name, rows in tables.items():
                write_jsonl(out / f"{name}.jsonl", rows)
        with contextlib.redirect_stdout(io.StringIO()) as cls.log:
            cls.first = RP.main("R1", cls.config, cls.tmp)
            cls.doc = RP.main("R2", cls.config, cls.tmp, prev_run="R1")
        cls.by = {c["sha"]: c for c in cls.doc["commits"]}

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def ids(self, sha, kind):
        return [s["call_site_id"] for s in self.by[sha]["call_sites"][kind]]

    def test_first_run_reports_everything_as_added(self):
        self.assertEqual([c["sha"] for c in self.first["commits"]], [C1[0]])
        c = self.first["commits"][0]
        self.assertEqual([s["call_site_id"] for s in c["call_sites"]["added"]], ["S1", "S2", "S3"])
        self.assertEqual(len(c["call_edges"]["added"]), 4)
        self.assertIsNone(self.first["blast_radius"][0]["before"])

    def test_each_change_is_under_the_commit_that_caused_it(self):
        self.assertEqual([(c["sha"], c["subject"]) for c in self.doc["commits"]], [C2, C3, C4])  # C1 changed nothing
        self.assertEqual(self.ids(C2[0], "added"), ["S4"])
        self.assertEqual(self.ids(C3[0], "removed"), ["S2"])
        self.assertEqual(self.ids(C4[0], "changed"), ["S3"])
        self.assertEqual(self.by[C4[0]]["call_sites"]["changed"][0]["was"], {"line": 2})
        self.assertEqual(self.by[C2[0]]["authored_at"], "2026-02-01T00:00:00Z")

    def test_call_edges_added_by_introduced_sha_removed_apart(self):
        self.assertEqual([(e["caller_scope"], e["line"]) for e in self.by[C2[0]]["call_edges"]["added"]], [("h", 7)])
        self.assertEqual([(e["caller_scope"], e["line"]) for e in self.by[C4[0]]["call_edges"]["added"]], [("k", 4)])
        self.assertEqual([(e["caller_scope"], e["line"], e["introduced_sha"]) for e in self.doc["call_edges_removed"]],
                         [("g", 5, C1[0]), ("k", 2, C1[0])])  # the moved c.py edge is in neither list
        self.assertEqual(self.doc["totals"], {"call_sites_added": 1, "call_sites_removed": 1, "call_sites_changed": 1,
                                              "call_edges_added": 2, "call_edges_removed": 2, "commits": 3})

    def test_blast_radius_before_and_after_by_depth(self):
        b = self.doc["blast_radius"][0]
        self.assertEqual((b["repo"], b["symbol"]), (REPO, "ugettext"))
        self.assertEqual((b["before"]["by_depth"], b["after"]["by_depth"]), ({"1": 4, "2": 0, "3": 0}, {"1": 3, "2": 1, "3": 0}))

    def test_no_people_and_the_same_bytes_again(self):
        path = self.tmp / "out" / "R2" / "changes.json"
        before = path.read_bytes()
        self.assertNotIn(b"example.com", before)
        with contextlib.redirect_stdout(io.StringIO()):
            RP.main("R2", self.config, self.tmp, prev_run="R1")
        self.assertEqual(path.read_bytes(), before)
        self.assertIn("sites +1 -0 ~0  edges +1  SHOP-1 a new call", self.log.getvalue())


if __name__ == "__main__":
    unittest.main()
