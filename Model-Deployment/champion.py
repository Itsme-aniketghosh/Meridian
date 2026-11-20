"""The champion deploy.yml ships (S3): which model version, its md5, and its model file, checked.

Two sources, one check:
- committed: Model-Pipeline/registry/champion.json and the model file next to it (a branch, or no GCP sign-in)
- pointer: the newest version of the registry's pointer package on Artifact Registry, the champion in force, and its
  model file from the model package (main, signed in as the deploy account)

Either way the model file's md5 must be the record's, and the record must be the one expected: the D3 dispatch's
version and md5 when given, else the committed registry/champion.json. Artifact Registry is read through phase 2's
own model.registry functions, never a second copy of them.

    python3 Model-Deployment/champion.py --source committed
    python3 Model-Deployment/champion.py --source pointer --dest "$RUNNER_TEMP/champion" \
        --github-output "$GITHUB_OUTPUT"
    python3 Model-Deployment/champion.py --source pointer --expect-version <sha> --expect-md5 <md5>   # D3
"""
import argparse
import hashlib
import shutil
import subprocess
import sys
from pathlib import Path

try:
    import tomllib
except ModuleNotFoundError:  # Python < 3.11
    import tomli as tomllib

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "Model-Pipeline"))
from model import registry as R  # noqa: E402

MODEL_TOML = REPO / "Model-Pipeline" / "model.toml"


class ChampionError(RuntimeError):
    """The champion can't be shipped: the message says why. deploy.yml fails on it, before anything is built."""


def md5(path):
    h = hashlib.md5()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def checked(record, model, where):
    """record, after its form and its model file's md5 are checked."""
    try:
        R.check_champion(record, where)
    except R.ContractError as e:
        raise ChampionError(str(e)) from None
    if model is None or not Path(model).exists():
        raise ChampionError(f"{where}: no {record['model_file']} to ship")
    got = md5(model)
    if got != record["model_md5"]:
        raise ChampionError(f"{where}: {Path(model).name} has md5 {got}, the record says {record['model_md5']}")
    return record


def committed(champion=R.CHAMPION):
    """(record, model path) from the committed registry."""
    champion = Path(champion)
    if not champion.exists():
        raise ChampionError(f"no {champion}: nothing has been registered")
    record = R.read_champion(champion)
    model = champion.parent / record["model_file"]
    return checked(record, model, str(champion)), model


def pointer(dest, runner=subprocess.run, config=MODEL_TOML):
    """(record, model path) from Artifact Registry: the pointer's newest champion.json, then its model file."""
    with open(config, "rb") as f:
        s = R.settings(tomllib.load(f))
    why = R.gcp_missing(s, runner)
    if why:
        raise ChampionError(f"can't read the registry pointer: {why}")
    dest = Path(dest)
    version, record = R.newest_pointer(s, runner, dest / "pointer")
    if record is None:
        raise ChampionError(f"{s['pointer']} has no version on {s['repository']}: no champion was ever pushed")
    model = R.download(s, s["package"], record["version"], record["model_file"], dest / "model", runner)
    return checked(record, model, f"{s['pointer']} {version}"), model


def expected(record, version=None, model_md5=None, champion=R.CHAMPION):
    """The record must be the champion asked for: the dispatch's (D3) when given, else the committed one."""
    if version or model_md5:
        want, why = {"version": version, "model_md5": model_md5}, "the dispatch (D3)"
    else:
        want, why = R.read_champion(champion), Path(champion).relative_to(REPO).as_posix()
    for key in ("version", "model_md5"):
        if want.get(key) and want[key] != record[key]:
            raise ChampionError(f"the champion's {key} is {record[key]}, {why} says {want[key]}")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--source", choices=("committed", "pointer"), required=True)
    ap.add_argument("--dest", type=Path, default=Path("champion-pulled"), help="where a pointer's files land")
    ap.add_argument("--expect-version", help="D3: the version the dispatch named")
    ap.add_argument("--expect-md5", help="D3: the md5 the dispatch named")
    ap.add_argument("--stage", type=Path, help="copy champion.json and the model file here, for the image build")
    ap.add_argument("--github-output", type=Path, help="append version, md5, model and source to this file")
    a = ap.parse_args(argv)
    try:
        record, model = committed() if a.source == "committed" else pointer(a.dest)
        expected(record, a.expect_version, a.expect_md5)
    except (ChampionError, R.ContractError) as e:
        sys.exit(f"champion: {e}")
    if a.stage:
        a.stage.mkdir(parents=True, exist_ok=True)
        if Path(model).resolve().parent != a.stage.resolve():
            shutil.copyfile(model, a.stage / record["model_file"])
            (a.stage / "champion.json").write_bytes(R.champion_bytes(record))
    out = {"version": record["version"], "md5": record["model_md5"], "model": str(model), "source": a.source,
           "pr_auc": f"{record['metrics']['pr_auc']['value']:.4f}"}
    print(f"champion {out['version'][:12]} (md5 {out['md5']}, val PR-AUC {out['pr_auc']}) from {a.source}: checked")
    if a.github_output:
        with open(a.github_output, "a", encoding="utf-8") as f:
            f.writelines(f"{k}={v}\n" for k, v in out.items())
    return out


if __name__ == "__main__":
    main()
