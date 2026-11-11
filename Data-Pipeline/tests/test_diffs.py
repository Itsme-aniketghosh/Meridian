"""The diff drafter (meridian/diffs.py). Fixture tests run everywhere; the 05-test bar runs on the team run only:

    python -m unittest tests.test_diffs -v                                           # from Data-Pipeline/
    MERIDIAN_RUN_ID=c5118b019d642a9b python -m unittest tests.test_diffs -v           # + the bars, on team data

Bars (Model-Pipeline/packets/utkarsh-data/pass-bars.md §5, written before the drafter existed):
- Django, drafted from the parent of c651331b34: >= 90% of its 267 site lines match the real after-line.
- Packages: >= 90% on the 12 repos whose migration finished. Not met (see test_packages_bar).
"""
import json
import os
import shutil
import sys
import tempfile
import unittest
import unittest.mock
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import meridian.diffs as D
import meridian.writer as W
from meridian.common import CONFIG, data_root, git, handoff, load_config, repo_checkout
from meridian.llm_cache import Cache as LLMCache

RUN = "R1"
SNAP = "2026-10-01T00:00:00Z"
TOML = """
[run]
handoff_dir = "out/{run_id}"

[datasets.fix]
symbols = [
  { name = "ugettext", module = "django.utils.translation" },
  { name = "ungettext", module = "django.utils.translation" },
]
"""
TRANSLATION = ("def gettext(m):\n    return m\n\n\ndef ugettext(m):\n    return m\n\n\n"
               "def ngettext(a, b, n):\n    return a\n\n\ndef ungettext(a, b, n):\n    return a\n")
APP = ("from django.utils.translation import ugettext, ungettext\n"
       "\n"
       "\n"
       "def hello():\n"
       "    return ugettext('hello')\n"
       "\n"
       "\n"
       "def safe():\n"
       "    try:\n"
       "        return 1\n"
       "    except ValueError:\n"
       "        return ugettext('oops')\n")


def site(sid, path, line, col, symbol, role, in_except=0):
    return {"call_site_id": sid, "run_id": RUN, "repo": "o/r", "path": path, "line": line, "col": col,
            "scope": "", "symbol": symbol, "role": role, "detection": "ast_resolved", "junk": "none",
            "in_except": in_except, "owner_team": "app", "bug_fixes": 0, "status": "present",
            "effective_date": SNAP, "effective_sha": "a" * 40, "known_at": SNAP}


SITES = [site("S1", "app.py", 1, 37, "ugettext", "import"), site("S2", "app.py", 1, 47, "ungettext", "import"),
         site("S3", "app.py", 5, 11, "ugettext", "call"), site("S4", "app.py", 12, 15, "ugettext", "call", 1)]


class Lines(unittest.TestCase):
    def test_rename_keeps_strings_and_attributes_right(self):
        self.assertEqual(D.rename("x = ugettext('ugettext')  # ugettext\n", "ugettext", "gettext"),
                         "x = gettext('ugettext')  # ugettext\n")
        self.assertEqual(D.rename("translation.ugettext(m)", "ugettext", "gettext"), "translation.gettext(m)")
        self.assertEqual(D.rename("    ugettext_lazy,", "ugettext", "gettext"), "    ugettext_lazy,")

    def test_sorted_imports_stay_sorted_and_duplicates_go(self):
        s = [{"symbol": "ungettext", "module": "django.utils.translation", "col": 41, "role": "import"}]
        line = "from django.utils.translation import gettext, pgettext, ungettext\n"
        self.assertEqual(D.draft_line(line, s, "re_path", set()),
                         "from django.utils.translation import gettext, ngettext, pgettext\n")
        s = [{"symbol": "ugettext", "module": "django.utils.translation", "col": 46, "role": "import"}]
        self.assertEqual(D.draft_line("from django.utils.translation import gettext, ugettext\n", s, "re_path", set()),
                         "from django.utils.translation import gettext\n")

    def test_url_styles(self):
        call = "    url(r'^(?P<pk>\\d+)/$', views.detail, name='detail'),\n"
        self.assertEqual(D.rewrite_url_call(call, "re_path"),
                         "    re_path(r'^(?P<pk>\\d+)/$', views.detail, name='detail'),\n")
        self.assertEqual(D.rewrite_url_call(call, "path_if_plain"),
                         "    path('<int:pk>/', views.detail, name='detail'),\n")
        self.assertEqual(D.rewrite_url_call(call, "alias"), call)
        self.assertEqual(D.rewrite_url_call("    url(r'^a(b|c)/$', v),\n", "path_if_plain"),
                         "    re_path(r'^a(b|c)/$', v),\n")        # real regex: path can't say it
        self.assertEqual(D.path_text(r"^(?P<slug>[\w-]+)/~update$"), "<slug:slug>/~update")
        self.assertEqual(D.path_text(r"^sitemap\.xml$"), "sitemap.xml")
        imp = "from django.conf.urls import include, url\n"
        self.assertEqual(D.rewrite_url_import(imp, "alias", set()), "from django.urls import include, re_path as url\n")
        self.assertEqual(D.rewrite_url_import(imp, "path_if_plain", {"path", "re_path"}),
                         "from django.urls import include, path, re_path\n")
        with self.assertRaises(D.Refused):
            D.rewrite_url_import("from django.conf.urls import handler404, url\n", "re_path", set())

    def test_a_class_without_slash_becomes_str_and_one_with_slash_stays_regex(self):     # decision 13
        self.assertEqual(D.path_text(r"wishlists/(?P<key>[a-z0-9]+)/$"), "wishlists/<str:key>/")
        self.assertEqual(D.path_text(r"^(?P<uuid>[0-9a-f-]+)/$"), "<str:uuid>/")
        self.assertIsNone(D.path_text(r"^(?P<p>[a-z/]+)/$"))                  # str never crosses a '/'
        self.assertEqual(D.path_text(r"^(?P<pk>\d+)/$"), "<int:pk>/")         # the specific converters still win

    def test_url_regex_keyword_is_passed_positionally(self):
        line = '    url(regex=r"^$", view=views.UserListView.as_view(), name="list"),\n'
        self.assertEqual(D.rewrite_url_call(line, "path_if_plain"),
                         '    path("", view=views.UserListView.as_view(), name="list"),\n')
        self.assertEqual(D.rewrite_url_call(line, "re_path"),            # re_path(regex=...) would be a TypeError
                         '    re_path(r"^$", view=views.UserListView.as_view(), name="list"),\n')

    def test_the_owners_own_lines_tell_the_style(self):
        plain = "    url(r'^about/$', v),"
        self.assertEqual(D.style_from_owner([(plain, "    re_path(r'^about/$', v),")]), "re_path")
        self.assertEqual(D.style_from_owner([(plain, "    path('about/', v),")]), "path_if_plain")
        self.assertEqual(D.style_from_owner([("from django.conf.urls import url",
                                              "from django.urls import re_path as url")]), "alias")
        # a pattern path() can't say is no vote: the owner had no choice there
        self.assertIsNone(D.style_from_owner([("    url(r'^a(b|c)/$', v),", "    re_path(r'^a(b|c)/$', v),")]))
        self.assertEqual(D.style_from_owner([(plain, "    path('about/', v),"),
                                             (plain, "    re_path(r'^about/$', v),")]), "path_if_plain")   # tie

    def test_a_draft_that_touches_another_line_is_refused(self):
        self.assertTrue(D.only_line(["a\n", "b\n"], ["a\n", "B\n"], 1))
        self.assertFalse(D.only_line(["a\n", "b\n"], ["A\n", "B\n"], 1))
        self.assertFalse(D.only_line(["a\n", "b\n"], ["a\n", "B\n", "c\n"], 1))

    def test_no_llm_diff_inside_except(self):
        self.assertFalse(D.llm_allowed({"in_except": 1}, 5))
        self.assertFalse(D.llm_allowed({"in_except": 0}, 1))          # fewer than 2 same-shape examples
        self.assertTrue(D.llm_allowed({"in_except": 0}, 2))


class Run(unittest.TestCase):
    """A one-file repo with its own django.utils.translation, through run() with the LLM off."""

    def make(self, translation=TRANSLATION):
        root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, root, True)
        (root / "meridian.toml").write_text(TOML)
        out = root / "out" / RUN
        out.mkdir(parents=True)
        (out / "run.jsonl").write_text(json.dumps({"run_id": RUN, "snapshot_at": SNAP, "repo_shas": "{}",
                                                   "source_hashes": "{}", "config_hash": "x", "image_digest": "x",
                                                   "status": "complete"}) + "\n")
        (out / "call_site.jsonl").write_text("".join(json.dumps(s) + "\n" for s in SITES))
        repo = root / "snapshots" / RUN / "repos" / "o__r"
        (repo / "django" / "utils" / "translation").mkdir(parents=True)
        (repo / "django" / "__init__.py").write_text("")
        (repo / "django" / "utils" / "__init__.py").write_text("")
        (repo / "django" / "utils" / "translation" / "__init__.py").write_text(translation)
        (repo / "app.py").write_text(APP)
        return root

    def draft(self, root):
        with redirect_stdout(StringIO()):
            return D.run(RUN, load_config(root / "meridian.toml"), root=root, out_dir=root / "d")

    def test_llm_off_the_codemod_drafts_and_the_run_completes(self):
        root = self.make()
        drafts, refused, _, rows = self.draft(root)
        self.assertEqual(sorted(drafts["o/r"]), [("app.py", 1), ("app.py", 5), ("app.py", 12)])
        self.assertEqual(drafts["o/r"][("app.py", 1)][1],
                         "from django.utils.translation import gettext, ngettext\n")
        # the codemod may draft inside except; only the LLM may not
        self.assertEqual(drafts["o/r"][("app.py", 12)][1], "        return gettext('oops')\n")
        self.assertEqual({(r["source"], r["model"], r["route"], r["agent"]) for r in rows},
                         {("codemod", None, "rules", "diff_drafter")})
        self.assertEqual(len(rows), 4)                                # one per site, both import sites share a line
        # the rows load into the suggestion table as they are
        blk = root / "blk" / RUN
        blk.mkdir(parents=True)
        shutil.copy(root / "out" / RUN / "run.jsonl", blk)
        shutil.copy(root / "d" / "suggestion.jsonl", blk)
        self.assertEqual(W.write(blk, root / "b.sqlite")["suggestion"], 4)

    def test_a_new_name_that_doesnt_resolve_is_refused(self):
        root = self.make(TRANSLATION.replace("def gettext(m):\n    return m\n\n\n", ""))   # no gettext to land on
        drafts, refused, _, rows = self.draft(root)
        self.assertEqual(drafts["o/r"], {})
        self.assertEqual(refused["o/r"]["resolve"], 4)
        self.assertEqual(rows, [])


# ───── the LLM path: a repo where the codemod refuses one line ─────
# c.py's `url,` sits inside a bracketed import, so the codemod can't see its module line (multi_line_import).
# a.py and b.py are two single-line url imports the codemod drafts: the two same-shape examples the LLM needs.
URL_TOML = """
[run]
handoff_dir = "out/{run_id}"

[datasets.urls]
symbols = [{ name = "url", module = "django.conf.urls" }]
"""
# laid out as in Django (django/urls/conf.py), so Jedi's bundled stubs map onto this code and not stay on themselves
URLS_CONF_PY = ("def include(x):\n    return x\n\n\n"
                "def path(p, v):\n    return p\n\n\n"
                "def re_path(p, v):\n    return p\n")
URLS_PY = "from .conf import include, path, re_path\n"
CONF_URLS_PY = "from django.urls import include, re_path\n\n\ndef url(p, v):\n    return p\n"
URL_FILES = {
    "a.py": "from django.conf.urls import url\n",
    "b.py": "from django.conf.urls import url\n",
    "c.py": "from django.conf.urls import (\n    include,\n    url,\n)\n",
}
URL_SITES = [site("U1", "a.py", 1, 29, "url", "import"), site("U2", "b.py", 1, 29, "url", "import"),
             site("U3", "c.py", 3, 4, "url", "import")]


def url_fixture(in_except=0, files=URL_FILES):
    """(root, cfg): a block with three url import sites, and a repo with its own small django.urls."""
    root = Path(tempfile.mkdtemp())
    (root / "meridian.toml").write_text(URL_TOML)
    out = root / "out" / RUN
    out.mkdir(parents=True)
    (out / "run.jsonl").write_text(json.dumps({"run_id": RUN, "snapshot_at": SNAP, "repo_shas": "{}",
                                               "source_hashes": "{}", "config_hash": "x", "image_digest": "x",
                                               "status": "complete"}) + "\n")
    sites = [dict(s, in_except=in_except) if s["call_site_id"] == "U3" else s for s in URL_SITES]
    (out / "call_site.jsonl").write_text("".join(json.dumps(s) + "\n" for s in sites))
    (out / "team_map.jsonl").write_text(json.dumps({"team": "app"}) + "\n")
    (out / "person.jsonl").write_text(json.dumps({"person_id": "p1", "name": "Ada Lovelace", "is_bot": 0}) + "\n")
    (out / "person_alias.jsonl").write_text(json.dumps({"person_id": "p1", "generic": 0}) + "\n")
    repo = root / "snapshots" / RUN / "repos" / "o__r"
    (repo / "django" / "conf" / "urls").mkdir(parents=True)
    (repo / "django" / "urls").mkdir(parents=True)
    for d in ("django", "django/conf"):
        (repo / d / "__init__.py").write_text("")
    (repo / "django" / "urls" / "__init__.py").write_text(URLS_PY)
    (repo / "django" / "urls" / "conf.py").write_text(URLS_CONF_PY)
    (repo / "django" / "conf" / "urls" / "__init__.py").write_text(CONF_URLS_PY)
    for name, text in files.items():
        (repo / name).write_text(text)
    return root, load_config(root / "meridian.toml")


def stand_in(after):
    """A stand-in LLM that answers every prompt with this after-line, and counts its calls."""
    def llm(model, prompt):
        llm.calls.append(model)
        return {"reply": json.dumps({"text": after, "used": ["before", "examples"]}), "tokens_in": 600,
                "tokens_out": 20, "cost_usd": 0.0003}
    llm.calls = []
    return llm


def draft_urls(root, cfg, **kw):
    with redirect_stdout(StringIO()):
        return D.run(RUN, cfg, root=root, out_dir=False, style="re_path", **kw)


class LLMPath(unittest.TestCase):
    def setUp(self):
        self.root, self.cfg = url_fixture()
        self.addCleanup(shutil.rmtree, self.root, True)
        self.cache = LLMCache(self.root / "llm_cache")
        self.calls = []

    def log(self, **row):
        self.calls.append(row)

    def test_the_codemod_refuses_the_bracketed_line_and_the_llm_drafts_it(self):
        drafts, refused, _, rows = draft_urls(self.root, self.cfg)          # LLM off: the codemod alone
        self.assertNotIn(("c.py", 3), drafts["o/r"])
        self.assertEqual(refused["o/r"]["multi_line_import"], 1)
        llm = stand_in("    re_path,")
        drafts, refused, _, rows = draft_urls(self.root, self.cfg, llm=llm, llm_cache=self.cache, log_call=self.log)
        self.assertEqual(drafts["o/r"][("c.py", 3)][1], "    re_path,\n")
        self.assertEqual(llm.calls, [D.LADDER[0]])                           # the small model passed: no more calls
        self.assertEqual(dict(refused["o/r"]), {})
        row = next(r for r in rows if r["target"] == "U3")
        self.assertEqual((row["source"], row["model"], row["agent"], row["route"]),
                         ("llm", D.LADDER[0], "diff_drafter", "rules"))
        self.assertEqual({r["source"] for r in rows if r["target"] != "U3"}, {"codemod"})
        self.assertEqual(len(self.calls), 1)
        c = self.calls[0]
        self.assertEqual((c["agent"], c["cache_hit"], c["gate_pass"], c["source"], c["facts_hash"]),
                         ("diff_drafter", False, True, "llm", row["facts_hash"]))

    def test_a_warm_cache_with_the_llm_off_drafts_the_same_with_0_calls(self):
        draft_urls(self.root, self.cfg, llm=stand_in("    re_path,"), llm_cache=self.cache)
        drafts, _, _, rows = draft_urls(self.root, self.cfg, llm_cache=self.cache, log_call=self.log)
        self.assertEqual(drafts["o/r"][("c.py", 3)][1], "    re_path,\n")
        self.assertEqual([(c["cache_hit"], c["tokens_in"], c["cost_usd"]) for c in self.calls], [(True, 0, 0.0)])

    def test_a_cold_cache_with_the_llm_off_drafts_nothing_or_stops_when_strict(self):
        drafts, refused, _, _ = draft_urls(self.root, self.cfg, llm_cache=self.cache, log_call=self.log)
        self.assertNotIn(("c.py", 3), drafts["o/r"])
        self.assertEqual(refused["o/r"]["multi_line_import"], 1)
        self.assertEqual(self.calls, [])                                      # no answer, no call: nothing logged
        with self.assertRaises(D.CacheMiss):
            draft_urls(self.root, self.cfg, llm_cache=self.cache, strict_cache=True)

    def test_the_gate_stops_a_line_that_adds_a_comment_a_number_or_a_person(self):
        for bad in ("    re_path,  # done", "    re_path, 42,", "    re_path, Ada_Lovelace,  # Ada Lovelace",
                    "    re_path, 'ada@example.com',"):
            with self.subTest(bad=bad):
                self.calls, cache = [], LLMCache(self.root / "c" / str(len(bad)))
                llm = stand_in(bad)
                drafts, refused, _, rows = draft_urls(self.root, self.cfg, llm=llm, llm_cache=cache,
                                                      log_call=self.log)
                self.assertNotIn(("c.py", 3), drafts["o/r"])
                self.assertEqual(llm.calls, list(D.LADDER))                   # every model tried, then nothing
                self.assertEqual([c["source"] for c in self.calls], [None] * 3)
                self.assertNotIn("U3", {r["target"] for r in rows})

    def test_a_ladder_passed_in_is_the_one_tried_and_ladder_itself_stays(self):      # GAPS 2
        llm = stand_in("    re_pathx,")                                      # fails resolve on every model
        draft_urls(self.root, self.cfg, llm=llm, llm_cache=self.cache, ladder=("gemini-flash", "mistral-medium"))
        self.assertEqual(llm.calls, ["gemini-flash", "mistral-medium"])
        self.assertEqual(D.LADDER, ("claude-haiku-5-5", "claude-sonnet-5-5", "claude-opus-5-5"))

    def test_a_line_that_doesnt_resolve_moves_up_the_ladder(self):
        drafts, _, _, _ = draft_urls(self.root, self.cfg, llm=stand_in("    re_pathx,"), llm_cache=self.cache,
                                     log_call=self.log)
        self.assertNotIn(("c.py", 3), drafts["o/r"])
        self.assertEqual([c["gate_pass"] for c in self.calls], [True] * 3)    # it passed the gate, not resolve

    def test_no_llm_inside_except_or_with_fewer_than_2_examples(self):
        for root, cfg in (url_fixture(in_except=1), url_fixture(files={**URL_FILES, "b.py": "x = 1\n"})):
            self.addCleanup(shutil.rmtree, root, True)
            llm = stand_in("    re_path,")
            drafts, _, _, _ = draft_urls(root, cfg, llm=llm, llm_cache=LLMCache(root / "llm_cache"))
            self.assertNotIn(("c.py", 3), drafts["o/r"])
            self.assertEqual(llm.calls, [])

    def test_the_prompt_never_carries_a_person_and_its_version_moves_with_the_text(self):
        llm = stand_in("    re_path,")
        draft_urls(self.root, self.cfg, llm=llm, llm_cache=self.cache)
        for entry in self.cache.entries().values():
            self.assertNotIn("Ada", json.dumps(entry))
        v = D.prompt_version()
        with unittest.mock.patch.object(D, "SYSTEM", D.SYSTEM + " "):
            self.assertNotEqual(D.prompt_version(), v)


TEAM = "c5118b019d642a9b"
ROOT = data_root()
TEAM_SNAP = ROOT / "snapshots" / TEAM


@unittest.skipUnless(os.environ.get("MERIDIAN_RUN_ID") == TEAM and TEAM_SNAP.is_dir(),
                     f"needs MERIDIAN_RUN_ID={TEAM} and {TEAM_SNAP} (team data, never in CI)")
class Bars(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cfg = load_config(CONFIG)
        cls.cache = Path(tempfile.mkdtemp())
        cls.sites = D.present_sites(handoff(cls.cfg, TEAM, ROOT), TEAM)
        cls.child = {"django/django": "c651331b34"}
        for d in cls.cfg["datasets"].values():
            cls.child.update({r["repo"]: r["ref"][:-2] for r in d.get("repos", []) if r["ref"].endswith("^1")})

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.cache, ignore_errors=True)

    def scored(self, repos, style):
        with redirect_stdout(StringIO()):
            drafts, _, _, _ = D.run(TEAM, self.cfg, root=ROOT, out_dir=False, style=style, cache=self.cache,
                                    repos=set(repos))
        got = {}
        for repo in repos:
            rd = repo_checkout(TEAM_SNAP, repo)
            m, n, _ = D.score(rd, git(rd, "rev-parse", "HEAD").strip(), self.child[repo],
                              [s for s in self.sites if s["repo"] == repo], drafts.get(repo, {}))
            got[repo] = (m, n)
        return got

    def test_django_bar(self):
        m, n = self.scored(["django/django"], "learned")["django/django"]
        self.assertEqual(n, 267)
        self.assertGreaterEqual(m / n, 0.90, f"{m}/{n}")              # 254/267 = 0.951 on Nov 5

    def test_packages_owner_seeded(self):
        """Decision 13: the owner migrates their first url() files by hand (until 3 style votes, at most 3 files);
        the drafter takes their style and drafts the rest. Scored only on the files it wasn't shown. On the 12
        finished repos: 67.0% against 51.8% for the default style on the same lines (Nov 11)."""
        seeded = default = n = 0
        for repo in self.DONE:
            rd = repo_checkout(TEAM_SNAP, repo)
            parent = git(rd, "rev-parse", "HEAD").strip()
            ss = [s for s in self.sites if s["repo"] == repo]
            seed, style = D.owner_seed(rd, parent, self.child[repo], ss)
            held = [s for s in ss if s["path"] not in seed]
            got = self.drafted([repo], style)[repo]
            base = self.drafted([repo], "path_if_plain")[repo]
            m, k, _ = D.score(rd, parent, self.child[repo], held, got)
            seeded, n = seeded + m, n + k
            default += D.score(rd, parent, self.child[repo], held, base)[0]
        self.assertGreaterEqual(seeded / n, 0.65, f"{seeded}/{n}")
        self.assertGreaterEqual(seeded - default, 0.10 * n, f"seeded {seeded}, default {default} of {n}")

    def drafted(self, repos, style):
        with redirect_stdout(StringIO()):
            return D.run(TEAM, self.cfg, root=ROOT, out_dir=False, style=style, cache=self.cache,
                         repos=set(repos))[0]

    DONE = ["carltongibson/django-filter", "cookiecutter/cookiecutter-django", "django-cms/django-cms",
            "django-guardian/django-guardian", "django-oscar/django-oscar", "django-tastypie/django-tastypie",
            "django/channels", "encode/django-rest-framework", "iMerica/dj-rest-auth",
            "pennersr/django-allauth", "jazzband/django-two-factor-auth", "wagtail/wagtail"]

    @unittest.expectedFailure
    def test_packages_bar(self):
        """>= 90% on the 12 packages whose migration finished. Codemod alone: 49% with the best single style
        (path_if_plain); 68% even with each repo's best style. Each real migration chose its own style per repo."""
        got = self.scored(self.DONE, "learned")
        m, n = sum(v[0] for v in got.values()), sum(v[1] for v in got.values())
        self.assertGreaterEqual(m / n, 0.90, f"{m}/{n}")


if __name__ == "__main__":
    unittest.main()
