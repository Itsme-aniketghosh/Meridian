"""GitLab-style CODEOWNERS parser/matcher + summary stats.
Semantics implemented (GitLab docs / Gitlab::CodeOwners):
  - `[Section]` / `^[Optional section]` / `[Section][N]` headers; owners on the header are the section default,
    used by rules in that section that list no owners.
  - Each section is matched independently; inside a section the LAST matching rule wins. A file's owners = union
    over sections.
  - Pattern: `*` alone -> everything; no leading `/` -> may match at any depth; trailing `/` -> everything below;
    a pattern with no glob chars also matches as a directory prefix. `*` does not cross `/`, `**` does,
    dotfiles match, `{a,b}` alternation supported.
  - `!pattern` = exclusion: a file matching it has no owners in that section.
  - Handle with a `/` = group. Handle without `/` = user, unless listed in data/toplevel_groups.txt
    (filled by code_resolve_handles.py).
Rerun: python3 code_codeowners.py   (needs data/CODEOWNERS = `git show SNAP:.gitlab/CODEOWNERS`)"""
import os, re, json
OUT = os.environ.get("OUT", "data/code")
HDR = re.compile(r"^(\^?)\[([^\]]+)\](\[\d+\])?\s*(.*)$")


def glob_to_regex(p):
    i, out = 0, ""
    while i < len(p):
        c = p[i]
        if p.startswith("**/", i):
            out += "(?:.*/)?"; i += 3; continue
        if p.startswith("**", i):
            out += ".*"; i += 2; continue
        if c == "*": out += "[^/]*"
        elif c == "?": out += "[^/]"
        elif c == "[":
            j = p.find("]", i)
            if j == -1: out += r"\["
            else: out += "[" + p[i + 1:j].replace("!", "^", 1) + "]"; i = j
        elif c == "{":
            j = p.find("}", i)
            if j == -1: out += r"\{"
            else: out += "(?:" + "|".join(re.escape(x) for x in p[i + 1:j].split(",")) + ")"; i = j
        elif c == "\\" and i + 1 < len(p): out += re.escape(p[i + 1]); i += 1
        else: out += re.escape(c)
        i += 1
    return out


def compile_pattern(pat):
    if pat == "*": return re.compile(r"^/.*$")
    plain = not re.search(r"[*?\[{]", pat)
    if not pat.startswith("/"): pat = "/**/" + pat
    if pat.endswith("/"): pat = pat + "**"
    rx = glob_to_regex(pat)
    if plain and not pat.endswith("**"): rx += "(?:/.*)?"
    return re.compile("^" + rx + "$")


def parse(path=None):
    path = path or f"{OUT}/data/CODEOWNERS"
    sections, cur = [], {"name": "(none)", "optional": False, "default": [], "rules": []}
    sections.append(cur)
    for ln in open(path, encoding="utf-8"):
        s = ln.strip()
        if not s or s.startswith("#"): continue
        m = HDR.match(s)
        if m:
            cur = {"name": m.group(2), "optional": bool(m.group(1)), "default": [t for t in m.group(4).split() if t.startswith("@")], "rules": []}
            sections.append(cur); continue
        s = re.sub(r"\s+#.*$", "", s)
        parts = re.split(r"(?<!\\)\s+", s)
        pat, owners = parts[0].replace("\\ ", " "), [t for t in parts[1:] if t.startswith("@") or "@" in t]
        neg = pat.startswith("!")
        if neg: pat = pat[1:]
        cur["rules"].append({"pattern": pat, "owners": owners, "rx": compile_pattern(pat), "neg": neg})
    return [s for s in sections if s["rules"] or s["name"] != "(none)"]


def owners_of(sections, f):
    """returns list of (section, rule pattern, owners) that apply to file f"""
    p = "/" + f
    hits = []
    for s in sections:
        win, excluded = None, False
        for r in s["rules"]:
            if r["rx"].match(p):
                if r["neg"]: excluded = True
                else: win = r
        if win is not None and not excluded:
            hits.append((s["name"], win["pattern"], win["owners"] or s["default"]))
    return hits


def toplevel_groups():
    p = f"{OUT}/data/toplevel_groups.txt"
    return set(open(p).read().split()) if os.path.exists(p) else set()


def is_user(h, tg):
    h = h.lstrip("@")
    return "/" not in h and "@" not in h and h.lower() not in tg


if __name__ == "__main__":
    secs = parse(); tg = toplevel_groups()
    rules = [r for s in secs for r in s["rules"] if not r["neg"]]
    handles = {o for r in rules for o in r["owners"]} | {o for s in secs for o in s["default"]}
    eff = [r["owners"] or next(s["default"] for s in secs if r in s["rules"]) for r in rules]
    st_neg = sum(r["neg"] for s in secs for r in s["rules"])
    n_user_rules = sum(any(is_user(o, tg) for o in e) for e in eff)
    n_group_only = sum(e and all(not is_user(o, tg) for o in e) for e in eff)
    n_none = sum(not e for e in eff)
    users = sorted(h for h in handles if is_user(h, tg)); groups = sorted(h for h in handles if not is_user(h, tg))
    st = {"sections": len(secs), "optional_sections": sum(s["optional"] for s in secs), "rules": len(rules),
          "rules_naming_a_user": n_user_rules, "rules_groups_only": n_group_only, "rules_no_owner": n_none,
          "distinct_handles": len(handles), "user_handles": len(users), "group_handles": len(groups),
          "toplevel_groups_known": len(tg), "exclusion_rules": st_neg}
    print(json.dumps(st, indent=1))
    json.dump(st, open(f"{OUT}/data/codeowners_stats.json", "w"), indent=1)
    open(f"{OUT}/data/codeowner_users.txt", "w").write("\n".join(users))
    open(f"{OUT}/data/codeowner_groups.txt", "w").write("\n".join(groups))
