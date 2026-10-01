"""Pull every SPARK ticket from Apache Jira (anonymous REST) as raw gzipped JSON pages, with changelog.

Pages are ORDER BY key ASC, so tickets created mid-pull land at the end and paging stays stable.
Re-running resumes: pages already on disk are skipped. Timing and failures go to pull_log.jsonl.
"""
import gzip, json, subprocess, sys, time, urllib.parse
from pathlib import Path

OUT = Path(__file__).parent / "data" / "jira_raw"
BASE = "https://issues.apache.org/jira/rest/api/2/search"
PAGE = 100  # 1000 works too, but changelog makes big pages slow and fragile


def fetch(start):
    q = urllib.parse.urlencode({"jql": "project=SPARK ORDER BY key ASC", "startAt": start,
                                "maxResults": PAGE, "fields": "*all", "expand": "changelog"})
    for attempt in range(6):  # curl, not urllib: python.org builds on macOS ship without CA certs
        t = time.time()
        r = subprocess.run(["curl", "-s", "--max-time", "120", "-w", "\n%{http_code}", f"{BASE}?{q}"],
                           capture_output=True)
        body, _, code = r.stdout.rpartition(b"\n")
        code = int(code or 0)
        if r.returncode == 0 and code == 200:
            return body, time.time() - t, attempt, code
        log({"start": start, "error": r.stderr.decode()[:200], "code": code, "curl_rc": r.returncode, "attempt": attempt})
        time.sleep(2 ** attempt * (10 if code == 429 else 1))
    raise SystemExit(f"gave up at startAt={start}")


def log(row):
    with open(OUT / "pull_log.jsonl", "a") as f:
        f.write(json.dumps({"t": time.time(), **row}) + "\n")


if __name__ == "__main__":
    OUT.mkdir(parents=True, exist_ok=True)
    start, total, t0 = 0, None, time.time()
    while total is None or start < total:
        path = OUT / f"page_{start:06d}.json.gz"
        if path.exists() and total is not None:
            start += PAGE
            continue
        body, secs, retries, status = fetch(start)
        data = json.loads(body)
        total = data["total"]
        path.write_bytes(gzip.compress(body))
        log({"start": start, "n": len(data["issues"]), "secs": round(secs, 2), "bytes": len(body),
             "retries": retries, "status": status, "total": total})
        print(f"{start:6d}/{total} {secs:5.1f}s elapsed {time.time() - t0:6.0f}s", flush=True)
        start += PAGE
    print(f"done in {time.time() - t0:.0f}s", file=sys.stderr)
