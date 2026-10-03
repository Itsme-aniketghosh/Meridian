"""Anonymous REST list of all gitlab-org/gitlab issues created 2025-01-01..2025-07-01 (slim fields:
closed_by actor, closed_at, merge_requests_count, closed_as_duplicate_of, moved_to_id). Usage: python3 tix_pull_rest_issues.py"""
import json, time, urllib.request, sys, os
S = 'data/tix'
OUT = f'{S}/rest_issues.jsonl'
def get(url):
    for a in range(6):
        try:
            r = urllib.request.urlopen(url, timeout=60)
            rem = int(r.headers.get('RateLimit-Remaining', 100))
            d = json.load(r)
            if rem < 50: time.sleep(30)
            return d, r.headers
        except Exception as e:
            print('retry', a, e, file=sys.stderr); time.sleep(10 * 2 ** a)
    raise SystemExit('fail ' + url)
months = ['2025-01-01','2025-02-01','2025-03-01','2025-04-01','2025-05-01','2025-06-01','2025-07-01']
if os.path.exists(OUT): os.remove(OUT)
with open(OUT, 'w') as f:
    for a, b in zip(months, months[1:]):
        page = 1
        while page:
            u = (f'https://gitlab.com/api/v4/projects/278964/issues?scope=all&state=all&created_after={a}T00:00:00Z'
                 f'&created_before={b}T00:00:00Z&per_page=100&page={page}&order_by=created_at&sort=asc')
            d, h = get(u)
            for x in d:
                f.write(json.dumps({'iid': x['iid'], 'state': x['state'], 'created_at': x['created_at'], 'closed_at': x['closed_at'],
                  'closed_by': (x.get('closed_by') or {}).get('username'), 'mr_count': x.get('merge_requests_count'),
                  'dup_of': x['_links'].get('closed_as_duplicate_of'), 'moved_to_id': x.get('moved_to_id'),
                  'labels': x['labels'], 'title': x['title'], 'author': x['author']['username'], 'web_url': x['web_url']}) + '\n')
            page = int(h.get('X-Next-Page') or 0)
            time.sleep(0.6)
        print('month done', a, time.strftime('%X'), flush=True)
