"""
Literals in a request are constraints, not prose.

`planner.interpret` splits a request into pieces at sentence punctuation
and reads each piece for a verb and an object. A filename has a dot in it.
So "Create jarvis-test.txt on my Desktop containing exactly the words:
version one" became the goals "Create jarvis-test" / "txt on my Desktop
containing exactly the words: version one", the planner invented
`friday-jarvis-test-txt-on-my-desktop.txt` as the path and "Created by
Friday for: ..." as the content, and the owner's exact file and exact
words were both discarded (room M1, 2026-09-20, step 3). The same split
corrupts a URL ("example.com/x"), an email, a time ("4.30"), a version
("opus 4.8"), a branch ("release/1.2").

The rule: an exact literal the owner spoke may not be renamed, rewritten
or summarised by the planner. It is `mutable=False`. The planner decides
HOW; it does not decide WHAT the file is called or what goes in it.

`protect()` finds every literal, replaces it with an opaque token that
contains no punctuation the splitter reacts to, and returns the mapping.
`restore()` puts the literals back in each piece. `for_segment()` hands a
goal the literals its own text contains, and `arguments_for` prefers them
over anything derived.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

PATH, CONTENT, URL, EMAIL, PHONE, TIME, BRANCH, VERSION = (
    "path", "content", "url", "email", "phone", "time", "branch", "version")


@dataclass(frozen=True)
class LiteralConstraint:
    kind: str
    value: str
    mutable: bool = False
    #: where in the original text it sat, for ordering
    start: int = 0

    @property
    def token(self) -> str:
        return f"LITERAL{self.start:05d}X"


#: A filename: a stem, a dot, a short alphanumeric extension; optionally
#: with a directory part in either slash convention. The stem has no
#: spaces (a spaced filename must be quoted or absolute - otherwise
#: "Create jarvis-test.txt" would swallow the verb), may not be a bare
#: number (3.5 is a version), and the extension is a real extension
#: shape, not "com" (that is a URL host).
_EXTS = (r"txt|md|py|js|ts|json|ya?ml|toml|csv|log|ini|cfg|html?|css|xml|pdf|docx?|xlsx?|pptx?|"
         r"png|jpe?g|gif|svg|mp3|mp4|wav|zip|tar|gz|exe|bat|sh|ps1|sql|db|sqlite3?|env|lock|rs|go|java|kt|swift|c|h|cpp|hpp|rb|php")
_FILENAME = re.compile(
    r"(?<![\w/\\.-])"
    r"((?:[A-Za-z]:[\\/]|~[\\/]|\.{0,2}[\\/])(?:[^\\/\s:*?\"<>|]+[\\/])*[^\\/\s:*?\"<>|]+"   # absolute / rooted path
    r"|(?:[\w.\-]+[\\/])*[\w\-][\w\-.]*?\.(?:" + _EXTS + r"))"                           # bare filename
    r"(?![\w/\\-]|\.(?=[\w/\\]))",   # a sentence-ending "." after the name is not part of it
    re.I)
_URL = re.compile(
    r"\b(?:https?://|www\.)[^\s,;'\")]+"
    r"|\b[a-z0-9-]+(?:\.[a-z0-9-]+)*\.(?:com|org|net|io|dev|ai|co|uk|in|edu|gov)\b(?:/[^\s,;'\")]*)?",
    re.I)
_EMAIL = re.compile(r"\b[\w.+-]+@[\w-]+(?:\.[\w-]+)+\b")
_PHONE = re.compile(r"(?<![\w.])\+?\d[\d\s()-]{6,18}\d(?![\w.])")
_TIME = re.compile(r"\b(?:[01]?\d|2[0-3])[:.][0-5]\d(?:\s?(?:am|pm))?\b|\b(?:[1-9]|1[0-2])\s?(?:am|pm)\b", re.I)
_BRANCH = re.compile(r"\b(?:branch|on|to|from|into|checkout)\s+((?:release|feature|fix|hotfix|bugfix|chore|main|master|develop|dev)[/\-][\w.\-/]+)", re.I)
_VERSION = re.compile(r"\b(?:v(?:ersion)?\s?)?(\d+\.\d+(?:\.\d+)*)\b")

#: Exact content the owner dictated: "containing exactly the words: X",
#: "with the words: X", "with content X", "that says X", or anything in
#: quotes. Runs to the end of the sentence (a full stop followed by space
#: or end), so a dot INSIDE the words survives when quoted.
_CONTENT_QUOTED = re.compile(r"[\"'“‘]([^\"'”’]{1,400})[\"'”’]")
_CONTENT_LEAD = re.compile(
    r"\b(?:containing|contains|with|that says|saying|reading|set to|equal to|whose contents? (?:is|are))\s+"
    r"(?:exactly\s+)?(?:the\s+)?(?:(?:words?|text|content|contents|line|string)\s*)?[:=]?\s*",
    re.I)


def _content_after_lead(text: str, start: int) -> tuple[int, int] | None:
    """The span of dictated content that begins at `start` and runs to the
    end of the sentence. A sentence ends at `. `/`.`$/`;`/`!`/`?` or at a
    sequencing word (" then ", " and then ", " after that")."""
    m = re.compile(r"(?:[.;!?](?=\s|$)|\s+(?:and\s+)?then\b|\s+after that\b)", re.I).search(text, start)
    end = m.start() if m else len(text)
    while end > start and text[end - 1] in " ,":
        end -= 1
    return (start, end) if end > start else None


def find(text: str) -> list[LiteralConstraint]:
    """Every literal in `text`, non-overlapping, earliest first. Content
    beats filename beats url beats the rest at a given position; a literal
    inside a longer literal is dropped."""
    found: list[LiteralConstraint] = []

    def add(kind: str, s: int, e: int) -> None:
        if e <= s:
            return
        for f in found:
            if s < f.start + len(f.value) and e > f.start:
                return
        found.append(LiteralConstraint(kind, text[s:e], start=s))

    # 1. content first: it may contain filenames and must be kept whole
    for m in _CONTENT_QUOTED.finditer(text):
        add(CONTENT, m.start(1), m.end(1))
    for m in _CONTENT_LEAD.finditer(text):
        # only a lead that introduces dictated words, i.e. has a colon or
        # the word "words"/"exactly" - "with the file" is not content
        head = m.group(0).lower()
        if ":" not in head and "=" not in head and not re.search(r"words?|exactly|text|says|saying|string|line\b", head):
            continue
        span = _content_after_lead(text, m.end())
        if span:
            add(CONTENT, *span)
    # 2. everything else - email before url, since every email contains a host
    for kind, rx in ((EMAIL, _EMAIL), (URL, _URL), (PATH, _FILENAME)):
        for m in rx.finditer(text):
            add(kind, m.start(), m.end())
    for m in _BRANCH.finditer(text):
        add(BRANCH, m.start(1), m.end(1))
    for m in _TIME.finditer(text):
        add(TIME, m.start(), m.end())
    for m in _PHONE.finditer(text):
        if sum(c.isdigit() for c in m.group(0)) >= 7:
            add(PHONE, m.start(), m.end())
    for m in _VERSION.finditer(text):
        add(VERSION, m.start(1), m.end(1))
    found.sort(key=lambda f: f.start)
    return found


def protect(text: str) -> tuple[str, dict[str, LiteralConstraint]]:
    """`text` with every literal replaced by its token, and token -> literal."""
    literals = find(text)
    if not literals:
        return text, {}
    out, last, mapping = [], 0, {}
    for lit in literals:
        out.append(text[last:lit.start])
        out.append(lit.token)
        mapping[lit.token] = lit
        last = lit.start + len(lit.value)
    out.append(text[last:])
    return "".join(out), mapping


_TOKEN = re.compile(r"LITERAL\d{5}X")


def restore(piece: str, mapping: dict[str, LiteralConstraint]) -> str:
    return _TOKEN.sub(lambda m: mapping[m.group(0)].value if m.group(0) in mapping else m.group(0), piece)


def for_segment(piece: str, mapping: dict[str, LiteralConstraint]) -> tuple[LiteralConstraint, ...]:
    """The literals a (still-tokenised) piece contains, in order."""
    return tuple(mapping[t] for t in _TOKEN.findall(piece) if t in mapping)


def first(literals, kind: str) -> str:
    for lit in literals or ():
        if lit.kind == kind:
            return lit.value
    return ""
