"""Failure #2 + ticket quality analysis. Inputs: issues.jsonl, tix/closing_mrs.jsonl, tix/rest_issues.jsonl, tix/related_sample*.jsonl.
Usage: python3 tix_analyze_done.py"""
import json, statistics, collections, datetime, os, glob
G = '.'
S = 'data/tix'
P = lambda t: datetime.datetime.fromisoformat(t.replace('Z', '+00:00')) if t else None
base = {d['iid']: d for d in map(json.loads, open(f'{G}/issues.jsonl'))}
cl = {}
for d in map(json.loads, open(f'{S}/closing_mrs.jsonl')):
    if not d.get('iid'): continue
    w = [x for x in d['widgets'] if x]
    d['closing'] = [n for x in w if 'closingMergeRequests' in x for n in x['closingMergeRequests']['nodes']]
    d['status'] = next((x['status']['name'] for x in w if 'status' in x and x['status']), None)
    cl[d['iid']] = d
rest = {str(d['iid']): d for d in map(json.loads, open(f'{S}/rest_issues.jsonl'))} if os.path.exists(f'{S}/rest_issues.jsonl') else {}
rel = {str(d['iid']): d for fn in glob.glob(f'{S}/related_sample*.jsonl') for d in map(json.loads, open(fn))}
print(f'N base={len(base)} closing-pulled={len(cl)} rest={len(rest)} related-sample={len(rel)}')
def typ(i):
    L = [l['title'] for l in base[i]['labels']['nodes']]
    for t in ('type::bug', 'type::feature', 'type::maintenance', 'type::ignore'):
        if t in L: return t
    return '(no type)'
def labels(i): return [l['title'] for l in base[i]['labels']['nodes']]
closed = [i for i in base if base[i]['state'] == 'closed' and i in cl]
def merged(n): return n.get('mergeRequest') and n['mergeRequest']['state'] == 'merged'
def cat(i):  # full population, CLOSING MRs only (REST merge_requests_count == closing MRs only, so it adds nothing)
    c = cl[i]['closing']
    if any(merged(n) for n in c): return 'A merged closing MR'
    if c: return 'C only unmerged closing MRs'
    return 'D no closing MR'
def cat2(i):  # sample: closing UNION related (mentions)
    c = cl[i]['closing']; r = rel[i]['related']
    if any(merged(n) for n in c): return 'A merged closing MR'
    if any(m['state'] == 'merged' for m in r): return 'B merged related MR only'
    if c or r: return 'C only unmerged/closed MRs'
    return 'D no MR at all'
def show(title, items, f):
    tab = collections.defaultdict(collections.Counter)
    for i in items: tab[typ(i)][f(i)] += 1; tab['ALL'][f(i)] += 1
    print(title)
    for t, c in sorted(tab.items(), key=lambda x: -sum(x[1].values())):
        n = sum(c.values()); print(f'{t:20s} N={n:6d} ' + ' | '.join(f'{k}: {v} ({100*v/n:.1f}%)' for k, v in sorted(c.items())))
BOT = 'project_278964_bot_87c17d71a842955abfceaf361a49f249'
botlab = lambda i: (rest.get(i) or {}).get('author') == BOT or (rest.get(i) or {}).get('title', '').startswith('[Test]')  # lead's 'human-filed' filter
show('\n## FULL closed issues by type x CLOSING MR (N closed=%d)' % len(closed), closed, cat)
show('\n## FULL closed, HUMAN-FILED (excl. flaky-test bot author or [Test] titles) (N=%d)' % len([i for i in closed if not botlab(i)]), [i for i in closed if not botlab(i)], cat)
rs = [i for i in rel if i in cl and base[i]['state'] == 'closed']
r3 = [i for i in rs if rel[i]['src'] == 'random3000']
show('\n## SAMPLE random3000 (seed 1): closing + related MRs (N=%d)' % len(r3), r3, cat2)
rb = [i for i in rs if typ(i) == 'type::bug']
show('\n## SAMPLE all sampled bugs (random3000 bugs + bug_extra) (N=%d)' % len(rb), rb, cat2)
show('\n## SAMPLE random3000 HUMAN-FILED (N=%d)' % len([i for i in r3 if not botlab(i)]), [i for i in r3 if not botlab(i)], cat2)
json.dump({i: cat2(i) for i in rs}, open(f'{S}/sample_cat2.json', 'w'))
# reverse: open issues with merged closing MR
op = [i for i in base if i in cl and cl[i]['state'] == 'OPEN']
rev = [i for i in op if any(n['mergeRequest'] and n['mergeRequest']['state'] == 'merged' for n in cl[i]['closing'])]
revd = [i for i in rev if any(n['fromMrDescription'] for n in cl[i]['closing'] if n['mergeRequest'] and n['mergeRequest']['state'] == 'merged')]
now = P('2026-10-02T00:00:00Z')
age = [(now - max(P(n['mergeRequest']['mergedAt']) for n in cl[i]['closing'] if n['mergeRequest'] and n['mergeRequest']['mergedAt'])).days for i in rev]
print(f'\n## Reverse: OPEN now N={len(op)}; with merged closing MR: {len(rev)} ({100*len(rev)/len(op):.1f}%), of which link came from MR description: {len(revd)}; median days since merge {statistics.median(age) if age else None}; >90d: {sum(a>90 for a in age)}')
print('   by type', collections.Counter(typ(i) for i in rev).most_common(), '| HUMAN-FILED: open', sum(1 for i in op if not botlab(i)), 'with merged closing MR', sum(1 for i in rev if not botlab(i)))
json.dump(rev, open(f'{S}/open_with_merged_closing_mr.json', 'w'))
# time gap
gaps = []
for i in closed:
    m = [P(n['mergeRequest']['mergedAt']) for n in cl[i]['closing'] if n['mergeRequest'] and n['mergeRequest']['state'] == 'merged' and n['mergeRequest']['mergedAt']]
    if m: gaps.append(((P(cl[i]['closedAt'] or base[i]['closedAt']) - max(m)).total_seconds() / 86400, i))
g = [x for x, _ in gaps]
print(f'\n## Close minus last merge (days) N={len(g)}: median {statistics.median(g):.3f}; p90 {statistics.quantiles(g, n=10)[-1]:.1f}; '
      f'closed >7d after merge {sum(x > 7 for x in g)} ({100*sum(x>7 for x in g)/len(g):.1f}%); closed >1h BEFORE merge {sum(x < -1/24 for x in g)} ({100*sum(x < -1/24 for x in g)/len(g):.1f}%); within +-1h {sum(abs(x) <= 1/24 for x in g)} ({100*sum(abs(x)<=1/24 for x in g)/len(g):.1f}%)')
gh = [x for x, i in gaps if not botlab(i)]
print(f'   HUMAN-FILED N={len(gh)}: median {statistics.median(gh):.3f}; >7d {sum(x>7 for x in gh)} ({100*sum(x>7 for x in gh)/len(gh):.1f}%); >1h before merge {sum(x < -1/24 for x in gh)} ({100*sum(x < -1/24 for x in gh)/len(gh):.1f}%)')
json.dump(sorted([(round(x, 2), i, typ(i)) for x, i in gaps if x < -1/24]), open(f'{S}/closed_before_merge.json', 'w'))
print('   closed before merge by type', collections.Counter(typ(i) for x, i in gaps if x < -1/24).most_common(), 'median days early', statistics.median([-x for x, i in gaps if x < -1/24]))
# how closed: status / dup / moved / closer for closed with no merged closing MR
nom = [i for i in closed if not cat(i).startswith('A')]
def how(i):
    r = rest.get(i, {}); c = cl[i]
    if c.get('movedToWorkItemUrl') or r.get('moved_to_id'): return 'moved'
    if c.get('duplicatedToWorkItemUrl') or r.get('dup_of'): return 'duplicate (linked)'
    return 'status:' + str(c['status'])
print('\n## How closed (no merged closing MR, full pop) N=%d' % len(nom))
nb = [i for i in rs if typ(i) == 'type::bug' and cat2(i) in ('C only unmerged/closed MRs', 'D no MR at all')]
print('SAMPLE bugs with NO merged MR (closing or related) N=%d' % len(nb), collections.Counter(how(i) for i in nb).most_common(), 'auto-closed/stale label:', sum(1 for i in nb if {'auto closed','stale'} & set(labels(i))))
json.dump(sorted(nb), open(f'{S}/sample_bug_no_merged_any.json', 'w'))
for t in ['type::bug', 'ALL']:
    sub = [i for i in nom if t == 'ALL' or typ(i) == t]
    print(t, len(sub), collections.Counter(how(i) for i in sub).most_common(10))
    print('   closer', collections.Counter((rest.get(i) or {}).get('closed_by') for i in sub).most_common(6))
    print('   labels', collections.Counter(l for i in sub for l in labels(i) if any(k in l.lower() for k in ['duplicate', 'auto closed', 'auto-closed', "won't", 'wontfix', 'needs', 'info', 'stale', 'cannot reproduce', 'invalid', 'workflow::', 'closed', 'support'])).most_common(14))
json.dump([i for i in nom if typ(i) == 'type::bug'], open(f'{S}/closed_bug_no_merged_mr.json', 'w'))
# ticket quality
print('\n## Ticket quality, N closed=%d, N all=%d' % (len(closed), len(cl)))
allc = [i for i in base if base[i]['state'] == 'closed']
dup = [i for i in allc if (rest.get(i) or {}).get('dup_of') or (cl.get(i) or {}).get('duplicatedToWorkItemUrl')]
print('closed as duplicate (original linked):', len(dup), 'of', len(allc), '| HUMAN-FILED', sum(1 for i in dup if not botlab(i)), 'of', sum(1 for i in allc if not botlab(i)))
print('HUMAN-FILED status (closed):', collections.Counter((cl.get(i) or {}).get('status') for i in allc if not botlab(i)).most_common())
print('bot-filed share of closed:', sum(1 for i in allc if botlab(i)), 'of', len(allc), '| of all:', sum(1 for i in base if botlab(i)), 'of', len(base))
print('bulk-closed that are bot-filed:', sum(1 for i in allc if botlab(i) and i in set(json.load(open(f'{S}/bulk_closed_iids.json')))) if os.path.exists(f'{S}/bulk_closed_iids.json') else '')
print('auto-closed label: all', sum('auto closed' in labels(i) for i in allc), '| HUMAN-FILED', sum('auto closed' in labels(i) for i in allc if not botlab(i)))
print('moved:', sum(1 for i in allc if (rest.get(i) or {}).get('moved_to_id') or (cl.get(i) or {}).get('movedToWorkItemUrl')))
print('status counts (closed):', collections.Counter((cl.get(i) or {}).get('status') for i in allc).most_common())
print('status counts (open):', collections.Counter((cl.get(i) or {}).get('status') for i in base if base[i]['state'] != 'closed').most_common(8))
lc = collections.Counter(l for i in allc for l in labels(i))
lch = collections.Counter(l for i in allc if not botlab(i) for l in labels(i))
print('selected labels on closed HUMAN-FILED:', {k: v for k, v in lch.items() if any(s in k.lower() for s in ['duplicate', 'auto closed', "won't", 'wontfix', 'needs', 'more info', 'stale', 'reproduc', 'invalid', 'type::ignore'])})
print('selected labels on closed:', {k: v for k, v in lc.items() if any(s in k.lower() for s in ['duplicate', 'auto closed', "won't", 'wontfix', 'needs', 'more info', 'stale', 'reproduc', 'invalid', 'workflow::complete', 'workflow::verification', 'workflow::production', 'type::ignore'])})
# bulk closes: hour buckets
hb = collections.Counter(); hba = collections.Counter()
for i in allc:
    t = P(base[i]['closedAt']); h = t.strftime('%Y-%m-%dT%H'); hb[h] += 1
    hba[(h, (rest.get(i) or {}).get('closed_by'))] += 1
print('hours with >=500 closes (any actor):', [(h, n) for h, n in hb.most_common() if n >= 500])
print('top hours:', hb.most_common(8))
print('hours with >=500 closes by ONE actor:', [(h, a, n) for (h, a), n in hba.most_common() if n >= 500])
print('top hour-actor:', hba.most_common(8))
bulk_hours = {h for (h, a), n in hba.items() if n >= 500}
bulk = [i for i in allc if (P(base[i]['closedAt']).strftime('%Y-%m-%dT%H'), (rest.get(i) or {}).get('closed_by')) in {k for k, n in hba.items() if n >= 500}]
print('issues in bulk-close events:', len(bulk))
print('closer of all closed:', collections.Counter((rest.get(i) or {}).get('closed_by') for i in allc).most_common(6))
json.dump(bulk, open(f'{S}/bulk_closed_iids.json', 'w'))
