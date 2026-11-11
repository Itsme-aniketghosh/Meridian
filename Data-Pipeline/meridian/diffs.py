"""Diff drafter · stage 6: a one-line swap for each present call site, as `suggestion` rows of kind 'diff'.
Step "diff drafter" of Model-Pipeline/packets/utkarsh-data. See docs/04-architecture.md §6 and Contract 5.

    python -m meridian.diffs --run-id <run_id> --config meridian.toml           # -> out/<run_id>/suggestion.jsonl
    python -m meridian.diffs --run-id <run_id> --out /tmp/d --url-style re_path        # elsewhere, one url() style

Reads out/<run_id>/call_site.jsonl and run.jsonl, and the code in snapshots/<run_id>/.

1. Codemod first. A rename rule per tracked symbol (RULES, or [diffs] rules in the config when it has them):
   ugettext -> gettext and the other four Django names (same module); django.conf.urls.url -> django.urls, as
   re_path, path, or an aliased import (`re_path as url`), by --url-style (decision 11):
     re_path         always re_path
     path_if_plain   path(...) when the pattern is plain text between ^ and $, else re_path
     alias           the import becomes `from django.urls import re_path as url`; url(...) calls stay as they are
     learned         per repo, from its owner's own migrations so far: every checkout line already on django.urls
                     votes (style_vote: `re_path as url`, a re_path that could have been path, a path). None, as
                     at every package's parent: path_if_plain, the best single style (decisions 11 and 13)
   All sites on one line get one draft for that line, so the draft is the whole after-line.
2. The LLM path may only draft a line the codemod refused, with 2 or more same-shape examples (the repo's own
   codemod drafts), and never inside `except` (llm_allowed). Models go coding small -> mid -> large (04 §6). Each
   answer: llm_cache or call -> JSON {"text", "used"} -> the fact gate on what the line adds (gated) -> the checks in
   3. Any failure moves one model up; the top model failing drafts nothing (code has no template). Every answer,
   cached or called, goes to log_call (model.tracking.log_llm_call). With no LLM and no cache, the codemod runs
   alone; with only the cache (--llm-cache, as in CI), a miss drafts nothing, or with --strict-cache stops the run.
3. Every draft must apply (the line holds the symbol at its column), touch only its own line, parse (ast, Python
   files only), and resolve: Jedi, through meridian.resolve.Repo, must land each new name on the new symbol. url()
   drafts resolve against a Django that has django.urls (git archive of [diffs] django_ref from the Django mirror,
   kept in snapshots/_cache/diffs/). Anything else: no diff, counted by reason.
4. One suggestion row per call site whose line changes: body is the unified diff of that one line.

score() compares drafts with what really happened: the after-line of each site's line at a later commit
(whitespace ignored), the 05-test bar (>= 90% on Django). A line with no draft counts as its own line unchanged.
"""
import ast
import hashlib
import io
import json
import re
import subprocess
import tarfile
import time
import tokenize
from collections import Counter, defaultdict
from pathlib import Path

from meridian.common import data_root, git, h, handoff, load_config, log, parser, read_jsonl, repo_checkout, \
    write_jsonl

# (module, old name) -> (new module, new name). url's new name depends on the style: see url_new()
RULES = {
    ("django.utils.translation", "ugettext"): ("django.utils.translation", "gettext"),
    ("django.utils.translation", "ugettext_lazy"): ("django.utils.translation", "gettext_lazy"),
    ("django.utils.translation", "ugettext_noop"): ("django.utils.translation", "gettext_noop"),
    ("django.utils.translation", "ungettext"): ("django.utils.translation", "ngettext"),
    ("django.utils.translation", "ungettext_lazy"): ("django.utils.translation", "ngettext_lazy"),
    ("django.conf.urls", "url"): ("django.urls", "re_path"),
}
STYLES = ("re_path", "path_if_plain", "alias", "learned")
DJANGO_URLS = {"include", "path", "re_path", "register_converter"}   # names `from django.urls import` can take over
DJANGO_REF = "3.2"                                                   # a Django with django.urls.path and re_path
FROM_IMPORT = re.compile(r"^(?P<indent>\s*)from\s+(?P<module>[\w.]+)\s+import\s+"
                         r"(?P<names>[^()#]+?)\s*(?P<comment>#.*)?$")
URL_CALL = re.compile(r"""\burl\(\s*(?P<kw>regex\s*=\s*)?(?P<prefix>[rRuU]{0,2})(?P<q>['"])"""
                      r"""(?P<pat>(?:\\.|(?!(?P=q)).)*)(?P=q)""")
REGEX_KW = re.compile(r"\burl\(\s*regex\s*=\s*")   # url(regex=...): path and re_path call it route, so it goes
REGEX_CHARS = re.compile(r"[.*+?()\[\]{}|\\]")
REASONS = ("stale", "other_line", "multi_line_import", "other_names", "not_python", "parse", "resolve", "no_change")


class Refused(Exception):
    pass


# ───────────── rewriting one line ─────────────

def name_spots(line, name):
    """Columns of `name` as a NAME token on this line (not in a string or comment). A fragment that doesn't tokenize
    to the end (an open bracket) keeps the tokens it got."""
    spots = []
    try:
        for t in tokenize.generate_tokens(io.StringIO(line).readline):
            if t.type == tokenize.NAME and t.string == name and t.start[0] == 1:
                spots.append(t.start[1])
    except (tokenize.TokenError, IndentationError, SyntaxError):
        pass
    return spots


def rename(line, old, new):
    for c in reversed(name_spots(line, old)):
        line = line[:c] + new + line[c + len(old):]
    return line


# Django's path converters for the named groups real migrations turn into them (docs: "Path converters")
CONVERTERS = [
    (re.compile(r"\(\?P<(\w+)>(?:\\d\+|\[0-9\]\+)\)"), r"<int:\1>"),
    (re.compile(r"\(\?P<(\w+)>(?:\[-\\w\]\+|\[\\w-\]\+|\[-a-zA-Z0-9_\]\+|\[a-zA-Z0-9_-\]\+)\)"), r"<slug:\1>"),
    (re.compile(r"\(\?P<(\w+)>\[\^/\]\+\)"), r"<\1>"),
    (re.compile(r"\(\?P<(\w+)>\.\+\)"), r"<path:\1>"),
    # any other one-or-more character class without '/', e.g. [a-z0-9]+: str ([^/]+) is what owners wrote for it
    # (oscar's <str:key>), wider than the class but never across a '/' (decision 13)
    (re.compile(r"\(\?P<(\w+)>\[(?![^\]]*/)[^\]]+\]\+\)"), r"<str:\1>"),
]


def path_text(pat):
    """The pattern as path() text ('about/' for '^about/$', '<int:pk>/' for '^(?P<pk>\\d+)/$'), or None when some
    regex is left that path() can't say."""
    body = pat[1:] if pat.startswith("^") else pat
    if body.endswith("$") and not body.endswith("\\$"):
        body = body[:-1]
    elif not pat.startswith("^"):
        return None                                   # unanchored at both ends: a search, not a path
    for rx, to in CONVERTERS:
        body = rx.sub(to, body)
    left = re.sub(r"\\[./~-]", "", re.sub(r"<[\w:]+>", "", body))   # what's left once converters and escaped
    if REGEX_CHARS.search(left):                                      # literals are out: any regex here can't
        return None                                                   # be said by path()
    return re.sub(r"\\([./~-])", r"\1", body)         # an escaped literal is the literal


def url_choice(line, style):
    """'path' or 're_path' for the url(...) call on this line (path_if_plain only), else the style's own name.
    path needs the pattern on this line: when it's on the next line (url(\\n r'^x/$'), path would have to change
    that line too, so the draft is re_path."""
    if style == "path_if_plain":
        m = URL_CALL.search(line)
        if m and path_text(m.group("pat")) is not None:
            return "path"
    return "re_path"


def rewrite_url_call(line, style):
    if style == "alias":
        return line                                   # the import carries the swap; the call stays url(...)
    if url_choice(line, style) == "path":
        m = URL_CALL.search(line)
        q = m.group("q")
        return line[:m.start()] + f"path({q}{path_text(m.group('pat'))}{q}" + line[m.end():]
    # url(regex=...) was valid; re_path(regex=...) is a TypeError (its first argument is route): pass it positionally
    return rename(REGEX_KW.sub("url(", line), "url", "re_path")


def rewrite_url_import(line, style, file_choices):
    m = FROM_IMPORT.match(line.rstrip("\n"))
    if not m or m.group("module") != "django.conf.urls":
        raise Refused("multi_line_import")             # `url,` inside a bracketed import: the module line is elsewhere
    names = [n.strip() for n in m.group("names").split(",") if n.strip()]
    out = []
    for n in names:
        base = n.split()[0]
        if base == "url":
            if style == "alias":
                out.append("re_path as url" if n == "url" else n.replace("url", "re_path", 1))
            else:
                out += sorted(file_choices or {"re_path"})
        elif base in DJANGO_URLS:
            out.append(n)
        else:
            raise Refused("other_names")               # e.g. handler404: it stays in django.conf.urls
    out = list(dict.fromkeys(out))
    comment = f"  {m.group('comment')}" if m.group("comment") else ""
    end = "\n" if line.endswith("\n") else ""
    return f"{m.group('indent')}from django.urls import {', '.join(out)}{comment}{end}"


def import_names(line):
    m = FROM_IMPORT.match(line.rstrip("\n"))
    return [n.strip() for n in m.group("names").split(",") if n.strip()] if m else None


def is_sorted(names):
    keys = [n.split()[0].lower() for n in names]
    return len(names) > 1 and keys == sorted(keys)


def tidy_import(line, was_sorted):
    """After a rename, on a plain one-line import: drop a name now listed twice (`gettext, gettext`), and keep the
    names sorted if they were sorted before (the owner's isort habit, as in Django)."""
    m = FROM_IMPORT.match(line.rstrip("\n"))
    if not m:
        return line
    names = list(dict.fromkeys(n.strip() for n in m.group("names").split(",") if n.strip()))
    if was_sorted:
        names.sort(key=lambda n: n.split()[0].lower())
    if names == [n.strip() for n in m.group("names").split(",") if n.strip()]:
        return line
    comment = f"  {m.group('comment')}" if m.group("comment") else ""
    return (f"{m.group('indent')}from {m.group('module')} import {', '.join(names)}{comment}"
            + ("\n" if line.endswith("\n") else ""))


def draft_line(line, sites, style, file_choices):
    """The after-line for one line holding these sites, or Refused."""
    new = line
    was_sorted = is_sorted(import_names(line) or [])
    for s in sorted(sites, key=lambda s: -s["col"]):
        if new[s["col"]:s["col"] + len(s["symbol"])] != s["symbol"] and s["symbol"] not in new:
            raise Refused("stale")
    url_sites = [s for s in sites if s["symbol"] == "url"]
    if url_sites:
        if any(s["role"] == "import" for s in url_sites):
            new = rewrite_url_import(new, style, file_choices)
        else:
            new = rewrite_url_call(new, style)
    for s in sites:
        if s["symbol"] != "url":
            new = rename(new, s["symbol"], RULES[(s["module"], s["symbol"])][1])
    new = tidy_import(new, was_sorted)
    if new == line:
        raise Refused("no_change")
    return new


# ───────────── checks ─────────────

def only_line(old_lines, new_lines, i):
    return len(old_lines) == len(new_lines) and all(a == b for k, (a, b) in enumerate(zip(old_lines, new_lines))
                                                    if k != i)


def parses(src):
    try:
        ast.parse(src)
        return True
    except (SyntaxError, ValueError):
        return False


def new_names(line, after, sites, style):
    """(column, new name, new module) that must resolve on the after-line."""
    want = []
    for s in sites:
        mod, name = RULES[(s["module"], s["symbol"])]
        if s["symbol"] == "url":
            names = ["re_path"] if style == "alias" else ["path", "re_path"]
        else:
            names = [name]
        for n in names:
            want += [(c, n, mod) for c in name_spots(after, n)]
    return sorted(set(want))


def resolves(repo, path, src, line_no, spots):
    """Every new name on the line lands on its new symbol (Jedi through meridian.resolve.Repo)."""
    import jedi
    script = jedi.Script(code=src, path=str(repo.dir / path), project=repo.project, environment=repo.env)
    for col, name, module in spots:
        try:
            got = script.goto(line_no, col, follow_imports=True)
        except Exception:
            return False
        if not any(g.full_name and g.full_name.startswith(module.split(".")[0] + ".")
                   and g.full_name.split(".")[-1] == name and in_real_code(g.module_path, repo) for g in got):
            return False
    return bool(spots)


def in_real_code(module_path, repo):
    """Jedi ships Django stubs (jedi/third_party/django-stubs): a name found only there isn't in the code it runs
    against. It must land in the repo or one of its dependencies (the Django tree)."""
    p = Path(module_path or "")
    return p.suffix == ".py" and any(p.is_relative_to(Path(d).resolve()) or p.is_relative_to(Path(d))
                                     for d in [repo.dir, *repo.deps])


def llm_allowed(site, same_shape_examples):
    """04 §6: the LLM may draft only with 2 or more same-shape examples, and never inside `except`."""
    return not site.get("in_except") and same_shape_examples >= 2


# ───────────── the LLM path ─────────────

AGENT = "diff_drafter"
ROUTE = "rules"                     # who picked the model: the fixed ladder, until Jev
LADDER = ("claude-haiku-5-5", "claude-sonnet-5-5", "claude-opus-5-5")   # 04 §6: coding small -> mid -> large
LLM_REASONS = {"multi_line_import", "other_names", "parse", "resolve", "other_line"}   # what the LLM may try again
EXAMPLES = 3                        # same-shape examples shown in the prompt
SYSTEM = """You rewrite one line of Python for a migration off a deprecated Django API.
Change only what the migration needs on this line: each symbol in `symbols` becomes its name in `new`, the way the
examples (this repository's own lines, already migrated) did it. Keep everything else on the line as it is:
indentation, names, arguments, quotes. Add no comment, and no number, path or name that isn't on the line or in the
examples. Reply with JSON only, no other text:
{"text": "<the whole new line, without its line ending>", "used": ["<fact name>", ...]}"""
TASK = "Rewrite the line in `before`."
FACTS_FORMAT = "json, sorted keys, indent 1"
FRAME = "{system}\n\nTask: {task}\n\nFacts:\n{facts}"


class CacheMiss(RuntimeError):
    pass


def prompt_version():
    """12 hex of sha256 over the system prompt, the task, the frame and the facts format: any edit is a new version,
    so the cache never serves an answer written for another prompt."""
    return hashlib.sha256("\x1f".join((SYSTEM, TASK, FRAME, FACTS_FORMAT)).encode("utf-8")).hexdigest()[:12]


def prompt(facts):
    return FRAME.format(system=SYSTEM, task=TASK,
                        facts=json.dumps(facts["facts"], sort_keys=True, indent=1, ensure_ascii=False))


def facts_hash(facts):
    """The suggestion row's facts_hash: sha256 over the facts (sorted keys), the same for codemod and LLM rows."""
    return hashlib.sha256(json.dumps(facts, sort_keys=True).encode()).hexdigest()


def shape(sites):
    """Lines whose sites have the same (symbol, role) set are the same shape: two url imports are, a url import and
    a url() call aren't."""
    return tuple(sorted({(s["symbol"], s["role"]) for s in sites}))


def llm_facts(repo, path, n, before, sites, examples):
    """Contract 5's facts object for one line. Examples are codemod drafts from the same repo that passed every
    check: the owner's own lines, migrated. No person is a fact."""
    new = sorted({RULES[(s["module"], s["symbol"])][1] if s["symbol"] != "url" else "path, re_path" for s in sites})
    ids = ",".join(sorted(s["call_site_id"] for s in sites))
    facts = {"repo": repo, "path": path, "line": n, "before": before.rstrip("\n"),
             "symbols": sorted({s["symbol"] for s in sites}), "new": new,
             "examples": [{"before": b.rstrip("\n"), "after": a.rstrip("\n")} for b, a in examples]}
    source = {"repo": f"call_site:{ids}", "path": f"call_site:{ids}", "line": f"call_site:{ids}",
              "before": f"snapshot:{repo}:{path}:{n}", "symbols": f"call_site:{ids}", "new": "diffs:RULES",
              "examples": "diffs:codemod"}
    return {"facts": facts, "source": source}


def code_tokens(line):
    toks = []
    try:
        for t in tokenize.generate_tokens(io.StringIO(line.strip() + "\n").readline):
            if t.type in (tokenize.NAME, tokenize.NUMBER, tokenize.STRING, tokenize.COMMENT):
                toks.append(t.string)
    except (tokenize.TokenError, IndentationError, SyntaxError):
        toks.append(line.strip())
    return toks


def gated(before, after, facts, teams, people):
    """The fact gate on what the draft adds: its tokens that aren't on the line already. Read as code, a regex like
    \\d{4} or a pattern like edit\\.html is a number or a path the gate can't find in the facts, so the whole line
    can't be gated as text. Rules for code (diffs.md, decision 12): no new comment; every number added is a fact
    (kinds='numbers'); a person, an email or a handle always fails."""
    from meridian.fact_gate import check
    added = list((Counter(code_tokens(after)) - Counter(code_tokens(before))).elements())
    if any(t.startswith("#") for t in added):
        return False
    return check("\n".join(added), facts, teams=teams, people=people, kinds="numbers").passed


def llm_draft(old_lines, drafts, path, n, sites, facts, resolver, *, teams, people, llm, cache, log, strict_cache,
              ladder=LADDER):
    """One line by the LLM, up the ladder: cache or call -> JSON -> fact gate -> one line, parses, resolves.
    (model, after) or None. With no LLM, a cached answer still counts; a miss drafts nothing (code has no
    template), or with strict_cache stops the run."""
    from meridian.agents import parse
    from meridian.llm_cache import key
    pv, fh, before = prompt_version(), facts_hash(facts["facts"]), old_lines[n - 1]
    end = "\n" if before.endswith("\n") else ""
    for i, model in enumerate(ladder):
        k = key(model, pv, facts["facts"])
        entry = cache.get(k) if cache else None
        hit, t0 = entry is not None, time.perf_counter()
        if not hit:
            if llm is None:
                if strict_cache:
                    raise CacheMiss(f"no llm_cache entry for diff {path}:{n} on {model} ({k})")
                return None
            r = llm(model, prompt(facts))
            entry = {"model": model, "prompt_version": pv, "facts_hash": fh, "reply": r["reply"],
                     "tokens_in": int(r.get("tokens_in", 0)), "tokens_out": int(r.get("tokens_out", 0)),
                     "cost_usd": float(r.get("cost_usd", 0.0))}
            if cache:
                cache.put(k, entry)
        text = parse(entry.get("reply"), facts)      # the writer's reader: it strips, so the indent comes back here
        indent = before[:len(before) - len(before.lstrip())]
        after = None if text is None or "\n" in text else indent + text + end
        gate = None if after is None else gated(before, after, facts, teams, people)
        ok = bool(gate) and after != before
        if ok:
            new_lines = [drafts[(path, j + 1)][1] if (path, j + 1) in drafts else ln for j, ln in enumerate(old_lines)]
            new_lines[n - 1] = after
            new_src = "".join(new_lines)
            spots = new_names(before, after, sites, "path_if_plain")
            ok = parses(new_src) and resolves(resolver, path, new_src, n, spots)
        last = ok or i == len(ladder) - 1 or (
            llm is None and cache.get(key(ladder[i + 1], pv, facts["facts"])) is None)
        if log is not None:
            paid = not hit
            log(agent=AGENT, model=model, prompt_version=pv, tokens_in=entry["tokens_in"] if paid else 0,
                tokens_out=entry["tokens_out"] if paid else 0, cost_usd=entry["cost_usd"] if paid else 0.0,
                cache_hit=hit, gate_pass=gate, route=ROUTE, source="llm" if ok else None, task="diff",
                repo=facts["facts"]["repo"], team=sites[0]["owner_team"],
                latency_ms=round((time.perf_counter() - t0) * 1000, 3), facts_hash=fh)
        if ok:
            return model, after
        if last:
            return None
    return None


def known_names(out):
    """(teams, people) for the gate, from the hand-off files: agents.names' rule, read from jsonl. Teams: team_map
    and every owner_team. People: every person who isn't a bot with an alias that isn't generic."""
    teams = {r["team"] for r in read_jsonl(out / "team_map.jsonl")} if (out / "team_map.jsonl").exists() else set()
    teams |= {r["owner_team"] for r in read_jsonl(out / "call_site.jsonl")}
    people = set()
    if (out / "person.jsonl").exists() and (out / "person_alias.jsonl").exists():
        named = {r["person_id"] for r in read_jsonl(out / "person_alias.jsonl") if not r["generic"]}
        people = {r["name"] for r in read_jsonl(out / "person.jsonl") if not r["is_bot"] and r["person_id"] in named}
    return sorted(t for t in teams if t), sorted(p for p in people if p)


# ───────────── the run ─────────────

ALIAS_IMPORT = re.compile(r"\bre_path\s+as\s+url\b")
NEW_CALL = re.compile(r"""(?<![\w.])(?<!def )(?P<fn>re_path|path)\(\s*[rRuU]{0,2}(?P<q>['"])"""
                      r"""(?P<pat>(?:\\.|(?!(?P=q)).)*)(?P=q)""")


def style_vote(line):
    """What one already-migrated line says about its owner's url() style, or None. `re_path as url`: alias. A
    re_path(...) whose pattern path() could have said: re_path, by choice. A path(...): path_if_plain. A re_path
    path() couldn't say is no vote: the owner had no choice there."""
    if ALIAS_IMPORT.search(line):
        return "alias"
    m = NEW_CALL.search(line)
    if not m:
        return None
    if m.group("fn") == "path":
        return "path_if_plain"
    return "re_path" if path_text(m.group("pat")) is not None else None


def pick_style(votes):
    """The style most votes say, ties to path_if_plain (decision 11's default); None with no votes."""
    if not votes:
        return None
    return max(sorted(votes), key=lambda s: (votes[s], s == "path_if_plain"))


def learned_style(repo_dir):
    """The owner's own migrations so far (decision 13): every line of the checkout already on django.urls votes
    (style_vote), outside the repo's own django/ package (Django defines path and re_path there). No votes, as at
    every package's parent commit: path_if_plain, the best single style (decision 11). Once an owner migrates a file
    by hand, the next run drafts the rest in their style."""
    votes = Counter()
    for p in sorted(Path(repo_dir).rglob("*.py")):
        rel = p.relative_to(repo_dir).parts
        if rel and rel[0] == "django":
            continue
        try:
            text = p.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for line in text.splitlines():
            v = style_vote(line)
            if v:
                votes[v] += 1
    return pick_style(votes) or "path_if_plain"


def style_from_owner(pairs):
    """The url() style an owner's own migrated lines show, from [(before, after)]: None when they show none. Each
    after-line votes as learned_style's do (style_vote); a url(...) call the owner kept as it was votes alias.
    Used by seeded_score, where the before-lines are known."""
    votes = Counter()
    for b, a in pairs:
        if a is None:
            continue
        v = style_vote(a)
        if v is None and URL_CALL.search(a) and URL_CALL.search(b or "") and path_text(
                URL_CALL.search(b).group("pat")) is not None:
            v = "alias"
        if v:
            votes[v] += 1
    return pick_style(votes)


def django_for_resolve(mirror, ref, cache):
    """A plain tree of django/ at ref, for Jedi. git archive reads the mirror and never writes to it."""
    dest = Path(cache) / f"django@{ref}"
    if not (dest / "django" / "__init__.py").exists():
        dest.mkdir(parents=True, exist_ok=True)
        data = subprocess.run(["git", "-C", str(mirror), "archive", ref, "django"], capture_output=True,
                              check=True).stdout
        with tarfile.open(fileobj=io.BytesIO(data)) as t:
            t.extractall(dest, filter="data")
    return dest


def present_sites(out, run_id):
    """The newest version of every call site, present ones only."""
    last = {}
    for r in read_jsonl(out / "call_site.jsonl"):
        if r["call_site_id"] not in last or r["known_at"] > last[r["call_site_id"]]["known_at"]:
            last[r["call_site_id"]] = r
    return sorted((r for r in last.values() if r["status"] == "present"),
                  key=lambda r: (r["repo"], r["path"], r["line"], r["col"]))


def symbols_of(cfg):
    return {s["name"]: s["module"] for d in cfg["datasets"].values() for s in d.get("symbols", [])}


def draft_repo(repo, repo_dir, sites, style, resolver, llm_opts=None):
    """{(path, line): (before, after)}, Counter of refusals (per site), the style used, and {(path, line): model}
    for the lines the LLM drafted. llm_opts (llm, cache, log, strict_cache, teams, people) turns the LLM path on:
    it may try a line the codemod refused (LLM_REASONS) when llm_allowed holds for every site on it. The LLM lines
    come back as {(path, line): (model, facts_hash)}."""
    style = learned_style(repo_dir) if style == "learned" else style
    drafts, refused, llm_models = {}, Counter(), {}
    todo, sources = [], {}                  # (path, line, reason) the codemod refused; each file's lines
    by_file = defaultdict(lambda: defaultdict(list))
    for s in sites:
        by_file[s["path"]][s["line"]].append(s)
    for path, lines in sorted(by_file.items()):
        f = repo_dir / path
        if not f.exists():
            refused["stale"] += sum(map(len, lines.values()))
            continue
        old_src = f.read_text(encoding="utf-8", errors="replace")
        old_lines = old_src.splitlines(keepends=True)
        sources[path] = old_lines
        is_py = path.endswith(".py")
        file_choices = {url_choice(old_lines[n - 1], style) for n, ss in lines.items() if n <= len(old_lines)
                        for s in ss if s["symbol"] == "url" and s["role"] == "call"}
        ok_before = parses(old_src)
        pending = {}
        for n, ss in sorted(lines.items()):  # each line on its own: apply, own line only, parse
            try:
                if n > len(old_lines):
                    raise Refused("stale")
                if not is_py:
                    raise Refused("not_python")
                before = old_lines[n - 1]
                after = draft_line(before, ss, style, file_choices)
                new_lines = old_lines[:n - 1] + [after] + old_lines[n:]
                if not only_line(old_lines, new_lines, n - 1) or after.count("\n") > before.count("\n"):
                    raise Refused("other_line")
                if ok_before and not parses("".join(new_lines)):
                    raise Refused("parse")
                pending[n] = (before, after)
            except Refused as e:
                refused[str(e)] += len(ss)
                todo.append((path, n, str(e)))
        # resolve with the file's drafts applied together: a renamed call resolves only once its import is renamed
        # too, as in a real migration. Each draft still changes only its own line
        while pending:
            new_lines = [pending[k + 1][1] if k + 1 in pending else ln for k, ln in enumerate(old_lines)]
            new_src = "".join(new_lines)
            if ok_before and not parses(new_src):   # each parsed alone, not together: refuse them all, to be safe
                for n in pending:
                    refused["parse"] += len(lines[n])
                    todo.append((path, n, "parse"))
                break
            bad = [n for n, (b, a) in pending.items()
                   if not resolves(resolver, path, new_src, n, new_names(b, a, lines[n], style))]
            if not bad:
                drafts.update({(path, n): d for n, d in pending.items()})
                break
            for n in bad:
                refused["resolve"] += len(lines[n])
                todo.append((path, n, "resolve"))
                del pending[n]
    if llm_opts:
        llm_models = llm_lines(repo, by_file, sources, drafts, refused, todo, resolver, llm_opts)
    return drafts, refused, style, llm_models


def llm_lines(repo, by_file, sources, drafts, refused, todo, resolver, o):
    """The LLM's turn, after the codemod has drafted the whole repo (its drafts are the examples). Adds each LLM
    draft to drafts and moves its sites out of refused."""
    from meridian.fact_gate import check
    by_shape = defaultdict(list)
    for (p, n), d in sorted(drafts.items()):
        by_shape[shape(by_file[p][n])].append(d)
    models = {}
    for path, n, reason in sorted(todo):
        ss = by_file[path][n]
        examples = by_shape[shape(ss)]
        if reason not in LLM_REASONS or not all(llm_allowed(s, len(examples)) for s in ss):
            continue
        facts = llm_facts(repo, path, n, sources[path][n - 1], ss, examples[:EXAMPLES])
        try:
            check("", facts, teams=o["teams"], people=o["people"])
        except ValueError:          # the line names a person: never sent to an LLM
            refused["names_a_person"] += len(ss)
            refused[reason] -= len(ss)
            continue
        got = llm_draft(sources[path], drafts, path, n, ss, facts, resolver, teams=o["teams"],
                        people=o["people"], llm=o.get("llm"), cache=o.get("cache"), log=o.get("log"),
                        strict_cache=o.get("strict_cache", False), ladder=o.get("ladder") or LADDER)
        if got:
            models[(path, n)] = (got[0], facts_hash(facts["facts"]))
            drafts[(path, n)] = (sources[path][n - 1], got[1])
            refused[reason] -= len(ss)
    for r in [r for r, c in refused.items() if c <= 0]:
        del refused[r]
    return models


def line_diff(path, n, before, after):
    b, a = before.rstrip("\n"), after.rstrip("\n")
    return f"--- a/{path}\n+++ b/{path}\n@@ -{n} +{n} @@\n-{b}\n+{a}\n"


def suggestion_rows(run_id, snapshot_at, repo, sites, drafts, llm_models=None):
    """One row per site whose line has a draft. A codemod row's facts_hash is over the line's before and after; an
    LLM row's is the facts_hash its log_llm_call rows carry, so a call joins to what it produced."""
    rows, llm_models = [], llm_models or {}
    by_line = defaultdict(list)
    for s in sites:
        by_line[(s["path"], s["line"])].append(s)
    for s in sites:
        at = (s["path"], s["line"])
        d = drafts.get(at)
        if not d:
            continue
        model, fh = llm_models.get(at, (None, None))
        if not model:
            fh = facts_hash({"repo": repo, "path": s["path"], "line": s["line"], "symbol": s["symbol"],
                             "before": d[0].rstrip("\n"), "after": d[1].rstrip("\n")})
        rows.append({"run_id": run_id, "suggestion_id": h("diff", s["call_site_id"], fh), "kind": "diff",
                     "target_kind": "call_site", "target": s["call_site_id"], "owner_team": s["owner_team"],
                     "source": "llm" if model else "codemod", "body": line_diff(s["path"], s["line"], *d),
                     "facts_hash": fh, "offered_at": snapshot_at, "agent": AGENT, "model": model,
                     "route": ROUTE, "policy_id": None})
    return rows


def run(run_id, cfg, root=None, out_dir=None, style="learned", django_ref=DJANGO_REF, cache=None, repos=None,
        llm=None, llm_cache=None, log_call=None, strict_cache=False, ladder=None):
    """Drafts for every repo with present sites. Returns ({repo: drafts}, {repo: refusals}, {repo: style}, rows).
    The LLM path is on when llm (a callable: llm(model, prompt) -> {"reply", "tokens_in", "tokens_out",
    "cost_usd"}) or llm_cache (meridian.llm_cache.Cache) is given; log_call is model.tracking.log_llm_call.
    ladder: the models to try, smallest first (GAPS 2, the model comparison); LADDER, Jev's, when None."""
    root = Path(root or data_root())
    out = handoff(cfg, run_id, root)
    run_row = read_jsonl(out / "run.jsonl")[0]
    snap = root / "snapshots" / run_id
    syms = symbols_of(cfg)
    sites = [dict(s, module=syms[s["symbol"]]) for s in present_sites(out, run_id) if s["symbol"] in syms]
    by_repo = defaultdict(list)
    for s in sites:
        if repos is None or s["repo"] in repos:
            by_repo[s["repo"]].append(s)
    from meridian.resolve import Repo
    cache = Path(cache or root / "snapshots" / "_cache" / "diffs")
    django_tree = None
    llm_opts = None
    if llm is not None or llm_cache is not None:
        teams, people = known_names(out)
        llm_opts = {"llm": llm, "cache": llm_cache, "log": log_call, "strict_cache": strict_cache,
                    "teams": teams, "people": people, "ladder": tuple(ladder) if ladder else LADDER}
    all_drafts, all_refused, styles, rows = {}, {}, {}, []
    for repo, ss in sorted(by_repo.items()):
        repo_dir = repo_checkout(snap, repo)
        deps = []
        if any(s["symbol"] == "url" for s in ss) and not (repo_dir / "django" / "urls" / "__init__.py").exists():
            if django_tree is None:
                django_tree = django_for_resolve(root / "snapshots" / "_cache" / "repos" / "django__django.git",
                                                 django_ref, cache)
            deps = [django_tree]
        new_syms = {RULES[(s["module"], s["symbol"])][1]: RULES[(s["module"], s["symbol"])][0] for s in ss}
        resolver = Repo(repo_dir, new_syms, deps)
        drafts, refused, used, by_llm = draft_repo(repo, repo_dir, ss, style, resolver, llm_opts)
        all_drafts[repo], all_refused[repo], styles[repo] = drafts, refused, used
        rows += suggestion_rows(run_id, run_row["snapshot_at"], repo, ss, drafts, by_llm)
        log(f"{repo:45} {len(ss):>5} sites  {len({(s['path'], s['line']) for s in ss}):>5} lines  "
            f"{len(drafts):>5} drafted ({len(by_llm)} by the LLM)  style {used:13} refused {dict(refused) or '-'}")
    rows.sort(key=lambda r: (r["run_id"], r["suggestion_id"]))
    if out_dir is not False:
        dest = Path(out_dir) if out_dir else out
        dest.mkdir(parents=True, exist_ok=True)
        write_jsonl(dest / "suggestion.jsonl", rows)
        log(f"{len(rows):,} suggestion rows -> {dest / 'suggestion.jsonl'}")
    return all_drafts, all_refused, styles, rows


# ───────────── scoring against what really happened ─────────────

HUNK = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")


def after_lines(repo_dir, parent, child, path):
    """({old line: (its after-line or None, the hunk's new lines)}, every new line of the file's diff) for the lines
    the child commit changed. Lines not listed are unchanged. A hunk that keeps its line count pairs lines in order;
    one that doesn't has no single after-line, only its new lines."""
    out = git(repo_dir, "diff", "-U0", "--no-color", "--no-ext-diff", parent, child, "--", path)
    changed, hunk, added = {}, None, []

    def close(hk):
        if not hk:
            return
        o, oc, plus = hk
        for k in range(oc):
            changed[o + k] = (plus[k] if len(plus) == oc else None, plus)

    for ln in out.split("\n"):
        m = HUNK.match(ln)
        if m:
            close(hunk)
            hunk = (int(m.group(1)), int(m.group(2) or 1) if m.group(2) != "0" else 0, [])
        elif hunk and ln.startswith("+") and not ln.startswith("+++"):
            hunk[2].append(ln[1:])
            added.append(ln[1:])
    close(hunk)
    return changed, added


def squash(s):
    return re.sub(r"\s+", "", s or "")


def owner_pairs(repo_dir, parent, child, path):
    """(before, after) for each line of one file that the child commit changed in a same-size hunk: what the owner
    wrote, line by line. Read only for the file the owner is shown to have migrated (seeded_score)."""
    changed, _ = after_lines(repo_dir, parent, child, path)
    src = (Path(repo_dir) / path).read_text(encoding="utf-8", errors="replace").splitlines()
    return [(src[n - 1], a) for n, (a, _) in sorted(changed.items()) if a is not None and n <= len(src)]


SEED_VOTES, SEED_FILES = 3, 3      # decision 13, set before the run: show files until 3 votes, at most 3 files


def owner_seed(repo_dir, parent, child, sites, votes=SEED_VOTES, max_files=SEED_FILES):
    """(seed files, style): the owner is taken to have migrated a repo's first url() files (by path) by hand, until
    their lines show `votes` style votes or `max_files` files are read. The style is what those lines say, else
    path_if_plain. Only these files' real after-lines are read; seeded scoring leaves them out."""
    seed, pairs = [], []
    for f in sorted({s["path"] for s in sites if s["symbol"] == "url"})[:max_files]:
        seed.append(f)
        pairs += owner_pairs(repo_dir, parent, child, f)
        if sum(1 for p in pairs if style_from_owner([p])) >= votes:
            break
    return seed, style_from_owner(pairs) or "path_if_plain"


def score(repo_dir, parent, child, sites, drafts):
    """Per site line: does the draft (or the line unchanged, when there's none) say what the commit did?
    - an unchanged line matches a prediction of no change;
    - a line paired in a same-size hunk matches its after-line;
    - otherwise (the commit merged, split, moved or re-sorted lines) the prediction must be one of the hunk's new
      lines, or, for an import line, one of the file's new lines (isort moves `from django.urls import` lines).
    Whitespace is ignored. (matched, lines, unmatched [(path, line, predicted, real)])"""
    lines = sorted({(s["path"], s["line"]) for s in sites})
    by_path = defaultdict(list)
    for p, n in lines:
        by_path[p].append(n)
    matched, misses = 0, []
    for path, ns in sorted(by_path.items()):
        changed, added = after_lines(repo_dir, parent, child, path)
        added_sq = {squash(a) for a in added}
        src = (Path(repo_dir) / path).read_text(encoding="utf-8", errors="replace").splitlines()
        for n in ns:
            before = src[n - 1] if n <= len(src) else ""
            pred = (drafts[(path, n)][1] if (path, n) in drafts else before).rstrip("\n")
            if n not in changed:
                ok, real = squash(pred) == squash(before), before
            else:
                paired, plus = changed[n]
                if paired is not None:
                    ok, real = squash(pred) == squash(paired), paired
                else:
                    ok = squash(pred) in {squash(p) for p in plus} or (
                        pred.lstrip().startswith(("from ", "import ")) and squash(pred) in added_sq)
                    real = "\n".join(plus) if plus else None
            if ok:
                matched += 1
            else:
                misses.append((path, n, pred, real))
    return matched, len(lines), misses


if __name__ == "__main__":
    ap = parser(__doc__)
    ap.add_argument("--out", default=None, help="where suggestion.jsonl goes (default: out/<run_id>/)")
    ap.add_argument("--url-style", choices=STYLES, default="learned", help="decision 11 (default: learned)")
    ap.add_argument("--django-ref", default=DJANGO_REF, help="Django the url() drafts resolve against")
    ap.add_argument("--cache", default=None, help="where that Django tree is kept (default: snapshots/_cache/diffs)")
    ap.add_argument("--llm-cache", action="store_true",
                    help="LLM path from llm_cache/ only (no key: a miss drafts nothing, as in CI)")
    ap.add_argument("--strict-cache", action="store_true", help="with --llm-cache, a cache miss stops the run")
    a = ap.parse_args()
    lc = None
    if a.llm_cache:
        from meridian.llm_cache import Cache
        lc = Cache()
    run(a.run_id, load_config(a.config), out_dir=a.out, style=a.url_style, django_ref=a.django_ref, cache=a.cache,
        llm_cache=lc, strict_cache=a.strict_cache)
