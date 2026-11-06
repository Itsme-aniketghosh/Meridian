"""Step L: the second real run (meridian.next.toml, --prev-run c5118b019d642a9b) against the answer keys. Team data,
never in CI: runs only with MERIDIAN_RUN2 set to that run's out/<run_id>/ folder.

    MERIDIAN_RUN2=<data root>/out/5bc7deda64fcfa0a python -m unittest tests.test_run2 -v      # from Data-Pipeline/

Answer keys (week1/pair1, week1/pair3, report/appendix-real-data.md §2):
- Django: the conversion commit c651331b34 edits all 267 site lines. 265 lose the legacy names. 2 (the imports in
  tests/i18n/tests.py) are rewritten but still import all four on purpose, for test_legacy_aliases, whose 4 calls
  come back as new sites.
- Packages: where the migration commit finished the job every line goes (oscar 247, wagtail 240, DRF 182, cms 160,
  ...). Partial migrations leave lines (graphene 3 of 13 gone, extensions 2 of 10, import-export 3 of 7).
  autocomplete-light's commit only rewrites its imports to `re_path as url`, so 65 of 67 lines stop resolving to
  django.conf.urls.url though only 20 were edited: removal goes by resolution, not by edit.
"""
import os
import sqlite3
import unittest
from pathlib import Path

PREV = "c5118b019d642a9b"
OUT = Path(os.environ.get("MERIDIAN_RUN2", "/nonexistent"))

# repo: (run 1 present lines, lines removed in run 2)
REMOVED = {
    "django/django": (267, 265),
    "django-oscar/django-oscar": (247, 247), "wagtail/wagtail": (240, 240),
    "encode/django-rest-framework": (182, 182), "django-cms/django-cms": (160, 160),
    "iMerica/dj-rest-auth": (51, 51), "pennersr/django-allauth": (50, 50),
    "jazzband/django-two-factor-auth": (37, 37), "django-tastypie/django-tastypie": (31, 31),
    "django-guardian/django-guardian": (25, 25), "django/channels": (17, 17),
    "cookiecutter/cookiecutter-django": (16, 16), "carltongibson/django-filter": (7, 7),
    "yourlabs/django-autocomplete-light": (67, 65), "jazzband/django-debug-toolbar": (29, 28),
    "jazzband/django-silk": (28, 26), "django-haystack/django-haystack": (13, 10),
    "graphql-python/graphene-django": (13, 3), "django-extensions/django-extensions": (10, 2),
    "django-import-export/django-import-export": (7, 3), "django-tenants/django-tenants": (6, 5),
}


@unittest.skipUnless((OUT / "meridian.sqlite").exists(), "needs MERIDIAN_RUN2=<out/<run_id>/> of the second run")
class SecondRun(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.db = sqlite3.connect(f"file:{OUT / 'meridian.sqlite'}?mode=ro", uri=True)
        cls.run_id, cls.snap = cls.db.execute("SELECT run_id, snapshot_at FROM run").fetchone()
        cls.db.executescript(f"""
            CREATE TEMP VIEW last AS SELECT c.* FROM call_site c
              WHERE c.known_at = (SELECT max(x.known_at) FROM call_site x WHERE x.call_site_id = c.call_site_id);
            CREATE TEMP VIEW first AS SELECT * FROM call_site WHERE run_id = '{PREV}' AND status = 'present';""")

    @classmethod
    def tearDownClass(cls):
        cls.db.close()

    def test_removed_lines_per_repo_match_the_answer_keys(self):
        got = {repo: (n, gone) for repo, n, gone in self.db.execute("""
            SELECT f.repo, count(DISTINCT f.path || ':' || f.line),
                   count(DISTINCT CASE WHEN l.status = 'removed' THEN f.path || ':' || f.line END)
            FROM first f JOIN last l ON l.call_site_id = f.call_site_id GROUP BY f.repo""")}
        self.assertEqual(got, REMOVED)
        packages = [v for r, v in REMOVED.items() if r != "django/django"]
        self.assertEqual((sum(g for _, g in packages), sum(n for n, _ in packages)), (1205, 1236))

    def test_django_keeps_only_the_legacy_alias_test(self):
        left = self.db.execute("""SELECT DISTINCT path, line FROM last
                                  WHERE repo = 'django/django' AND status = 'present' ORDER BY 1, 2""").fetchall()
        self.assertEqual(left, [("tests/i18n/tests.py", n) for n in (28, 60, 61, 62, 64)])
        new = self.db.execute(f"""SELECT symbol FROM call_site WHERE run_id = '{self.run_id}' AND status = 'present'
                                  AND call_site_id NOT IN (SELECT call_site_id FROM first) ORDER BY 1""").fetchall()
        self.assertEqual([s for (s,) in new], ["ugettext", "ugettext_lazy", "ungettext", "ungettext_lazy"])

    def test_removed_rows_are_known_at_run_2s_snapshot(self):
        bad = self.db.execute("""SELECT count(*) FROM call_site
                                 WHERE status = 'removed' AND known_at != ?""", (self.snap,)).fetchone()[0]
        self.assertEqual(bad, 0)
        self.assertGreater(self.snap, self.db.execute("SELECT max(known_at) FROM first").fetchone()[0])


if __name__ == "__main__":
    unittest.main()
