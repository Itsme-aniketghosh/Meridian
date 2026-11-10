"""The LLM cache (Contract 5): one JSON file per answer in llm_cache/, committed: a rerun makes 0 calls.

    from meridian.llm_cache import Cache, key
    cache = Cache()                                   # Data-Pipeline/llm_cache/
    k = key("claude-haiku-5-5", "3f2a...", facts)     # sha256 of (model, prompt version, facts JSON, sorted keys)
    cache.get(k) or cache.put(k, {...})

The key covers everything the answer depends on, so the cache can never serve text written for other facts, another
prompt or another model. An entry keeps the model's raw reply, not the text that shipped: the writer gates it again
on every run, so a stricter gate also re-checks every cached answer (vishwa-agents decisions, the writer).

Entries are written sorted, with '\\n' endings, so the same answer is the same bytes on every OS.
"""
import hashlib
import json
from pathlib import Path

from meridian.common import atomic_write

ROOT = Path(__file__).resolve().parents[1] / "llm_cache"


def canonical(obj):
    """The one JSON spelling of obj that hashes are taken over: sorted keys, no spaces, UTF-8 as is."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def key(model, prompt_version, facts):
    """sha256 of (model, prompt version, facts with sorted keys), as hex: the cache entry's file name."""
    return hashlib.sha256(canonical({"model": model, "prompt_version": prompt_version, "facts": facts})
                          .encode("utf-8")).hexdigest()


class Cache:
    """llm_cache/<key>.json. get() is None on a miss; put() never overwrites an entry with different content."""

    def __init__(self, root=ROOT):
        self.root = Path(root)

    def path(self, k):
        return self.root / f"{k}.json"

    def get(self, k):
        p = self.path(k)
        return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None

    def put(self, k, entry):
        old = self.get(k)
        if old is not None:
            if old != entry:
                raise ValueError(f"llm_cache/{k}.json already holds a different answer: the key must cover it")
            return
        self.root.mkdir(parents=True, exist_ok=True)
        body = json.dumps(entry, sort_keys=True, indent=1, ensure_ascii=False) + "\n"

        def write(tmp):
            with open(tmp, "w", encoding="utf-8", newline="\n") as f:
                f.write(body)
        atomic_write(self.path(k), write)

    def entries(self):
        """Every entry, by key: for the scan that no fact, prompt or reply names a person."""
        return {p.stem: json.loads(p.read_text(encoding="utf-8")) for p in sorted(self.root.glob("*.json"))}
