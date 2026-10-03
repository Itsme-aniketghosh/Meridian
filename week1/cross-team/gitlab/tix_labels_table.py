"""Pair-2-style labels table. Usage: python3 tix_labels_table.py (after tix_analyze_done.py)"""
import json
S = 'data/tix'
rest = {str(d['iid']): d for d in map(json.loads, open(f'{S}/rest_issues.jsonl'))}
dupw = {d['iid'] for d in map(json.loads, open(f'{S}/closing_mrs.jsonl')) if d.get('duplicatedToWorkItemUrl')}  # REST _links.closed_as_duplicate_of is always null
stat = {d['iid']: next((w['status']['name'] for w in d.get('widgets') or [] if w and w.get('status')), None) for d in map(json.loads, open(f'{S}/closing_mrs.jsonl')) if d.get('iid')}
bulk = set(json.load(open(f'{S}/bulk_closed_iids.json')))
BOT = 'project_278964_bot_87c17d71a842955abfceaf361a49f249'
bot = lambda d: d['author'] == BOT or d['title'].startswith('[Test]')
cl = [d for d in rest.values() if d['state'] == 'closed']
rows = [('All closed', lambda d: True), ('type::bug', lambda d: 'type::bug' in d['labels']), ('type::feature', lambda d: 'type::feature' in d['labels']),
        ('type::maintenance', lambda d: 'type::maintenance' in d['labels']), ('auto closed (label)', lambda d: 'auto closed' in d['labels']),
        ('stale (label)', lambda d: 'stale' in d['labels']), ('closed as duplicate (GraphQL duplicatedTo link)', lambda d: str(d['iid']) in dupw), ('Status = Duplicate', lambda d: stat.get(str(d['iid'])) == 'Duplicate'), ("Status = Won't do", lambda d: stat.get(str(d['iid'])) == "Won't do"),
        ("won't do / wontfix (label)", lambda d: bool({"won't do", 'wontfix'} & set(d['labels']))),
        ('cannot reproduce (label)', lambda d: bool({'Cannot reproduce', 'closed::cannot reproduce'} & set(d['labels']))),
        ('moved to other project', lambda d: bool(d['moved_to_id']))]
print('| Label | Total closed | bulk-closed | bot-filed | usable (human-filed, not bulk, not auto-closed) |')
for n, f in rows:
    x = [d for d in cl if f(d)]
    print(f"| {n} | {len(x)} | {sum(str(d['iid']) in bulk for d in x)} | {sum(bot(d) for d in x)} | {sum(1 for d in x if not bot(d) and str(d['iid']) not in bulk and 'auto closed' not in d['labels'])} |")
