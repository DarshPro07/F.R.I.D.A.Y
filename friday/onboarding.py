"""
Progressive onboarding: who the user is, written where production reads it.

FR-ONB-004 and FR-POL-002. The PRD asks for a conversation rather than a
form, a user who is operational in roughly five to ten minutes, deep
personalisation that continues over days instead of blocking first use, and
- the part that decides the design - a profile that lands in *inspectable
memory and policy* rather than in a hidden system prompt.

That last requirement rules out the obvious implementation. A prompt
paragraph assembled from the answers would be faster to write, would work,
and would be unauditable: the user could not see what FRIDAY believed about
them, could not correct one fact without rewriting the paragraph, and could
not tell a remembered answer from a model's inference about them. So every
answer becomes a memory row with a subject, a source and a workspace, and
the autonomy answer becomes policy through the existing slider.

Three boundaries this module does NOT cross, because each already has an
owner and a second implementation would become a competing source of truth:

* `friday/first_run.py` owns the ten operating-contract questions (autonomy,
  confirmation boundary, cost mode, build quality, computer authority, auto
  communications, spend envelopes, learning consent, progress detail). This
  module asks about the user's WORLD and delegates the contract to it.
* `friday/user_policy.py` owns delegation, versioning and the constitutional
  refusal. Policy is compiled here by calling `set_level`, never by writing
  policy rows.
* `friday/store.py` owns durable memory. Profile answers are `remember()`
  calls, scoped to the workspace, with `source="onboarding"` so their origin
  is visible in provenance forever.

What "progressive" means concretely: the stages are ordered, `ESSENTIAL`
names the ones that make FRIDAY useful, and `pending()` returns the next
unanswered question in that order. Nothing is mandatory - a skipped question
is recorded as skipped so it is not asked again every session, and can be
answered later in plain conversation.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

#: Memory subject prefix. Every profile fact shares it, so the whole profile
#: is one `search_memories("profile.")` away and a user asking "what do you
#: know about me" gets an answer from the store rather than from a model.
SUBJECT_PREFIX = "profile."


@dataclass(frozen=True)
class Question:
    """One thing to learn. `field` is the profile key and the memory subject
    suffix; `kind` is the memory kind the answer becomes."""

    id: str
    field: str
    ask: str
    kind: str = "FACT"
    example: str = ""
    #: A question whose answer is a list rather than a sentence (apps,
    #: websites, folders, team). Stored as JSON so the UI can show chips and
    #: a later answer can add one without restating all of them.
    multi: bool = False


@dataclass(frozen=True)
class Stage:
    """A group of questions with a purpose the user can see. Stages exist so
    onboarding can stop at a sensible place and resume, rather than being a
    flat list someone abandons at question nine."""

    id: str
    title: str
    why: str
    questions: tuple[Question, ...]


#: The interview. Ordered: identity first because everything else is easier
#: to ask once FRIDAY can use the person's name and knows what they do.
STAGES: tuple[Stage, ...] = (
    Stage("who", "Who you are",
          "so FRIDAY can address you correctly and judge what matters",
          (Question("name", "call_me", "What should I call you?"),
           Question("profession", "profession",
                    "What do you do? Your role or profession is enough."),
           Question("company", "company",
                    "Do you run or work for a company or brand? What is it called?"),
           Question("languages", "languages",
                    "Which languages should FRIDAY speak with you?",
                    multi=True, example="English, Hindi"))),

    Stage("work", "What your work looks like",
          "so FRIDAY can create the right specialists instead of a generic team",
          (Question("typical_work", "typical_work",
                    "What does a normal working day involve for you?"),
           Question("goals", "goals", "What would you most like to spend less time on?",
                    kind="PREFERENCE", multi=True),
           Question("recurring", "recurring_tasks",
                    "Anything you do repeatedly that FRIDAY could learn?",
                    multi=True),
           Question("hours", "working_hours",
                    "Roughly when do you work? FRIDAY uses it to decide when to "
                    "interrupt and when to get on with things quietly.",
                    kind="PREFERENCE"))),

    Stage("tools", "What you already use",
          "so FRIDAY works in your accounts rather than asking you for API keys",
          (Question("apps", "applications",
                    "Which applications do you use most?", multi=True,
                    example="Chrome, Shopify admin, Gmail, Excel"),
           Question("websites", "websites",
                    "Which websites or portals does your work happen on?",
                    multi=True),
           Question("folders", "folders",
                    "Which folders should FRIDAY know about?", multi=True),
           Question("model_accounts", "model_accounts",
                    "Which AI accounts do you already pay for or have access to?",
                    multi=True, example="Claude, ChatGPT, Gemini"))),

    Stage("people", "Who else is involved",
          "so FRIDAY knows whose decisions outrank whose",
          (Question("team", "team", "Who else works on this with you?",
                    multi=True),
           Question("customers", "customer_types",
                    "Who are your customers or the people you serve?",
                    multi=True))),

    Stage("limits", "What must stay private",
          "so the privacy rules are yours rather than a default",
          (Question("private", "private_topics",
                    "Anything FRIDAY must keep off cloud AI entirely?",
                    kind="PREFERENCE", multi=True),
           Question("sensitive", "sensitive_data",
                    "What kinds of sensitive information does your work involve?",
                    kind="PREFERENCE", multi=True))),
)

#: The stages that make FRIDAY useful. The PRD's five-to-ten-minute target is
#: about these; everything after them is personalisation that continues over
#: days, and `complete_enough()` is what says so out loud rather than leaving
#: a user to guess whether they may stop.
ESSENTIAL = ("who", "work", "tools")

#: Recorded against a field the user declined, so it is not asked again every
#: session. Declining is an answer.
SKIPPED = "__skipped__"


def _questions() -> dict[str, Question]:
    return {q.field: q for stage in STAGES for q in stage.questions}


@dataclass
class Profile:
    """What FRIDAY knows about this workspace's owner, read back out of
    memory rather than kept in a second file.

    Reading from the store rather than from a cached dict is deliberate: if
    the user deletes a memory in the control room, the profile must change.
    A cache would let the UI say "forgotten" while onboarding still believed
    it.
    """

    workspace_id: str
    answers: dict[str, object] = field(default_factory=dict)
    skipped: tuple[str, ...] = ()

    def get(self, field_name: str, default=None):
        value = self.answers.get(field_name, default)
        return default if value == SKIPPED else value

    def as_dict(self) -> dict:
        return {"workspace_id": self.workspace_id,
                "answers": {k: v for k, v in self.answers.items() if v != SKIPPED},
                "skipped": list(self.skipped)}


class Onboarding:
    """The interview, over one workspace's memory."""

    def __init__(self, store=None, *, workspace_id: str | None = None,
                 policy=None) -> None:
        if store is None:
            from friday.store import Store
            store = Store()
        from friday.workspace import resolve
        self.store = store
        self.workspace_id = resolve(workspace_id)
        self._policy = policy

    # -- policy ------------------------------------------------------------

    @property
    def policy(self):
        """The versioned delegation store, made on demand.

        Lazily, because onboarding is often constructed just to read the
        profile, and opening the policy database for a read that never
        touches it is work nobody asked for.
        """
        if self._policy is None:
            from friday.user_policy import UserPolicy
            self._policy = UserPolicy()
        return self._policy

    # -- reading -----------------------------------------------------------

    def profile(self) -> Profile:
        rows = self.store.search_memories(SUBJECT_PREFIX, limit=500,
                                          workspace_id=self.workspace_id)
        answers: dict[str, object] = {}
        skipped: list[str] = []
        known = _questions()
        for row in rows:
            subject = str(row.get("subject") or "")
            if not subject.startswith(SUBJECT_PREFIX):
                continue
            name = subject[len(SUBJECT_PREFIX):]
            if name not in known:
                continue
            raw = row.get("value")
            if raw == SKIPPED:
                skipped.append(name)
                answers[name] = SKIPPED
                continue
            if known[name].multi:
                try:
                    answers[name] = json.loads(str(raw))
                except ValueError:
                    answers[name] = [str(raw)]
            else:
                answers[name] = raw
        return Profile(workspace_id=self.workspace_id, answers=answers,
                       skipped=tuple(sorted(skipped)))

    def pending(self, *, essential_only: bool = False) -> list[dict]:
        """The questions still worth asking, in interview order.

        A question whose answer is already known - or was declined - is not
        returned. This is the anti-questionnaire rule: the interview shrinks
        as FRIDAY learns, including from things the user simply told it in
        conversation.
        """
        known = set(self.profile().answers)
        out = []
        for stage in STAGES:
            if essential_only and stage.id not in ESSENTIAL:
                continue
            for question in stage.questions:
                if question.field in known:
                    continue
                out.append({"stage": stage.id, "stage_title": stage.title,
                            "why": stage.why, "id": question.id,
                            "field": question.field, "ask": question.ask,
                            "example": question.example, "multi": question.multi})
        return out

    def complete_enough(self) -> bool:
        """Whether FRIDAY can be useful now.

        True once every essential question has an answer or a decline. The
        PRD's five-to-ten-minute promise is this predicate, and a user is
        told they may stop rather than left to wonder.
        """
        return not self.pending(essential_only=True)

    def progress(self) -> dict:
        answered = len(self.profile().answers)
        total = sum(len(s.questions) for s in STAGES)
        essential_left = len(self.pending(essential_only=True))
        return {"answered": answered, "total": total,
                "essential_remaining": essential_left,
                "useful_now": essential_left == 0,
                "stages": [{"id": s.id, "title": s.title,
                            "remaining": len([q for q in s.questions
                                              if q.field not in self.profile().answers])}
                           for s in STAGES]}

    # -- writing -----------------------------------------------------------

    def record(self, answers: dict) -> dict:
        """Persist answers as inspectable memory.

        Every row carries `source="onboarding"`, so provenance can always
        distinguish a thing the user said during setup from a thing FRIDAY
        later inferred about them. A repeated answer supersedes the earlier
        one through the store's normal supersession, which keeps the history
        rather than overwriting it.
        """
        known = _questions()
        unknown = [name for name in answers if name not in known]
        if unknown:
            raise KeyError(
                f"no onboarding question writes {unknown!r}; known fields: "
                f"{sorted(known)}")
        written = []
        for name, value in answers.items():
            question = known[name]
            if value is None:
                continue
            if value == SKIPPED:
                stored = SKIPPED
            elif question.multi:
                items = value if isinstance(value, (list, tuple)) else [value]
                stored = json.dumps([str(v).strip() for v in items if str(v).strip()])
            else:
                stored = str(value).strip()
                if not stored:
                    continue
            self.store.remember(
                SUBJECT_PREFIX + name, stored, kind=question.kind,
                source="onboarding", scope="user", memory_type="user",
                importance=0.8, workspace_id=self.workspace_id)
            written.append(name)
        return {"status": "recorded", "fields": written,
                "workspace_id": self.workspace_id,
                "useful_now": self.complete_enough()}

    def skip(self, field_name: str, *, why: str = "") -> dict:
        """Record a decline so it is not asked again. `why` is kept in the
        memory source, because "they would rather not say" is itself useful
        context for a later conversation."""
        known = _questions()
        if field_name not in known:
            raise KeyError(f"no onboarding question writes {field_name!r}")
        self.store.remember(
            SUBJECT_PREFIX + field_name, SKIPPED, kind=known[field_name].kind,
            source=f"onboarding: declined{(' - ' + why) if why else ''}",
            scope="user", memory_type="user", importance=0.3,
            workspace_id=self.workspace_id)
        return {"status": "skipped", "field": field_name}

    # -- the workspace and its policy --------------------------------------

    def open_workspace(self, *, label: str = "", kind: str = "user",
                       make_active: bool = True) -> str:
        """Register the workspace this profile belongs to.

        Derived from the company name when there is one, because that is the
        boundary a person recognises - an agency running three brands ends up
        with three workspaces named after the brands rather than three
        opaque ids.
        """
        from friday import workspace as W
        label = label or str(self.profile().get("company") or "") or "This computer"
        workspace_id = self.workspace_id
        if workspace_id == W.DEFAULT_ID and label not in ("", "This computer"):
            workspace_id = W.slug(label)
        self.store.register_workspace(workspace_id, label=label, kind=kind,
                                      make_active=make_active)
        self.workspace_id = workspace_id
        return workspace_id

    def compile_policy(self, *, level: str = "", reason: str = "") -> dict:
        """Turn the profile into policy (FR-POL-002).

        Policy comes from what the user said, not from a profession template:
        the level is whatever they chose, and the default when they have not
        chosen is `Ask Before Acting` - which is exactly the existing
        defaults, so a user who answers nothing gets the behaviour the
        product always had rather than a guess made on their behalf.

        Templates may accelerate this later; they must stay editable, and
        the audit trail in `user_policy_events` is what makes that true.
        """
        from friday.user_policy import LEVELS
        chosen = level or self._level_from_profile()
        if chosen not in LEVELS:
            raise ValueError(f"unknown autonomy level {chosen!r}; known: {list(LEVELS)}")
        note = reason or "compiled from onboarding"
        result = self.policy.set_level(chosen, reason=note)
        return {"level": chosen, "reason": note, "policy": result}

    def _level_from_profile(self) -> str:
        """The level implied by the operating contract, if the user set one.

        Read from `first_run`'s contract rather than asked again here: two
        places asking "how autonomous should I be" is how the two answers
        end up disagreeing.
        """
        from friday.first_run import FirstRunContract
        try:
            contract = FirstRunContract().read()
        except Exception:
            contract = {}
        said = str(contract.get("autonomy") or "").strip().lower()
        if said in ("act", "yes", "auto", "autonomous"):
            return "Autonomous Routine Work"
        if said in ("ask-first", "ask", "confirm"):
            return "Ask Before Acting"
        if said in ("draft", "drafts"):
            return "Draft"
        if said in ("advise", "advice"):
            return "Advise"
        return "Ask Before Acting"

    # -- what the profile implies ------------------------------------------

    def suggested_workers(self) -> list[dict]:
        """Specialist roles this profile actually justifies (FR-AGT-001).

        Deliberately a SUGGESTION and deliberately small. The PRD forbids
        instantiating a fixed swarm at install, and the owner's direction is
        that workers are created once FRIDAY knows the work - so this
        returns what the answers support, with the answer that supports it,
        and creates nothing.
        """
        profile = self.profile()
        text = " ".join(str(v) for v in profile.answers.values()).lower()
        out: list[dict] = []

        def suggest(role: str, specialty: str, because: str) -> None:
            out.append({"role": role, "specialty": specialty, "because": because})

        if any(word in text for word in ("shopify", "store", "ecommerce",
                                         "e-commerce", "catalogue", "catalog",
                                         "product listing", "myntra", "amazon")):
            suggest("Storefront operator", "catalogue and orders",
                    "the profile names an online store")
        if any(word in text for word in ("customer", "support", "refund",
                                         "complaint", "ticket")):
            suggest("Customer correspondent", "support replies and escalation",
                    "the profile names customers or support work")
        if any(word in text for word in ("content", "instagram", "social",
                                         "post", "video", "marketing", "seo")):
            suggest("Content producer", "drafting and publishing",
                    "the profile names content or social work")
        if any(word in text for word in ("code", "developer", "repository",
                                         "repo", "next.js", "website", "deploy")):
            suggest("Engineer", "code changes and review",
                    "the profile names software work")
        if any(word in text for word in ("report", "analytics", "dashboard",
                                         "numbers", "revenue", "spreadsheet")):
            suggest("Analyst", "reporting and anomalies",
                    "the profile names reporting work")
        return out


def state_root_marker(root: Path | str | None = None) -> Path:
    """Where the fact that onboarding finished is recorded.

    A marker file rather than a memory row, because the launcher's first-run
    check has to answer "has this install been set up" before it opens a
    database, and a check that needs the thing it is checking for is not a
    check.
    """
    if root is None:
        from friday.config import STATE_ROOT
        root = STATE_ROOT
    return Path(root) / "config" / "onboarded.json"

# ---------------------------------------------------------------------------
# The owner's console
# ---------------------------------------------------------------------------
#
# The interview's natural front end is a conversation - voice or the control
# room - and neither is a place a packaged install can be driven from before
# it is set up. So the same operations exist as subcommands, the way
# `friday.skills.cli` exposes the skill layer:
#
#     FRIDAY.exe -m friday.onboarding status
#     FRIDAY.exe -m friday.onboarding next
#     FRIDAY.exe -m friday.onboarding answer profession "I run a clothing brand"
#     FRIDAY.exe -m friday.onboarding skip sensitive_data --why "rather not say"
#     FRIDAY.exe -m friday.onboarding workspace --kind brand
#     FRIDAY.exe -m friday.onboarding policy --level "Ask Before Acting"
#     FRIDAY.exe -m friday.onboarding workers
#
# This is also what makes the module honestly reachable: argparse dispatch is
# a production path the reachability walk cannot follow, which is why
# `friday/onboarding` carries a DECLARED entry naming this console rather
# than an exemption per function.


def console(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(
        prog="friday.onboarding",
        description="Ask, record and inspect what FRIDAY knows about you.")
    parser.add_argument("--workspace", default="",
                        help="the workspace to read or write (default: the active one)")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("status", help="how far the interview has got")
    ask = sub.add_parser("next", help="the next question to ask")
    ask.add_argument("--essential", action="store_true",
                     help="only the questions needed to be useful")
    answer = sub.add_parser("answer", help="record an answer")
    answer.add_argument("field")
    answer.add_argument("value", nargs="+")
    skipped = sub.add_parser("skip", help="record a declined question")
    skipped.add_argument("field")
    skipped.add_argument("--why", default="")
    sub.add_parser("profile", help="print what is known")
    ws = sub.add_parser("workspace", help="register this profile's workspace")
    ws.add_argument("--label", default="")
    ws.add_argument("--kind", default="user")
    pol = sub.add_parser("policy", help="compile policy from the profile")
    pol.add_argument("--level", default="")
    sub.add_parser("workers", help="the specialists this profile justifies")

    args = parser.parse_args(argv)
    interview = Onboarding(workspace_id=args.workspace or None)

    if args.command == "status":
        progress = interview.progress()
        print(f"workspace: {interview.workspace_id}")
        print(f"answered {progress['answered']} of {progress['total']}; "
              f"{'useful now' if progress['useful_now'] else str(progress['essential_remaining']) + ' essential question(s) left'}")
        for stage in progress["stages"]:
            print(f"  {stage['id']:<8} {stage['title']:<28} {stage['remaining']} left")
        return 0

    if args.command == "next":
        pending = interview.pending(essential_only=args.essential)
        if not pending:
            print("nothing left to ask" + ("" if args.essential else " - the profile is complete"))
            return 0
        question = pending[0]
        print(f"[{question['stage_title']}] {question['ask']}")
        if question["example"]:
            print(f"  for example: {question['example']}")
        print(f"  ({question['why']})")
        print(f"  answer with: answer {question['field']} ...")
        return 0

    if args.command == "answer":
        value = " ".join(args.value)
        field_value = [v.strip() for v in value.split(",")] if "," in value else value
        out = interview.record({args.field: field_value})
        print(f"recorded {args.field}"
              + ("; you have enough for FRIDAY to be useful" if out["useful_now"] else ""))
        return 0

    if args.command == "skip":
        interview.skip(args.field, why=args.why)
        print(f"skipped {args.field}; it will not be asked again")
        return 0

    if args.command == "profile":
        profile = interview.profile()
        for name, value in sorted(profile.answers.items()):
            if value == SKIPPED:
                continue
            print(f"{name:<18} {value}")
        if profile.skipped:
            print(f"declined: {', '.join(profile.skipped)}")
        return 0

    if args.command == "workspace":
        workspace_id = interview.open_workspace(label=args.label, kind=args.kind)
        print(f"workspace {workspace_id} registered and active")
        return 0

    if args.command == "policy":
        out = interview.compile_policy(level=args.level)
        changed = out["policy"].get("changed_domains") or []
        print(f"autonomy level: {out['level']}")
        print(f"changed: {', '.join(changed) if changed else 'nothing (already at that level)'}")
        return 0

    if args.command == "workers":
        suggestions = interview.suggested_workers()
        if not suggestions:
            print("nothing to suggest yet - the profile does not say enough")
            return 0
        for suggestion in suggestions:
            print(f"{suggestion['role']:<24} {suggestion['specialty']:<28} "
                  f"({suggestion['because']})")
        return 0

    parser.error(f"unknown command {args.command!r}")
    return 2


if __name__ == "__main__":          # pragma: no cover - the console entry
    raise SystemExit(console())
