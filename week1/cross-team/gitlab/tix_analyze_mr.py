"""Analyze mr_ticketkey.json. Usage: python3 tix_analyze_mr.py"""
import json, collections
S = 'data/tix'
d = json.load(open(f'{S}/mr_ticketkey.json')); M = d['mrs']; n = len(M)
print('merged MRs created 2025-H1 (GraphQL count):', d['total_merged_2025H1'], '| sample N =', n, 'from', len(d['days']), 'days')
# MR /closes_issues returns [] once an MR is merged (verified on !179793 which closes #516141) -> use issue-side links instead
inv = set()
for l in open(f'{S}/closing_mrs.jsonl'):
    x = json.loads(l)
    for w in x.get('widgets') or []:
        for nd in (w or {}).get('closingMergeRequests', {}).get('nodes', []):
            if nd.get('mergeRequest') and nd['mergeRequest']['project']['fullPath'] == 'gitlab-org/gitlab': inv.add(int(nd['mergeRequest']['iid']))
for m in M: m['issue_side_closing'] = m['iid'] in inv
f = lambda k: sum(1 for m in M if k(m))
rows = [('closes_issues API (BROKEN for merged MRs)', lambda m: m['closes_issues']),
        ('is closing MR of a 2025-H1 issue (issue-side link)', lambda m: m['issue_side_closing']),
        ('description "Closes/Fixes/Resolves #/URL"', lambda m: m['kw_close']),
        ('description "Related to/Part of/... #/URL"', lambda m: m['kw_related']),
        ('ANY issue ref in title/description (#N or issue URL)', lambda m: m['any_issue_ref']),
        ('issue-side closing link OR any issue ref', lambda m: m['issue_side_closing'] or m['any_issue_ref']),
        ('epic ref only (no issue ref, no closing link)', lambda m: m['any_epic_ref'] and not m['any_issue_ref'] and not m['closes_issues'])]
for name, k in rows: print(f'{name:55s} {f(k):4d}/{n} = {100*f(k)/n:.1f}%')
none = [m for m in M if not m['issue_side_closing'] and not m['any_issue_ref']]
bots = {'gitlab-bot', 'gitlab-dependency-update-bot', 'gitlab-crowdin-bot', 'project_278964_bot'}
print('no ref at all:', len(none), '| by author top:', collections.Counter(m['author'] for m in none).most_common(6))
nb = [m for m in M if not any(b in m['author'] for b in bots) and 'bot' not in m['author']]
print(f'excluding bot authors: N={len(nb)}, closing link or any issue ref = {sum(1 for m in nb if m["closes_issues"] or m["any_issue_ref"])} ({100*sum(1 for m in nb if m["closes_issues"] or m["any_issue_ref"])/len(nb):.1f}%)')
print('no-ref labels top:', collections.Counter(l for m in none for l in m['labels'] if l.startswith('type::') or l in ('backstage', 'documentation', 'Technical Writing', 'database')).most_common(8))
