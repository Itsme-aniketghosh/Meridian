"""Pull closing MRs + work-item Status + duplicate/moved links for all issues in issues.jsonl.
Anonymous GraphQL, 50 iids per request. Resumable. Usage: python3 tix_pull_closing.py"""
import json, os, time, urllib.request, sys
S = '.'
OUT = f'data/tix/closing_mrs.jsonl'
Q = '''query($iids:[String!]){ project(fullPath:"gitlab-org/gitlab"){ workItems(iids:$iids, first:50){ nodes{ iid state closedAt duplicatedToWorkItemUrl movedToWorkItemUrl
 widgets{ ... on WorkItemWidgetDevelopment { closingMergeRequests(first:10){ nodes{ fromMrDescription mergeRequest{ iid state mergedAt project{ fullPath } } } } }
 ... on WorkItemWidgetStatus { status { name category } } } } } } }'''
def post(iids):
    body = json.dumps({'query': Q, 'variables': {'iids': iids}}).encode()
    for a in range(6):
        try:
            r = urllib.request.urlopen(urllib.request.Request('https://gitlab.com/api/graphql', body, {'Content-Type': 'application/json'}), timeout=60)
            d = json.load(r)
            if 'errors' in d and not d.get('data'): raise RuntimeError(d['errors'])
            return d
        except Exception as e:
            print('retry', a, str(e)[:200], file=sys.stderr); time.sleep(5 * 2 ** a)
    raise SystemExit('failed')
if __name__ == '__main__':
    iids = [json.loads(l)['iid'] for l in open(f'{S}/issues.jsonl')]
    done = set()
    if os.path.exists(OUT):
        done = {json.loads(l)['_batch'] for l in open(OUT)}
    test = len(sys.argv) > 1 and sys.argv[1] == 'test'
    with open(OUT, 'a') as f:
        for b in range(0, len(iids), 50):
            if b in done: continue
            d = post(iids[b:b + 50])
            if test: print(json.dumps(d)[:1500]); break
            if d.get('errors'): print('partial errors', b, str(d['errors'])[:200], file=sys.stderr)
            for n in d['data']['project']['workItems']['nodes']:
                n['_batch'] = b; f.write(json.dumps(n) + '\n')
            if not d['data']['project']['workItems']['nodes']:
                f.write(json.dumps({'_batch': b, 'iid': None}) + '\n')
            f.flush()
            if b % 2500 == 0: print('batch', b, time.strftime('%X'), flush=True)
            time.sleep(0.3)
