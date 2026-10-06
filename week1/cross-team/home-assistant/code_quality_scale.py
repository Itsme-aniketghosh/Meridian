"""#6 optional: per-integration quality_scale.yaml rule statuses (done/todo/exempt) at quarterly snapshots = a burndown across owners.
Run: python3 code_quality_scale.py"""
import subprocess, re, collections
from code_specs import R
def sh(*a): return subprocess.run(["git", "-C", R, *a], capture_output=True, text=True).stdout
for d in ["2024-11-01", "2025-01-01", "2025-04-01", "2025-07-01", "2025-10-01", "2026-01-01", "2026-04-01", "2026-07-01", "2026-10-01"]:
    c = sh("rev-list", "-1", "--first-parent", f"--before={d}T00:00:00Z", "dev").strip()
    files = [f for f in sh("ls-tree", "-r", "--name-only", c, "homeassistant/components").splitlines() if f.endswith("/quality_scale.yaml")]
    st = collections.Counter(); full = 0
    for f in files:
        txt = sh("show", f"{c}:{f}"); s = collections.Counter(re.findall(r"^\s{2}[\w-]+:\s*(done|todo|exempt)\b", txt, re.M) + re.findall(r"^\s{4}status:\s*(done|todo|exempt)\b", txt, re.M))
        st.update(s); full += s["todo"] == 0
    tot = sum(st.values()) or 1
    print(d, "integrations with quality_scale.yaml", len(files), "| rules", sum(st.values()), {k: f"{v/tot:.0%}" for k, v in st.items()}, "| integrations with 0 todo", full, flush=True)
