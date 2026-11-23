"""Who serves what on the Cloud Run service, read from `gcloud run services describe --format=json` (S3).

deploy.yml asks two things, each as lines for $GITHUB_OUTPUT:
- tagged: the revision a tag points at (the new, no-traffic revision), its tag URL and its share of traffic
- serving: the revisions that carry traffic now, by name (a "latest" entry is resolved to the latest ready
  revision), and the one with the most; with --expect, it fails unless that revision carries 100%

    python Model-Deployment/traffic.py tagged service.json c-85b4684ae336
    python Model-Deployment/traffic.py serving before.json --key previous
    python Model-Deployment/traffic.py serving after.json --key serving --expect meridian-risk-00002-abc
"""
import argparse
import json
import sys


class TrafficError(RuntimeError):
    """The service isn't in the state deploy.yml expects: the message says how."""


def status(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f).get("status") or {}


def tagged(st, tag):
    """(revision, tag URL, percent) for tag."""
    for t in st.get("traffic") or []:
        if t.get("tag") == tag:
            if not t.get("revisionName") or not t.get("url"):
                raise TrafficError(f"tag {tag} has no revision or URL: {t}")
            return t["revisionName"], t["url"], int(t.get("percent") or 0)
    raise TrafficError(f"no traffic entry is tagged {tag}")


def serving(st):
    """{revision: percent} for every revision with traffic, "latest" resolved to the latest ready revision."""
    shares = {}
    for t in st.get("traffic") or []:
        percent = int(t.get("percent") or 0)
        if not percent:
            continue
        name = t.get("revisionName") or (st.get("latestReadyRevisionName") if t.get("latestRevision") else None)
        if not name:
            raise TrafficError(f"a traffic entry with {percent}% names no revision: {t}")
        shares[name] = shares.get(name, 0) + percent
    return shares


def describe(shares):
    return ", ".join(f"{name} {p}%" for name, p in sorted(shares.items(), key=lambda kv: -kv[1])) or "nothing"


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="what", required=True)
    t = sub.add_parser("tagged")
    t.add_argument("service_json")
    t.add_argument("tag")
    s = sub.add_parser("serving")
    s.add_argument("service_json")
    s.add_argument("--key", default="serving", help="the output's name")
    s.add_argument("--expect", help="fail unless this revision carries 100%%")
    a = ap.parse_args(argv)
    try:
        st = status(a.service_json)
        if a.what == "tagged":
            name, url, percent = tagged(st, a.tag)
            lines = [f"name={name}", f"url={url}", f"percent={percent}"]
        else:
            shares = serving(st)
            top = max(shares, key=shares.get) if shares else ""
            lines = [f"{a.key}={describe(shares)}", f"{a.key}_revision={top}"]
            if a.expect:
                if shares.get(a.expect) != 100:
                    raise TrafficError(f"{a.expect} should carry 100% of traffic; the service says {describe(shares)}")
                lines.append(f"url={st.get('url', '')}")
    except (TrafficError, OSError, ValueError) as e:
        sys.exit(f"traffic: {e}")
    print("\n".join(lines))
    return lines


if __name__ == "__main__":
    main()
