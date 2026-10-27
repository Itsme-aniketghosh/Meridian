"""The hand-checked label files hold the numbers the docs quote: a flipped verdict or a lost row fails here.

    python -m unittest tests.test_labels -v      # from Data-Pipeline/

Reads only files in git: labels/*.csv and week1/pair1/results/. Docs: labels/README.md, CSV-POLICY.md, GRAPH.md,
KNOWLEDGE-BASE.md, report/appendix-real-data.md.
"""
import csv
import sys
import unittest
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import tests.hand_check_aliases as HA
import tests.hand_check_edit_sites as HE

LABELS = Path(__file__).resolve().parents[1] / "labels"


def read(name):
    with open(LABELS / name, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def initials(s):
    return 2 <= len(s) <= 3 and s.isalpha() and s.isupper()  # CSV-POLICY.md: model, or the checker's initials


class Labels(unittest.TestCase):
    def assert_initials(self, f, rows):
        bad = [r["checked_by"] for r in rows if not initials(r["checked_by"])]
        self.assertEqual(bad, [], f"{f}: docs say every row was checked by a person (checked_by = initials)")

    def test_spark_collision_pairs(self):
        f = "spark_collision_pairs_50.csv"
        rows = read(f)
        self.assertEqual(len({(r["key_a"], r["key_b"]) for r in rows}), 50, f"{f}: GRAPH.md says 50 unique pairs")
        self.assertEqual(len(rows), 50, f"{f}: GRAPH.md says 50 pairs")
        self.assert_initials(f, rows)
        self.assertEqual(Counter(r["human_verdict"] for r in rows), {"real": 32, "no": 18},
                         f"{f}: GRAPH.md says 32 real, 18 not (hunk headers alone 0.64)")
        self.assertEqual([r["key_a"] for r in rows if r["verdict"] != r["human_verdict"]], [],
                         f"{f}: GRAPH.md says the model's read agrees with the hand check on all 50")
        self.assertEqual([r["key_a"] for r in rows if (r["inside"] == "1") != (r["human_verdict"] == "real")], [],
                         f"{f}: GRAPH.md says the inside rule keeps 32, all real (precision 1.00), missing none")

    def test_commit_ticket_hand_check(self):
        # The sample is drawn from out/70dd.../commit_ticket.jsonl, not in git, so only the answers are checked.
        f = "commit_ticket_hand_check_50.csv"
        rows = read(f)
        self.assertEqual([int(r["order"]) for r in rows], list(range(1, 51)), f"{f}: README says 50 rows")
        self.assertEqual(len({r["sha"] for r in rows}), 50, f"{f}: README says 50 commit_ticket rows")
        self.assert_initials(f, rows)
        self.assertEqual(Counter(r["human_verdict"] for r in rows), {"yes": 49, "no": 1},
                         f"{f}: README says 49 yes, 1 no (0.98, bar 0.95)")
        self.assertEqual([r["key"] for r in rows if r["human_verdict"] == "no"], ["SPARK-3723"],
                         f"{f}: the appendix says the one wrong link is SPARK-3723")

    def test_gitlab_cross_team_blocks(self):
        # tests/test_gold.py checks the seed-1 sample and checked_by; these are the counts the docs quote.
        f = "gitlab_cross_team_blocks.csv"
        rows = read(f)
        self.assertEqual(len(rows), 262, f"{f}: CSV-POLICY.md says 262 pairs")
        checked = [r for r in rows if r["human_verdict"]]
        self.assertEqual(len(checked), 50, f"{f}: docs say 50 of 262 human-checked")
        self.assertEqual(sum(r["verdict"] == r["human_verdict"] for r in checked), 18,
                         f"{f}: docs say the model agrees with the person on 18 of 50")
        self.assertEqual(sum(r["human_verdict"] in ("wait", "planned") for r in checked), 15,
                         f"{f}: the appendix says 15 real (wait or planned) pairs in the 50")
        waits = [r["human_verdict"] for r in checked if r["verdict"] == "wait"]
        self.assertEqual((waits.count("wait"), len(waits)), (4, 7),
                         f"{f}: KNOWLEDGE-BASE.md says the model's waits hold 4 of 7")

    def test_alias_hand_check(self):
        f = "alias_hand_check_30.csv"
        rows = read(f)
        got = [(int(r["order"]), r["path"], int(r["line"]), int(r["col"]), r["key_symbol"]) for r in rows]
        self.assertEqual(got, HA.pick(), f"{f}: the 30 calls hand_check_aliases draws from the week-1 key (seed 1)")
        self.assert_initials(f, rows)
        self.assertEqual([r["agrees"] for r in rows], ["yes" if r["checker_answer"] == r["key_symbol"] else "no"
                                                       for r in rows], f"{f}: agrees is checker_answer == key")
        self.assertEqual([r["order"] for r in rows if r["agrees"] != "yes"], [],
                         f"{f}: the appendix says 30 of 30 resolve to the checker's symbol")

    def test_django_edit_sites(self):
        # The 2 sites in code.sample aren't .py lines: the file holds the 265 only.
        f = "django_edit_sites_265.csv"
        rows = read(f)
        self.assertEqual([(r["path"], int(r["line"]), r["kind"], r["symbol"]) for r in rows],
                         sorted((k["path"], k["line"], k["kind"], k["symbol"]) for k in HE.load_key()),
                         f"{f}: docs say these are the 265 lines of week1's edit_sites.csv")
        self.assertEqual(Counter(r["kind"] for r in rows), {"import": 113, "call": 151, "alias": 1},
                         f"{f}: the appendix says 113 imports, 151 calls and 1 alias")
        self.assert_initials(f, rows)
        self.assertEqual([r["path"] for r in rows if r["is_edit_line"] != "yes"], [],
                         f"{f}: KNOWLEDGE-BASE.md says scan gets 265 of 265, 0 false")
        week1 = HE.load_week1()
        self.assertEqual({(r["path"], int(r["line"])) for r in rows if r["checked_in"] == "week1"}, set(week1),
                         f"{f}: hand_check_edit_sites carries week 1's 30 checks over as checked_in = week1")
        self.assertEqual([p for p, w in week1.items() if w["is_call_site"] != "y"], [],
                         f"{f}: week 1's 30 carried-over checks are all edit lines")

    def test_model_read_gitlab_files(self):
        for f, n, doc in (("gitlab_stall_reasons.csv", 57, "CSV-POLICY.md says the 57 stall reads"),
                          ("gitlab_spot_check_model.csv", 26, "the appendix says a 26-row model spot check")):
            rows = read(f)
            self.assertEqual(len(rows), n, f"{f}: {doc}")
            self.assertEqual({r["checked_by"] for r in rows}, {"model"}, f"{f}: docs say it is model-read")
        holds = Counter(r["holds"] for r in read("gitlab_spot_check_model.csv"))
        self.assertEqual(holds["1"], 21, "gitlab_spot_check_model.csv: the appendix says 21 of 26 hold")
        reasons = Counter(r["reason"] for r in read("gitlab_stall_reasons.csv"))
        self.assertEqual(reasons, {"upstream": 41, "unknown": 7, "other": 6, "owner": 2, "risk": 1},
                         "gitlab_stall_reasons.csv: the appendix says upstream 41, unknown 7, other 6, owner 2, risk 1")


if __name__ == "__main__":
    unittest.main()
