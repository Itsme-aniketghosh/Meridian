"""No CI job has an LLM key (PASS-BARS §6.5): the workflows never name one, and the cloud sign-in comes after every
step that could reach an LLM.

    python -m unittest tests.test_ci_keys -v                        # from Data-Pipeline/
"""
import re
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from meridian.common import load_settings

WORKFLOWS = Path(__file__).resolve().parents[2] / ".github" / "workflows"
PROVIDER_KEYS = ("ANTHROPIC_API_KEY", "GOOGLE_API_KEY", "GEMINI_API_KEY", "OPENAI_API_KEY", "MISTRAL_API_KEY",
                 "GOOGLE_GENAI_USE_VERTEXAI", "HF_TOKEN")
SIGN_IN = "google-github-actions/auth"
LLM_STEPS = ("meridian.agents", "model.llm_bias", "q2_audit", "writer_compare")


def keys():
    return (load_settings("llm").get("project_env", "MERIDIAN_GCP_PROJECT"),) + PROVIDER_KEYS


def problems(name, text):
    """What's wrong with one workflow's text: an LLM key named, or an LLM step after the cloud sign-in."""
    out = [f"{name}:{i}: names {k}" for i, line in enumerate(text.splitlines(), 1)
           for k in keys() if re.search(rf"\b{k}\b", line.split("#")[0])]
    signed_in = text.find(SIGN_IN)
    if signed_in >= 0:
        out += [f"{name}: {s} runs after {SIGN_IN}" for s in LLM_STEPS if s in text[signed_in:]]
    return out


class Workflows(unittest.TestCase):
    def test_no_workflow_has_an_llm_key(self):
        files = sorted(WORKFLOWS.glob("*.yml"))
        self.assertTrue(files)
        found = [p for f in files for p in problems(f.name, f.read_text(encoding="utf-8"))]
        self.assertEqual(found, [], "\n".join(found))


class Planted(unittest.TestCase):
    def test_a_key_in_env_is_caught(self):
        self.assertEqual(len(problems("x.yml", "env:\n  MERIDIAN_GCP_PROJECT: ${{ secrets.P }}\n")), 1)

    def test_a_key_in_a_comment_is_not(self):
        self.assertEqual(problems("x.yml", "run: true  # never MERIDIAN_GCP_PROJECT here\n"), [])

    def test_an_llm_step_after_the_sign_in_is_caught(self):
        text = f"- uses: {SIGN_IN}@v2\n- run: python -m model.llm_bias\n"
        self.assertEqual(problems("x.yml", text), [f"x.yml: model.llm_bias runs after {SIGN_IN}"])

    def test_an_llm_step_before_it_is_not(self):
        self.assertEqual(problems("x.yml", f"- run: python -m model.llm_bias\n- uses: {SIGN_IN}@v2\n"), [])


if __name__ == "__main__":
    unittest.main()
