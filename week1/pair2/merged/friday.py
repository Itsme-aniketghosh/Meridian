"""Print the Pair 2 Friday numbers (brief Q1-Q4) and the Jira label counts, from the merged dataset.

Everything is as of the snapshot in build.py (Spark f868de6914, Jira cut at SNAPSHOT_AT).
Q5 (pull time, rate limits) comes from jira/analyze.py, since it's about the pull itself.
"""
import re, sqlite3
from pathlib import Path

db = sqlite3.connect(Path(__file__).parent / "data" / "spark_jira.sqlite")  # from build.py
q = lambda s, *a: db.execute(s, a).fetchall()
one = lambda s, *a: q(s, *a)[0][0]
KEY = re.compile(r"\bSPARK-\d+\b")
BOT = re.compile(r"\[bot\]|dependabot|github-actions|renovate|jenkins|buildbot|\brobot\b", re.I)  # whole word: not wherobots.com, datarobot.com
pct = lambda a, b: f"{100 * a / b:.1f}%"

print(f"snapshot: {dict(q('select k, v from meta'))['snapshot_at']}")

# Q1: ticket key in the commit message, last 1,000 first-parent commits
last = q("select sha, subject, n_keys from commits order by seq desc limit 1000")
subj = sum(bool(KEY.search(s)) for _, s, _ in last)
any_ = sum(n > 0 for _, _, n in last)
print(f"\nQ1  keyed, last {len(last)} commits: subject {pct(subj, len(last))} ({subj}), subject or body {pct(any_, len(last))} ({any_})")
for y, n, k in q("""select substr(authored_at, 1, 4) y, count(*), sum(n_keys > 0) from commits
                   where authored_at >= '2021' group by y order by y"""):
    print(f"    {y}: {pct(k, n)} of {n}")

# Q2: of those tickets, how many are Bug + Fixed
db.execute("create temp table last1000 as select sha from commits order by seq desc limit 1000")
keys = one("select count(distinct key) from commit_ticket where sha in (select sha from last1000)")
strict = one("""select count(distinct key) from commit_ticket join tickets using(key)
               where sha in (select sha from last1000) and type = 'Bug' and resolution = 'Fixed'""")
parent = one("""select count(distinct key) from commit_ticket join tickets using(key)
               where sha in (select sha from last1000) and effective_type = 'Bug' and resolution = 'Fixed'""")
n_commits = one("""select count(distinct sha) from commit_ticket join tickets using(key)
                  where sha in (select sha from last1000) and type = 'Bug' and resolution = 'Fixed'""")
print(f"\nQ2  {keys} tickets -> Bug+Fixed {strict} ({pct(strict, keys)}), touched by {n_commits} commits; "
      f"+{parent - strict} bug sub-tasks if sub-tasks take the parent's type")
for t, n in q("""select type, count(distinct key) from commit_ticket join tickets using(key)
                where sha in (select sha from last1000) group by type order by 2 desc limit 4"""):
    print(f"    {t}: {pct(n, keys)}")
bug_fixed = one("select count(*) from tickets where type = 'Bug' and resolution = 'Fixed'")
print(f"    project-wide Bug+Fixed: {bug_fixed}")

# Q3: duplicate people, authors only
emails = one("select count(distinct author_email) from commits")
people = one("select count(distinct person_id) from commits")
multi = q("""select person_id from commits group by person_id having count(distinct author_email) > 1""")
share = one(f"""select count(*) from commits where person_id in
               (select person_id from commits group by person_id having count(distinct author_email) > 1)""")
total = one("select count(*) from commits")
print(f"\nQ3  {emails} author emails -> {people} people: {emails - people} duplicates ({pct(emails - people, emails)}), "
      f"{len(multi)} people with 2+ emails wrote {pct(share, total)} of commits")

# Q4: bots, committer vs author, co-authors
bots = sum(bool(BOT.search(f"{e}")) for (e,) in q("select author_email from commits"))
diff = one("select count(*) from commits where sha in (select sha from last1000) and author_email != committer_email")
coauth = one("select count(distinct sha) from commit_people where role = 'coauthor' and sha in (select sha from last1000)")
print(f"\nQ4  bot-like authors in all history: {bots}; last 1,000: committer != author {diff}, Co-authored-by {coauth}")

# Jira labels: bulk-closed share and what's usable
print("\nJira labels (resolution at snapshot)")
for res in ("Incomplete", "Duplicate", "Cannot Reproduce", "Won't Fix", "Fixed"):
    n, b = q("select count(*), sum(bulk_closed) from tickets where resolution = ?", res)[0]
    print(f"    {res:17} {n:6}  bulk {b:5} ({pct(b, n)})  not bulk {n - b}")
dup_linked = one("""select count(distinct t.key) from tickets t join ticket_links l using(key)
                   where t.resolution = 'Duplicate' and l.link_type = 'Duplicate' and t.bulk_closed = 0""")
print(f"    Duplicate, not bulk, linked to the original: {dup_linked}")
n_t = one("select count(*) from tickets")
linked = one("select count(distinct key) from ticket_links")
blocks = one("select count(distinct key) from ticket_links where link_type in ('Blocker', 'Blocked')")
print(f"    tickets {n_t}, open {one('select count(*) from tickets where resolution is null')}, "
      f"any link {pct(linked, n_t)}, block link {blocks} ({pct(blocks, n_t)})")
