"""Sample counted url() lines at the parent of each migration commit and check each one by hand."""
import random, re, subprocess, sys
sys.path.insert(0, str(__import__("pathlib").Path(__file__).parent))
from pair3_scan import ROOT, git, imports_url, CALL_RE, ATTR_RE

TARGETS = [  # repo, migration commit
    ("encode_django-rest-framework", "410575da"), ("wagtail_wagtail", "4076b9ef5e"),
    ("django-oscar_django-oscar", "10dce286b"), ("pennersr_django-allauth", "c4d7b410"),
    ("jazzband_django-two-factor-auth", "fe57c40"), ("jazzband_django-debug-toolbar", "47d6b5e9"),
    ("django_channels", "a12800e"), ("django-guardian_django-guardian", "5a97f63"),
]


def counted_lines(repo, sha):
    out = []
    for f in git(repo, "grep", "-l", "-E", "conf\\.urls|conf import urls", sha, "--", "*.py").split():
        path = f.split(":", 1)[1]
        src = git(repo, "show", f)
        uses = imports_url(src)
        for i, line in enumerate(src.splitlines(), 1):
            s = line.lstrip()
            if uses and s.startswith("from django.conf.urls import") or ATTR_RE.search(line) or (
                    uses and CALL_RE.search(line) and not s.startswith(("#", "from", "import"))):
                out.append((path, i, line.strip()))
    return out


def deleted_old_lines(repo, parent, sha, path):
    diff = git(repo, "diff", "-U0", parent, sha, "--", path)
    gone = set()
    for m in re.finditer(r"^@@ -(\d+)(?:,(\d+))? ", diff, re.M):
        start, n = int(m.group(1)), int(m.group(2) or 1)
        gone.update(range(start, start + n))
    return gone


random.seed(7)
pool = []
for name, sha in TARGETS:
    repo = ROOT / name
    parent = git(repo, "rev-parse", f"{sha}^1").strip()
    pool += [(name, parent, sha, *l) for l in counted_lines(repo, parent)]
print("pool", len(pool))
for name, parent, sha, path, ln, text in sorted(random.sample(pool, 30)):
    changed = ln in deleted_old_lines(ROOT / name, parent, sha, path)
    after = git(ROOT / name, "show", f"{sha}:{path}").splitlines()
    print(f"{'EDITED' if changed else 'KEPT  '} | {name.split('_',1)[1]}:{path}:{ln} | {text[:90]}")
