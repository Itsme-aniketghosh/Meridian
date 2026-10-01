"""Join Spark git + Apache Jira into one SQLite file: data/pair2_spark.sqlite.

Inputs (both local, nothing is re-queried):
  data/repos/apache_spark   full clone (not blobless), read at SPARK_SHA
  data/jira_raw        raw pages from pair2_jira_pull.py

Tables
  commits                one row per first-parent commit
  commit_files           files a commit touched, with +/- lines
  commit_people          author + Co-authored-by, as person_id
  people                 one row per merged human (alias table: people_emails)
  tickets                one row per Jira ticket, with parent type and bulk-closed flag
  ticket_links           Jira issue links
  ticket_history         status / resolution changes (rebuild "open at time t")
  commit_ticket          commit -> ticket links (key regex, must exist in Jira)
  meta                   snapshot facts
Views
  bug_fix_commits        commits linked to a Bug+Fixed ticket (sub-tasks take the parent's type)

Same inputs -> same rows: inserts are sorted, IDs are derived from content.
"""
import collections, gzip, json, re, sqlite3, subprocess, time
from pathlib import Path

HERE = Path(__file__).parent
DATA = HERE / "data"
REPO = DATA / "repos" / "apache_spark"
SPARK_SHA = "f868de6914d0e6ede86309648614bb436b2b82e6"  # snapshot used in PAIR2-REVIEW.md
RAW = DATA / "jira_raw"
OUT = DATA / "pair2_spark.sqlite"

KEY = re.compile(r"\bSPARK-\d+\b")
MINOR = re.compile(r"\[(MINOR|HOTFIX|FOLLOW-?UP)\]", re.I)
COAUTH = re.compile(r"^Co-authored-by:\s*(.*?)\s*<([^>]+)>", re.M | re.I)
GENERIC = {"root", "ubuntu", "unknown", "user", "admin", "spark", "localhost", "none", "test", "jenkins"}
BULK = 20  # resolutions by one person in one hour = mass close

SCHEMA = """
CREATE TABLE meta (k TEXT PRIMARY KEY, v TEXT);
CREATE TABLE people (person_id TEXT PRIMARY KEY, name TEXT, n_emails INT, n_commits INT);
CREATE TABLE people_emails (email TEXT PRIMARY KEY, person_id TEXT, name TEXT, generic INT);
CREATE TABLE commits (sha TEXT PRIMARY KEY, seq INT, parent TEXT, authored_at TEXT, committed_at TEXT,
  author_email TEXT, person_id TEXT, committer_email TEXT, subject TEXT, is_minor INT,
  n_keys INT, n_files INT, lines_added INT, lines_deleted INT);
CREATE TABLE commit_files (sha TEXT, path TEXT, added INT, deleted INT, PRIMARY KEY (sha, path));
CREATE TABLE commit_people (sha TEXT, person_id TEXT, role TEXT, PRIMARY KEY (sha, person_id, role));
CREATE TABLE tickets (key TEXT PRIMARY KEY, num INT, type TEXT, is_subtask INT, parent_key TEXT,
  parent_type TEXT, effective_type TEXT, status TEXT, resolution TEXT, priority TEXT,
  created_at TEXT, resolved_at TEXT, component TEXT, components TEXT, assignee TEXT, reporter TEXT,
  labels TEXT, bulk_closed INT, desc_empty INT, summary TEXT);
CREATE TABLE ticket_links (key TEXT, link_type TEXT, direction TEXT, other_key TEXT,
  PRIMARY KEY (key, link_type, direction, other_key));
CREATE TABLE ticket_history (key TEXT, at TEXT, field TEXT, from_value TEXT, to_value TEXT, author TEXT);
CREATE TABLE commit_ticket (sha TEXT, key TEXT, source TEXT, PRIMARY KEY (sha, key));
CREATE INDEX ix_cf_path ON commit_files(path);
CREATE INDEX ix_ct_key ON commit_ticket(key);
CREATE INDEX ix_th_key ON ticket_history(key, at);
CREATE VIEW bug_fix_commits AS
  SELECT c.*, t.key FROM commits c JOIN commit_ticket ct USING (sha) JOIN tickets t USING (key)
  WHERE t.effective_type = 'Bug' AND t.resolution = 'Fixed';
"""


def git(*a):
    return subprocess.run(["git", "-C", str(REPO), *a], capture_output=True, text=True, errors="replace").stdout


# ---------- git ----------

def read_commits():
    fmt = "%x1e%H%x1f%P%x1f%aI%x1f%cI%x1f%an%x1f%ae%x1f%ce%x1f%s%x1f%b%x1f"
    out = git("log", "--first-parent", "--reverse", "--no-renames", "--numstat", f"--format={fmt}", SPARK_SHA)
    commits = []
    for seq, rec in enumerate(out.split("\x1e")[1:]):
        sha, parents, a_at, c_at, an, ae, ce, subj, body, stat = rec.split("\x1f")
        files = []
        for line in stat.strip().splitlines():
            add, dele, path = line.split("\t", 2)
            files.append((path, int(add) if add != "-" else None, int(dele) if dele != "-" else None))
        commits.append({"sha": sha, "seq": seq, "parent": parents.split()[0] if parents else None,
                        "a_at": a_at, "c_at": c_at, "an": an, "ae": ae.lower(), "ce": ce.lower(),
                        "subj": subj, "body": body, "files": files,
                        "coauthors": [(n, e.lower()) for n, e in COAUTH.findall(body)]})
    return commits


def merge_people(commits):
    """Union-find over emails: same normalised name, or same email local part. Generic users never merge."""
    parent = {}

    def find(x):
        parent.setdefault(x, x)
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a, b):
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[max(ra, rb)] = min(ra, rb)  # deterministic root

    names = collections.defaultdict(collections.Counter)
    for c in commits:
        for n, e in [(c["an"], c["ae"])] + c["coauthors"]:
            names[e][n] += 1
    generic = set()
    for e, ns in names.items():
        node = "E:" + e
        find(node)
        local = re.sub(r"^\d+\+", "", e.split("@")[0])  # github noreply: 12345+login
        n = re.sub(r"[\s.\-_]", "", ns.most_common(1)[0][0].lower())
        if local in GENERIC or n in GENERIC:
            generic.add(e)
            continue
        if n:
            union(node, "N:" + n)
        if len(local) >= 4:
            union(node, "L:" + local)
    groups = collections.defaultdict(list)
    for e in names:
        groups[find("E:" + e)].append(e)
    email_to_pid = {}
    for root, emails in groups.items():
        pid = min(emails)  # stable ID: the group's smallest email
        for e in emails:
            email_to_pid[e] = pid
    return email_to_pid, names, generic


# ---------- jira ----------

def read_jira():
    issues = []
    for p in sorted(RAW.glob("page_*.json.gz")):
        issues += json.loads(gzip.decompress(p.read_bytes()))["issues"]
    seen, uniq = set(), []
    for it in issues:  # a ticket created mid-pull can shift paging and appear twice
        if it["key"] not in seen:
            seen.add(it["key"])
            uniq.append(it)
    return uniq


def ticket_rows(issues):
    by_key = {it["key"]: it for it in issues}
    last_res = {}
    history, links = [], []
    for it in issues:
        for h in (it.get("changelog") or {}).get("histories", []):
            who = (h.get("author") or {}).get("name")
            for i in h["items"]:
                if i["field"] in ("status", "resolution"):
                    history.append((it["key"], h["created"], i["field"], i.get("fromString"), i.get("toString"), who))
                    if i["field"] == "resolution" and i.get("toString"):
                        last_res[it["key"]] = (who, h["created"][:13])
        for l in it["fields"].get("issuelinks", []):
            if "outwardIssue" in l:
                links.append((it["key"], l["type"]["name"], "out", l["outwardIssue"]["key"]))
            if "inwardIssue" in l:
                links.append((it["key"], l["type"]["name"], "in", l["inwardIssue"]["key"]))
    batch = collections.Counter(last_res.values())
    rows = []
    for it in issues:
        f = it["fields"]
        par = (f.get("parent") or {}).get("key")
        ptype = by_key[par]["fields"]["issuetype"]["name"] if par in by_key else None
        comps = sorted(c["name"] for c in f.get("components") or [])
        labels = sorted(f.get("labels") or [])
        bulk = "bulk-closed" in labels or batch.get(last_res.get(it["key"]), 0) >= BULK
        rows.append((it["key"], int(it["key"].split("-")[1]), f["issuetype"]["name"], int(f["issuetype"]["subtask"]),
                     par, ptype, ptype if f["issuetype"]["subtask"] and ptype else f["issuetype"]["name"],
                     f["status"]["name"], (f["resolution"] or {}).get("name"), (f.get("priority") or {}).get("name"),
                     f["created"], f.get("resolutiondate"), comps[0] if comps else None, ",".join(comps),
                     (f.get("assignee") or {}).get("name"), (f.get("reporter") or {}).get("name"),
                     ",".join(labels), int(bulk), int(not (f.get("description") or "").strip()), f.get("summary")))
    return sorted(rows), sorted(history), sorted(set(links))


# ---------- build ----------

def main():
    t0 = time.time()
    commits = read_commits()
    email_to_pid, names, generic = merge_people(commits)
    issues = read_jira()
    tickets, history, links = ticket_rows(issues)
    known = {t[0] for t in tickets}
    print(f"read {len(commits)} commits, {len(issues)} tickets in {time.time() - t0:.0f}s")

    tmp = OUT.with_suffix(".tmp")
    tmp.unlink(missing_ok=True)
    db = sqlite3.connect(tmp)
    db.executescript(SCHEMA)

    c_rows, f_rows, p_rows, ct_rows = [], [], [], []
    dead = 0
    for c in commits:
        subj_keys = sorted(set(KEY.findall(c["subj"])))
        # subject first; body only if the subject has none (body also holds branch names like SPARK-123-fix)
        keys, src = (subj_keys, "subject") if subj_keys else (sorted(set(KEY.findall(c["body"]))), "body")
        live = [k for k in keys if k in known]
        dead += len(keys) - len(live)
        ct_rows += [(c["sha"], k, src) for k in live]
        adds = sum(a or 0 for _, a, _ in c["files"])
        dels = sum(d or 0 for _, _, d in c["files"])
        c_rows.append((c["sha"], c["seq"], c["parent"], c["a_at"], c["c_at"], c["ae"], email_to_pid[c["ae"]],
                       c["ce"], c["subj"], int(bool(MINOR.search(c["subj"]))), len(live), len(c["files"]), adds, dels))
        f_rows += [(c["sha"], p, a, d) for p, a, d in c["files"]]
        p_rows.append((c["sha"], email_to_pid[c["ae"]], "author"))
        p_rows += [(c["sha"], email_to_pid[e], "coauthor") for _, e in c["coauthors"] if email_to_pid[e] != email_to_pid[c["ae"]]]

    n_commits = collections.Counter(email_to_pid[c["ae"]] for c in commits)
    by_pid = collections.defaultdict(list)
    for e, pid in email_to_pid.items():
        by_pid[pid].append(e)
    people = sorted((pid, names[min(es, key=lambda e: -sum(names[e].values()))].most_common(1)[0][0], len(es), n_commits[pid])
                    for pid, es in by_pid.items())
    pe = sorted((e, pid, names[e].most_common(1)[0][0], int(e in generic)) for e, pid in email_to_pid.items())

    db.executemany("INSERT INTO commits VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)", c_rows)
    db.executemany("INSERT OR IGNORE INTO commit_files VALUES (?,?,?,?)", f_rows)
    db.executemany("INSERT OR IGNORE INTO commit_people VALUES (?,?,?)", sorted(set(p_rows)))
    db.executemany("INSERT INTO people VALUES (?,?,?,?)", people)
    db.executemany("INSERT INTO people_emails VALUES (?,?,?,?)", pe)
    db.executemany("INSERT INTO tickets VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", tickets)
    db.executemany("INSERT INTO ticket_links VALUES (?,?,?,?)", links)
    db.executemany("INSERT INTO ticket_history VALUES (?,?,?,?,?,?)", history)
    db.executemany("INSERT INTO commit_ticket VALUES (?,?,?)", sorted(set(ct_rows)))
    pages = sorted(RAW.glob("page_*.json.gz"))
    db.executemany("INSERT INTO meta VALUES (?,?)", sorted({
        "repo_head": commits[-1]["sha"], "repo_head_at": commits[-1]["c_at"],
        "jira_tickets": str(len(issues)), "jira_pages": str(len(pages)),
        "jira_pulled_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(pages[-1].stat().st_mtime)),
        "dead_keys": str(dead), "bulk_rule": f"label bulk-closed OR >= {BULK} resolutions by one person in one hour",
    }.items()))
    db.commit()
    db.close()
    tmp.replace(OUT)
    print(f"wrote {OUT.relative_to(HERE)} ({OUT.stat().st_size / 1e6:.0f} MB) in {time.time() - t0:.0f}s, dead keys {dead}")


if __name__ == "__main__":
    main()
