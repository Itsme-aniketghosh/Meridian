"""plant.py (S3, Nov 28): the planted broken image on main's workflow, by hand only, never committed, never under
the champion's tag, and stopped by the revision's smoke test (wrong-risk) or the canary (slow).

    python -m unittest discover -s Model-Deployment/tests -p "test_plant.py" -v
"""
import io
import shutil
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

try:
    import yaml
except ModuleNotFoundError:
    yaml = None

DEPLOY = Path(__file__).resolve().parents[1]
REPO = DEPLOY.parent
sys.path.insert(0, str(DEPLOY))
import plant as P  # noqa: E402

DEPLOY_YML = REPO / ".github" / "workflows" / "deploy.yml"


class Plant(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.TemporaryDirectory()
        self.scorer = Path(self.d.name) / "scorer.py"
        shutil.copy(P.SCORER, self.scorer)

    def tearDown(self):
        self.d.cleanup()

    def test_each_plant_edits_one_line_and_still_compiles(self):
        before = self.scorer.read_text()
        for kind in P.PLANTS:
            shutil.copy(P.SCORER, self.scorer)
            P.plant(kind, self.scorer, canary_percent=10)
            after = self.scorer.read_text()
            self.assertEqual(after.count(P.MARK), 1, kind)
            self.assertNotEqual(after, before, kind)
            compile(after, "scorer.py", "exec")

    def test_wrong_risk_zeroes_a_feature_on_known_shas(self):
        P.plant("wrong-risk", self.scorer)
        self.assertIn(".assign(prior_bug_fixes=0)", self.scorer.read_text())

    def test_slow_waits_on_every_score_and_needs_the_canary(self):
        with self.assertRaises(P.PlantError) as e:
            P.plant("slow", self.scorer, canary_percent=0)
        self.assertIn("only the canary catches it", str(e.exception))
        P.plant("slow", self.scorer, canary_percent=10)
        self.assertIn("sleep(2.5)", self.scorer.read_text())

    def test_twice_or_a_moved_anchor_fails_loudly(self):
        P.plant("wrong-risk", self.scorer)
        with self.assertRaises(P.PlantError):
            P.plant("wrong-risk", self.scorer)
        self.scorer.write_text("nothing to plant here\n")
        with self.assertRaises(P.PlantError) as e:
            P.plant("slow", self.scorer, canary_percent=10)
        self.assertIn("update plant.py", str(e.exception))

    def test_the_cli_reads_the_canary_from_deploy_toml(self):
        with redirect_stdout(io.StringIO()) as out:
            self.assertEqual(P.main(["slow"], scorer=self.scorer), 0)
        self.assertIn("plant: slow", out.getvalue())

    def test_the_committed_scorer_is_never_planted(self):
        self.assertNotIn(P.MARK, P.SCORER.read_text())


@unittest.skipUnless(yaml, "needs PyYAML")
class Workflow(unittest.TestCase):
    def setUp(self):
        self.text = DEPLOY_YML.read_text(encoding="utf-8")
        doc = yaml.safe_load(self.text)
        self.on = doc.get("on", doc.get(True))
        self.job = doc["jobs"]["deploy"]
        self.steps = {s.get("id") or s.get("name"): s for s in self.job["steps"]}

    def test_by_hand_only_and_off_by_default(self):
        inp = self.on["workflow_dispatch"]["inputs"]["plant"]
        self.assertEqual((inp["default"], inp["options"][0]), ("none", "none"))
        self.assertEqual(set(inp["options"]), {"none", *P.PLANTS})
        self.assertEqual(self.job["env"]["PLANT"], "${{ github.event.inputs.plant || 'none' }}")
        self.assertEqual(self.steps["plant"]["if"], "env.PLANT != 'none'")

    def test_planted_after_the_tests_and_before_the_build(self):
        order = [s.get("id") or s.get("name") for s in self.job["steps"]]
        self.assertLess(order.index("Tests (champion.py, smoke.py, deploy.toml)"), order.index("plant"))
        self.assertLess(order.index("plant"), order.index("build"))

    def test_only_wrong_risk_on_main_skips_the_runners_smoke_test(self):
        self.assertIn("!(env.PLANT == 'wrong-risk' && env.ON_MAIN == 'true')", self.steps["smoke_local"]["if"])
        self.assertNotIn("PLANT", self.steps["smoke"]["if"])   # the revision's smoke test always runs

    def test_never_under_the_champions_tag_or_its_revision_tag(self):
        self.assertIn('IMAGE="$IMAGE-planted-$PLANT"', self.steps["push"]["run"])
        self.assertIn("TAG_PREFIX=p", self.steps["plant"]["run"])
        self.assertIn('tag="${TAG_PREFIX:-c}-', self.steps["revision"]["run"])

    def test_slack_and_the_summary_say_its_a_planted_test(self):
        self.assertIn('--planted "$PLANT"', self.steps["slack"]["run"])
        self.assertIn("Planted test", self.text[self.text.index("- name: Summary"):])


if __name__ == "__main__":
    unittest.main()
