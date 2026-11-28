"""S10, deploy speed: the serving image's layers are cached so a new champion rebuilds only its own layers, and
deploy.yml reports the build and model.yml -> serving against PLAN §6's bars (3 min, 15 min).

    python -m unittest discover -s Model-Deployment/tests -p "test_speed.py" -v
"""
import re
import tomllib
import unittest
from pathlib import Path

DEPLOY = Path(__file__).resolve().parents[1]
REPO = DEPLOY.parent
DOCKERFILE = (DEPLOY / "serve" / "Dockerfile").read_text(encoding="utf-8")
DEPLOY_YML = (REPO / ".github" / "workflows" / "deploy.yml").read_text(encoding="utf-8")


def instructions():
    """The Dockerfile's instructions in order, comments and continuations folded."""
    out, cur = [], ""
    for line in DOCKERFILE.splitlines():
        if line.lstrip().startswith("#") or not line.strip():
            continue
        cur += line.rstrip("\\").strip() + " "
        if not line.rstrip().endswith("\\"):
            out.append(cur.strip())
            cur = ""
    return out


class Layers(unittest.TestCase):
    def index(self, pattern):
        return next(i for i, x in enumerate(instructions()) if re.match(pattern, x))

    def test_the_models_args_come_after_every_slow_layer(self):
        # a changed ARG misses the cache for every RUN after it: the packages must come before it
        arg = min(self.index(r"ARG MODEL_VERSION"), self.index(r"ARG MODEL_MD5"))
        self.assertGreater(arg, self.index(r"RUN apt-get"))
        self.assertGreater(arg, self.index(r"RUN pip install"))
        self.assertGreater(arg, self.index(r"COPY Model-Deployment/serve/ "))
        self.assertLess(arg, self.index(r"RUN cd Model-Deployment && python -m serve.buildcheck"))

    def test_the_champion_and_history_are_copied_after_the_packages_and_tables(self):
        self.assertGreater(self.index(r"COPY Model-Pipeline/registry/"), self.index(r"RUN pip install"))
        self.assertGreater(self.index(r"COPY Model-Pipeline/registry/"), self.index(r"COPY Model-Pipeline/data/"))
        self.assertGreater(self.index(r"COPY Model-Deployment/serve/ "), self.index(r"COPY Model-Pipeline/registry/"))


class Workflow(unittest.TestCase):
    build = DEPLOY_YML[DEPLOY_YML.index("- name: Build the serving image"):
                       DEPLOY_YML.index("- name: Smoke test, the image on the runner")]

    def test_the_build_reads_the_cache_and_only_main_writes_it(self):
        self.assertIn("--cache-from type=gha,scope=risk", self.build)
        self.assertIn("$CACHE_TO", self.build)
        self.assertIn("env.ON_MAIN == 'true' && '--cache-to type=gha,scope=risk,mode=max'", self.build)
        self.assertIn("--load", self.build)   # the runner's smoke test runs the image it just built
        self.assertIn("docker/setup-buildx-action", DEPLOY_YML)

    def test_the_build_and_serving_times_are_reported_against_the_bars(self):
        self.assertIn("build_s=", self.build)
        self.assertIn("cached_layers=", self.build)
        self.assertIn('"$build_s" -gt "$BUILD_BAR_S"', self.build)
        summary = DEPLOY_YML[DEPLOY_YML.index("- name: Summary"):]
        self.assertIn("github.event.workflow_run.updated_at", summary)
        self.assertIn("Serving after model.yml", summary)


class Bars(unittest.TestCase):
    def test_deploy_toml_holds_plans_numbers(self):
        with open(DEPLOY / "deploy.toml", "rb") as f:
            speed = tomllib.load(f)["speed"]
        self.assertEqual(speed, {"build_s": 180, "serving_s": 900})
        plan = (DEPLOY / "PLAN.md").read_text(encoding="utf-8")
        self.assertIn("Image build ≤ 3 min with cached layers; new champion serving ≤ 15 min after `model.yml`", plan)


if __name__ == "__main__":
    unittest.main()
