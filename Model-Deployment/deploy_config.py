"""Read Model-Deployment/deploy.toml (contract D4), the one place phase 3's names and settings live.

In Python:
    from deploy_config import load
    cfg = load()                         # "{project}" filled in; in Cloud Run, from $MERIDIAN_DEPLOY_PROJECT
    cfg = load(project="other-project")  # a fresh project (bootstrap.sh)
    cfg["derived"]["images"]["risk"]     # names built from the rest: account emails, image URLs, the sign-in provider

From a shell or a workflow:
    python3 Model-Deployment/deploy_config.py --get services.risk.name
    python3 Model-Deployment/deploy_config.py --get derived.workload_identity_provider
    python3 Model-Deployment/deploy_config.py --get buckets.monitor --project other-project

Terraform reads it the same way (infra/terraform/main.tf, an external data source): --terraform takes the
query on stdin and prints {"json": "<the whole config>"}. Before that it checks that the run's state is the
project's own bucket, so a run for one project can never plan with another's state. Needs Python 3.11+
(tomllib), or tomli installed.
"""
import argparse
import json
import os
import sys
from pathlib import Path

try:
    import tomllib
except ModuleNotFoundError:  # Python < 3.11
    try:
        import tomli as tomllib
    except ModuleNotFoundError:
        sys.exit("deploy_config.py needs Python 3.11 or later, or `pip install tomli`")

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


def _derived(cfg):
    """Names built from the rest, so a workflow never types them. The provider needs the project's number,
    which deploy.toml knows only for its own project."""
    gcp, reg = cfg["gcp"], cfg["registry"]
    project, region = gcp["project"], gcp["region"]
    images_repo = f"{region}-docker.pkg.dev/{project}/{reg['images']}"
    wif = cfg["workload_identity"]
    number = gcp.get("project_number")
    return {
        "service_accounts": {k: f"{v}@{project}.iam.gserviceaccount.com" for k, v in cfg["service_accounts"].items()},
        "images_repo": images_repo,
        "images": {k: f"{images_repo}/{v}" for k, v in cfg["images"].items()},
        "models_repo": f"{region}-generic.pkg.dev/{project}/{reg['models']}",
        "workload_identity_provider": (f"projects/{number}/locations/global/workloadIdentityPools/{wif['pool']}"
                                       f"/providers/{wif['provider']}") if number else None,
    }


def load(path=DEPLOY_TOML, project=None, project_number=None, use_env=True):
    """deploy.toml as a dict, for `project`: the argument, else $MERIDIAN_DEPLOY_PROJECT (Terraform sets it in Cloud
    Run; not $MERIDIAN_GCP_PROJECT, which turns on live LLM calls), else its own [gcp] project.
    [gcp] team_project keeps deploy.toml's own project; project_number is known only for that one."""
    with open(path, "rb") as f:
        cfg = tomllib.load(f)
    gcp = cfg["gcp"]
    team = gcp["project"]
    project = project or (os.environ.get("MERIDIAN_DEPLOY_PROJECT") if use_env else None) or team
    gcp.update(project=project, team_project=team,
               project_number=project_number or (gcp.get("project_number") if project == team else None))
    cfg = _fill(cfg, project)
    cfg["derived"] = _derived(cfg)
    return cfg


def get(cfg, key):
    """One value by its dotted key, e.g. get(cfg, "services.risk.name")."""
    value = cfg
    for part in key.split("."):
        if not isinstance(value, dict) or part not in value:
            raise KeyError(f"deploy.toml has no {key!r}")
        value = value[part]
    if value is None:
        raise KeyError(f"{key!r} isn't known for this project (pass --project-number)")
    return value


def check_state_bucket(cfg, data_dir=None):
    """A run's state must be its project's own bucket ([buckets] tfstate). A fresh project's run left on the team's
    state would plan to replace the team's resources, and `apply -target` skips infra/terraform/guard.tf. Terraform
    starts this script on every plan, targeted or not, so a mismatch stops it before anything is planned."""
    backend_file = Path(data_dir or os.environ.get("TF_DATA_DIR") or ".terraform") / "terraform.tfstate"
    if not backend_file.is_file():
        return  # not initialised here, e.g. `terraform validate` after `init -backend=false`
    backend = json.loads(backend_file.read_text()).get("backend") or {}
    if backend.get("type") != "gcs":
        return  # a local dry run (an *_override.tf backend) has its own state
    bucket, want = (backend.get("config") or {}).get("bucket"), cfg["buckets"]["tfstate"]
    if bucket != want:
        raise ValueError(f"this checkout's Terraform state is gs://{bucket}, but the run is for "
                         f"{cfg['gcp']['project']}, whose state is gs://{want}. Run `terraform init "
                         f"-reconfigure -backend-config=bucket={want}` first (infra/terraform/README.md)")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--config", type=Path, default=DEPLOY_TOML)
    ap.add_argument("--project", help="another project id (default: $MERIDIAN_DEPLOY_PROJECT, else deploy.toml's)")
    ap.add_argument("--project-number", help="that project's number, for derived.workload_identity_provider")
    ap.add_argument("--get", metavar="KEY", help="print one value, e.g. services.risk.name")
    ap.add_argument("--terraform", action="store_true",
                    help="Terraform's external data source: the query (JSON) on stdin, the config as one string")
    a = ap.parse_args(argv)
    project = a.project
    if a.terraform:
        query = json.loads(sys.stdin.read() or "{}")
        project = query.get("project") or project
    try:
        cfg = load(a.config, project, a.project_number, use_env=not a.terraform)  # Terraform: only its own query
        if a.terraform:
            check_state_bucket(cfg)
            print(json.dumps({"json": json.dumps(cfg)}))
        elif a.get:
            value = get(cfg, a.get)
            print(value if isinstance(value, str) else json.dumps(value))
        else:
            print(json.dumps(cfg, indent=2))
    except (OSError, KeyError, ValueError) as e:  # ValueError: a TOML or JSON error, or the state check
        sys.exit(f"deploy_config.py: {e}")


if __name__ == "__main__":
    main()
