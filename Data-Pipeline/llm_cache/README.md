# llm_cache

One JSON file per LLM answer, written by `meridian/llm_cache.py` and committed (Contract 5), so CI and reruns make 0 LLM calls and need no API key.

- File name: sha256 of (model, prompt version, facts JSON with sorted keys). A prompt's version is a hash of its text, so an edited prompt never reads an old answer.
- Each entry keeps the model's raw reply, not the text that shipped: the writer runs the fact gate on it again every run.
- No entry may name a person: `tests/test_agents.py` scans every entry.

Empty until the writer has an LLM: with the LLM off, every text is its template.
