"""Seeded (seed=1) 20 of the sampled closed type::bug issues with NO merged MR (closing or related), from tix/sample_bug_no_merged_any.json.
Fetches last notes for hand-reading -> tix/handread_20_raw.json. Usage: python3 tix_handread_fetch.py"""
import json, random, urllib.request, time
S = 'data/tix'
pool = json.load(open(f'{S}/sample_bug_no_merged_any.json'))
pick = sorted(random.Random(1).sample(pool, 20))
rest = {str(d['iid']): d for d in map(json.loads, open(f'{S}/rest_issues.jsonl'))}
out = []
for i in pick:
    # REST /notes returns 401 anonymously -> anonymous GraphQL
    q = '{ project(fullPath:"gitlab-org/gitlab"){ issue(iid:"%s"){ description notes(last:12){ nodes{ body system author{ username } } } } } }' % i
    g = json.load(urllib.request.urlopen(urllib.request.Request('https://gitlab.com/api/graphql', json.dumps({'query': q}).encode(), {'Content-Type': 'application/json'})))
    iss = g['data']['project']['issue']; notes = [{'author': n['author'] or {'username': '?'}, 'system': n['system'], 'body': n['body']} for n in reversed(iss['notes']['nodes'])]
    r = rest[i]
    out.append({'iid': i, 'url': r['web_url'], 'title': r['title'], 'closed_by': r['closed_by'], 'dup_of': r['dup_of'], 'moved_to_id': r['moved_to_id'],
                'labels': [l for l in r['labels'] if not l.startswith(('Category:', 'section::', 'devops::', 'GitLab '))],
                'desc': (iss['description'] or '')[:500].replace('\n', ' '), 'notes': [(n['author']['username'], n['system'], n['body'][:300].replace('\n', ' ')) for n in notes]})
    time.sleep(0.5)
json.dump(out, open(f'{S}/handread_20_raw.json', 'w'), indent=1)
