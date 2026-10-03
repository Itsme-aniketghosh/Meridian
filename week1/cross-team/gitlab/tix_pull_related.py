"""REST /issues/:iid/related_merge_requests (per-issue only; GraphQL also refuses >1 per query) for a seeded sample:
3,000 random closed issues (seed=1) + 700 extra random closed type::bug (seed=2). Anonymous, ~180 req/min, resumable.
Note: REST merge_requests_count counts CLOSING MRs only, so this pull is needed to see mention/related MRs.
Usage: python3 tix_pull_related.py [NSHARDS]   (runs NSHARDS parallel workers, default 4; each ~1 req/s)"""
import json, time, urllib.request, random, os, sys, glob, multiprocessing
G = '.'
OUT = f'data/tix/related_sample.jsonl'
base = [json.loads(l) for l in open(f'{G}/issues.jsonl')]
closed = sorted(d['iid'] for d in base if d['state'] == 'closed')
bugs = sorted(d['iid'] for d in base if d['state'] == 'closed' and any(l['title'] == 'type::bug' for l in d['labels']['nodes']))
s1 = random.Random(1).sample(closed, 3000)
rest_bugs = sorted(set(bugs) - set(s1))
s2 = random.Random(2).sample(rest_bugs, min(700, len(rest_bugs)))
todo = [(i, 'random3000') for i in s1] + [(i, 'bug_extra') for i in s2]
def work(shard, nsh):
  done = {json.loads(l)['iid'] for fn in glob.glob(f'data/tix/related_sample*.jsonl') for l in open(fn)}
  with open(f'data/tix/related_sample.s{shard}.jsonl', 'a') as f:
    for k, (i, src) in enumerate(todo):
        if k % nsh != shard or i in done: continue
        for a in range(7):
            try:
                r = urllib.request.urlopen(f'https://gitlab.com/api/v4/projects/278964/issues/{i}/related_merge_requests?per_page=100', timeout=60)
                d = json.load(r); rem = int(r.headers.get('RateLimit-Remaining', 100)); break
            except Exception as e:
                print('retry', i, a, e, file=sys.stderr, flush=True); time.sleep(15 * 2 ** a)
        else: raise SystemExit('fail')
        f.write(json.dumps({'iid': i, 'src': src, 'related': [{'iid': m['iid'], 'state': m['state'], 'merged_at': m.get('merged_at'),
                 'project_id': m['project_id']} for m in d]}) + '\n'); f.flush()
        time.sleep(0.5 if rem > 150 else 10)
        if k % 500 == 0: print(k, time.strftime('%X'), flush=True)
if __name__ == '__main__':
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 4
    ps = [multiprocessing.Process(target=work, args=(k, n)) for k in range(n)]
    [p.start() for p in ps]; [p.join() for p in ps]
