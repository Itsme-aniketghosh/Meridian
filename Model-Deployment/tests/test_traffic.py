"""traffic.py (S3): deploy.yml reads who serves what from Cloud Run's own description of the service.

    python -m unittest discover -s Model-Deployment/tests -p "test_traffic.py" -v
"""
import json
import sys
import tempfile
import unittest
from pathlib import Path

DEPLOY = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(DEPLOY))
import traffic as T  # noqa: E402

URL = "https://meridian-risk-abc-ue.a.run.app"


def service(traffic, latest="meridian-risk-00002-new"):
    return {"status": {"url": URL, "latestReadyRevisionName": latest, "traffic": traffic}}


PLACEHOLDER = service([{"latestRevision": True, "percent": 100}], latest="meridian-risk-00001-old")
NO_TRAFFIC = service([
    {"revisionName": "meridian-risk-00001-old", "percent": 100},
    {"revisionName": "meridian-risk-00002-new", "tag": "c-85b4684ae336", "url": "https://c-85b4684ae336---" + URL[8:]},
])
MOVED = service([
    {"revisionName": "meridian-risk-00002-new", "percent": 100},
    {"revisionName": "meridian-risk-00002-new", "tag": "c-85b4684ae336", "url": "https://c-85b4684ae336---" + URL[8:]},
])


class Traffic(unittest.TestCase):
    def run_main(self, doc, *args):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as d:
            path = Path(d) / "service.json"
            path.write_text(json.dumps(doc))
            return T.main([args[0], str(path), *args[1:]])

    def test_the_tagged_revision_has_no_traffic(self):
        lines = self.run_main(NO_TRAFFIC, "tagged", "c-85b4684ae336")
        self.assertEqual(lines, ["name=meridian-risk-00002-new", "url=https://c-85b4684ae336---" + URL[8:],
                                 "percent=0"])

    def test_a_missing_tag_fails(self):
        with self.assertRaises(SystemExit) as e:
            self.run_main(NO_TRAFFIC, "tagged", "c-000000000000")
        self.assertIn("no traffic entry is tagged", str(e.exception))

    def test_latest_is_resolved_to_a_revision_name(self):
        lines = self.run_main(PLACEHOLDER, "serving", "--key", "previous")
        self.assertEqual(lines, ["previous=meridian-risk-00001-old 100%", "previous_revision=meridian-risk-00001-old"])

    def test_the_old_revision_still_serves_before_the_move(self):
        self.assertEqual(T.serving(NO_TRAFFIC["status"]), {"meridian-risk-00001-old": 100})

    def test_after_the_move_the_new_revision_serves_all_and_the_service_url_is_given(self):
        lines = self.run_main(MOVED, "serving", "--expect", "meridian-risk-00002-new")
        self.assertIn("serving=meridian-risk-00002-new 100%", lines)
        self.assertIn(f"url={URL}", lines)

    def test_a_move_that_didnt_happen_fails(self):
        with self.assertRaises(SystemExit) as e:
            self.run_main(NO_TRAFFIC, "serving", "--expect", "meridian-risk-00002-new")
        self.assertIn("should carry 100%", str(e.exception))

    def test_a_split_is_described_largest_first(self):
        st = service([{"revisionName": "a", "percent": 10}, {"revisionName": "b", "percent": 90}])["status"]
        self.assertEqual(T.describe(T.serving(st)), "b 90%, a 10%")


if __name__ == "__main__":
    unittest.main()
