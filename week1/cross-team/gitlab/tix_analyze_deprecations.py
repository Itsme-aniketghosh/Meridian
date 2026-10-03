"""Analyze deprecations.json (from tix_deprecations.py). Usage: python3 tix_analyze_deprecations.py"""
import json, statistics, re, collections
S = 'data/tix'
D = json.load(open(f'{S}/deprecations.json'))
def ms(v):
    m = re.match(r'^(\d+)\.(\d+)', v or ''); return (int(m.group(1)), int(m.group(2))) if m else None
def gap(a, r):  # milestones between; GitLab does 12 minors/major historically (x.0..x.11), 16 and 17 have 16.0-16.11, 17.0-17.11
    return (r[0] - a[0]) * 12 + (r[1] - a[1])
def flat(snap):
    out = {}
    for f, recs in D[snap]['records'].items():
        for i, r in enumerate(recs):
            out[(f, i)] = {k: re.split(r'\s+#', v)[0].strip().strip('"\'') for k, v in r.items()}
    return out
cur = flat('master')
print('files master', len(D['master']['records']), 'records', len(cur))
R = list(cur.values())
has_issue = sum(1 for r in R if re.match(r'https://gitlab\.com/.+/(issues|work_items|epics)/\d+', r.get('issue_url') or ''))
has_gl_issue = sum(1 for r in R if re.match(r'https://gitlab\.com/gitlab-org/gitlab/-/(issues|work_items)/\d+', r.get('issue_url') or ''))
print('with issue_url (any gitlab issue/epic):', has_issue, 'gitlab-org/gitlab issue:', has_gl_issue)
bc = collections.Counter((r.get('breaking_change') or '').lower() for r in R); print('breaking_change', dict(bc))
gaps = [gap(ms(r['announcement_milestone']), ms(r['removal_milestone'])) for r in R if ms(r.get('announcement_milestone')) and ms(r.get('removal_milestone'))]
print('ann->removal gaps N', len(gaps), 'median', statistics.median(gaps), 'p25/p75', statistics.quantiles(gaps, n=4), 'gap<=0', sum(g <= 0 for g in gaps), 'gap<3', sum(g<3 for g in gaps))
print('missing removal_milestone', sum(1 for r in R if not ms(r.get('removal_milestone'))))
print('stage top', collections.Counter((r.get('stage') or '').lower() for r in R).most_common(6))
print('announce major', collections.Counter(ms(r['announcement_milestone'])[0] for r in R if ms(r.get('announcement_milestone'))))
for old in ['2024-01-01', '2025-01-01']:
    o = flat(old); common = [k for k in o if k in cur]
    later = [k for k in common if ms(o[k].get('removal_milestone')) and ms(cur[k].get('removal_milestone')) and ms(cur[k]['removal_milestone']) > ms(o[k]['removal_milestone'])]
    earlier = [k for k in common if ms(o[k].get('removal_milestone')) and ms(cur[k].get('removal_milestone')) and ms(cur[k]['removal_milestone']) < ms(o[k]['removal_milestone'])]
    gone = [k for k in o if k not in cur]
    print(f'vs {old}: records {len(o)}, still present {len(common)}, removal pushed LATER {len(later)}, pulled earlier {len(earlier)}, record file deleted {len(gone)}')
    for k in later[:5]: print('   slip', k[0], o[k]['removal_milestone'], '->', cur[k]['removal_milestone'])
# overdue: removal milestone already past (current milestone ~ 19.4 in Oct 2026) but record still in folder
print('removal_milestone <= 19.4 still listed', sum(1 for r in R if ms(r.get('removal_milestone')) and ms(r['removal_milestone']) <= (19, 4)))
