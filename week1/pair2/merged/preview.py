"""Preview Meridian rules (04-architecture) on the merged Spark dataset: coverage, fragility, ownership."""
import collections, re, sqlite3, statistics
from pathlib import Path

db = sqlite3.connect(Path(__file__).parent / "data" / "spark_jira.sqlite")  # from build.py
q = lambda s, *a: db.execute(s, a).fetchall()
TEST = re.compile(r"(^|/)(tests?|test-data|benchmarks?)/|Suite\.scala$|(^|/)test_[^/]*\.py$|Test\.java$")
SRC = re.compile(r"\.(scala|java|py)$")
src = lambda p: SRC.search(p) and not TEST.search(p)

# 1. coverage: does every Bug+Fixed ticket have a fix commit?
n_bug, = q("select count(*) from tickets where effective_type='Bug' and resolution='Fixed' and created_at >= '2015'")[0]
n_cov, = q("""select count(distinct b.key) from bug_fix_commits b join tickets t using(key)
              where t.created_at >= '2015'""")[0]
lag = [r[0] for r in q("""select julianday(min(c.authored_at)) - julianday(substr(t.created_at, 1, 19)) from tickets t
    join commit_ticket ct using(key) join commits c using(sha) where t.effective_type='Bug' and t.resolution='Fixed'
    and t.created_at >= '2020' group by t.key""")]
print(f"[coverage] Bug+Fixed tickets since 2015 with a fix commit: {n_cov}/{n_bug} ({100*n_cov/n_bug:.0f}%)  "
      f"median days ticket->first fix commit (2020+): {statistics.median(lag):.0f}")

# 2. fragility: bug_fixes per source file over 365 days; does it predict next year's fixes?
def fixes(a, b):
    c = collections.Counter()
    for sha, path in q("""select distinct f.sha, f.path from bug_fix_commits b join commit_files f using(sha)
                          where b.authored_at >= ? and b.authored_at < ?""", a, b):
        if src(path):
            c[path] += 1
    return c
prev, nxt = fixes("2024-10-01", "2025-10-01"), fixes("2025-10-01", "2026-10-01")
live = {p for (p,) in q("select distinct path from commit_files f join commits c using(sha) where c.authored_at >= '2024-10-01'") if src(p)}
risky = {p for p, n in prev.items() if n >= 3}
nf = sum(nxt.values())
in_risky = sum(n for p, n in nxt.items() if p in risky)
print(f"[fragility] source files touched in 2 yrs {len(live)}, risky (>=3 fixes in prior year) {len(risky)} "
      f"({100*len(risky)/len(live):.1f}%) -> receive {100*in_risky/nf:.0f}% of next year's {nf} file-fixes "
      f"(lift {in_risky/nf/(len(risky)/len(live)):.1f}x)")
hit = sum(1 for p in risky if nxt[p] > 0)
print(f"            risky files fixed again next year: {hit}/{len(risky)} ({100*hit/len(risky):.0f}%), "
      f"non-risky files fixed next year: {100*sum(1 for p in live - risky if nxt[p])/len(live - risky):.0f}%")

# 3. ownership (180 days, top author share >= 0.30 else unowned), merged people vs raw email; predict next author
def owners(cut, key):
    share = collections.defaultdict(collections.Counter)
    for path, who in q(f"""select f.path, c.{key} from commit_files f join commits c using(sha)
        where c.authored_at >= date(?, '-180 days') and c.authored_at < ? and c.parent is not null""", cut, cut):
        if src(path):
            share[path][who] += 1
    out = {}
    for p, c in share.items():
        (top, n), tot = c.most_common(1)[0], sum(c.values())
        out[p] = top if n / tot >= 0.30 else None
    return out
cut = "2026-04-01"
nxt_author, last_toucher = {}, {}
for path, pid, at in q("select f.path, c.person_id, c.authored_at from commit_files f join commits c using(sha) order by c.seq"):
    if not src(path):
        continue
    if at < cut:
        last_toucher[path] = pid
    elif path not in nxt_author:
        nxt_author[path] = pid
same = None
for key, label in (("person_id", "merged people"), ("author_email", "raw emails")):
    own = owners(cut, key)
    unowned = sum(v is None for v in own.values())
    emap = dict(q("select email, person_id from people_emails")) if key == "author_email" else None
    ev = [p for p in own if p in nxt_author and own[p]]
    same = same or ev
    norm = (lambda x: emap.get(x, x)) if emap else (lambda x: x)
    acc = sum(norm(own[p]) == nxt_author[p] for p in ev) / len(ev)
    print(f"[ownership/{label}] files {len(own)}, unowned {100*unowned/len(own):.0f}%, "
          f"owner == next author {100*acc:.0f}% (N={len(ev)})")
ev = [p for p in same if p in last_toucher]  # same files the owner rule was scored on
print(f"[ownership/baseline] last toucher == next author {100*sum(last_toucher[p]==nxt_author[p] for p in ev)/len(ev):.0f}% (N={len(ev)})")

# 4. ticket lifecycle: is the ticket filed before the work, or as a wrapper for a ready PR?
d = [r[0] for r in q("""select julianday(min(c.authored_at)) - julianday(substr(t.created_at, 1, 19)) from tickets t
    join commit_ticket ct using(key) join commits c using(sha) where t.created_at >= '2024' group by t.key""")]
b = lambda lo, hi: 100 * sum(lo <= x < hi for x in d) / len(d)
print(f"[lifecycle] tickets since 2024 with a commit (N={len(d)}): first commit <1 day after filing {b(-1e9, 1):.0f}%, "
      f"1-14 days {b(1, 14):.0f}%, 14-90 {b(14, 90):.0f}%, >90 days {b(90, 1e9):.0f}%")
