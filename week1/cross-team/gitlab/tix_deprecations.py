"""Fetch data/deprecations/*.yml at master and at snapshots 2024-01-01 / 2025-01-01, detect removal-milestone slips.
Anonymous REST: one repository/archive.tar.gz?path=data/deprecations per snapshot (web raw 429s). Usage: python3 tix_deprecations.py   (no PyYAML needed: regex parse of flat keys)"""
import json, tarfile, io, time, urllib.request, urllib.parse, os, sys, re
S = 'data/tix'
API = 'https://gitlab.com/api/v4/projects/278964'
def get(url, raw=False):
    for a in range(6):
        try:
            r = urllib.request.urlopen(url, timeout=60); b = r.read()
            return (b.decode() if raw else json.loads(b)), r.headers
        except Exception as e:
            if getattr(e, 'code', 0) == 404: return None, {}
            print('retry', a, e, url, file=sys.stderr); time.sleep(5 * 2 ** a)
    raise SystemExit('fail')
KEYS = ['title','announcement_milestone','removal_milestone','breaking_change','reporter','stage','issue_url','end_of_support_milestone','window','removal_date','announcement_date']
def parse(txt):
    # each record starts with '- title:'; keep flat scalar keys only
    recs, cur = [], None
    for line in txt.splitlines():
        m = re.match(r'^(- |  )([a-z_]+):\s*(.*)$', line)
        if not m: continue
        if m.group(1) and m.group(1).startswith('-'): cur = {}; recs.append(cur)
        if cur is None or m.group(2) not in KEYS: continue
        v = m.group(3).strip().strip('"\'')
        if m.group(2) not in cur: cur[m.group(2)] = v
    return recs
def sha_at(until):
    d, _ = get(f'{API}/repository/commits?ref_name=master&until={until}&per_page=1'); return d[0]['id']
def tree(ref):
    out, page = [], 1
    while page:
        d, h = get(f'{API}/repository/tree?path=data/deprecations&ref={ref}&per_page=100&page={page}')
        out += [x['path'] for x in d if x['type'] == 'blob' and x['path'].endswith('.yml')]
        page = int(h.get('X-Next-Page') or 0)
    return out
snaps = {'master': 'master', '2024-01-01': sha_at('2024-01-01T00:00:00Z'), '2025-01-01': sha_at('2025-01-01T00:00:00Z')}
res = {}
for name, ref in snaps.items():
    for a in range(6):
        try: b = urllib.request.urlopen(f'{API}/repository/archive.tar.gz?sha={ref}&path=data/deprecations', timeout=120).read(); break
        except Exception as e: print('retry', e, file=sys.stderr); time.sleep(20 * 2 ** a)
    recs = {}
    with tarfile.open(fileobj=io.BytesIO(b)) as t:
        for m in t.getmembers():
            if m.isfile() and m.name.endswith('.yml') and '/templates/' not in m.name:
                recs[m.name.split('/data/deprecations/')[1]] = parse(t.extractfile(m).read().decode('utf-8', 'replace'))
    res[name] = {'ref': ref, 'records': recs}
    print(name, ref, len(recs), flush=True); time.sleep(5)
json.dump(res, open(f'{S}/deprecations.json', 'w'), default=str)
