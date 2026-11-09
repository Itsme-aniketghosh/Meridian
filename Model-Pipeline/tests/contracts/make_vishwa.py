"""Vishwa's contract examples: tests/contracts/facts.json and suggestions.json (packets/README.md, Integration tests).

    python -m tests.contracts.make_vishwa        # from Model-Pipeline/: rewrites both files

Both come from one writer run (Data-Pipeline/meridian/agents.py) on KNOWLEDGE-BASE.md's fixture, acme/shop at R2,
with the LLM off, so they have the real shape:
- facts.json: one Contract 5 facts object per task kind, {"facts": {...}, "source": {...}}: what an agent is given
- suggestions.json: the suggestion rows that run offers (Contract 5, schema.sql), every column

With the LLM off every row is a template: source 'template', model null. A row an LLM wrote has source 'llm' and the
model id; consumers must handle both, and a null policy_id (a stall_reason in a block without policy rows).

Data-Pipeline/tests/test_agents.py builds both again and checks the committed files have the same keys, so a change
to either shape fails a test until this is run again and the change is announced (S10).
"""
import importlib.util
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent  # tests/contracts/
DATA = HERE.parents[2] / "Data-Pipeline"


def build():
    """(facts by kind, the run's new suggestion rows), from the fixture with the LLM off."""
    sys.path.insert(0, str(DATA))
    # Data-Pipeline/tests/test_kb.py by its path: both pipelines have a package named tests
    spec = importlib.util.spec_from_file_location("data_test_kb", DATA / "tests" / "test_kb.py")
    K = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(K)
    from meridian import agents
    from meridian.common import CONFIG, load_config
    written, rows = agents.run(K.build(), load_config(CONFIG), "R2", K.R2[1])
    facts = {}
    for w in written:
        facts.setdefault(w.task.kind, w.task.facts)
    return facts, [r for r in rows if r["run_id"] == "R2"]


def dump(obj):
    return json.dumps(obj, indent=1, sort_keys=True, ensure_ascii=False) + "\n"


def main():
    facts, rows = build()
    (HERE / "facts.json").write_text(dump(facts), encoding="utf-8", newline="\n")
    (HERE / "suggestions.json").write_text(dump(rows), encoding="utf-8", newline="\n")
    print(f"wrote facts.json ({len(facts)} kinds) and suggestions.json ({len(rows)} rows)")


if __name__ == "__main__":
    main()
