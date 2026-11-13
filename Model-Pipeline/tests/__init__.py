"""Model-Pipeline's tests. None of them may write to the real MLflow store at the repo root: the whole package gets
a temporary one (artifacts go next to it), removed when the tests end. A test that needs its own sets its own."""
import atexit
import os
import shutil
import tempfile
from pathlib import Path

_store = tempfile.mkdtemp(prefix="meridian-mlflow-tests-")
os.environ["MLFLOW_TRACKING_URI"] = f"sqlite:///{Path(_store) / 'mlflow.db'}"
atexit.register(shutil.rmtree, _store, True)
_template = None


def mlflow_uri(folder):
    """A tracking URI for a new, empty MLflow store at folder/mlflow.db. MLflow builds its tables for each new SQLite
    store (about 4.5 s, a third of the suite's time), so they're built once per run and copied (0.2 s)."""
    global _template
    if _template is None:
        from mlflow.tracking import MlflowClient
        _template = Path(_store) / "template.db"
        MlflowClient(f"sqlite:///{_template}").search_experiments()
    path = Path(folder) / "mlflow.db"
    path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(_template, path)
    return f"sqlite:///{path}"
