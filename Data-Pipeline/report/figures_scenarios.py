"""Does the real block carry the signal the product needs? Runs every named query in KNOWLEDGE-BASE.md (the
"situations") against a real run's meridian.sqlite, one dataset at a time, scores the block against the hand-checked
labels, replays the pinned repos' next commit through the push check, and draws the signal charts.

    python report/figures_scenarios.py --db <meridian.sqlite> [--mirrors ../snapshots/_cache/repos] [--replay]

Reads the block read-only. Writes report/figures/*.png and report/figures/scenarios.json (every number the appendix
quotes). --replay reruns the push-check replay (git checkouts, slow); otherwise its cached JSON is reused.
Needs matplotlib for the charts; the push replay imports meridian.pushcheck, so it also needs tree-sitter
(requirements.txt).
"""
import argparse, csv, json, re, sqlite3, subprocess, sys, tempfile, time, tomllib
from collections import Counter, defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
DP = HERE.parent
FIG = HERE / "figures"
sys.path.insert(0, str(DP))

# Each scenario: which rows of the block belong to the dataset (a WHERE per table, shadowed by TEMP views so the
# doc's queries run unchanged), and the asof moment.
DJ = "repo = 'django/django'"
PK = "repo NOT IN ('django/django', 'apache/spark')"
SP = "repo = 'apache/spark'"
RUN_AT = "2026-10-01T01:23:03Z"  # run 70dd1dabfed544b8 snapshot_at
SCOPES = {
    # asof = the run's time for all: call_site.known_at is when the run learned a fact, so any earlier moment sees nothing
    # (Django's code is pinned at 4353640ea9, 2017-02-03, but the block only knows it since 2026-10-01)
    "django": dict(at=RUN_AT, repo="django/django", symbol="ugettext*",
                   where={"call_site": DJ, "resolution": DJ, "call_edge": DJ, "deprecation": DJ, "module_import": DJ,
                          "file_owner": DJ, "file_bug_fixes": DJ, "git_commit": DJ, "commit_file": DJ,
                          "ticket": "0", "ticket_map": "0", "ticket_link": "0", "blocked_by": "0",
                          "chain": "from_key IN (SELECT DISTINCT owner_team FROM main.call_site WHERE " + DJ + ")",
                          "coverage": "1", "team_map": "dataset = 'django'"}),
    "packages": dict(at=RUN_AT, repo="20 django packages", symbol="url",
                     where={"call_site": PK, "resolution": PK, "call_edge": PK, "deprecation": PK, "module_import": PK,
                            "file_owner": PK, "file_bug_fixes": PK, "git_commit": PK, "commit_file": PK,
                            "ticket": "0", "ticket_map": "0", "ticket_link": "0", "blocked_by": "0",
                            "chain": "from_key IN (SELECT DISTINCT owner_team FROM main.call_site WHERE " + PK + ")",
                            "coverage": "1", "team_map": "dataset = 'django_packages'"}),
    "spark": dict(at=RUN_AT, repo="apache/spark", symbol="(none tracked)",
                  where={"call_site": SP, "resolution": SP, "call_edge": SP, "deprecation": SP, "module_import": SP,
                         "file_owner": SP, "file_bug_fixes": SP, "git_commit": SP, "commit_file": SP,
                         "ticket": "tracker = 'jira'", "ticket_map": "key LIKE 'SPARK-%'", "ticket_link": "key LIKE 'SPARK-%'",
                         "chain": "0", "blocked_by": "0", "coverage": "1", "team_map": "dataset = 'spark'"}),
    "gitlab": dict(at=RUN_AT, repo="gitlab-org/gitlab (issues only)", symbol="(none: Ruby)",
                   where={"call_site": "0", "resolution": "0", "call_edge": "0", "deprecation": "0", "module_import": "0",
                          "file_owner": "0", "file_bug_fixes": "0", "git_commit": "0", "commit_file": "0",
                          "ticket": "tracker = 'gitlab'", "ticket_map": "key LIKE 'gitlab-org/%'",
                          "ticket_link": "key LIKE 'gitlab-org/%'", "chain": "0", "blocked_by": "0", "coverage": "1",
                          "team_map": "0"}),
}
# Django read at the code's own moment: known_at moved to the pin, so days, stalls and owners count from 2017, not 2026
PIN_AT = "2017-02-03T16:11:39Z"  # 4353640ea9
CS_COLS = ("call_site_id, run_id, repo, path, line, col, scope, symbol, role, detection, junk, in_except, owner_team, "
           "bug_fixes, status, effective_date")  # this block is schema v1.1: no effective_sha
SCOPES["django@pin"] = dict(SCOPES["django"], at=PIN_AT, views={
    "call_site": f"SELECT {CS_COLS}, '{PIN_AT}' AS known_at FROM main.call_site WHERE {DJ}"})
# Fixture parameters name fixture rows; on real data each scenario gets the equivalent real target
PARAMS = {
    "django": {"consumer_files": {"team": "contrib.admin"}, "consumer_blocked": {"team": "contrib.admin"},
               "consumer_blocking": {"team": "utils"}, "p1_drift": {"base": RUN_AT},
               "p2_legacy_use": {"base": RUN_AT}, "p3_collision": {"path": "django/contrib/admin/actions.py",
               "scope": "delete_selected", "team": "forms"}, "p7_risky_edit": {"path": "django/contrib/admin/actions.py"},
               "p9_defect_risk": {"sha": "4353640ea9495d58fabd0357253b82de3b069408"},
               "replay_as_of": {"t": "2017-02-03T16:11:39Z"}},
    "packages": {"consumer_files": {"team": "wagtail/wagtail"}, "consumer_blocked": {"team": "wagtail/wagtail"},
                 "consumer_blocking": {"team": "django/django"}, "p1_drift": {"base": RUN_AT},
                 "p2_legacy_use": {"base": RUN_AT}, "replay_as_of": {"t": RUN_AT}},
    "spark": {"p7_risky_edit": {"path": "sql/core/src/main/scala/org/apache/spark/sql/Dataset.scala"},
              "p9_defect_risk": {"sha": "f868de6914d0e6ede86309648614bb436b2b82e6"}},
}
PARAMS["django@pin"] = PARAMS["django"] | {"p1_drift": {"base": PIN_AT}, "p2_legacy_use": {"base": PIN_AT}}
SKIP = {"setup", "schema_outcomes"}
OUTCOME_TABLES = {"suggestion", "outcome", "push_alert"}  # proposed in the doc, not in schema.sql: stage 6


def kb_blocks():
    out = []
    for body in re.findall(r"```sql\n(.*?)```", (DP / "KNOWLEDGE-BASE.md").read_text(encoding="utf-8"), re.S):
        m = re.search(r"^-- name: (\S+)", body, re.M)
        if m:
            p = re.search(r"^-- params: (.*)$", body, re.M)
            e = re.search(r"^-- expect: (.*)$", body, re.M)
            out.append((m.group(1), body, json.loads(p.group(1)) if p else {}, json.loads(e.group(1)) if e else None))
    return out


def connect(db_path):
    db = sqlite3.connect(Path(db_path).resolve().as_uri() + "?mode=ro", uri=True)
    db.execute("PRAGMA cache_size = -65536")  # 64 MB: the machine is short on RAM
    return db


def scoped(db_path, scope):
    db = connect(db_path)
    for t, w in scope["where"].items():
        db.execute(f"CREATE TEMP VIEW {t} AS " + scope.get("views", {}).get(t, f"SELECT * FROM main.{t} WHERE {w}"))
    db.execute("CREATE TEMP TABLE asof (run_id TEXT, at TEXT, repo TEXT, symbol TEXT)")
    # the views above scope the data; scope's repo and symbol are labels ("20 django packages"), never asof filters
    db.execute("INSERT INTO asof VALUES ('70dd1dabfed544b8', ?, NULL, NULL)", (scope["at"],))
    return db


def run_scenarios(db_path, timeout_s=120):
    blocks = kb_blocks()
    setup = next(sql for n, sql, _, _ in blocks if n == "setup")
    out = {}
    for sname, scope in SCOPES.items():
        db = scoped(db_path, scope)
        db.executescript(setup)
        res = {}
        for name, sql, params, expect in blocks:
            if name in SKIP:
                continue
            missing = [t for t in OUTCOME_TABLES if re.search(rf"\b(FROM|JOIN)\s+{t}\b", sql)]
            if missing:
                res[name] = dict(status="no table", note=f"{', '.join(sorted(missing))} not in schema.sql (G1)")
                continue
            p = PARAMS.get(sname, {}).get(name, params)
            t0, deadline = time.time(), time.time() + timeout_s
            db.set_progress_handler(lambda: time.time() > deadline, 100000)
            try:
                rows = [list(r) for r in db.execute(sql, p).fetchall()]
                res[name] = dict(status="ran", n=len(rows), rows=rows[:12], params=p, secs=round(time.time() - t0, 1),
                                 fixture_n=len(expect) if expect is not None else None)
                if name == "owner_teams":
                    res[name]["rows_all"] = rows
            except sqlite3.OperationalError as e:
                res[name] = dict(status="error", note=str(e), secs=round(time.time() - t0, 1))
            db.set_progress_handler(None, 0)
            print(f"{sname:9} {name:28} {res[name].get('status')} n={res[name].get('n')} {res[name].get('secs')}s", flush=True)
        out[sname] = res
        db.close()
    return out


# ───────────── the same queries, with the fixture's symbol swapped for the real one ─────────────

def blast_radius_real(db_path):
    """derive_blast_radius per real symbol (asof names the repo and symbol), next to graph's blast_radius rows."""
    sql = next(s for n, s, _, _ in kb_blocks() if n == "derive_blast_radius")
    db, out = connect(db_path), {}
    db.execute("CREATE TEMP TABLE asof (run_id TEXT, at TEXT, repo TEXT, symbol TEXT)")
    for repo, symbol, direct, inv, files, by_depth in db.execute(
            "SELECT repo, symbol, direct_sites, invocations, files, by_depth FROM blast_radius").fetchall():
        mine = None
        if repo == "django/django":
            db.execute("DELETE FROM asof")
            db.execute("INSERT INTO asof VALUES ('70dd1dabfed544b8', ?, ?, ?)", (RUN_AT, repo, symbol.rsplit(".", 1)[-1]))
            mine = {d: n for d, n in db.execute(sql)}
        out[f"{repo}:{symbol}"] = dict(direct=direct, invocations=inv, files=files, by_depth=json.loads(by_depth), kb_query=mine)
    db.close()
    return out


# ───────────── answer keys (labels/) ─────────────

def read_csv(name):
    return list(csv.DictReader(open(DP / "labels" / name, encoding="utf-8")))


def one(db, sql, params=()):
    return db.execute(sql, params).fetchone()[0]


def score_labels(db_path):
    db, out = connect(db_path), {}
    # Django: the 265 hand-verified edit lines
    gold = {(r["path"], int(r["line"])) for r in read_csv("django_edit_sites_265.csv") if r["is_edit_line"] == "yes"}
    kb = set(db.execute("SELECT DISTINCT path, line FROM call_site WHERE repo = 'django/django'").fetchall())
    kb_py = {s for s in kb if s[0].endswith(".py")}
    out["django_265"] = dict(gold=len(gold), kb_lines=len(kb), kb_py_lines=len(kb_py), tp=len(gold & kb_py),
                             fp=sorted(kb_py - gold)[:10], fn=sorted(gold - kb_py)[:10], outside_py=sorted(kb - kb_py),
                             sites=one(db, "SELECT count(*) FROM call_site WHERE repo = 'django/django'"),
                             roles=dict(db.execute("SELECT role, count(DISTINCT path || ':' || line) FROM call_site "
                                                   "WHERE repo = 'django/django' AND path LIKE '%.py' GROUP BY role").fetchall()))
    # Django: the 542 aliased _() calls (blast radius, not burndown), and 30 of them hand-checked
    out["django_alias"] = dict(zip(("calls", "lines", "files"), db.execute(
        "SELECT count(*), count(DISTINCT path || ':' || line), count(DISTINCT path) FROM resolution "
        "WHERE repo = 'django/django' AND via IN ('import_alias', 'assign_alias')").fetchone()))
    agree = 0
    for r in read_csv("alias_hand_check_30.csv"):
        got = db.execute("SELECT resolved_to FROM resolution WHERE repo = 'django/django' AND path = ? AND line = ? AND col = ?",
                         (r["path"], int(r["line"]), int(r["col"]))).fetchone()
        agree += bool(got and got[0] and got[0].endswith("." + r["checker_answer"]))
    out["django_alias"]["hand_check"] = dict(n=30, agree=agree)
    # Spark: 50 same-file ticket pairs within 30 days, labelled real / no. The block has no Spark call sites, so the
    # best it can do is path level: both tickets mapped to the file
    pairs = read_csv("spark_collision_pairs_50.csv")
    mapped = lambda k, p: db.execute("SELECT 1 FROM ticket_map WHERE key = ? AND target = ?", (k, "apache/spark:" + p)).fetchone()
    out["spark_collisions"] = dict(n=len(pairs), real=sum(p["verdict"] == "real" for p in pairs),
                                   both_mapped_to_path=sum(bool(mapped(p["key_a"], p["path"]) and mapped(p["key_b"], p["path"])) for p in pairs),
                                   inside_agrees=sum((p["inside"] == "1") == (p["verdict"] == "real") for p in pairs),
                                   human_checked=sum(bool(p["human_verdict"]) for p in pairs))
    # GitLab: cross-team blocks. KB side: a blocked_by link whose two issues carry disjoint group:: labels
    comp = dict(db.execute("SELECT key, components FROM ticket WHERE tracker = 'gitlab'").fetchall())
    links = db.execute("SELECT key, other_key FROM ticket_link WHERE link_type = 'blocked_by' AND key LIKE 'gitlab-org/%'").fetchall()
    cross = {(a, b) for a, b in links if a in comp and b in comp and comp[a] != "[]" and comp[b] != "[]"
             and not set(json.loads(comp[a])) & set(json.loads(comp[b]))}
    gold = read_csv("gitlab_cross_team_blocks.csv")
    gset = {(g["blocked_ref"], g["blocker_ref"]): g["verdict"] for g in gold}
    in_gold = [gset[p] for p in cross if p in gset]
    sc = read_csv("gitlab_spot_check_model.csv")
    out["gitlab_blocks"] = dict(
        links=len(links), live=one(db, "SELECT count(*) FROM ticket_link WHERE link_type = 'blocked_by' AND key LIKE 'gitlab-org/%' "
                                       "AND removed_at IS NULL"),
        other_in_snapshot=sum(b in comp for _, b in links), kb_cross_team=len(cross), gold_rows=len(gold),
        gold_verdicts=dict(Counter(g["verdict"] for g in gold)), kb_cross_in_gold=len(in_gold),
        kb_cross_in_gold_verdicts=dict(Counter(in_gold)), gold_wait=sum(v == "wait" for v in gset.values()),
        gold_wait_found_by_kb=sum(v == "wait" and k in cross for k, v in gset.items()),
        gold_wait_any_kb_link=sum(v == "wait" and k in set(links) for k, v in gset.items()),
        spot_check=dict(n=len(sc), holds=sum(r["holds"] == "1" for r in sc)), human_checked=sum(bool(g["human_verdict"]) for g in gold))
    # GitLab: stall reasons (57 blocked issues read by a model). Could the block's links see the upstream ones?
    st = read_csv("gitlab_stall_reasons.csv")
    link = lambda ref: bool(db.execute("SELECT 1 FROM ticket_link WHERE key = ? AND link_type = 'blocked_by'", (ref,)).fetchone())
    out["gitlab_stalls"] = dict(n=len(st), reasons=dict(Counter(r["reason"] for r in st)), in_kb=sum(r["ref"] in comp for r in st),
                                any_kb_link=sum(link(r["ref"]) for r in st),
                                upstream_with_kb_link=sum(r["reason"] == "upstream" and link(r["ref"]) for r in st),
                                cross_team=sum(r["cross_team"] == "1" for r in st))
    # ticket_open says Done/Closed/Resolved; GitLab writes 'closed'
    out["gitlab_open_by_doc_rule"] = list(db.execute(
        "SELECT sum(status NOT IN ('Done', 'Closed', 'Resolved')), sum(status = 'opened'), count(*) FROM ticket "
        "WHERE tracker = 'gitlab'").fetchone())
    db.close()
    return out


# ───────────── Spark: the ticket side, read straight from the block ─────────────

def spark_signal(db_path, threshold):
    db, out = connect(db_path), {}
    last = db.execute("SELECT sha, subject FROM git_commit WHERE repo = 'apache/spark' AND first_parent = 1 "
                      "ORDER BY seq DESC LIMIT 1000").fetchall()
    keyed = {s for (s,) in db.execute("SELECT DISTINCT sha FROM commit_ticket WHERE repo = 'apache/spark'")}
    out["keyed_last_1000"] = sum(s in keyed for s, _ in last)
    known = {k for (k,) in db.execute("SELECT key FROM ticket WHERE tracker = 'jira'")}
    # P8 as pushcheck reads it: a key in the subject that is not a ticket in the snapshot (subjects only here)
    out["p8_dead_keys_last_1000"] = sorted({k for _, s in last for k in re.findall(r"\bSPARK-\d+\b", s)} - known)
    out["map_by_method"] = dict(db.execute("SELECT method, count(DISTINCT key) FROM ticket_map WHERE key LIKE 'SPARK-%' "
                                           "GROUP BY method").fetchall())
    out["mapped_any"] = one(db, "SELECT count(DISTINCT key) FROM ticket_map WHERE key LIKE 'SPARK-%' AND target_kind != 'unmapped'")
    out["tickets"], out["open"] = db.execute("SELECT count(*), sum(status NOT IN ('Done', 'Closed', 'Resolved')) FROM ticket "
                                             "WHERE tracker = 'jira'").fetchone()
    open_mapped = ("FROM ticket t JOIN ticket_map m ON m.key = t.key AND m.target_kind != 'unmapped' "
                   "WHERE t.tracker = 'jira' AND t.status NOT IN ('Done', 'Closed', 'Resolved')")
    out["open_mapped"] = one(db, f"SELECT count(DISTINCT t.key) {open_mapped}")
    out["open_mapped_no_done_when"] = one(db, f"SELECT count(DISTINCT t.key) {open_mapped} AND lower(t.description) NOT LIKE '%done when%'")
    newest = [k for (k,) in db.execute("SELECT key FROM ticket WHERE tracker = 'jira' ORDER BY created_at DESC LIMIT 500")]
    has = lambda k, extra: bool(db.execute(f"SELECT 1 FROM ticket_link WHERE (key = ? OR other_key = ?) {extra}", (k, k)).fetchone())
    out["newest_500"] = dict(any_link=sum(has(k, "AND link_type != 'parent'") for k in newest),
                             blocked_by=sum(has(k, "AND link_type = 'blocked_by'") for k in newest),
                             desc_empty=one(db, "SELECT sum(desc_empty) FROM (SELECT desc_empty FROM ticket WHERE tracker = 'jira' "
                                                "ORDER BY created_at DESC LIMIT 500)"))
    out["ever_blocked_by"] = one(db, "SELECT count(DISTINCT key) FROM ticket_link WHERE key LIKE 'SPARK-%' AND link_type = 'blocked_by'")
    # fragility: per-file bug fixes (risky >= 3), and how many call sites anywhere carry one
    out["file_bug_fixes"] = [n for (n,) in db.execute("SELECT bug_fixes FROM file_bug_fixes")]
    out["call_sites_with_bug_fixes"] = one(db, "SELECT count(*) FROM call_site WHERE bug_fixes > 0")
    # P9 (lines x files) on every first-parent Spark commit since [szz] fix_since, vs SZZ's bug-inducing commits
    inducing = {s for (s,) in db.execute("SELECT DISTINCT inducing_sha FROM bug_inducing WHERE repo = 'apache/spark'")}
    risk = db.execute("SELECT f.sha, sum(coalesce(f.added, 0) + coalesce(f.deleted, 0)) * count(*) FROM commit_file f "
                      "JOIN git_commit g ON g.run_id = f.run_id AND g.repo = f.repo AND g.sha = f.sha "
                      "WHERE f.repo = 'apache/spark' AND g.first_parent = 1 AND g.authored_at >= '2015-01-01' GROUP BY f.sha").fetchall()
    out["p9"] = dict(commits=len(risk), threshold=threshold, flagged=sum(r > threshold for _, r in risk),
                     inducing=sum(s in inducing for s, _ in risk), flagged_inducing=sum(r > threshold and s in inducing for s, r in risk),
                     risk_inducing=[r for s, r in risk if s in inducing], risk_other=[r for s, r in risk if s not in inducing])
    db.close()
    return out


# ───────────── replay: each pinned SHA's next commit, against this block ─────────────

def next_commits():
    cfg = tomllib.loads((DP / "meridian.toml").read_text(encoding="utf-8"))
    out = {"django/django": ("4353640ea9", "c651331b34")}  # the conversion commit
    for r in cfg["datasets"]["django_packages"]["repos"]:  # each pinned at the parent of its url() migration commit
        out[r["repo"]] = (r["ref"], r["ref"].removesuffix("^1"))
    return out


def git(*args):
    return subprocess.run(["git", "-c", "core.quotePath=false", *args], check=True, capture_output=True).stdout.decode("utf-8", "replace")


def conversion_check(db_path, mirrors):
    """Burndown at the next commit: how many of the block's call-site lines that commit edits (old side of its diff)."""
    from meridian.join import parse_diff
    db, out = connect(db_path), {}
    for repo, (parent, child) in next_commits().items():
        m = str(Path(mirrors) / (repo.replace("/", "__") + ".git"))
        edited = defaultdict(set)
        for old, (_, hunks) in parse_diff(git("--git-dir", m, "diff", "-U0", "--no-renames", parent, child)).items():
            for a, b, _, _ in hunks:
                edited[old] |= set(range(a, a + b))
        sites = db.execute("SELECT DISTINCT path, line FROM call_site WHERE repo = ?", (repo,)).fetchall()
        out[repo] = dict(parent=parent, child=child, site_lines=len(sites),
                         edited_by_child=sum(ln in edited.get(p, ()) for p, ln in sites),
                         py_lines_edited=sum(len(v) for p, v in edited.items() if p.endswith(".py")),
                         date=git("--git-dir", m, "log", "-1", "--format=%aI", child).strip())
    db.close()
    return out


def push_replay(db_path, mirrors):
    """meridian.pushcheck on each pinned repo's next commit, with settings.toml's limits and with them lifted."""
    import meridian.pushcheck as P
    from meridian.common import CONFIG, load_config, load_settings
    cfg, settings, out = load_config(CONFIG), load_settings("push_check"), {}
    for repo, (parent, child) in next_commits().items():
        m = Path(mirrors) / (repo.replace("/", "__") + ".git")
        with tempfile.TemporaryDirectory() as tmp:
            co = Path(tmp) / "co"
            git("clone", "-q", "--shared", "--no-checkout", str(m), str(co))
            git("-C", str(co), "checkout", "-q", child)
            res = {}
            for label, s in (("default", settings), ("uncapped", settings | {"max_scope_files": 10 ** 6, "max_lines": 10 ** 6})):
                text = P.push_check(db_path, repo, co, [], cfg, s, f"{parent}...{child}")
                lines = text.splitlines()
                res[label] = dict(checks=dict(Counter(re.findall(r"^- \*\*(P\d) ", text, re.M))),
                                  skipped=next((l for l in lines if l.startswith("Skipped")), None),
                                  scope=next((l for l in lines if "changed files" in l), None),
                                  sample=[l for l in lines if l.startswith("- **")][:6])
        out[repo] = res
        print("replay", repo, res["default"]["checks"], res["default"]["skipped"], res["uncapped"]["checks"], flush=True)
    return out


def mapping_coverage(db_path):
    """Tickets per dataset by their best mapping method (commit key > stack trace > path), unmapped otherwise."""
    db = connect(db_path)
    rank = "CASE method WHEN 'commit_key' THEN 1 WHEN 'stack_trace' THEN 2 WHEN 'path' THEN 3 ELSE 9 END"
    out = {}
    for name, tracker in (("Spark (Jira)", "jira"), ("GitLab", "gitlab")):
        best = Counter(dict(db.execute(
            f"SELECT b, count(*) FROM (SELECT t.key, min(coalesce({rank.replace('method', 'm.method')}, 9)) AS b FROM ticket t "
            f"LEFT JOIN ticket_map m ON m.key = t.key WHERE t.tracker = ? GROUP BY t.key) GROUP BY b", (tracker,)).fetchall()))
        out[name] = {"commit key": best[1], "stack trace": best[2], "path in text": best[3], "unmapped": best[9]}
    db.close()
    return out


# ───────────── charts ─────────────

INK, INK2, GRID, SURF = "#0b0b0b", "#52514e", "#e4e3df", "#fcfcfb"
C1, C2, C3, C4, MUTED = "#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#b7b6b0"  # reference palette slots 1-4, gray


def style(plt):
    plt.rcParams.update({"figure.facecolor": SURF, "axes.facecolor": SURF, "savefig.facecolor": SURF, "axes.edgecolor": GRID,
                         "axes.labelcolor": INK2, "xtick.color": INK2, "ytick.color": INK2, "text.color": INK,
                         "axes.spines.top": False, "axes.spines.right": False, "axes.grid": True, "grid.color": GRID,
                         "grid.linewidth": 0.6, "axes.axisbelow": True, "font.size": 9, "axes.titlesize": 10.5,
                         "axes.titleweight": "bold", "axes.titlelocation": "left", "legend.frameon": False})


def save(fig, name):
    fig.tight_layout()
    fig.savefig(FIG / name, dpi=160)
    print("wrote", FIG / name)


def charts(R):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from datetime import datetime
    style(plt)
    day = lambda s: datetime.fromisoformat(s.replace("Z", "+00:00")).replace(tzinfo=None)

    # 1 Django burndown: the 267 lines by blame date, then the conversion commit
    dates = sorted(day(d) for d in R["django_site_dates"])
    conv = R["conversion"]["django/django"]
    fig, ax = plt.subplots(figsize=(7.2, 3.4))
    ax.step(dates, range(1, len(dates) + 1), where="post", color=C1, lw=2, label="call-site lines in the block, by blame date")
    cd = day(conv["date"][:19] + "Z")
    left = conv["site_lines"] - conv["edited_by_child"]
    added = R["push_replay"]["django/django"]["uncapped"]["checks"].get("P1", 0)  # the commit's own new calls (P1)
    ax.plot([dates[-1], cd, cd], [len(dates), len(dates), left + added], color=C2, lw=2,
            label=f"c651331b34 edits {conv['edited_by_child']} of {conv['site_lines']} ({left} left), adds {added} (P1)")
    ax.annotate(f"{left + added}: test_legacy_aliases, added on purpose", (cd, left + added), xytext=(-170, 25),
                textcoords="offset points", fontsize=8, color=INK2, arrowprops=dict(arrowstyle="-", color=INK2, lw=0.8))
    ax.axhline(265, color=MUTED, lw=1, ls="--")
    ax.text(dates[0], 268, "answer key: 265 .py lines (+2 in code.sample)", color=INK2, va="bottom", fontsize=8)
    ax.set_ylabel("lines someone must edit")
    ax.set_title("Django ugettext*: the block's 267 lines, and the commit that converts them")
    ax.legend(loc="center left")
    ax.set_ylim(0, 300)
    save(fig, "django_burndown.png")
    plt.close(fig)

    # 2 the same check on all 21 pinned repos: lines in the block vs lines the next (migration) commit edits
    rows = sorted(R["conversion"].items(), key=lambda kv: kv[1]["site_lines"])
    fig, ax = plt.subplots(figsize=(7.2, 5.6))
    y = range(len(rows))
    ax.barh(y, [r["site_lines"] for _, r in rows], color=MUTED, height=0.7, label="call-site lines in the block")
    ax.barh(y, [r["edited_by_child"] for _, r in rows], color=C1, height=0.42, label="edited by the migration commit")
    ax.set_yticks(list(y), [k.split("/")[1] + ("  (ugettext*)" if k == "django/django" else "") for k, _ in rows], fontsize=8)
    for i, (_, r) in enumerate(rows):
        ax.text(r["site_lines"] + 3, i, f"{r['edited_by_child']}/{r['site_lines']}", va="center", fontsize=7.5, color=INK2)
    tot_s, tot_e = sum(r["site_lines"] for _, r in rows), sum(r["edited_by_child"] for _, r in rows)
    ax.set_title(f"Next commit vs the block: {tot_e} of {tot_s} call-site lines edited")
    ax.set_xlabel("lines (url() in 20 packages, ugettext* in Django)")
    ax.legend(loc="lower right")
    save(fig, "burndown_next_commit.png")
    plt.close(fig)

    # 3 Django call sites by synthetic owner team, code vs tests, with the owner view's status at the pin
    teams = R["django_teams"]
    status = {t: (left_, days, st) for t, left_, _, days, st in R["scenarios"]["django@pin"]["owner_teams"]["rows_all"]}
    order = sorted(teams, key=lambda t: sum(teams[t].values()))
    fig, ax = plt.subplots(figsize=(7.2, 5.2))
    y = range(len(order))
    code = [teams[t].get("none", 0) for t in order]
    test = [teams[t].get("test", 0) for t in order]
    ax.barh(y, code, color=C1, height=0.65, label="code")
    ax.barh(y, test, left=code, color=C3, height=0.65, label="tests")
    for i, t in enumerate(order):
        n, days, st = status.get(t, (0, 0, "?"))
        ax.text(code[i] + test[i] + 1, i, f"{st}, {days} d", va="center", fontsize=7.5, color=INK2)
    ax.set_yticks(list(y), order, fontsize=8)
    ax.set_xlabel("call sites (289 on 267 lines)")
    ax.set_title("Django ugettext*: call sites per team, status at 4353640ea9")
    ax.legend(loc="lower right")
    save(fig, "django_owner_teams.png")
    plt.close(fig)

    # 4 blast radius: direct sites (burndown) vs invocations reached, by depth
    br = {k: v for k, v in R["blast"].items() if k.startswith("django/django") or v["direct"] >= 40}
    keys = sorted(br, key=lambda k: -br[k]["invocations"])
    fig, ax = plt.subplots(figsize=(7.2, 3.8))
    x = range(len(keys))
    ax.bar([i - 0.2 for i in x], [br[k]["direct"] for k in keys], width=0.36, color=MUTED, label="direct call sites (burndown)")
    bottom = [0] * len(keys)
    for d, c in (("1", C1), ("2", C2), ("3", C4)):
        v = [br[k]["by_depth"].get(d, 0) for k in keys]
        ax.bar([i + 0.2 for i in x], v, width=0.36, bottom=bottom, color=c, label=f"invocations at depth {d}")
        bottom = [a + b for a, b in zip(bottom, v)]
    ax.set_xticks(list(x), [k.split(":")[1] if k.startswith("django/django") else k.split("/")[1].split(":")[0] for k in keys],
                  rotation=35, ha="right", fontsize=8)
    ax.set_title("Blast radius vs burndown: reached vs must-edit")
    ax.set_ylabel("count")
    ax.legend(fontsize=7.5)
    save(fig, "blast_radius.png")
    plt.close(fig)

    # 5 fragility: bug fixes per file exist only on Spark, where nothing is tracked
    fb = R["spark_full"]["file_bug_fixes"]
    fig, ax = plt.subplots(figsize=(7.2, 3.2))
    top = max(fb)
    ax.hist(fb, bins=range(1, top + 2), color=C1, rwidth=0.85)
    ax.axvline(2.5, color=C2, lw=1.5)
    risky = sum(n >= 3 for n in fb)
    ax.text(3, ax.get_ylim()[1] * 0.85, f"risky (>= 3): {risky} of {len(fb)} Spark files", color=INK, fontsize=8.5)
    ax.set_yscale("log")
    ax.set_xlabel("Bug+Fixed commits touching the file in the 365 days before the snapshot")
    ax.set_ylabel("files (log)")
    ax.set_title("Spark file fragility; 0 of 1,526 call sites carry any")
    save(fig, "fragility.png")
    plt.close(fig)

    # 6 ticket -> code mapping coverage by method and dataset
    cov = R["mapping"]
    fig, ax = plt.subplots(figsize=(7.2, 2.4))
    cols = {"commit key": C1, "stack trace": C3, "path in text": C4, "unmapped": MUTED}
    for i, (ds, m) in enumerate(cov.items()):
        total, left_ = sum(m.values()), 0
        for meth, n in m.items():
            ax.barh(i, n / total, left=left_, color=cols[meth], height=0.55, label=meth if i == 0 else None)
            if n / total > 0.06:
                ax.text(left_ + n / total / 2, i, f"{n:,}", ha="center", va="center", fontsize=7.5, color=SURF)
            left_ += n / total
    ax.set_yticks([0, 1], [f"{k} ({sum(v.values()):,})" for k, v in cov.items()])
    ax.set_xlim(0, 1)
    ax.set_xlabel("share of tickets, by the best way the block maps them to code")
    ax.set_title("Ticket → code: 61% of Spark tickets reach a file, GitLab 0%")
    ax.legend(ncol=4, loc="upper center", bbox_to_anchor=(0.5, -0.45), fontsize=7.5)
    save(fig, "ticket_mapping.png")
    plt.close(fig)

    # 7 graph output: fixture vs real, and GitLab's ticket-level blocks as a funnel against the gold set
    g = R["labels"]["gitlab_blocks"]
    fig, (a1, a2) = plt.subplots(1, 2, figsize=(7.6, 3.2), gridspec_kw={"width_ratios": [1, 1.2]})
    fx = {"blocked_by": 1, "depends_on": 1, "collides": 2, "duplicates": 1, "untracked": 3}
    real = R["graph_counts"]
    names = list(fx)
    a1.barh([i + 0.18 for i in range(5)], [fx[n] for n in names], height=0.34, color=MUTED, label="fixture (acme/shop)")
    a1.barh([i - 0.18 for i in range(5)], [real.get(n, 0) for n in names], height=0.34, color=C1, label="real block")
    for i, n in enumerate(names):
        a1.text(real.get(n, 0) + 0.5, i - 0.18, str(real.get(n, 0)), va="center", fontsize=7.5, color=INK2)
    a1.set_yticks(range(5), names)
    a1.set_title("graph rows: only 'untracked' fires")
    a1.legend(fontsize=7, loc="lower right")
    steps = [("GitLab blocked_by links", g["links"]), ("both ends in snapshot", g["other_in_snapshot"]),
             ("different group:: labels", g["kb_cross_team"]), ("in the model-read gold set", g["kb_cross_in_gold"]),
             ("gold verdict 'wait'", g["kb_cross_in_gold_verdicts"].get("wait", 0))]
    a2.barh(range(len(steps))[::-1], [n for _, n in steps], color=[C1, C1, C1, C3, C2], height=0.6)
    for i, (lbl, n) in enumerate(steps):
        a2.text(n * 1.08, len(steps) - 1 - i, f"{n:,}", va="center", fontsize=7.5, color=INK2)
    a2.set_yticks(range(len(steps))[::-1], [s for s, _ in steps], fontsize=7.5)
    a2.set_xscale("log")
    a2.set_title("GitLab: links ≠ cross-team waits")
    save(fig, "graph_and_blocks.png")
    plt.close(fig)

    # 8 push check replay: findings per check over the 21 next commits (default limits vs lifted)
    pr = R["push_replay"]
    checks = [f"P{i}" for i in range(1, 10)]
    tot = {lbl: Counter() for lbl in ("default", "uncapped")}
    for repo, res in pr.items():
        for lbl in tot:
            tot[lbl].update(res[lbl]["checks"])
    skipped = sum(bool(res["default"]["skipped"]) for res in pr.values())
    fig, ax = plt.subplots(figsize=(7.2, 3.2))
    x = range(9)
    ax.bar([i - 0.2 for i in x], [tot["default"][c] for c in checks], width=0.38, color=C1, label="settings.toml limits")
    ax.bar([i + 0.2 for i in x], [tot["uncapped"][c] for c in checks], width=0.38, color=MUTED, label="scope limit lifted")
    for i, c in enumerate(checks):
        for dx, lbl in ((-0.2, "default"), (0.2, "uncapped")):
            ax.text(i + dx, tot[lbl][c], str(tot[lbl][c]), ha="center", va="bottom", fontsize=7, color=INK2)
    ax.set_xticks(list(x), checks)
    ax.set_ylabel("findings (all 21 pushes)")
    ax.set_title(f"Push check on 21 real migration commits ({skipped} skipped: > 200 files)")
    ax.legend()
    save(fig, "push_replay.png")
    plt.close(fig)

    # 9 P9 (lines x files) vs SZZ on Spark
    p9 = R["spark_full"]["p9"]
    import numpy as np
    fig, ax = plt.subplots(figsize=(7.2, 3.2))
    for vals, c, lbl in ((p9["risk_other"], MUTED, "other commits"), (p9["risk_inducing"], C2, "bug-inducing (SZZ)")):
        v = np.sort(np.maximum(np.array(vals, dtype=float), 1))
        ax.plot(v, 1 - np.arange(len(v)) / len(v), color=c, lw=2, label=f"{lbl}, n={len(v):,}")
    ax.axvline(p9["threshold"], color=INK2, lw=1, ls="--")
    fi, i_ = p9["flagged_inducing"], p9["inducing"]
    fo, o = p9["flagged"] - fi, p9["commits"] - i_
    ax.text(p9["threshold"] * 1.15, 0.85, f"P9 > {p9['threshold']}:\n{fi / i_:.0%} of inducing\n{fo / o:.0%} of others",
            fontsize=8, color=INK)
    ax.set_xscale("log")
    ax.set_xlabel("lines × files of the commit (log)")
    ax.set_ylabel("share of commits above x")
    ax.set_title("P9 on 41k Spark commits: big pushes are where SZZ finds the bugs")
    ax.legend(loc="lower left")
    save(fig, "p9_vs_szz.png")
    plt.close(fig)

    # 10 the block against the answer keys
    L = R["labels"]
    rows = [("Django edit lines (265 key)", L["django_265"]["tp"] / L["django_265"]["kb_py_lines"], L["django_265"]["tp"] / L["django_265"]["gold"]),
            ("Django aliased _() hand check (30)", L["django_alias"]["hand_check"]["agree"] / 30, None),
            ("Spark collisions, path level (50)", L["spark_collisions"]["real"] / L["spark_collisions"]["n"],
             L["spark_collisions"]["both_mapped_to_path"] / L["spark_collisions"]["n"]),
            ("GitLab cross-team by labels (gold 'wait')", L["gitlab_blocks"]["kb_cross_in_gold_verdicts"].get("wait", 0) / L["gitlab_blocks"]["kb_cross_in_gold"],
             L["gitlab_blocks"]["gold_wait_found_by_kb"] / L["gitlab_blocks"]["gold_wait"]),
            ("GitLab upstream stalls with a KB link (41)", None, L["gitlab_stalls"]["upstream_with_kb_link"] / L["gitlab_stalls"]["reasons"]["upstream"])]
    fig, ax = plt.subplots(figsize=(7.2, 3.0))
    for i, (lbl, p, r) in enumerate(rows[::-1]):
        if p is not None:
            ax.plot(p, i + 0.12, "o", color=C1, ms=8, label="precision")
            ax.text(p, i + 0.32, f"{p:.2f}", ha="center", fontsize=7, color=INK2)
        if r is not None:
            ax.plot(r, i - 0.12, "s", color=C2, ms=7, label="recall / coverage")
            ax.text(r, i - 0.45, f"{r:.2f}", ha="center", fontsize=7, color=INK2)
    ax.axvline(0.9, color=MUTED, lw=1, ls="--")
    ax.set_yticks(range(len(rows)), [r[0] for r in rows[::-1]], fontsize=8)
    ax.set_xlim(0, 1.05)
    ax.set_ylim(-0.7, len(rows) - 0.4)
    ax.set_title("The block vs the answer keys (dashed: 0.90 bar)")
    h, l = ax.get_legend_handles_labels()
    ax.legend(dict(zip(l, h)).values(), dict(zip(l, h)).keys(), loc="lower left", fontsize=7.5)
    save(fig, "labels_vs_block.png")
    plt.close(fig)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--db", required=True, help="a run's meridian.sqlite (opened read-only)")
    ap.add_argument("--mirrors", default=str(DP.parent / "snapshots/_cache/repos"), help="collect's bare git mirrors")
    ap.add_argument("--replay", action="store_true", help="rerun the push-check replay instead of reading its cached JSON")
    a = ap.parse_args(argv)
    FIG.mkdir(parents=True, exist_ok=True)
    cache = FIG / "push_replay.json"
    if a.replay or not cache.exists():
        cache.write_text(json.dumps(push_replay(a.db, a.mirrors), indent=1), encoding="utf-8")
    R = dict(scenarios=run_scenarios(a.db), blast=blast_radius_real(a.db), labels=score_labels(a.db),
             conversion=conversion_check(a.db, a.mirrors), spark_full=spark_signal(a.db, 2000), mapping=mapping_coverage(a.db),
             push_replay=json.loads(cache.read_text(encoding="utf-8")))
    db = connect(a.db)
    R["django_site_dates"] = [d for (d,) in db.execute("SELECT effective_date FROM call_site WHERE repo = 'django/django' "
                                                         "GROUP BY path, line")]
    R["django_teams"] = defaultdict(dict)
    for t, junk, n in db.execute("SELECT owner_team, junk, count(*) FROM call_site WHERE repo = 'django/django' GROUP BY 1, 2"):
        R["django_teams"][t][junk] = n
    R["graph_counts"] = dict(db.execute("SELECT type, count(*) FROM chain GROUP BY type").fetchall()) | {
        "blocked_by": one(db, "SELECT count(*) FROM blocked_by")}
    db.close()
    charts(R)
    for s in R["scenarios"].values():  # the JSON keeps a few rows per query; owner_teams fed chart 3 in full
        s.get("owner_teams", {}).pop("rows_all", None)
    R["spark_full"] = {k: v for k, v in R["spark_full"].items() if k != "file_bug_fixes"} | {
        "file_bug_fixes_n": len(R["spark_full"]["file_bug_fixes"]),
        "file_bug_fixes_risky": sum(n >= 3 for n in R["spark_full"]["file_bug_fixes"])}
    R["spark_full"]["p9"] = {k: v for k, v in R["spark_full"]["p9"].items() if not k.startswith("risk_")}
    (FIG / "scenarios.json").write_text(json.dumps(R, indent=1, default=str), encoding="utf-8")
    print("wrote", FIG / "scenarios.json")


if __name__ == "__main__":
    main()
