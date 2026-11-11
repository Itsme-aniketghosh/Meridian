"""gate.py (Chaitali): PASS-BARS §6.3. Every failure exits 1 and sends one alert, for real, to the local Slack and
SMTP servers of Data-Pipeline/tests/alert_sinks.py. The inputs are the Contract 4 examples in tests/contracts/,
made to agree with each other; each test breaks one thing."""
import contextlib
import copy
import hashlib
import importlib.util
import io
import json
import math
import os
import tempfile
import tomllib
import unittest
from itertools import permutations
from pathlib import Path
from unittest import mock

from model import gate
from tests.fake_table import fake_table

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
_spec = importlib.util.spec_from_file_location("alert_sinks", ROOT / "Data-Pipeline" / "tests" / "alert_sinks.py")
alert_sinks = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(alert_sinks)

GATE = {"split": "val", "reference": "lines_x_files", "min_lift": 0.0, "noise_sds": 2.83}
REPO_TOML = (HERE.parent / "model.toml").read_text(encoding="utf-8")


def example(name):
    return json.loads((HERE / "contracts" / name).read_text(encoding="utf-8"))


def good_set():
    """The four examples made to agree: one candidate (lgbm), one split, one N, one table; bias passes; the
    champion has the candidate's own PR-AUC."""
    m = example("metrics.json")
    cand = m["candidate"]["column"]
    b = example("bias_report.json")
    b.update(candidate=cand, n=m["n"], split=m["split"], accepted=[f"{f['slice']}:{f['metric']}" for f in b["flags"]],
             open_flags=0)
    b["pass"] = True
    s = example("seed_noise.json")
    s.update(kind=m["candidate"]["kind"], n=m["n"], split=m["split"])
    c = example("champion.json")
    c["metrics"]["pr_auc"]["value"] = m["models"][cand]["pr_auc"]["value"]
    c["metrics"]["features_md5"] = m["features_md5"]
    return {"metrics.json": m, "bias_report.json": b, "seed_noise.json": s, "champion.json": c}


def toml(settings):
    """The repo's model.toml with its [gate] replaced by these settings."""
    rest = REPO_TOML[:REPO_TOML.index("\n[gate]")]
    return rest + "\n\n[gate]\n" + "\n".join(f"{k} = {json.dumps(v)}" for k, v in settings.items()) + "\n"


class Case(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: __import__("shutil").rmtree(self.tmp, ignore_errors=True))
        self.docs = good_set()

    def write(self, gate_settings=GATE, skip=()):
        for name, doc in self.docs.items():
            if name not in skip:
                (self.tmp / name).write_text(json.dumps(doc), encoding="utf-8")
        (self.tmp / "model.toml").write_text(toml(gate_settings), encoding="utf-8")

    def gate(self, gate_settings=GATE, skip=(), send=None):
        """(exit code, gate.json, what Slack and email got, stdout) for one run against the local sinks."""
        self.write(gate_settings, skip)
        with alert_sinks.sinks() as s:
            settings = self.tmp / "settings.toml"
            settings.write_text(f'[alerts]\non = ["failure", "error", "warn"]\nemail_to = ["team@example.com"]\n'
                                f'smtp_host = "127.0.0.1"\nsmtp_port = {s.smtp_port}\ntimeout_s = 5\n',
                                encoding="utf-8")
            env = {"MERIDIAN_SETTINGS": str(settings), "MERIDIAN_SLACK_WEBHOOK": s.webhook}
            out = io.StringIO()
            with mock.patch.dict(os.environ, env), contextlib.redirect_stdout(out):
                code, result = gate.run(self.tmp, self.tmp / "champion.json", self.tmp / "model.toml",
                                        **({"send": send} if send else {}))
            return code, result, list(s.slack), list(s.mail), out.getvalue()

    def failed(self, result):
        return [c["check"] for c in result["checks"] if not c["ok"]]

    def assert_fails_once(self, check, **kw):
        code, result, slack, mail, _ = self.gate(**kw)
        self.assertEqual(code, 1)
        self.assertEqual(self.failed(result), [check])
        self.assertEqual(len(slack), 1, "exactly one Slack message")
        self.assertEqual(len(mail), 1, "exactly one email")
        self.assertIn("model gate failed", slack[0]["text"])
        self.assertIn(f"{check}:", slack[0]["text"])
        self.assertEqual(sorted(result["alerted"]), ["email", "slack"])
        return result


class Passes(Case):
    def test_a_passing_set_exits_0_and_alerts_nothing(self):
        code, result, slack, mail, out = self.gate()
        self.assertEqual(code, 0)
        self.assertTrue(result["pass"])
        self.assertEqual([c["check"] for c in result["checks"]], ["inputs", "agree", "bar", "bias", "champion"])
        self.assertEqual((slack, mail), ([], []))
        self.assertIn("gate: pass", out)

    def test_no_champion_is_the_first_model(self):
        code, result, *_ = self.gate(skip=("champion.json",))
        self.assertEqual(code, 0)
        self.assertTrue(result["first_model"])

    def test_gate_json_is_written(self):
        self.gate()
        written = json.loads((self.tmp / "gate.json").read_text(encoding="utf-8"))
        self.assertTrue(written["pass"])
        self.assertEqual(written["settings"], GATE)


class EveryFailureExits1AndAlertsOnce(Case):
    def test_below_the_bar(self):
        ref = self.docs["metrics.json"]["models"]["lines_x_files"]["pr_auc"]
        ref["value"] = 0.5
        self.assert_fails_once("bar")

    def test_below_the_champion_by_more_than_the_tolerance(self):
        sd = self.docs["seed_noise.json"]["sd"]
        self.docs["champion.json"]["metrics"]["pr_auc"]["value"] += 2.83 * sd + 1e-6
        result = self.assert_fails_once("champion")
        self.assertIn("floor", result["checks"][-1]["why"])

    def test_below_the_champion_within_the_tolerance_passes(self):
        sd = self.docs["seed_noise.json"]["sd"]
        self.docs["champion.json"]["metrics"]["pr_auc"]["value"] += 2.83 * sd - 1e-6
        self.assertEqual(self.gate()[0], 0)

    def test_bias_fail(self):
        b = self.docs["bias_report.json"]
        b.update(accepted=[], open_flags=len(b["flags"]))
        b["pass"] = False
        result = self.assert_fails_once("bias")
        self.assertIn("module=core:recall", result["checks"][3]["why"])

    def test_a_missing_input(self):
        for name in ("metrics.json", "bias_report.json", "seed_noise.json"):
            with self.subTest(missing=name):
                (self.tmp / name).unlink(missing_ok=True)
                result = self.assert_fails_once("inputs", skip=(name,))
                self.assertIn(f"{name}: not found", result["checks"][0]["why"])

    def test_a_malformed_input(self):
        self.write()
        (self.tmp / "bias_report.json").write_text("{not json", encoding="utf-8")
        self.docs.pop("bias_report.json")
        self.assert_fails_once("inputs")

    def test_a_missing_key(self):
        del self.docs["seed_noise.json"]["sd"]
        result = self.assert_fails_once("inputs")
        self.assertIn("sd", result["checks"][0]["why"])

    def test_a_malformed_champion(self):
        self.docs["champion.json"]["model_md5"] = "not an md5"
        self.assert_fails_once("inputs")

    def test_inputs_that_disagree(self):
        for name, key, value in (("bias_report.json", "candidate", "forest"), ("seed_noise.json", "n", 9),
                                 ("seed_noise.json", "kind", "logreg"), ("bias_report.json", "split", "test")):
            with self.subTest(file=name, key=key):
                docs = copy.deepcopy(good_set())
                docs[name][key] = value
                self.docs = docs
                self.assert_fails_once("agree")

    def test_a_champion_on_another_table(self):
        """Without its model next to champion.json it can't be re-scored on this table, so it fails and says why."""
        self.docs["champion.json"]["metrics"]["features_md5"] = "0" * 32
        result = self.assert_fails_once("champion")
        self.assertIn("another feature table", result["checks"][-1]["why"])
        self.assertIn("its model.joblib isn't next to champion.json, so it can't be re-scored",
                      result["checks"][-1]["why"])

    def test_every_failure_in_one_message(self):
        self.docs["metrics.json"]["models"]["lines_x_files"]["pr_auc"]["value"] = 0.5
        self.docs["bias_report.json"]["pass"] = False
        code, result, slack, mail, _ = self.gate()
        self.assertEqual((code, self.failed(result)), (1, ["bar", "bias"]))
        self.assertEqual((len(slack), len(mail)), (1, 1))
        self.assertIn("bar:", slack[0]["text"])
        self.assertIn("bias:", slack[0]["text"])

    def test_no_gate_section(self):
        self.write()
        (self.tmp / "model.toml").write_text(REPO_TOML[:REPO_TOML.index("\n[gate]")], encoding="utf-8")
        with contextlib.redirect_stdout(io.StringIO()):
            code, result = gate.run(self.tmp, self.tmp / "champion.json", self.tmp / "model.toml", send=lambda *a: [])
        self.assertEqual(code, 1)
        self.assertIn("no [gate]", result["checks"][0]["why"])


class AChampionOnAnotherTableIsReScored(Case):
    """Decision 7: a champion judged on another feature table (the first run after a new block) is scored on this
    run's val rows with its own model, the file next to champion.json, and then compared as usual."""

    @classmethod
    def setUpClass(cls):
        import joblib
        from model import train
        df = train.load_features(gate.FEATURES)
        model, _, _ = train.fit_and_score(df, "logreg", 1)
        buf = io.BytesIO()
        joblib.dump(model, buf)
        val = train.rows(df, "val")
        cls.model, cls.n = buf.getvalue(), len(val)
        cls.value = float(train.pr_auc(val["label"].to_numpy(), train.score_with(model, val)))
        cls.table_md5 = hashlib.md5(gate.FEATURES.read_bytes()).hexdigest()

    def setUp(self):
        super().setUp()
        (self.tmp / "model.joblib").write_bytes(self.model)
        m, b, s, c = (self.docs[k] for k in ("metrics.json", "bias_report.json", "seed_noise.json", "champion.json"))
        m["n"] = b["n"] = s["n"] = self.n
        m["features_md5"] = self.table_md5
        m["models"]["lines_x_files"]["pr_auc"]["value"] = 0.0
        m["models"][m["candidate"]["column"]]["pr_auc"]["value"] = self.value
        c.update(model_file="model.joblib", model_md5=hashlib.md5(self.model).hexdigest())
        c["metrics"]["features_md5"] = "f" * 32
        c["metrics"]["pr_auc"]["value"] = 0.99  # its number on its own table: not what it's compared at here

    def candidate(self, value):
        m = self.docs["metrics.json"]
        m["models"][m["candidate"]["column"]]["pr_auc"]["value"] = value

    def test_it_is_re_scored_with_its_own_model_and_compared(self):
        code, result, slack, mail, _ = self.gate()
        self.assertEqual(code, 0, result["checks"])
        self.assertIn(f"vs champion 00000000 {self.value:.4f}, re-scored on this table (0.9900 on its own table)",
                      result["checks"][-1]["why"])
        self.assertEqual((slack, mail), ([], []))

    def test_below_the_re_scored_champion_by_more_than_the_tolerance_fails(self):
        self.candidate(self.value - 2.83 * self.docs["seed_noise.json"]["sd"] - 1e-6)
        self.assert_fails_once("champion")

    def test_a_model_file_that_isnt_the_champions_fails(self):
        self.docs["champion.json"]["model_md5"] = "e" * 32
        why = self.assert_fails_once("champion")["checks"][-1]["why"]
        self.assertIn("the model.joblib next to it isn't the one champion.json records", why)

    def test_a_table_that_isnt_this_runs_fails(self):
        self.docs["metrics.json"]["features_md5"] = "e" * 32
        why = self.assert_fails_once("champion")["checks"][-1]["why"]
        self.assertIn("isn't this run's feature table", why)

    def test_a_model_that_doesnt_load_fails(self):
        junk = b"not a model"
        (self.tmp / "model.joblib").write_bytes(junk)
        self.docs["champion.json"]["model_md5"] = hashlib.md5(junk).hexdigest()
        why = self.assert_fails_once("champion")["checks"][-1]["why"]
        self.assertIn("re-scoring it failed", why)

    def test_val_rows_that_arent_this_runs_fail(self):
        for name in ("metrics.json", "bias_report.json", "seed_noise.json"):
            self.docs[name]["n"] = self.n + 1
        why = self.assert_fails_once("champion")["checks"][-1]["why"]
        self.assertIn(f"re-scored on {self.n:,} val rows, but this run's metrics have {self.n + 1}", why)

    def test_a_file_it_cant_read_fails_the_check_instead_of_crashing(self):
        with mock.patch("model.evaluate.file_md5", side_effect=PermissionError(13, "Permission denied")):
            result = self.assert_fails_once("champion")
        self.assertIn("can't read what it needs to re-score it (PermissionError", result["checks"][-1]["why"])
        self.assertTrue((self.tmp / "gate.json").exists())


class ABrokenChannelNeverDecides(Case):
    def test_webhook_down_same_exit_code_error_logged(self):
        self.docs["bias_report.json"]["pass"] = False
        self.write()
        env = {"MERIDIAN_SLACK_WEBHOOK": "http://127.0.0.1:9/hook", "MERIDIAN_SETTINGS": str(self.tmp / "s.toml")}
        (self.tmp / "s.toml").write_text('[alerts]\non = ["failure"]\ntimeout_s = 2\n', encoding="utf-8")
        with mock.patch.dict(os.environ, env), contextlib.redirect_stdout(io.StringIO()), \
                self.assertLogs("meridian", level="ERROR") as logs:
            code, result = gate.run(self.tmp, self.tmp / "champion.json", self.tmp / "model.toml")
        self.assertEqual(code, 1)
        self.assertEqual(result["alerted"], [])
        self.assertTrue(any("Slack failed" in line for line in logs.output))

    def test_a_sender_that_raises_is_logged(self):
        self.docs["bias_report.json"]["pass"] = False
        self.write()
        with mock.patch.dict("sys.modules", {"meridian.alert": None}), \
                contextlib.redirect_stdout(io.StringIO()), self.assertLogs("meridian.gate", level="ERROR") as logs:
            code, _ = gate.run(self.tmp, self.tmp / "champion.json", self.tmp / "model.toml")
        self.assertEqual(code, 1)
        self.assertIn("alert not sent", logs.output[0])

    def test_a_pass_with_the_webhook_down_still_passes(self):
        self.write()
        with mock.patch.dict(os.environ, {"MERIDIAN_SLACK_WEBHOOK": "http://127.0.0.1:9/hook"}), \
                contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(gate.run(self.tmp, self.tmp / "champion.json", self.tmp / "model.toml")[0], 0)


class ThresholdsInConfig(Case):
    def test_min_lift_decides_the_bar(self):
        m = self.docs["metrics.json"]
        lift = m["models"]["lgbm"]["pr_auc"]["value"] - m["models"]["lines_x_files"]["pr_auc"]["value"]
        self.assertEqual(self.gate({**GATE, "min_lift": lift - 0.001})[0], 0)
        self.assertEqual(self.failed(self.gate({**GATE, "min_lift": lift + 0.001})[1]), ["bar"])

    def test_noise_sds_decides_the_champion_check(self):
        sd = self.docs["seed_noise.json"]["sd"]
        self.docs["champion.json"]["metrics"]["pr_auc"]["value"] += 1.5 * sd
        self.assertEqual(self.gate({**GATE, "noise_sds": 2.0})[0], 0)
        self.assertEqual(self.failed(self.gate({**GATE, "noise_sds": 1.0})[1]), ["champion"])

    def test_reference_decides_what_the_bar_is_against(self):
        self.docs["metrics.json"]["models"]["churn"]["pr_auc"]["value"] = 0.9
        self.assertEqual(self.gate()[0], 0)
        self.assertEqual(self.failed(self.gate({**GATE, "reference": "churn"})[1]), ["bar"])

    def test_every_key_is_required(self):
        for key in GATE:
            with self.subTest(key=key), self.assertRaises(ValueError):
                gate.gate_settings({"gate": {k: v for k, v in GATE.items() if k != key}})

    def test_the_repo_config_has_every_key(self):
        cfg = tomllib.loads((HERE.parent / "model.toml").read_text(encoding="utf-8"))
        self.assertEqual(set(gate.gate_settings(cfg)), set(GATE))


class Decision6(unittest.TestCase):
    """Noise passes, bad models fail (PASS-BARS §6.3), at the repo's [gate] noise_sds."""

    @classmethod
    def setUpClass(cls):
        cfg = tomllib.loads((HERE.parent / "model.toml").read_text(encoding="utf-8"))
        cls.k = gate.gate_settings(cfg)["noise_sds"]

    def test_rachnas_seeds_pass_against_each_other(self):
        noise = json.loads((HERE.parent / "reports" / "seed_noise.json").read_text(encoding="utf-8"))
        self.assertEqual(len(noise["pr_auc"]), 5)
        pairs = list(permutations(noise["pr_auc"], 2))
        self.assertEqual(sum(not gate.below_champion(c, h, noise["sd"], self.k) for h, c in pairs), 20)

    def test_planted_bad_models_fail(self):
        from model.train import seed_noise
        # big enough that seed noise is real-sized (val about 1,800 rows), as on the real table
        table = fake_table(n=20000, seed=7)
        params = {"n_estimators": 100, "min_samples_leaf": 5}
        with tempfile.TemporaryDirectory() as d:
            noise = seed_noise(table, "forest", params, d)
        r = gate.decision_6(table, noise, "forest", params, 1, "lines_x_files")
        self.assertEqual(set(r["planted_pr_auc"]), {"shuffled labels", "size only", "half the features"})
        for name, value in r["planted_pr_auc"].items():
            with self.subTest(planted=name):
                self.assertTrue(gate.below_champion(value, r["champion"], noise["sd"], self.k),
                                f"{name}: {value:.3f} vs champion {r['champion']:.3f}, sd {noise['sd']:.4f}")

    def test_the_options_and_the_pick(self):
        # seeds at most 0.004 apart, sd 0.003: 0 blocks the 3 pairs where the candidate is lower, 2 sd blocks none
        noise = {"seeds": [1, 2, 3], "pr_auc": [0.400, 0.403, 0.399], "sd": 0.003, "split": "val", "n": 10}
        with mock.patch("model.train.pmap", lambda fn, args: [0.10, 0.30, 0.35]):
            r = gate.decision_6(None, noise, "forest", {}, 1, "lines_x_files")
        self.assertEqual(r["options"]["0"]["seed_pairs_pass"], 3)
        self.assertEqual(r["picked"], "2 sd")
        self.assertAlmostEqual(r["options"]["2.83 sd (2 sd of a difference)"]["noise_sds"], 2 * math.sqrt(2))


if __name__ == "__main__":
    unittest.main()
