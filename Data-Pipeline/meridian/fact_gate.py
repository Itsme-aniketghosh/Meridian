"""N: the fact gate. LLM text ships only if every fact-like token in it is one of its facts.

    from meridian.fact_gate import check, gate
    check(text, facts)                       # -> Result, truthy when the text passes
    gate(text, facts, template)              # -> (text to ship, "llm" or "template", Result)

The gate is plain code, no LLM: same text and facts, same answer. It pulls every number, date, path, `path:line`,
SHA, ticket key and team name out of the text and looks each one up in the facts. One miss and the whole text is
replaced by the template. The bad part is never stripped out, which leaves broken sentences (docs/06-dead-ideas.md).

Emails, @handles and known person names always fail: no person is ever a fact (Contract 5).

`facts` is Contract 5's object, {"facts": {...}, "source": {...}}, or just the inner {name: value}. Values can be
numbers, strings, or lists and dicts of them (block rows). A dict that has both `path` and `line` also makes the fact
"path:line". Strings inside the facts are read with the same extractors as the text, so "billing/invoice.py:12, ..."
from a group_concat gives its paths and lines.

Settings (decisions 1, 2 and 11 in Model-Pipeline/packets/vishwa-agents/decisions.md):
- kinds: which token kinds are checked. "numbers" < "ids" < "teams" (each adds to the one before).
- numbers: "exact", "format" or "round".
- team_rule: "strict", "article" or "ownership" (decision 11): when a team name is read as an ordinary word.
"""
import re
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal

KIND_SETS = {
    "numbers": frozenset({"number", "date"}),
    "ids": frozenset({"number", "date", "path", "sha", "ticket"}),
    "teams": frozenset({"number", "date", "path", "sha", "ticket", "team"}),
}
NUMBER_MODES = ("exact", "format", "round")
DEFAULT_KINDS = "teams"
DEFAULT_NUMBERS = "round"
TEAM_RULES = ("strict", "article", "ownership")
DEFAULT_TEAM_RULE = "article"  # decision 11
_ARTICLE = re.compile(r"\b(?:the|a|an|this|that|our|your)\s+$", re.IGNORECASE)
_OWNS_AFTER = re.compile(r"^(?:['\u2019]s\b|\s+(?:team|teams|owns|own)\b)", re.IGNORECASE)

_EXT = (r"(?:py|pyi|rb|erb|rake|js|jsx|mjs|cjs|ts|tsx|vue|java|kt|kts|scala|go|rs|c|h|cc|cpp|hpp|cs|php|swift|"
        r"proto|toml|cfg|ini|txt|md|rst|sql|yml|yaml|json|html|css|sh|ipynb)")
_PATH = re.compile(rf"(?<![\w/.\-])((?:\./|/)?(?:[\w.\-]+/)*[\w\-][\w.\-]*\.{_EXT})(?::(\d+))?(?![\w/])")
_LINE_ONLY = re.compile(r"(?<![\w:/.])(:\d+)(?!\w)")  # ":231" after a path, as in "cart/pricing.py:214 and :231"
_TICKET = re.compile(r"(?<![\w\-])([A-Z][A-Z0-9_]*-\d+)(?![\w\-])")
_SHA = re.compile(r"(?<![\w])([0-9a-fA-F]{7,40})(?![\w])")
_EMAIL = re.compile(r"[\w.+\-]+@[\w\-]+(?:\.[\w\-]+)+")
_HANDLE = re.compile(r"(?<![\w@.])@([A-Za-z0-9_][A-Za-z0-9_.\-]*[A-Za-z0-9_]|[A-Za-z0-9_])")
_ISO_DATE = re.compile(r"(?<!\d)(\d{4})-(\d{2})-(\d{2})(?:T[\d:.]+Z?)?(?!\d)")
_MONTHS = ("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec")
_MONTH_DATE = re.compile(r"\b(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\.?\s+(\d{1,2})(?:st|nd|rd|th)?"
                         r"(?:,?\s+(\d{4}))?\b")
_NUMBER = re.compile(r"(?<![\w.])(-?(?:\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?))(%?)"
                     r"(?:st|nd|rd|th|ms|[smhdwkx]|mo|yrs?|kb|mb|gb)?(?![\w])")
# counts written as words. "one" is left out: it's a pronoun far more often than a count ("no one", "one of")
_WORDS = ("zero", "", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten", "eleven", "twelve",
          "thirteen", "fourteen", "fifteen", "sixteen", "seventeen", "eighteen", "nineteen", "twenty")
_WORD_NUMBER = re.compile(r"\b(" + "|".join(w for w in _WORDS if w) + r")\b", re.IGNORECASE)


@dataclass(frozen=True)
class Miss:
    kind: str     # number, date, path, sha, ticket, team, person
    token: str    # as written in the text
    at: int       # offset in the text


@dataclass(frozen=True)
class Result:
    passed: bool
    misses: tuple
    checked: int  # fact-like tokens found in the text

    def __bool__(self):
        return self.passed


@dataclass(frozen=True)
class _Num:
    text: str
    value: Decimal
    decimals: int
    pct: bool


def _num(raw, pct=""):
    clean = raw.replace(",", "")
    return _Num(raw + pct, Decimal(clean), len(clean.split(".")[1]) if "." in clean else 0, bool(pct))


class _Facts:
    """Every fact value, sorted into the sets the text is looked up in."""

    def __init__(self, facts, teams, people=()):
        if isinstance(facts, dict) and "facts" in facts and set(facts) <= {"facts", "source"}:
            facts = facts["facts"]
        self.numbers, self.raw_numbers, self.strings = set(), set(), set()
        self.shares = set()  # floats and "x%" strings: the only facts a percent in the text can be
        self.paths, self.path_lines, self.tickets, self.shas, self.dates = set(), set(), set(), set(), set()
        self.teams = {t.lower() for t in teams}
        self._people = _words(people)
        self._walk(facts)

    def _walk(self, v):
        if isinstance(v, bool) or v is None:
            return
        if isinstance(v, (int, float)):
            self.numbers.add(Decimal(str(v)))
            self.raw_numbers.add(str(v))
            if isinstance(v, float):
                self.shares.add(Decimal(str(v)))
        elif isinstance(v, str):
            self._string(v)
        elif isinstance(v, dict):
            line = v.get("line")
            paired = isinstance(v.get("path"), str) and isinstance(line, int) and not isinstance(line, bool)
            if paired:
                self.path_lines.add((v["path"], v["line"]))
            for k, x in v.items():
                if not (paired and k == "line"):  # a line is a fact only next to its path, never as a count
                    self._walk(x)
        elif isinstance(v, (list, tuple, set, frozenset)):
            for x in v:
                self._walk(x)

    def _string(self, s):
        if _EMAIL.search(s) or (self._people and self._people.search(s)):
            raise ValueError(f"a fact names a person ({s[:40]!r}): no person is ever a fact (Contract 5)")
        self.strings.add(s.strip().lower())
        for m in _PATH.finditer(s):
            self.paths.add(m.group(1))
            if m.group(2):
                self.path_lines.add((m.group(1), int(m.group(2))))
        self.tickets.update(_TICKET.findall(s))
        self.shas.update(x for x in _SHA.findall(s) if _is_sha(x))
        for m in _ISO_DATE.finditer(s):
            self.dates.add((int(m.group(1)), int(m.group(2)), int(m.group(3))))
        if re.fullmatch(r"\d{1,3}(?:,\d{3})+(?:\.\d+)?%?|\d+(?:\.\d+)?%?", s.strip()):
            n = _num(s.strip().rstrip("%"), "%" if s.strip().endswith("%") else "")
            self.numbers.add(n.value / 100 if n.pct else n.value)
            self.raw_numbers.add(s.strip())
            if n.pct or n.decimals:
                self.shares.add(n.value / 100 if n.pct else n.value)

    def path_known(self, path, line):
        """A path written in full, or a unique tail of one at a '/' ("pricing.py" for "cart/pricing.py")."""
        path = path.removeprefix("./").lstrip("/")
        known = {p for p, _ in self.path_lines} | self.paths
        full = [path] if path in known else sorted(p for p in known if p.endswith("/" + path))
        if len(full) != 1:
            return False
        return line is None or (full[0], line) in self.path_lines

    def number_known(self, n, mode):
        if mode == "exact":
            return n.text in self.raw_numbers or (not n.pct and n.text in {str(x) for x in self.numbers})
        # a percent is a share (0.596 -> 59.6%), never a count: "4%" doesn't pass from bug_fixes = 4
        pool, scale = (self.shares, 100) if n.pct else (self.numbers, 1)
        if any(f * scale == n.value for f in pool):
            return True
        if mode == "round" and (n.pct or n.decimals):
            q = Decimal(1).scaleb(-n.decimals)
            if any((f * scale).quantize(q, rounding=ROUND_HALF_UP) == n.value for f in pool):
                return True
        # a year written alone is a fact when a fact date has that year
        return not n.pct and n.decimals == 0 and int(n.value) in {d[0] for d in self.dates}

    def date_known(self, year, month, day):
        return any(d[1:] == (month, day) and (year is None or d[0] == year) for d in self.dates)


def _is_sha(s):
    """Hex with at least one letter (deadbeef too). All digits is a number."""
    return any(c.isalpha() for c in s)


def _words(names):
    names = sorted({n for n in names if n and n.strip()}, key=len, reverse=True)
    if not names:
        return None
    return re.compile(r"(?<![\w\-])(" + "|".join(re.escape(n) for n in names) + r")(?![\w\-])", re.IGNORECASE)


def check(text, facts, *, teams, people, kinds=DEFAULT_KINDS, numbers=DEFAULT_NUMBERS,
          team_rule=DEFAULT_TEAM_RULE):
    """Every fact-like token in `text` must be in `facts`. `teams` and `people` are the known names from the block
    (team_map.team, person.name): a team in the text must be a fact, a person never may be. Both are required:
    left out, the gate couldn't see a swapped team or a name. A fact that names a person raises ValueError."""
    if kinds not in KIND_SETS:
        raise ValueError(f"kinds must be one of {sorted(KIND_SETS)}, not {kinds!r}")
    if numbers not in NUMBER_MODES:
        raise ValueError(f"numbers must be one of {NUMBER_MODES}, not {numbers!r}")
    if team_rule not in TEAM_RULES:
        raise ValueError(f"team_rule must be one of {TEAM_RULES}, not {team_rule!r}")
    on = KIND_SETS[kinds]
    known = _Facts(facts, teams, people)
    misses, checked = [], 0
    taken = [False] * len(text)  # characters already read as one token, so 214 in "pricing.py:214" isn't a number

    def take(m, g=0):
        a, b = m.span(g)
        if any(taken[a:b]):
            return False
        taken[a:b] = [True] * (b - a)
        return True

    def miss(kind, token, at):
        misses.append(Miss(kind, token, at))

    # people first, always on: emails, @handles, known names
    for m in _EMAIL.finditer(text):
        if take(m):
            checked += 1
            miss("person", m.group(0), m.start())
    for m in _HANDLE.finditer(text):
        if take(m):
            checked += 1
            miss("person", m.group(0), m.start())
    person_re = _words(people)
    if person_re:
        for m in person_re.finditer(text):
            if take(m):
                checked += 1
                miss("person", m.group(0), m.start())

    for m in _PATH.finditer(text):
        if not take(m):
            continue
        path, line = m.group(1), int(m.group(2)) if m.group(2) else None
        if "path" in on:
            checked += 1
            if not known.path_known(path, line):
                miss("path", m.group(0), m.start())
    for m in _LINE_ONLY.finditer(text):
        # a bare ":231" belongs to the path written last before it
        prev = None
        for p in _PATH.finditer(text, 0, m.start()):
            prev = p.group(1)
        if prev is None or not take(m):
            continue
        if "path" in on:
            checked += 1
            if not known.path_known(prev, int(m.group(1)[1:])):
                miss("path", m.group(0), m.start())

    for m in _TICKET.finditer(text):
        if take(m) and "ticket" in on:
            checked += 1
            if m.group(1) not in known.tickets:
                miss("ticket", m.group(0), m.start())
    for m in _SHA.finditer(text):
        if _is_sha(m.group(1)) and take(m) and "sha" in on:
            checked += 1
            if not any(f.lower().startswith(m.group(1).lower()) for f in known.shas):
                miss("sha", m.group(0), m.start())

    for m in _ISO_DATE.finditer(text):
        if take(m) and "date" in on:
            checked += 1
            if not known.date_known(int(m.group(1)), int(m.group(2)), int(m.group(3))):
                miss("date", m.group(0), m.start())
    for m in _MONTH_DATE.finditer(text):
        if take(m) and "date" in on:
            checked += 1
            year = int(m.group(3)) if m.group(3) else None
            if not known.date_known(year, _MONTHS.index(m.group(1).lower()) + 1, int(m.group(2))):
                miss("date", m.group(0), m.start())

    variant = {}  # "payment" and "payments" both name the team payments
    for t in sorted(known.teams):
        for v in (t, t[:-1] if t.endswith("s") else t + "s", t + "es"):
            variant.setdefault(v, t)
    team_re = _words(variant)
    if team_re:
        hits = list(team_re.finditer(text))
        for m in hits:
            if not _names_a_team(text, m, hits, team_rule):
                continue  # decision 11: read as an ordinary word, not a team
            if take(m) and "team" in on:
                checked += 1
                if variant[m.group(1).lower()] not in known.strings:
                    miss("team", m.group(0), m.start())

    for m in _NUMBER.finditer(text):
        if take(m) and "number" in on:
            checked += 1
            if not known.number_known(_num(m.group(1), m.group(2)), numbers):
                miss("number", m.group(0), m.start())

    for m in _WORD_NUMBER.finditer(text):
        if take(m) and "number" in on:
            checked += 1
            if Decimal(_WORDS.index(m.group(1).lower())) not in known.numbers:
                miss("number", m.group(0), m.start())

    misses.sort(key=lambda x: x.at)
    return Result(not misses, tuple(misses), checked)


def _names_a_team(text, m, hits, rule):
    """Decision 11: whether a team-name match is used as a team. strict: always. article: not right after
    the/a/an/this/that/our/your, unless "team", "'s", "owns" or "own" follows or it's in a list with another team
    name ("the payments and checkout teams"). ownership: only next to one of those, or in such a list."""
    if rule == "strict":
        return True
    owns = bool(_OWNS_AFTER.match(text[m.end():]))
    between = (text[min(m.end(), o.end()):max(m.start(), o.start())] for o in hits if o is not m)
    listed = any(re.fullmatch(r"\s*(?:,|and|or|&|/|,\s*and)\s*", b) for b in between)
    if rule == "article":
        return owns or listed or not _ARTICLE.search(text[:m.start()])
    return owns or listed


def gate(text, facts, template, *, teams, people, **kw):
    """The text to ship: the LLM's if it passes, else the template, whole. `template` is a string or a function of
    the facts. Returns (text, source, result) with source "llm" or "template" (the suggestion row's `source`)."""
    result = check(text, facts, teams=teams, people=people, **kw) if text is not None else Result(False, (), 0)
    if result:
        return text, "llm", result
    body = template(facts) if callable(template) else template
    return body, "template", result


def fallback_rate(sources):
    """The share of texts that fell back to the template, for the run's output."""
    sources = list(sources)
    fell = sum(s == "template" for s in sources)
    return {"texts": len(sources), "fallbacks": fell, "rate": round(fell / len(sources), 4) if sources else 0.0}
