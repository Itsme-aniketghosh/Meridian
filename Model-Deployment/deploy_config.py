"""Read Model-Deployment/deploy.toml (contract D4), the one place phase 3's names and settings live.

In Python:
    from deploy_config import load
    cfg = load()                        # "{project}" filled in from [gcp] project
    cfg = load(project="other-project") # a fresh project (bootstrap.sh)

From a shell or a workflow:
    python3 Model-Deployment/deploy_config.py --get services.risk.name
    python3 Model-Deployment/deploy_config.py --get buckets.monitor --project other-project

Terraform reads it the same way (infra/terraform/main.tf, an external data source): --terraform takes the
query on stdin and prints {"json": "<the whole config>"}. Needs Python 3.11+ (tomllib).
"""
import argparse
import json
import sys
from pathlib import Path

try:
    import tomllib
except ModuleNotFoundError:  # Python < 3.11
    sys.exit("deploy_config.py needs Python 3.11 or later (tomllib)")

HERE = Path(__file__).resolve().parent
DEPLOY_TOML = HERE / "deploy.toml"


def _fill(value, project):
    """Every "{project}" in a string, a list or a table, replaced by the project id."""
    if isinstance(value, str):
        return value.replace("{project}", project)
    if isinstance(value, list):
        return [_fill(v, project) for v in value]
    if isinstance(value, dict):
        return {k: _fill(v, project) for k, v in value.items()}
    return value


def load(path=DEPLOY_TOML, project=None):
    """deploy.toml as a dict, for `project` (default: its own [gcp] project)."""
    with open(path, "rb") as f:
        cfg = tomllib.load(f)
    project = project or cfg["gcp"]["project"]
    cfg["gcp"]["project"] = project
    return _fill(cfg, project)


def get(cfg, key):
    """One value by its dotted key, e.g. get(cfg, "services.risk.name")."""
    value = cfg
    for part in key.split("."):
        if not isinstance(value, dict) or part not in value:
            raise KeyError(f"deploy.toml has no {key!r}")
        value = value[part]
    return value


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--config", type=Path, default=DEPLOY_TOML)
    ap.add_argument("--project", help="another project id (default: deploy.toml's)")
    ap.add_argument("--get", metavar="KEY", help="print one value, e.g. services.risk.name")
    ap.add_argument("--terraform", action="store_true",
                    help="Terraform's external data source: the query (JSON) on stdin, the config as one string")
    a = ap.parse_args(argv)
    project = a.project
    if a.terraform:
        query = json.loads(sys.stdin.read() or "{}")
        project = query.get("project") or project
    try:
        cfg = load(a.config, project)
        if a.terraform:
            print(json.dumps({"json": json.dumps(cfg)}))
        elif a.get:
            value = get(cfg, a.get)
            print(value if isinstance(value, str) else json.dumps(value))
        else:
            print(json.dumps(cfg, indent=2))
    except (OSError, KeyError, tomllib.TOMLDecodeError) as e:
        sys.exit(f"deploy_config.py: {e}")


if __name__ == "__main__":
    main()
