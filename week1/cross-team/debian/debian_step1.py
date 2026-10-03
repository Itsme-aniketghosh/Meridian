"""Step 1 numbers for Debian py2removal, from the public UDD mirror (read-only)."""
import statistics
import psycopg2

conn = psycopg2.connect(host="udd-mirror.debian.net", dbname="udd",
                        user="udd-mirror", password="udd-mirror")
conn.set_client_encoding("UTF8")
cur = conn.cursor()

# All py2removal bugs, open (bugs) or archived (archived_bugs).
cur.execute("""
    with tagged as (select distinct id from bugs_usertags where tag = 'py2removal'),
    allbugs as (
        select id, source, arrival, last_modified, status, done, 'open_table' as t from bugs
        union all
        select id, source, arrival, last_modified, status, done, 'archived' from archived_bugs)
    select b.* from allbugs b join tagged using (id)
""")
rows = cur.fetchall()
ids = {r[0] for r in rows}
print(f"py2removal bugs found in bugs/archived_bugs: {len(rows)} (tagged: 3480)")

# Block links, both tables, both directions.
cur.execute("select id, blocker from bugs_blockedby union select id, blocker from archived_bugs_blockedby")
edges = [(a, b) for a, b in cur.fetchall() if a in ids or b in ids]
blocked = {a for a, b in edges if a in ids}
blocking = {b for a, b in edges if b in ids}
both_tagged = [(a, b) for a, b in edges if a in ids and b in ids]
print(f"edges touching py2removal bugs: {len(edges)}")
print(f"  bugs that are blocked by something: {len(blocked)} ({len(blocked)/len(ids):.0%})")
print(f"  bugs that block something:          {len(blocking)}")
print(f"  edges where BOTH ends are py2removal (package -> package): {len(both_tagged)}")

# Cycles: A blocked by B and B blocked by A (sign of bulk-generated links).
s = set(both_tagged)
print(f"  2-cycles (A<-B and B<-A): {sum(1 for a, b in s if (b, a) in s) // 2}")

# Status and time-to-close (upper bound: last_modified).
done = [r for r in rows if r[5]]
print(f"closed: {len(done)}  still open: {len(rows) - len(done)}")
days = sorted((r[3] - r[2]).days for r in done if r[3] and r[2])
print(f"days filed -> last_modified (closed bugs, upper bound on close): "
      f"median {statistics.median(days)}, p90 {days[int(len(days)*.9)]}, max {days[-1]}")
arr = sorted(r[2] for r in rows if r[2])
from collections import Counter
day, n = Counter(a.date() for a in arr).most_common(1)[0]
print(f"filed between {arr[0]:%Y-%m-%d} and {arr[-1]:%Y-%m-%d}; busiest day {day} with {n} bugs")

# py2keep: packages that asked to keep Python 2 (natural stall/exception cases).
cur.execute("select count(distinct id) from bugs_usertags where tag = 'py2keep'")
print(f"py2keep bugs: {cur.fetchone()[0]}")

# Save edges for step 2 sampling.
with open("debian_edges.tsv", "w") as f:
    f.write("blocked_bug\tblocker_bug\n")
    for a, b in sorted(edges):
        f.write(f"{a}\t{b}\n")
