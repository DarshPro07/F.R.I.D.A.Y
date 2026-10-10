
"""
Understanding a request before deciding what to call.

The planner this replaces did one thing: split the sentence on connectives and
match each fragment to the nearest capability name. Everything it got wrong
follows from there.

    "Friday, check my computer"       -> a task called objective.unmapped,
                                         because "Friday" is a fragment and
                                         fragments become tasks
    "keep going"                      -> memory_remember
    "do not ask me to continue"       -> absorbed into whatever clause it
                                         touched, and with it the goal that
                                         clause actually contained: in two of
                                         the six cases "open Paint" vanished
    a 1967-word request               -> 205 tasks, 68 of which failed

The last one is the shape of the problem. Length became work, because prose
was being read as a list of instructions when most of it is not: it is
address, constraint, reporting, and how-to-behave. A request is mostly *about*
the work.

So this reads a request in three stages, and only the first one is about
language:

    interpret   what the person wants, as goals with an operation and a
                target and nothing else - no capability names, because a
                planner that reaches for tool names starts fitting the request
                to the registry instead of to the person
    resolve     which capabilities could realise each goal, asked of the
                registry by machine semantics, which returns a handful rather
                than a hundred
    validate    whether the result is a graph worth running, before anything
                is persisted

The middle stage is deliberately not a model looking at 125 schemas. That is
the token and accuracy problem this codebase spent CORE-02B removing, and
re-creating it inside the planner would be the same mistake wearing a hat.
"""

from __future__ import annotations

import re

from dataclasses import dataclass, field

from friday import semantics as S

OBJECTIVE = "OBJECTIVE"        # work to do

CONSTRAINT = "CONSTRAINT"      # how the work must be done

REPORTING = "REPORTING"        # what to say afterwards

SAFETY = "SAFETY"              # what must not happen

VOCATIVE = "VOCATIVE"          # address; not about the work at all

PRESENTATION = "PRESENTATION"  # how to talk while working

SEGMENT_KINDS = (OBJECTIVE, CONSTRAINT, REPORTING, SAFETY, VOCATIVE,
                 PRESENTATION)

#: Names the boss calls Friday. A vocative is not a step.
_VOCATIVES = frozenset({"friday", "jarvis", "ada", "hey", "ok", "okay"})

#: Politeness and framing that wraps a request without adding to it.
_WRAPPERS = re.compile(
    r"^\s*(?:please|can you|could you|would you|i want you to|i'd like you to|"
    r"i would like you to|go ahead and|now)\s+", re.IGNORECASE)

#: How the work should be carried out. Not work.
_CONSTRAINT = re.compile(
    r"\b(?:do not|don't|dont|never|without)\s+(?:ask|wait|stop|tell|say|"
    r"repeat|make me|need me)|"
    r"\bwithout me (?:saying|telling)|"
    r"\b(?:from start to finish|autonomously|on your own|by yourself)\b|"
    r"\bkeep going\b|\bcarry on\b|\bcontinue (?:until|through)\b|"
    r"\bif (?:one|any|some|a) .{0,40}(?:fails|does not work|doesn't work)",
    re.IGNORECASE)

#: What to say at the end. Reading run state, not doing work.
_REPORTING = re.compile(
    r"\b(?:tell me|let me know|report|summar(?:ise|ize)|show me the result|"
    r"give me a (?:report|summary|rundown))\b.{0,30}"
    r"\b(?:when|at the end|afterwards|after|finished|done|complete)|"
    r"\bat the end\b|\bwhen (?:it is|it's|everything is|you are|you're) "
    r"(?:finished|done|complete)|"
    # "tell me the final result" carries no temporal word at all, and without
    # this it read as an instruction to go and find something out: READ of
    # SYSTEM, resolved to system_battery. A fifth task nobody asked for.
    r"\b(?:tell me|let me know|give me|show me)\s+(?:the\s+)?"
    r"(?:final\s+|end\s+)?(?:result|results|outcome|answer|summary|report)\b",
    re.IGNORECASE)

#: Things that must not happen. Never executable.
_SAFETY = re.compile(
    r"\b(?:do not|don't|dont|never)\s+(?:shut down|shutdown|restart|reboot|"
    r"delete|erase|terminate|kill|touch|modify|change|purchase|buy|publish|"
    r"send|share|remove|recycle|move|rename|overwrite|uninstall|empty)\b",
    re.IGNORECASE)

#: How to speak during the work.
_PRESENTATION = re.compile(
    r"\b(?:short|brief|concise|quiet(?:ly)?)\b.{0,20}\b(?:updates?|answers?|"
    r"replies)|\b(?:updates?|answers?|replies)\s+(?:short|brief|concise)\b|"
    r"\b(?:don't|do not|dont) narrate\b|\bkeep me posted\b",
    re.IGNORECASE)

#: Where one instruction ends and the next begins. Kept simple on purpose:
#: the interesting work is classification, not sentence splitting.
#:
#: The outer group captures, so `re.split` hands back the separator alongside
#: the pieces. What separated two instructions is information: "then" means
#: one follows the other, and a non-capturing split throws that away.
#: A full stop ends a sentence only before a space or the end: the dot in
#: `notes.txt` or `v1.2` is part of what the person named (INTENT-001).
_SPLIT = re.compile(
    r"((?:[;!?\n]|\.(?=\s|$))+|,?\s+(?:and then|then|after that|afterwards)\s+|"
    # A prohibition, or a word about how to work, starts its own clause:
    # left behind, "and do not" handed "delete anything" to the next piece
    # as a request (V7-D39).
    r"(?:,\s*(?:and\s+|but\s+)?|\s+(?:and|but)\s+)(?=(?:do not|don't|dont|never|keep)\b)|"
    r"[,\s]\s*(?:and\s+)?(?=(?:check|open|find|create|read|write|play|pause|stop|"
    r"resume|search|look|make|delete|remove|recycle|rename|move|copy|list|"
    r"show|tell|research|get|set|close|start|run|clean|tidy)\b))",
    re.IGNORECASE)

#: A split on a bare verb (no comma, no "and") inside a question is a
#: misreading: in "which windows are open right now" the word "open" is a
#: state, not a second request, and "which windows are" + "open right now"
#: became windows_list + open_in_browser (Golden GO-general-005, 2026-09-05).
#: Punctuation, "then" and ", and <verb>" still split a question; a lone
#: whitespace-before-verb separator does not.
_WHITESPACE_ONLY_SEPARATOR = re.compile(r"^\s+$")


#: Quoted words are the person's own, to be used as given: nothing inside
#: them is a separator. Single quotes are left alone - they are apostrophes
#: ("don't") far more often than quotation marks.
_QUOTED = re.compile(r'"[^"\n]*"|“[^”\n]*”|`[^`\n]*`')
_HELD = re.compile("\x00(\\d+)\x00")

#: "keep a reading list", "the shopping list": a verb word straight after an
#: article and one more word is a noun, not a second request. Split on it,
#: "list" became `system_list_processes`.
_NOUN_PHRASE_END = re.compile(
    r"\b(?:a|an|the|my|your|our|his|her|their|this|that)\s+[\w-]+$", re.IGNORECASE)

#: "do not" / "never" at the end of a piece: the verb after it is what is
#: forbidden, and stays with it (V7-D39).
_NEGATION_END = re.compile(r"\b(?:do not|don't|dont|never|not)$", re.IGNORECASE)

#: A file the person named: a name with an extension of two or more
#: characters (so not "v1.2" or "e.g"), or a path to one.
_NAMED_PATH = re.compile(
    r"(?<![\w.\\/-])((?:[A-Za-z]:[\\/]|~?[\\/])?(?:[\w.-]+[\\/])*[\w-]+"
    r"\.[A-Za-z][A-Za-z0-9]{1,7})(?![\w\\/])")

#: Content the person gave in words: "containing ...", "saying ...".
_CONTENT_AFTER = re.compile(
    r"\b(?:containing|saying|that says|with the (?:text|words|content))\s+(.+)$",
    re.IGNORECASE)


#: "I want to ...", "we'd like to ...", "the goal is ...": why, not what.
_PURPOSE = re.compile(
    r"^\s*(?:(?:i|we)\s+(?:want|would like|need|am trying|are trying|plan)\s+to\b"
    r"|(?:i|we)'d like to\b|(?:the\s+)?(?:goal|idea|point|plan)\s+is\b)",
    re.IGNORECASE)


def _unquoted(text: str) -> str:
    """The sentence without the person's quoted words, for reading what it
    asks: words inside quotes are content, not instructions."""
    return _QUOTED.sub('""', text or "")


def _named_path(text: str) -> str:
    """The file the person named, verbatim, or ""."""
    found = _NAMED_PATH.search(_unquoted(text))
    if found:
        return found.group(1)
    for quoted in _QUOTED.findall(text or ""):
        if _NAMED_PATH.fullmatch(quoted[1:-1]):
            return quoted[1:-1]
    return ""


def _named_content(text: str) -> str:
    """The words the person gave as content, verbatim, or ""."""
    path = _named_path(text)
    for quoted in _QUOTED.findall(text or ""):
        if quoted[1:-1] != path:
            return quoted[1:-1]
    found = _CONTENT_AFTER.search(_unquoted(text))
    return found.group(1).strip() if found else ""


def _split_requests(text: str) -> list[str]:
    quoted: list[str] = []

    def hold(match: re.Match) -> str:
        quoted.append(match.group(0))
        return f"\x00{len(quoted) - 1}\x00"

    def restore(piece: str) -> str:
        return _HELD.sub(lambda m: quoted[int(m.group(1))], piece)

    pieces = [restore(piece) for piece in _SPLIT.split(_QUOTED.sub(hold, text))]
    if len(pieces) < 3:
        return pieces
    merged: list[str] = [pieces[0]]
    for index in range(1, len(pieces), 2):
        separator, following = pieces[index], pieces[index + 1] if index + 1 < len(pieces) else ""
        head = merged[-1]
        if (_WHITESPACE_ONLY_SEPARATOR.match(separator or "")
                and (S._QUESTION.match(head.lstrip(" ,"))
                     or _NOUN_PHRASE_END.search(head.rstrip())
                     or _NEGATION_END.search(head.rstrip()))):
            merged[-1] = f"{head}{separator}{following}"
            continue
        merged.append(separator)
        merged.append(following)
    return merged

_SEQUENCING = re.compile('\\b(?:then|after that|afterwards)\\b', re.IGNORECASE)


@dataclass(frozen=True)
class Segment:
    """One piece of what was said, and what kind of thing it is."""

    text: str
    kind: str
    #: Whether it was joined to the piece before it by a sequencing word
    #: ("then", "after that"), which makes it depend on that piece.
    follows: bool = False


@dataclass
class Goal:
    """
    Something the person wants to be true. No capability names.

    The absence is the point: a goal that already names a tool has skipped
    the question of what was actually wanted, which is how "find a technology
    story" became `product_status`.
    """

    goal_id: str
    intent: str
    operation: str
    target: str
    entity: str = ""
    depends_on: tuple[str, ...] = ()
    #: Filled in by `resolve`, not by interpretation.
    candidates: tuple[str, ...] = ()
    capability: str = ""
    confidence: float = 0.0
    why: str = ""
    #: Leaves of a composite goal, as task specs; empty for a plain goal.
    children: tuple[dict, ...] = ()
    #: True when no verb was recognised and READ was assumed: the shape is
    #: weak, and resolution lets a capability's own examples overrule it.
    operation_assumed: bool = False


@dataclass
class Plan:
    """What was understood, in full - including the parts that are not work."""

    goals: list[Goal] = field(default_factory=list)
    constraints: list[str] = field(default_factory=list)
    reporting: list[str] = field(default_factory=list)
    safety: list[str] = field(default_factory=list)
    presentation: list[str] = field(default_factory=list)
    #: Why the person wants this ("I want to keep a reading list"), when a
    #: concrete instruction follows it: the objective's purpose, not a step.
    purpose: list[str] = field(default_factory=list)
    discarded: list[str] = field(default_factory=list)
    unresolved: list[str] = field(default_factory=list)

    @property
    def executable(self) -> list[Goal]:
        return [goal for goal in self.goals if goal.capability]


def _strip_vocative(text: str) -> tuple[str, str]:
    """Remove a leading name. Returns (remainder, what was removed)."""
    removed = []
    words = text.split()
    while words and re.sub(r"[^a-z]", "", words[0].lower()) in _VOCATIVES:
        # Without the punctuation it was said with: what is recorded is the
        # name, not "Friday," - the trailing comma is a fact about the
        # sentence, not about who was addressed.
        removed.append(words.pop(0).strip(" ,.!?;:"))
    return " ".join(words), " ".join(removed)

#: The patterns that mean a piece is about the work rather than being work,
#: in the order `classify_segment` consults them.
_NOT_WORK = ((_SAFETY, SAFETY), (_REPORTING, REPORTING),
             (_PRESENTATION, PRESENTATION), (_CONSTRAINT, CONSTRAINT))


def split_off_the_instruction(text: str) -> tuple[str, str, str] | None:
    """
    `(instruction, remainder, kind)` when a request has a constraint stuck to
    its tail, else None.

    A whole segment used to take the kind of whatever pattern matched
    anywhere inside it, so:

        "find one current technology story, and finish the complete job
         without me saying continue"

    classified as CONSTRAINT - because it does contain one - and the request
    to find a technology story went with it. Silently. That is the third time
    a real goal has been lost to a neighbouring phrase, and a lost goal is
    worse than a wrong one: nothing reports a step that was never planned.

    So the tail is cut off and the head is read on its own.
    """
    for pattern, kind in _NOT_WORK:
        match = pattern.search(text)
        if match is None or match.start() == 0:
            continue
        head = text[:match.start()].strip(" ,.;:and")
        if not head:
            continue
        if S.for_request(head) is None and S.target_for_request(head) is None:
            # The head is not a request either, so there is nothing to save
            # and splitting would only make two fragments out of one.
            continue
        return head, text[match.start():].strip(" ,.;:"), kind
    return None


def classify_segment(text: str) -> str:
    """What kind of thing this piece of the request is."""
    stripped = text.strip()
    if not stripped:
        return VOCATIVE
    bare, _removed = _strip_vocative(stripped)
    if not bare.strip(" ,."):
        return VOCATIVE
    # Safety before constraint: "do not shut down" is both shaped like a
    # constraint and specifically about a thing that must not happen.
    if _SAFETY.search(bare):
        return SAFETY
    if _REPORTING.search(bare):
        return REPORTING
    if _PRESENTATION.search(bare):
        return PRESENTATION
    if _CONSTRAINT.search(bare):
        return CONSTRAINT
    return OBJECTIVE

_BARE_VERB = re.compile('^(?:check|open|find|create|read|write|play|pause|stop|resume|search|look|make|delete|remove|recycle|rename|move|copy|list|show|research|get|set|close|start|run|clean|tidy)(?:\\s+(?:up|out|it|them))?$', re.IGNORECASE)


def segments(text: str) -> list[Segment]:
    """Split a request, say what each piece is, and whether it follows."""
    found: list[Segment] = []
    # The separators are kept (the pattern captures them), so a sequencing
    # word between two pieces can be read before the next piece is.
    pieces = _split_requests(text or "")
    follows = False

    # A bare verb ("open") followed by a piece that asks for something
    # ("and also Paint, please") borrows the object from that piece, so the
    # verb does not stand alone as a request for nothing.
    content = [(index, piece) for index, piece in enumerate(pieces)
               if index % 2 == 0 and (piece or "").strip(" ,")]
    borrowed = {}
    for position, (index, piece) in enumerate(content):
        if (_BARE_VERB.match(piece.strip(" ,"))
                and position + 1 < len(content)):
            following = content[position + 1][1].strip(" ,")
            without_verb = _ASKING.sub("", following).strip(" ,")
            if without_verb and without_verb != following:
                borrowed[index] = f"{piece.strip(' ,')} {without_verb}"

    for index, piece in enumerate(pieces):
        raw = piece or ""
        if index % 2 == 1:
            # A separator. "then" and its relatives make the next piece
            # follow the previous one; a plain "and" does not.
            if _SEQUENCING.search(raw):
                follows = True
            continue
        piece = borrowed.get(index, raw.strip(" ,"))
        if not piece:
            continue
        bare, removed = _strip_vocative(piece)
        if removed and not bare.strip(" ,."):
            found.append(Segment(removed, VOCATIVE))
            continue
        if removed:
            found.append(Segment(removed, VOCATIVE))
        bare = _WRAPPERS.sub("", bare).strip(" ,")
        if not bare:
            continue

        kind = classify_segment(bare)
        if kind in (CONSTRAINT, REPORTING, SAFETY, PRESENTATION):
            # "open Paint but do not ask me to continue": the instruction is
            # in there, and it is the part that becomes a task.
            divided = split_off_the_instruction(bare)
            if divided is not None:
                head, tail, tail_kind = divided
                found.append(Segment(head, classify_segment(head), follows))
                found.append(Segment(tail, tail_kind))
                follows = False
                continue
        found.append(Segment(bare, kind, follows))
        follows = False
    return found

#: A pronoun standing in for the thing the previous goal acted on.
_REFERS_BACK = re.compile(r"\b(?:it|that|them|those|the file|the note)\b",
                          re.IGNORECASE)


def interpret(text: str) -> Plan:
    """
    Read a request into goals and everything that is not a goal.

    Deterministic. A model may later refine the goals - the structure is built
    to receive that - but the separation of address, constraint and reporting
    from actual work does not need one, and a request whose meaning depends on
    a model call is a request that cannot be planned while the model is down.
    """
    plan = Plan()
    counter = 0
    previous: Goal | None = None

    pieces = segments(text)
    for position, segment in enumerate(pieces):
        # "I want to keep a reading list: create reading.md" - the first
        # half says why; only the second is work. Said alone, the purpose is
        # the request, and stays a goal (INTENT-001).
        if (segment.kind == OBJECTIVE and _PURPOSE.match(segment.text)
                and any(later.kind == OBJECTIVE for later in pieces[position + 1:])):
            plan.purpose.append(segment.text.strip())
            continue
        if segment.kind == VOCATIVE:
            plan.discarded.append(segment.text)
            continue
        if segment.kind == CONSTRAINT:
            plan.constraints.append(segment.text)
            continue
        if segment.kind == REPORTING:
            plan.reporting.append(segment.text)
            continue
        if segment.kind == SAFETY:
            plan.safety.append(segment.text)
            continue
        if segment.kind == PRESENTATION:
            plan.presentation.append(segment.text)
            continue

        # What is asked is read without the person's quoted words, which
        # are content: "write "call Anna..." to todo.md" is about a file,
        # not about Anna.
        plain = _unquoted(segment.text)
        operation = S.for_request(plain)
        target = S.target_for_request(plain)
        if target is None and _named_path(segment.text):
            target = "FILE"
        counter += 1

        if operation is None and target is None:
            # Nothing about this reads as an instruction, so it gets no
            # capability - but it is still a goal, and it still reaches the
            # graph as an unmapped task that compile records as immediately
            # failed. Dropping it silently would be worse than either: the
            # run would look complete while a step nobody understood had
            # simply gone missing. It is not forced onto the nearest
            # capability either, which is how "keep going" became
            # `memory_remember`.
            plan.unresolved.append(segment.text)
            unplaceable = Goal(
                goal_id=f"g{counter}", intent=segment.text,
                operation="", target="",
                depends_on=(previous.goal_id,)
                if previous is not None and segment.follows else (),
                why="nothing in this reads as a request to do something")
            plan.goals.append(unplaceable)
            # It still counts as what came before. "flurb the wibble, then
            # open Paint" says Paint follows it, and a step that depends on
            # something nobody understood should be skipped rather than run
            # as though its dependency had succeeded.
            previous = unplaceable
            continue

        depends: tuple[str, ...] = ()
        # "read it" after "create a note" is the same thing, later. A pronoun
        # with no target of its own inherits the previous goal's.
        if previous is not None and _REFERS_BACK.search(segment.text):
            if target is None or target == previous.target:
                target = previous.target
                depends = (previous.goal_id,)
        elif previous is not None and segment.follows:
            # "then" makes it a step after the last one, whatever it is about.
            depends = (previous.goal_id,)

        goal = Goal(
            goal_id=f"g{counter}",
            intent=segment.text,
            operation=operation or S.READ,
            target=target or "",
            entity=_entity(plain),
            depends_on=depends,
            operation_assumed=operation is None,
        )
        plan.goals.append(goal)
        previous = goal

    return plan

#: A capitalised word that is not the first is usually the thing being named.
_ENTITY = re.compile(r"\b([A-Z][a-zA-Z]{2,})\b")


def _entity(text: str) -> str:
    for match in _ENTITY.finditer(text):
        word = match.group(1)
        if word.lower() in _VOCATIVES:
            continue
        if match.start() == 0 and text.split()[0] == word:
            continue
        return word
    return ""


def candidates(operation: str, target: str, *, limit: int = 8) -> list[str]:
    """
    Capabilities that could realise a goal of this shape.

    Asked of the registry by machine semantics, so the answer is a handful.
    The planner never sees 125 schemas: it sees the few that are structurally
    capable of the thing, which is both cheaper and more accurate than asking
    a model to pick out of everything.
    """
    from friday import capabilities as C

    exact, near = [], []
    for capability in C._ALL:
        capability_operation, capability_target = S.for_capability(capability.id)
        if target and capability_target != target:
            continue
        if capability_operation == operation:
            exact.append(capability.id)
        elif S.compatible(operation, capability.id):
            near.append(capability.id)
    return (exact + near)[:limit]


def resolve(plan: Plan) -> Plan:
    """Choose a capability for each goal, or leave it unresolved and say so."""
    for goal in plan.goals:
        if not goal.operation:
            # Interpretation could not place it. Resolution does not get a
            # second guess at it - that is where a wrong tool comes from.
            continue
        backer = ""
        blind_fallback = False
        if goal.operation_assumed:
            # "bring that window to the front", "put the computer to sleep":
            # no verb the grammar knows, so READ was assumed - and READ of
            # a WINDOW is windows_list, which is not what was said. When a
            # capability of the same target (any operation) fits the words
            # decisively and nothing READ-shaped fits at all, the words
            # decide the operation. A recognised verb is never overruled
            # here; only the assumption is.
            decided = _operation_from_examples(goal.intent, goal.target)
            if decided:
                goal.operation, backer = decided
        if not goal.target:
            # The nouns named no target. Before falling back to the first
            # eight capabilities of that operation in registry order (which
            # is how "how many monitors do I have" reached world news), let
            # a capability whose OWN example phrasings clearly fit the words
            # supply its target - still only among capabilities structurally
            # compatible with the operation, and only when the fit is
            # decisive (Golden Objective suite, general category).
            inferred, backer = _target_from_examples(goal.intent, goal.operation)
            if inferred:
                goal.target = inferred
            else:
                blind_fallback = True
        found = candidates(goal.operation, goal.target)
        if blind_fallback and not any(_fit(goal.intent, cid) > 0 for cid in found):
            # No target, no example fit anywhere, nothing in the fallback
            # shortlist shares a word with the request: choosing one anyway
            # is a confident wrong tool (KPI "capability routing accuracy":
            # 25 of 144 labelled phrasings landed on world news / secrets /
            # resource usage this way). Unresolved is the honest answer -
            # the model planner takes it, or the run says a step was not
            # understood - never a guess dressed as a decision.
            goal.candidates = tuple(found)
            goal.why = (f"no target named and nothing of shape {goal.operation} "
                        f"fits these words")
            continue
        if not backer and goal.target:
            # A named target, but the shortlist is eight registry-order
            # slots: a capability of that exact shape whose examples fit
            # decisively must still be considered ("which drive is nearly
            # full" -> system_disks, ninth in the SYSTEM shortlist). And a
            # noun can point at the wrong target ("screen brightness" ->
            # VISION): when nothing in the noun's shortlist fits the words
            # at all and one capability elsewhere fits decisively, the
            # words win over the noun.
            inferred, other = _target_from_examples(goal.intent, goal.operation)
            if other and other not in found:
                if inferred == goal.target:
                    backer = other
                elif inferred:
                    best_here = max((_fit(goal.intent, cid) for cid in found), default=0)
                    # Nothing in the noun's shortlist shares a word with the
                    # request ("lock my computer": computer -> SYSTEM, whose
                    # CONTROL shortlist is secrets/policy tools, all at 0)
                    # while the other capability's own examples fit it
                    # decisively: the noun was a red herring, the words win
                    # outright. Only when something here does fit is the
                    # 2x / 24-point margin required.
                    if best_here <= 0 or _fit(goal.intent, other) >= max(
                            _EXAMPLE_OVERRULE_MIN, best_here * _EXAMPLE_OVERRULE_FACTOR):
                        goal.target = inferred
                        found = candidates(goal.operation, goal.target)
                        backer = other
        if backer and backer not in found:
            # The capability whose examples decided the target is a
            # candidate even when the registry-order shortlist is full: it
            # fit the words decisively, and the shortlist cut is about
            # prompt size, not about who may be considered.
            found.append(backer)
        goal.candidates = tuple(found)
        if not found:
            goal.why = (f"no capability does {goal.operation} to a "
                        f"{goal.target}" if goal.target
                        else f"no capability does {goal.operation}")
            continue

        if len(found) == 1:
            operation, _target = S.for_capability(found[0])
            if operation != goal.operation and not (
                    goal.operation in S._INFORMATIONAL
                    and operation in S._INFORMATIONAL):
                # The one candidate is merely compatible - a CONTROL where an
                # OPEN was asked for. Choosing it would do something the
                # person did not ask for and call it the only option. It is
                # left as a candidate so the report can say what was near,
                # and the goal stays unresolved so the run says so too.
                #
                # This is the "what windows are open" case seen from the
                # other side: a LIST tool must not win an OPEN request just
                # by being the only tool left standing, any more than by
                # having better example sentences.
                #
                # Two INFORMATIONAL operations are not that case: a
                # question ("which windows are open right now" -> READ) and
                # the LIST tool that answers it change nothing on the
                # machine, so the only read-shaped tool for that target is
                # the answer, not a guess (Golden Objective general-005).
                goal.candidates = tuple(found)
                goal.why = (f"{found[0]} is the nearest thing to "
                            f"{goal.operation}, not a match for it")
                continue
            goal.capability, goal.confidence = found[0], 0.6
            goal.why = "the only capability of that shape"
            continue

        # More than one is structurally capable, so the words decide - but
        # only among these. Text similarity ranks; it does not select, and
        # it never sees a capability the shape ruled out.
        # Equal fits go to the more direct action path (PRD v7 §48), and a
        # path that keeps failing when run, or whose computer-use backend is
        # not up, goes behind the others.
        from friday import action_path
        from friday import computer_use
        from friday import routing_memory
        measured = routing_memory.execution_reliability()
        down = computer_use.unavailable_paths(found)
        ranked = sorted(found, key=lambda cid: (-_fit(goal.intent, cid),
                                                action_path.order_key(cid, measured, down)))
        goal.capability = ranked[0]
        goal.confidence = 0.8
        goal.why = f"best of {len(found)} capabilities of that shape"
    return plan


def _fit(intent: str, capability_id: str) -> int:
    """How well the words of a request fit one candidate."""
    from friday import capabilities as C
    from friday import capability_router as R

    meta = C.by_id(capability_id)
    lowered = intent.lower()
    score = (R._phrase_score(lowered, meta.intent_examples) * 4
             if meta else 0)
    if meta:
        score -= R._phrase_score(lowered, meta.negative_examples) * 2
    words = R._content(lowered)
    name = capability_id.lower().replace("_", " ")
    for word in words:
        if word in name.split():
            score += 6
        elif word in name:
            score += 3
    return score


#: A capability's own examples must share at least this many content
#: words with the request before its target is borrowed, and lead the
#: runner-up by the margin: one shared word is a coincidence.
_EXAMPLE_DECISIVE_WORDS = 2
_EXAMPLE_DECISIVE_MARGIN = 1
#: For the words to overrule a noun-derived target, the backer's fit must
#: be at least this, and at least this many times the best fit inside the
#: noun's own shortlist ("screen brightness": brightness_get 38 vs
#: vision_inspect_screen 14).
_EXAMPLE_OVERRULE_MIN = 24
_EXAMPLE_OVERRULE_FACTOR = 2


def _operation_from_examples(intent: str, target: str) -> tuple[str, str] | None:
    """(operation, capability id) when one capability of this target fits
    the words decisively and no READ-shaped one fits at all; else None."""
    from friday import capabilities as C
    from friday import capability_router as R

    lowered = intent.lower()
    best: tuple[int, str, str] | None = None
    read_fit = 0
    for capability in C._ALL:
        cap_operation, cap_target = S.for_capability(capability.id)
        if target and cap_target != target:
            continue
        if not target and not cap_target:
            continue
        fit = (R._phrase_score(lowered, capability.intent_examples)
               - R._phrase_score(lowered, capability.negative_examples))
        if cap_operation in S._INFORMATIONAL:
            read_fit = max(read_fit, fit)
            continue
        if fit >= _EXAMPLE_DECISIVE_WORDS and (best is None or fit > best[0]):
            best = (fit, cap_operation, capability.id)
    if best is None or read_fit >= best[0]:
        return None
    return best[1], best[2]


def _target_from_examples(intent: str, operation: str) -> tuple[str, str]:
    """(target, capability id) of the capability whose own example
    phrasings decisively fit the request, among capabilities compatible
    with the operation; ("", "") when nothing is decisive."""
    from friday import capabilities as C
    from friday import capability_router as R

    lowered = intent.lower()
    scored: list[tuple[int, str, str]] = []
    for capability in C._ALL:
        cap_operation, cap_target = S.for_capability(capability.id)
        if not cap_target:
            continue
        if cap_operation != operation and not S.compatible(operation, capability.id):
            continue
        hit = R._phrase_score(lowered, capability.intent_examples)
        miss = R._phrase_score(lowered, capability.negative_examples)
        if hit - miss >= _EXAMPLE_DECISIVE_WORDS:
            scored.append((hit - miss, capability.id, cap_target))
    if not scored:
        return "", ""
    scored.sort(reverse=True)
    if len(scored) > 1 and scored[0][0] - scored[1][0] < _EXAMPLE_DECISIVE_MARGIN \
            and scored[0][2] != scored[1][2]:
        return "", ""                       # two targets tie: not decisive
    return scored[0][2], scored[0][1]


class PlanProblem(Exception):
    """The plan is not worth running, and here is why."""


def validate(plan: Plan) -> list[str]:
    """
    What is wrong with this plan, before anything is persisted.

    Returns complaints rather than raising, because a plan with one bad goal
    is usually still worth running for the others - the executor already
    isolates failures. What must not happen is persisting nonsense and
    discovering it 205 tasks later.
    """
    from friday import capabilities as C
    from friday.objectives import COMPOSITE

    complaints: list[str] = []
    known = {capability.id for capability in C._ALL}
    seen: dict[str, str] = {}

    for goal in plan.goals:
        if goal.children:
            # A group is checked as a group: it must be the composite
            # marker, and every leaf must name something. The leaves
            # themselves are checked when compile persists them, against
            # the same registry.
            if goal.capability != COMPOSITE:
                complaints.append(
                    f"{goal.goal_id}: has children but is not a group")
            if any(not child.get("capability") for child in goal.children):
                complaints.append(f"{goal.goal_id}: a child names no capability")
            continue
        if goal.capability == COMPOSITE:
            complaints.append(f"{goal.goal_id}: is a group with no children")
            continue
        if goal.capability and goal.capability not in known:
            complaints.append(
                f"{goal.goal_id}: {goal.capability!r} is not a capability")
        if goal.capability and goal.target:
            operation, target = S.for_capability(goal.capability)
            if target != goal.target:
                complaints.append(
                    f"{goal.goal_id}: wants {goal.target}, "
                    f"{goal.capability} acts on {target}")
        for dependency in goal.depends_on:
            if dependency not in {other.goal_id for other in plan.goals}:
                complaints.append(
                    f"{goal.goal_id}: depends on {dependency}, which is not "
                    f"in the plan")
        key = f"{goal.capability}|{goal.entity}"
        if goal.capability and key in seen:
            complaints.append(
                f"{goal.goal_id}: repeats {seen[key]} ({goal.capability})")
        elif goal.capability:
            seen[key] = goal.goal_id

    for kind, items in (("constraint", plan.constraints),
                        ("reporting", plan.reporting),
                        ("safety", plan.safety)):
        for item in items:
            if any(goal.intent == item for goal in plan.goals):
                complaints.append(f"a {kind} became a task: {item!r}")

    return complaints


def plan_objective(text: str) -> Plan:
    """Interpret, resolve, validate. The whole planner, in order."""
    return resolve(interpret(text))

# Restored from the .pyc oracle: proven by a LOAD_CONST/STORE_NAME
# pair in the running system's bytecode, present in no source candidate.
_FROM_ENTITY = ('name', 'app', 'application', 'program', 'title')

_FROM_QUERY = ('query', 'topic', 'question', 'search', 'text', 'prompt')

_FROM_PATH = ('path', 'file', 'filename', 'destination')

# Restored from the .pyc oracle. Each pattern is a LOAD_CONST string
# and each flag a LOAD_ATTR on `re` in the compiled module - primary
# evidence from the running system, not inference.
_ASKING = re.compile('^\\s*(?:find|search for|search|look up|look for|research|get me|get|tell me about|show me|read|open|create|make|write|clean up|clean|tidy up|tidy|delete|remove|recycle)\\s+(?:me\\s+)?(?:one|a|an|the|some|two|three)?\\s*', re.IGNORECASE)


def _wanted(intent: str) -> str:
    """The thing being asked for, with the asking removed."""
    return (_ASKING.sub("", intent).strip(" .,")
            or intent.strip(" .,"))


def parameters_of(capability_id: str) -> dict[str, bool]:
    """
    `{parameter: required}` for one capability, read from its implementation.

    Deliberately done for finalists only. Discovery runs on compact summaries
    - operation, target, one line of outcome - and the full contract is loaded
    for the capability that was actually chosen. That is what keeps a registry
    of this size out of the planning prompt.
    """
    import inspect

    from friday import capability_runtime as R

    resolution = R.resolutions().get(capability_id)
    if resolution is None:
        return {}
    try:
        signature = inspect.signature(resolution.load())
    except Exception:                                        # noqa: BLE001
        return {}

    found = {}
    for name, parameter in signature.parameters.items():
        if name in ("run", "engine", "self") or name.startswith("_"):
            continue
        if parameter.kind in (parameter.VAR_POSITIONAL,
                              parameter.VAR_KEYWORD):
            continue
        found[name] = parameter.default is inspect.Parameter.empty
    return found


def arguments_for(goal: Goal) -> dict:
    """
    What to call the chosen capability with.

    Only fills parameters the capability actually has, and only from what the
    goal actually knows. A planner that invents arguments produces tasks that
    fail on contact - `apps_open` with no name is not a plan, it is a
    guaranteed failure with a task id.
    """
    if not goal.capability:
        return {}
    wanted = parameters_of(goal.capability)
    arguments: dict = {}
    for name, required in wanted.items():
        lowered = name.lower()
        if lowered in _FROM_ENTITY and goal.entity:
            arguments[name] = goal.entity
        elif lowered in _FROM_QUERY:
            arguments[name] = _wanted(goal.intent)
        elif lowered in _FROM_PATH:
            # What the person named is used as named (INTENT-001); a file of
            # FRIDAY's own only when they named none.
            named = _named_path(goal.intent)
            if named:
                arguments[name] = named
            elif required:
                arguments[name] = _note_path(goal)
        elif lowered == "content" and goal.target == "FILE":
            given = _named_content(goal.intent)
            if given:
                arguments[name] = given
            elif not _named_path(goal.intent):
                # FRIDAY's own note file says what it is for. A file the
                # person named gets no words they did not give.
                arguments[name] = f"Created by Friday for: {goal.intent}"
    return arguments


def _note_path(goal: Goal) -> str:
    """A file this run owns, named for what it is."""
    stem = re.sub("[^a-z0-9]+", "-",
                  _wanted(goal.intent).lower()).strip("-")
    return f"friday-{stem or 'note'}.txt"


#: "at most 20 changes", "no more than 3 emails": a ceiling on side effects.
_LIMIT = re.compile(
    r"\b(?:at most|no more than|up to|only)\s+(\d+)\s+(?:changes?|edits?|writes?|"
    r"updates?|emails?|messages?|posts?|files?)\b", re.IGNORECASE)
#: "do not delete anything", "never send it", "don't delete config.yaml".
_PROHIBITION = re.compile(r"\b(?:do not|don't|dont|never)\s+(.+)$", re.IGNORECASE)
#: What a prohibition is about when it names no particular thing.
_ANY_OBJECT = re.compile(
    r"^\s*(?:anything|everything|any\b|all\b|files?\b|emails?\b|messages?\b|"
    r"posts?\b|it\b|them\b)", re.IGNORECASE)


#: "currency must be USD", "keep the currency as USD", "the region stays eu-west-1".
_INVARIANT = re.compile(
    r"\b(?:keep\s+(?:the\s+)?(\w+)\s+(?:as|at|in)|(\w+)\s+(?:must\s+(?:be|stay)|"
    r"has\s+to\s+(?:be|stay)|stays?|is\s+always))\s+"
    r"(\"[^\"]+\"|'[^']+'|[A-Za-z0-9][\w.-]*)",
    re.IGNORECASE)
#: Words an invariant cannot be about: "this must be done", "it stays open".
_NOT_A_FIELD = frozenset((
    "it", "this", "that", "these", "those", "which", "what", "who", "there",
    "everything", "anything", "something", "nothing", "all", "one", "i",
    "you", "we", "they", "he", "she"))


def _invariant(found: re.Match) -> dict | None:
    """
    An invariant from one _INVARIANT match, or None for ordinary prose ("the
    email must be short"): the field must be a noun, not a pronoun, and the
    value must look like a code - quoted, two or more capitals as written
    ("USD"), or holding a digit ("eu-west-1").
    """
    name = (found.group(1) or found.group(2)).lower()
    value = found.group(3).rstrip(".")
    if name in _NOT_A_FIELD:
        return None
    if value[0] in "\"'":
        return {"field": name, "equals": value[1:-1]}
    if sum(ch.isupper() for ch in value) >= 2 or any(ch.isdigit() for ch in value):
        return {"field": name, "equals": value}
    return None
#: "once the invoice is approved", "after the order has been paid": a state
#: that must hold first. Only a recognised state word counts, so "when it is
#: ready" stays prose.
_STATE_WORDS = ("approved", "paid", "signed", "confirmed", "verified",
                "reviewed", "authorized", "authorised", "accepted", "released")
_PRECONDITION = re.compile(
    r"\b(?:once|after|when|as\s+soon\s+as|only\s+if|provided)\s+(?:the\s+)?(\w+)\s+"
    r"(?:is|are|was|has\s+been|have\s+been|gets|get)\s+(?:been\s+)?("
    + "|".join(_STATE_WORDS) + r")\b", re.IGNORECASE)


def _requirement(found: re.Match) -> dict | None:
    """A state precondition from one _PRECONDITION match: the noun's status."""
    name = found.group(1).lower()
    if name in _NOT_A_FIELD:
        return None
    return {"field": f"{name}_status", "equals": found.group(2).lower()}


#: "retry only the failed ones", "resume the failed items only".
_RESUME_FAILED = re.compile(
    r"\b(?:only\s+(?:retry|redo|resume)\s+(?:the\s+)?failed|"
    r"(?:retry|redo|resume)\s+(?:only\s+)?(?:the\s+)?failed\s+(?:ones|items?))",
    re.IGNORECASE)


def governance_for(plan: Plan, text: str) -> dict:
    """
    What the person forbade or limited, in the form the objective enforces
    (BCON-001): an action class forbidden outright ("do not delete
    anything"), a named thing protected from one ("don't delete
    config.yaml"), and a ceiling on changes ("at most 20 changes"); and what
    must hold - a data invariant ("currency must be USD") and the declared
    recovery ("retry only the failed ones"). A prohibition about something
    unnamed and specific ("don't touch my photos") stays a recorded
    constraint.
    """
    from friday import contracts as c

    governance: dict = {}
    limit = _LIMIT.search(text or "")
    if limit:
        governance["max_side_effects"] = int(limit.group(1))
    forbidden: list[str] = []
    protected: dict[str, list[str]] = {}
    for rule in plan.safety:
        found = _PROHIBITION.search(rule)
        if not found:
            continue
        verb, _, rest = found.group(1).partition(" ")
        kind = c.action_class(verb) or c.action_class(found.group(1)[:12])
        if not kind:
            continue
        named = _named_path(rest)
        if named:
            protected.setdefault(kind, [])
            if named not in protected[kind]:
                protected[kind].append(named)
        elif not rest.strip() or _ANY_OBJECT.match(rest):
            if kind not in forbidden:
                forbidden.append(kind)
    invariants = [rule for rule in map(_invariant, _INVARIANT.finditer(text or ""))
                  if rule]
    if invariants:
        governance["invariants"] = invariants
    requires = [rule for rule in map(_requirement, _PRECONDITION.finditer(text or ""))
                if rule]
    if requires:
        governance["requires"] = requires
    if _RESUME_FAILED.search(text or ""):
        governance["recovery"] = c.RESUME_FAILED_ITEMS_ONLY
    if forbidden:
        governance["forbidden"] = sorted(forbidden)
    if protected:
        governance["protected"] = protected
    return governance


def contract_for(plan: Plan, text: str, *, assumptions: tuple[str, ...] = (),
                 open_questions: tuple[str, ...] = ()):
    """
    `assumptions` and `open_questions` come from requirements.ground_for: what
    was decided on the person's behalf and what is still unanswered.

    The objective's contract, as this reading of `text` found it (PRD v7
    §10.3): the whole request as the goal, why (purpose), what must not
    happen and how the work must be done (hard constraints), how to speak
    (preferences), what done means for each goal (success criteria), and
    the names and words the person gave, which the plan must use verbatim.
    """
    from friday import contracts as c

    literals: list[str] = []
    for goal in plan.goals:
        if not goal.capability:
            continue
        for found in (_named_path(goal.intent), _named_content(goal.intent)):
            if found and found not in literals:
                literals.append(found)
    return c.ObjectiveContract(
        goal=text,
        purpose=tuple(plan.purpose),
        hard_constraints=tuple(plan.safety) + tuple(plan.constraints),
        preferences=tuple(plan.presentation) + tuple(plan.reporting),
        exact_literals=tuple(literals),
        success_criteria=tuple(goal.intent.strip() for goal in plan.goals
                               if goal.capability),
        assumptions=tuple(assumptions),
        open_questions=tuple(open_questions),
        governance=governance_for(plan, text),
    )


#: Every parameter that names a place a capability works on. `_FROM_PATH` is
#: what `arguments_for` can fill; the rest it cannot, so a task whose
#: capability takes one of them and was given none has an unknown surface.
_PATH_PARAMETERS = _FROM_PATH + ('source', 'root', 'directory', 'folder')


def surface_for(capability_id: str, arguments: dict) -> list[str]:
    """
    The files one capability call works on (ORCH-001), or [] when unknown.

    Read off the path parameters the capability takes. Unknown is the safe
    answer - the wave scheduler runs an unknown task alone - so it is given
    whenever any path parameter has no usable value: naming only some of
    what a call touches would let two writers share a file.
    """
    paths: list[str] = []
    for name in parameters_of(capability_id):
        if name.lower() not in _PATH_PARAMETERS:
            continue
        value = arguments.get(name)
        if not isinstance(value, str) or not value.strip():
            return []
        paths.append(value.strip())
    return paths


def task_specs(plan: Plan) -> list[dict]:
    """
    A validated Plan as the task specs `compile_objective` persists.

    Goal ids become plan-facing task ids in order, so the dependencies a goal
    carries survive into the graph. A goal that resolved to nothing is not
    silently dropped and not forced onto a capability either: it becomes the
    unmapped marker, which compile records as immediately FAILED with a
    reason, so the run says out loud that a step was not understood.
    """
    from friday.toolsets.objectives import UNMAPPED_CAPABILITY

    order = {goal.goal_id: f"t{index}"
             for index, goal in enumerate(plan.goals, start=1)}
    specs: list[dict] = []
    for goal in plan.goals:
        dependencies = [order[dep] for dep in goal.depends_on
                        if dep in order]
        if goal.children or goal.capability:
            spec = {
                "capability": goal.capability,
                "arguments": arguments_for(goal),
                "dependencies": dependencies,
                "reason": goal.intent,
            }
            if goal.children:
                # A group is a view over its leaves and compile keeps no
                # surface on them, so a grouped plan stays serial.
                spec["children"] = list(goal.children)
            else:
                # Left off when unknown: the wave scheduler runs it alone.
                surface = surface_for(goal.capability, spec["arguments"])
                if surface:
                    spec["surface"] = surface
            specs.append(spec)
        else:
            specs.append({
                "capability": UNMAPPED_CAPABILITY,
                "arguments": {"clause": goal.intent},
                "dependencies": dependencies,
                "reason": goal.why or "no capability matches this goal",
            })
    return specs


# --- what a piece of a request is ------------------------------------------


# ---------------------------------------------------------------------------
# Stage 1 - interpretation
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Stage 2 - resolution
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Stage 3 - validation
# ---------------------------------------------------------------------------
