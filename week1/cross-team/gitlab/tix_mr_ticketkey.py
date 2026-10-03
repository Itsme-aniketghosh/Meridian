"""Ticket-key rate for merged MRs in gitlab-org/gitlab created 2025-H1. Seeded (seed=1): 25 random days, 20 random merged MRs
from each day (anon REST, per_page=100 of that day) -> ~500 MRs; then /closes_issues per MR. Usage: python3 tix_mr_ticketkey.py"""
import json, time, urllib.request, random, re, datetime, sys
S = 'data/tix'
API = 'https://gitlab.com/api/v4/projects/278964'
def get(url):
    for a in range(6):
        try:
            r = urllib.request.urlopen(url, timeout=60); d = json.load(r)
            if int(r.headers.get('RateLimit-Remaining', 100)) < 60: time.sleep(30)
            return d
        except Exception as e:
            print('retry', a, e, file=sys.stderr); time.sleep(10 * 2 ** a)
    raise SystemExit('fail')
def gql(q):
    r = urllib.request.urlopen(urllib.request.Request('https://gitlab.com/api/graphql', json.dumps({'query': q}).encode(), {'Content-Type': 'application/json'}))
    return json.load(r)
total = gql('{ project(fullPath:"gitlab-org/gitlab"){ mergeRequests(state:merged, createdAfter:"2025-01-01T00:00:00Z", createdBefore:"2025-07-01T00:00:00Z"){ count } } }')
total = total['data']['project']['mergeRequests']['count']
rnd = random.Random(1)
days = sorted(rnd.sample(range(181), 25))
out = []
for dd in days:
    d0 = datetime.date(2025, 1, 1) + datetime.timedelta(dd); d1 = d0 + datetime.timedelta(1)
    mrs = get(f'{API}/merge_requests?state=merged&created_after={d0}T00:00:00Z&created_before={d1}T00:00:00Z&per_page=100')
    for m in rnd.sample(mrs, min(20, len(mrs))):
        ci = get(f'{API}/merge_requests/{m["iid"]}/closes_issues'); time.sleep(0.5)
        desc = m.get('description') or ''
        out.append({'iid': m['iid'], 'day': str(d0), 'title': m['title'], 'author': m['author']['username'], 'labels': m['labels'],
            'closes_issues': [x.get('web_url') or x.get('iid') for x in ci],
            'kw_close': bool(re.search(r'(?i)\b(clos(e|es|ed|ing)|fix(es|ed|ing)?|resolv(e|es|ed|ing)|implement(s|ed|ing)?)\b:?\s+(https://gitlab\.com/\S+/-/(issues|work_items)/\d+|[\w./-]*#\d+)', desc)),
            'kw_related': bool(re.search(r'(?i)(related( to)?|relates to|part of|contributes to|see|for)\b:?\s+(https://gitlab\.com/\S+/-/(issues|work_items)/\d+|[\w./-]*#\d+)', desc)),
            'any_issue_ref': bool(re.search(r'(https://gitlab\.com/\S+/-/(issues|work_items)/\d+|(?<![\w!&])[\w./-]*#\d+)', desc + ' ' + m['title'])),
            'any_epic_ref': bool(re.search(r'/-/epics/\d+|&\d+', desc))})
    print(d0, len(mrs), len(out), flush=True)
json.dump({'total_merged_2025H1': total, 'days': days, 'mrs': out}, open(f'{S}/mr_ticketkey.json', 'w'))
