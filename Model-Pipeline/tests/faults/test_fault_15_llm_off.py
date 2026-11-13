"""Fault 15: LLM off (no key, empty cache).
Expected: every view renders with templates.
Why: a product rule."""
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tests.contracts.make_vishwa import fixture


class LLMOff(unittest.TestCase):
    def test_every_view_renders_with_templates(self):
        K, _, cfg = fixture()
        from meridian import views as V
        from meridian.llm_cache import Cache
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        db = K.build()
        db.executescript("DELETE FROM outcome; DELETE FROM suggestion;")  # no stored LLM text either
        shown = 0
        with mock.patch.dict(os.environ):
            os.environ.pop("MERIDIAN_GCP_PROJECT", None)
            b = V.Block(db, cfg, "R2", fixture=True, cache=Cache(Path(tmp.name) / "empty"))
            repo, symbol = b.scope()
            pages = ["/", V.href("owner", b.pairs[0][1]), "/push", "/tickets"] + [
                V.href("consumer", t, repo=repo, symbol=symbol) for (t,) in db.execute(
                    "SELECT DISTINCT owner_team FROM call_site WHERE repo = ? AND symbol = ?", (repo, symbol))]
            for p in pages:
                v = V.route(b, p)[0]
                self.assertIn("fixture acme/shop", V.to_html(v), p)
                texts = [t for s in v["sections"] for t in s.get("texts", [])]
                self.assertTrue(all(t["source"] == "template" and t["body"] for t in texts), p)
                shown += len(texts)
        self.assertGreater(shown, 0)


if __name__ == "__main__":
    unittest.main()
