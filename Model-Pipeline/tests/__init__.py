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
