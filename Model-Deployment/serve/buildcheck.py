"""The image's build-time check (S2): the champion baked in is the one deploy.yml checked.

    python -m serve.buildcheck --version <MODEL_VERSION> --md5 <MODEL_MD5>     # the Dockerfile runs it

deploy.yml checks the champion (Model-Deployment/champion.py) and passes its version and md5 as build args, with the
model staged into Model-Pipeline/registry/. This refuses to build an image whose champion.json isn't that version,
or whose model file's md5 isn't that md5, so an image can only hold the model deploy.yml meant. At start, the
endpoint checks the md5 again (scorer.py), so a file changed after the build can't serve either.
"""
import argparse
import hashlib
import json
import sys
from pathlib import Path

REGISTRY = Path(__file__).resolve().parents[2] / "Model-Pipeline" / "registry"


def check(version, model_md5, registry=REGISTRY):
    record = json.loads((Path(registry) / "champion.json").read_text(encoding="utf-8"))
    problems = []
    if version and record.get("version") != version:
        problems.append(f"champion.json is version {record.get('version')}, the build was given {version}")
    if model_md5 and record.get("model_md5") != model_md5:
        problems.append(f"champion.json records md5 {record.get('model_md5')}, the build was given {model_md5}")
    path = Path(registry) / record["model_file"]
    got = hashlib.md5(path.read_bytes()).hexdigest() if path.is_file() else None
    if got != record.get("model_md5"):
        problems.append(f"{record['model_file']} has md5 {got}, champion.json records {record.get('model_md5')}")
    return record, problems


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--version", default="")
    ap.add_argument("--md5", default="")
    a = ap.parse_args(argv)
    record, problems = check(a.version, a.md5)
    if problems:
        for p in problems:
            print(f"won't build: {p}", file=sys.stderr)
        return 1
    print(f"champion {record['version'][:12]} md5 {record['model_md5']}: the one deploy.yml checked")
    return 0


if __name__ == "__main__":
    sys.exit(main())
