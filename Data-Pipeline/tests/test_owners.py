"""D3 tests: person and person_alias on a fixture repo with made-up authors.

    python -m unittest tests.test_owners -v      # from Data-Pipeline/
"""
import contextlib
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import meridian.history as C
import meridian.owners as O
import meridian.writer as W
from meridian.common import CONFIG, check_fixed, load_config, write_jsonl

RUN = "abcdef0123456789"
AUTHORS = [
    ("Ann Lee <ann@example.com>", ""),
    ("ann lee <ann.lee@corp.example>", ""),                     # same name -> Ann
    ("J Smith <jsmith@a.example>", ""),
    ("John Smith <jsmith@b.example>", ""),                      # same local part -> one person
    ("C. Old <carol-old@x.example>", ""),                       # .mailmap -> carol@new.example
    ("Carol Ng <carol@new.example>", "\n\nCo-authored-by: Dee Wu < Dee@Example.com >"),  # spaces are stripped
    ("root <root@host1.example>", ""),                          # generic: never merged
    ("root <root@host2.example>", ""),
    ("dependabot[bot] <49699333+dependabot[bot]@users.noreply.github.com>", ""),
]


def make_repo(path, commits, mailmap=""):
    """commits: [(author, authored_at, file, message)], oldest first. -> HEAD sha."""
    path.mkdir(parents=True)

    def run(*a, date="2026-01-01T00:00:00Z"):
        return subprocess.run(["git", "-C", str(path), *a], check=True, capture_output=True, text=True,
                              env={**os.environ, "GIT_AUTHOR_DATE": date, "GIT_COMMITTER_DATE": date}).stdout.strip()

    run("init", "--quiet", "--initial-branch=main")
    if mailmap:
        (path / ".mailmap").write_text(mailmap)
    for i, (who, date, name, msg) in enumerate(commits):
        (path / name).write_text(f"x = {i}\n")
        run("add", ".")
        run("-c", "user.name=Merger", "-c", "user.email=merger@example.com", "commit", "--quiet",
            "--author", who, "-m", msg, date=date)
    return run("rev-parse", "HEAD")


def fixture_repo(path):
    return make_repo(path, [(who, f"2026-01-{i + 1:02d}T00:00:00Z", f"f{i}.py", f"c{i}{extra}")
                            for i, (who, extra) in enumerate(AUTHORS)],
                     "Carol Ng <carol@new.example> <carol-old@x.example>\n")


class People(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp())
        snap = cls.tmp / "snapshots" / RUN
        cls.sha = fixture_repo(snap / "repos" / "fix__repo")
        (snap / "manifest.json").write_text(json.dumps({"run_id": RUN, "repos": {"fix/repo": cls.sha}}))
        commits, files = C.read_repo(snap / "repos" / "fix__repo", "fix/repo", cls.sha, RUN)
        cls.out = cls.tmp / "out" / RUN
        cls.out.mkdir(parents=True)
        write_jsonl(cls.out / "git_commit.jsonl", commits, ("repo", "sha"))
        write_jsonl(cls.out / "commit_file.jsonl", files, ("repo", "sha", "path"))
        (cls.out / "run.jsonl").write_text(json.dumps({
            "run_id": RUN, "snapshot_at": "2026-02-01T00:00:00Z", "repo_shas": json.dumps({"fix/repo": cls.sha}),
            "source_hashes": "{}", "config_hash": "x", "image_digest": "x", "status": "complete"}) + "\n")
        (cls.out / "ticket.jsonl").write_text("")
        (cls.out / "commit_ticket.jsonl").write_text("")
        cls.config = cls.tmp / "meridian.toml"  # the real config, plus the fixture repo as one more repo-owned dataset
        cls.config.write_text(CONFIG.read_text() + '\n[datasets.fixture]\nrepos = [{ repo = "fix/repo", ref = "x" }]\n'
                              'owner_mode = "repo"\n\n[teams.fixture]\nrule = "repo"\n')
        cls.person, cls.alias, cls.team_map, cls.file_owner = O.main(RUN, cls.config, root=cls.tmp)
        cls.pid = {a["email"]: a["person_id"] for a in cls.alias}
        cls.by_email = {a["email"]: a for a in cls.alias}

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def same(self, *emails):
        return len({self.pid[e] for e in emails}) == 1

    def test_merges_on_name_local_part_and_mailmap(self):
        self.assertTrue(self.same("ann@example.com", "ann.lee@corp.example"))
        self.assertTrue(self.same("jsmith@a.example", "jsmith@b.example"))
        self.assertTrue(self.same("carol-old@x.example", "carol@new.example"))
        self.assertEqual(self.by_email["carol-old@x.example"]["source"], "mailmap")
        self.assertEqual(self.by_email["ann@example.com"]["source"], "name_email")

    def test_generic_and_bots_never_merge(self):
        self.assertFalse(self.same("root@host1.example", "root@host2.example"))
        self.assertEqual((self.by_email["root@host1.example"]["generic"],
                          self.by_email["root@host1.example"]["source"]), (1, "single"))
        bot = self.pid["49699333+dependabot[bot]@users.noreply.github.com"]
        self.assertEqual({p["person_id"]: p["is_bot"] for p in self.person}[bot], 1)
        self.assertEqual(sum(p["is_bot"] for p in self.person), 1)

    def test_coauthor_kept_committer_not(self):
        self.assertIn("dee@example.com", self.pid)
        self.assertNotIn("merger@example.com", self.pid)

    def test_person_id_is_hash_of_smallest_email(self):
        self.assertEqual(self.pid["ann.lee@corp.example"], O.h("ann.lee@corp.example"))
        self.assertEqual(self.pid["carol@new.example"], O.h("carol-old@x.example"))
        self.assertEqual(len(self.person), 7)  # ann, smith, carol, dee, root x2, bot

    def test_person_name_is_most_common_mapped_name(self):
        name = {p["person_id"]: p["name"] for p in self.person}
        self.assertEqual(name[self.pid["carol@new.example"]], "Carol Ng")

    def test_rerun_same_bytes_and_loads(self):
        before = {p: (self.out / p).read_bytes()
                  for p in ("person.jsonl", "person_alias.jsonl", "team_map.jsonl", "file_owner.jsonl")}
        O.main(RUN, self.config, root=self.tmp)
        self.assertEqual(before, {p: (self.out / p).read_bytes() for p in before})
        counts = W.write(self.out, self.tmp / "db.sqlite")
        self.assertEqual((counts["person"], counts["person_alias"], counts["team_map"], counts["file_owner"]),
                         (7, 10, 1, 10))

    def test_every_file_at_the_sha_has_an_owner(self):
        self.assertEqual(sorted(r["path"] for r in self.file_owner),
                         sorted([".mailmap"] + [f"f{i}.py" for i in range(9)]))
        self.assertEqual({(r["owner_team"], r["basis"]) for r in self.file_owner}, {("fix/repo", "repo")})
        self.assertEqual(self.team_map, [{"dataset": "fixture", "subject_kind": "repo", "subject": "fix/repo",
                                          "team": "fix/repo", "rule": "repo"}])

    def test_top_lists_groups_joined_on_a_local_part_with_other_names(self):
        with contextlib.redirect_stdout(io.StringIO()) as got:
            O.main(RUN, self.config, root=self.tmp, top=1)
        risky = got.getvalue().split("shared local part")[1]
        self.assertIn("jsmith@b.example", risky)     # J Smith ~ John Smith on jsmith@
        self.assertNotIn("ann@example.com", risky)   # joined on the same name

    def test_emails_must_match_git_commit(self):
        gc = self.out / "git_commit.jsonl"
        keep = gc.read_text()
        try:
            gc.write_text("".join(l for l in keep.splitlines(True) if "jsmith@a.example" not in l))
            with self.assertRaises(SystemExit):
                O.main(RUN, self.config, root=self.tmp)
        finally:
            gc.write_text(keep)


class Modes(unittest.TestCase):
    """D5 through main(): two author-mode datasets (Spark-like) and a repo-mode package pinned years before
    snapshot_at."""
    ANN, CY, BO = "Ann Lee <ann@a.example>", "Cy Ray <cy@c.example>", "Bo Tan <bo@b.example>"
    BOT = "dependabot[bot] <49699333+dependabot[bot]@users.noreply.github.com>"
    REPOS = {
        "fx/spark": [(ANN, "2020-01-01T00:00:00Z", "old.py", "FX-1 old"),
                     (ANN, "2026-08-01T00:00:00Z", "a.py", "FX-1 one"),
                     (ANN, "2026-08-02T00:00:00Z", "a.py", "FX-1 two"), (BOT, "2026-08-03T00:00:00Z", "a.py", "bump"),
                     (BOT, "2026-08-04T00:00:00Z", "a.py", "bump"), (BOT, "2026-08-05T00:00:00Z", "a.py", "bump"),
                     (CY, "2026-08-06T00:00:00Z", "c.py", "no key")],
        "fx/other": [(ANN, "2026-08-07T00:00:00Z", "b.py", "no key")],   # ann has no component here
        "fx/pkg": [(BO, "2017-03-01T00:00:00Z", "p.py", "one"), (BO, "2017-03-02T00:00:00Z", "p.py", "two")],
    }

    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp())
        snap, out = cls.tmp / "snapshots" / RUN, cls.tmp / "out" / RUN
        out.mkdir(parents=True)
        shas, commits, files = {}, [], []
        for repo, cs in cls.REPOS.items():
            co = snap / "repos" / repo.replace("/", "__")
            shas[repo] = make_repo(co, cs)
            c, f = C.read_repo(co, repo, shas[repo], RUN)
            commits, files = commits + c, files + f
        (snap / "manifest.json").write_text(json.dumps({"run_id": RUN, "repos": shas}))
        write_jsonl(out / "git_commit.jsonl", commits, ("repo", "sha"))
        write_jsonl(out / "commit_file.jsonl", files, ("repo", "sha", "path"))
        write_jsonl(out / "run.jsonl", [{"run_id": RUN, "snapshot_at": "2026-10-01T00:00:00Z"}])
        write_jsonl(out / "ticket.jsonl", [{"key": "FX-1", "components": '["SQL"]'}])
        write_jsonl(out / "commit_ticket.jsonl",
                    [{"sha": c["sha"], "key": "FX-1"} for c in commits if c["subject"].startswith("FX-1")])
        config = cls.tmp / "meridian.toml"
        config.write_text(CONFIG.read_text() + "".join(
            f'\n[datasets.{ds}]\nrepos = [{{ repo = "{repo}", ref = "x" }}]\nowner_mode = "{mode}"\n'
            f'[teams.{ds}]\nrule = "{rule}"\n'
            + ('unassigned = "unassigned"\n' if mode == "author" else "")
            for ds, repo, mode, rule in [("fx_a", "fx/spark", "author", "person_component"),
                                         ("fx_b", "fx/other", "author", "person_component"),
                                         ("fx_p", "fx/pkg", "repo", "repo")]))
        person, alias, cls.team_map, file_owner = O.main(RUN, config, root=cls.tmp)
        cls.pid = {a["email"]: a["person_id"] for a in alias}
        cls.own = {r["path"]: r for r in file_owner}

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_bots_dont_count(self):
        a = self.own["a.py"]
        self.assertEqual((a["owner_team"], a["share"], a["n_commits"], a["top_person_id"]),
                         ("SQL", 1.0, 2, self.pid["ann@a.example"]))

    def test_no_commits_in_window_is_basis_none(self):
        o = self.own["old.py"]
        self.assertEqual((o["owner_team"], o["basis"], o["n_commits"], o["top_person_id"], o["last_commit_at"]),
                         ("unowned", "none", 0, None, "2020-01-01T00:00:00Z"))

    def test_unassigned_person_never_owns(self):
        self.assertEqual((self.own["c.py"]["owner_team"], self.own["c.py"]["basis"]), ("unowned", "author"))

    def test_team_is_per_dataset(self):
        # ann is SQL in fx_a but unassigned in fx_b: b.py takes fx_b's teams, not the last dataset's
        self.assertEqual(self.own["b.py"]["owner_team"], "unowned")
        teams = {(r["dataset"], r["subject"]): r["team"] for r in self.team_map if r["subject_kind"] == "person"}
        self.assertEqual((teams["fx_a", self.pid["ann@a.example"]], teams["fx_b", self.pid["ann@a.example"]]),
                         ("SQL", "unassigned"))

    def test_repo_window_ends_at_the_pinned_commit(self):
        p = self.own["p.py"]
        self.assertEqual((p["owner_team"], p["basis"], p["n_commits"], p["top_person_id"], p["last_commit_at"]),
                         ("fx/pkg", "repo", 2, self.pid["bo@b.example"], "2017-03-02T00:00:00Z"))


class Merge(unittest.TestCase):
    ident = load_config(CONFIG)["identity"]

    def test_short_local_parts_dont_merge(self):
        _, alias, _ = O.merge([("dev@a.example", "dev@a.example", "Al", []),
                               ("dev@b.example", "dev@b.example", "Bo", [])], self.ident)
        self.assertEqual(len({a["person_id"] for a in alias}), 2)

    def test_local_part_shared_by_many_names_doesnt_merge(self):
        _, alias, stats = O.merge([(f"chris@{d}.example", f"chris@{d}.example", n, [])
                                   for d, n in [("a", "Chris Adams"), ("b", "Chris Lamb"), ("c", "Chris May")]],
                                  self.ident)
        self.assertEqual(len({a["person_id"] for a in alias}), 3)
        self.assertEqual(stats["shared"], {"chris"})

    def test_config_the_code_doesnt_implement_stops(self):
        check_fixed(load_config(CONFIG))  # the real config is what the code does
        cfg = load_config(CONFIG)
        cfg["ownership"]["skip_merges"] = False
        with self.assertRaises(SystemExit):
            check_fixed(cfg)

    def test_local_part_min_length_comes_from_config(self):
        recs = [("jojo@a.example", "jojo@a.example", "Al", []), ("jojo@b.example", "jojo@b.example", "Bo", [])]
        self.assertEqual(len({a["person_id"] for a in O.merge(recs, self.ident)[1]}), 1)
        self.assertEqual(len({a["person_id"] for a in O.merge(recs, {**self.ident, "local_part_min_length": 5})[1]}), 2)

    def test_transitive_chain_is_one_risky_person(self):
        # A ~ B by name, B ~ C by local part: one person, listed by --top because C's name differs
        _, alias, stats = O.merge([("ann.lee@a.example", "ann.lee@a.example", "Ann Lee", []),
                                   ("alee@b.example", "alee@b.example", "Ann Lee", []),
                                   ("alee@c.example", "alee@c.example", "Lee Ann", [])], self.ident)
        self.assertEqual({a["person_id"] for a in alias}, {O.h("alee@b.example")})
        self.assertEqual(stats["risky"], {O.h("alee@b.example")})

    def test_generic_name_with_real_email_not_merged_on_name(self):
        _, alias, _ = O.merge([("alice@a.example", "alice@a.example", "jenkins", []),
                               ("bob@b.example", "bob@b.example", "jenkins", [])], self.ident)
        self.assertEqual(len({a["person_id"] for a in alias}), 2)
        self.assertTrue(all(a["generic"] for a in alias))


class Teams(unittest.TestCase):
    """D4: a person's team = their most common Jira component in the window, 1/n per component."""
    SINCE, UNTIL = "2026-01-01T00:00:00Z", "2026-07-01T00:00:00Z"
    TICKETS = {"T-1": ["SQL"], "T-2": ["Core"], "T-3": ["SQL", "Core", "ML"], "T-4": [], "T-5": ["ML"]}

    def teams(self, *commits):
        """commits: (email, 'MM-DD' in 2026 or 'YYYY-MM-DD', [keys], is_merge)."""
        rows, keys = [], {}
        for i, (email, day, ks, merge) in enumerate(commits):
            day = day if len(day) == 10 else f"2026-{day}"
            rows.append({"author_email": email, "authored_at": f"{day}T00:00:00Z", "seq": i, "sha": f"s{i}",
                         "is_merge": merge})
            keys[f"s{i}"] = ks
        pid = {r["author_email"]: r["author_email"] for r in rows}
        return O.person_teams(rows, keys, self.TICKETS, pid, self.SINCE, self.UNTIL, "unassigned")

    def test_most_common_component_wins(self):
        self.assertEqual(self.teams(("a", "02-01", ["T-1"], 0), ("a", "03-01", ["T-1"], 0),
                                    ("a", "04-01", ["T-2"], 0))["a"], "SQL")

    def test_n_components_give_one_nth_each(self):
        # T-3 gives SQL, Core, ML 1/3 each: one T-5 (ML 1) beats it
        self.assertEqual(self.teams(("a", "02-01", ["T-3"], 0), ("a", "01-15", ["T-5"], 0))["a"], "ML")

    def test_three_thirds_tie_with_one(self):
        # T-3 three times: SQL, Core, ML 1/3 + 1/3 + 1/3 each. T-2 (Core 1) and T-1 (SQL 1) make SQL = Core = 2: newest
        # wins
        got = self.teams(("a", "02-01", ["T-3"], 0), ("a", "02-02", ["T-3"], 0), ("a", "02-03", ["T-3"], 0),
                         ("a", "03-01", ["T-2"], 0), ("a", "03-02", ["T-1"], 0))
        self.assertEqual(got["a"], "SQL")

    def test_tie_goes_to_most_recent_commit(self):
        self.assertEqual(self.teams(("a", "02-01", ["T-1"], 0), ("a", "03-01", ["T-2"], 0))["a"], "Core")
        self.assertEqual(self.teams(("a", "03-01", ["T-1"], 0), ("a", "02-01", ["T-2"], 0))["a"], "SQL")

    def test_window_merges_and_no_components_are_unassigned(self):
        got = self.teams(("old", "2025-06-01", ["T-1"], 0), ("m", "02-01", ["T-1"], 1),
                         ("none", "02-01", ["T-4"], 0), ("nokey", "02-01", [], 0))
        self.assertEqual(got, {"old": "unassigned", "m": "unassigned", "none": "unassigned", "nokey": "unassigned"})

    def test_after_snapshot_doesnt_count(self):
        self.assertEqual(self.teams(("a", "08-01", ["T-1"], 0))["a"], "unassigned")


class FileOwner(unittest.TestCase):
    """D5: owner per file."""
    cfg = load_config(CONFIG)
    TEAM = {"ann": "SQL", "bo": "SQL", "ed": "SQL", "cy": "Core", "di": "unassigned"}.get

    def ts(self, *people):
        return [(f"2026-01-{i + 1:02d}T00:00:00Z", p) for i, p in enumerate(people)]

    def own(self, *people):
        return O.team_owner(self.ts(*people), self.TEAM, "unassigned", self.cfg["ownership"]["min_share"])

    def test_team_share_sums_people(self):
        # cy is the top person, SQL the top team
        self.assertEqual(self.own("ann", "bo", "ed", "cy", "cy"), ("SQL", 0.6))

    def test_tie_goes_to_most_recent_and_low_share_is_unowned(self):
        self.assertEqual(self.own("cy", "ann")[0], "SQL")
        self.assertEqual(self.own("ann", "cy")[0], "Core")
        self.assertEqual(self.own("ann", "cy", "di", "di", "di", "di", "di", "di", "di", "di"), ("unowned", 0.1))

    def test_share_of_exactly_min_share_owns(self):
        self.assertEqual(self.own("ann", "bo", "ed", *["di"] * 7), ("SQL", 0.3))

    def test_unassigned_never_owns(self):
        self.assertEqual(self.own("di", "di", "di", "ann"), ("unowned", 0.25))
        self.assertEqual(self.own(), ("unowned", 0.0))

    def test_touches_follow_renames_and_skip_merges(self):
        c = lambda sha, seq, merge=0: {"sha": sha, "seq": seq, "authored_at": f"2026-01-0{seq + 1}T00:00:00Z",
                                       "is_merge": merge}
        commits = [c("a", 0), c("b", 1), c("m", 2, 1), c("d", 3), c("fmt", 4)]
        files = [{"sha": "a", "path": "old.py", "old_path": None}, {"sha": "b", "path": "mid.py", "old_path": "old.py"},
                 {"sha": "m", "path": "new.py", "old_path": None}, {"sha": "d", "path": "new.py", "old_path": "mid.py"},
                 {"sha": "fmt", "path": "new.py", "old_path": None}]
        got = O.touches(commits, files, skip={"fmt"})
        self.assertEqual({p: [x["sha"] for x in cs] for p, cs in got.items()}, {"new.py": ["a", "b", "d"]})

    def test_django_path_teams(self):
        prefixes = self.cfg["teams"]["django"]["prefixes"]
        got = {p: O.path_team(p, prefixes) for p in ("django/contrib/admin/options.py", "django/db/models/query.py",
                                                     "django/__init__.py", "tests/runtests.py", "setup.py")}
        self.assertEqual(got, {"django/contrib/admin/options.py": ("django/contrib/admin/", "contrib.admin"),
                               "django/db/models/query.py": ("django/db/", "db"), "django/__init__.py": ("", "root"),
                               "tests/runtests.py": ("tests/", "tests"), "setup.py": ("", "root")})

    def test_last_toucher_test(self):
        d = lambda m: f"2026-{m:02d}-01T00:00:00Z"
        # owner SQL, last toucher Core, next SQL
        hist = {"a.py": [(d(2), "ann"), (d(3), "ann"), (d(4), "cy"), (d(8), "bo")],
                "b.py": [(d(2), "cy"), (d(8), "cy")],                                 # owner Core = last = next
                "tests/c.py": [(d(2), "ann"), (d(8), "cy")]}                          # tests are not scored
        src, test = (re.compile(self.cfg["checks"][k]) for k in ("source_files", "test_files"))
        team, person = O.last_toucher_test(hist, self.TEAM, "unassigned", 0.30, d(1), d(6), d(12), src, test)
        self.assertEqual(team, {"n": 2, "owner": 2, "last": 1})
        self.assertEqual(person, {"n": 2, "owner": 1, "last": 1})


if __name__ == "__main__":
    unittest.main()
