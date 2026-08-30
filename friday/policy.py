"""
Policy engine v1 (§20).

Deterministic authorization for tool calls. Three decisions:

    AUTO  - run it, no prompt
    ASK   - require explicit user approval first
    DENY  - refuse; only an out-of-band escalation can change this

The one rule that shapes the API: **the learned user model must never grant
authorization.** Preference learning and security are separate systems, and
the separation has to be structural rather than remembered - so `decide()`
takes a tool id and nothing else. There is no parameter through which a
preference, a persona, a memory row or a conversation could reach it, which
means no future caller can accidentally wire them together.

Mark-L's ApprovalPolicy was the starting reference (a HIGH_RISK set plus a
`gate()` returning a reason). It is binary - approved or not - with no DENY
tier and no notion of an action that must never be auto-approved. This is an
independent implementation with the three tiers §20 asks for.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

AUTO = "AUTO"
ASK = "ASK"
DENY = "DENY"

#: Requires a human to say yes, and no autonomy mode says it for them.
#:
#: ASK exists so a person can approve something, and FULL autonomy turns every
#: ASK into a yes - which is right for the volume, for opening an app, for
#: writing a file on his own machine. It is not right for shutting the machine
#: down. That loses unsaved work in every application at once, kills Friday
#: mid-sentence, and cannot be undone by another tool call.
#:
#: What makes this necessary rather than fussy is the routing evidence. Four
#: batches in a row, a request landed on a capability that shared its
#: vocabulary with the intended one - "what's playing right now" reached a
#: window arranger, "set a reminder" reached the brightness control. A
#: misrouted "shut it down" when he meant the music is not hypothetical here,
#: and AUTO would carry it out.
#:
#: Distinct from DENY, which means never and cannot be granted at all.
CONFIRM = "CONFIRM"
DECISIONS = (AUTO, ASK, CONFIRM, DENY)

# Categories, ordered loosest to strictest.
READ_LOCAL_SAFE = "READ_LOCAL_SAFE"
MEMORY_READ = "MEMORY_READ"
WEB_SEARCH = "WEB_SEARCH"
SAFE_APP_OPEN = "SAFE_APP_OPEN"
MEMORY_WRITE = "MEMORY_WRITE"
APP_CLOSE = "APP_CLOSE"
DEVICE_SETTING = "DEVICE_SETTING"
CLIPBOARD_WRITE = "CLIPBOARD_WRITE"
MEDIA_CONTROL = "MEDIA_CONTROL"
SCREEN_CAPTURE = "SCREEN_CAPTURE"
CAMERA_CAPTURE = "CAMERA_CAPTURE"
REMINDER = "REMINDER"
BROWSER_CONTROL = "BROWSER_CONTROL"
BROWSER_AUTOMATION = "BROWSER_AUTOMATION"
FILE_WRITE = "FILE_WRITE"
COMMAND_EXECUTION = "COMMAND_EXECUTION"
DELETE = "DELETE"

#: Stopping things, split into what each one actually costs.
#:
#: These were one category, `POWER_ACTION`, which meant ending an application
#: and turning off the machine shared a policy - so one change moved both, and
#: neither could be described honestly. Asking Word to close is not the same
#: decision as shutting down mid-render, and a table that cannot tell them
#: apart cannot express "this yes was for that one".
GRACEFUL_PROCESS_CLOSE = "GRACEFUL_PROCESS_CLOSE"
FORCE_PROCESS_TERMINATION = "FORCE_PROCESS_TERMINATION"
SESSION_LOCK = "SESSION_LOCK"
SLEEP = "SLEEP"
HIBERNATE = "HIBERNATE"
SHUTDOWN = "SHUTDOWN"
RESTART = "RESTART"
FORCED_SHUTDOWN = "FORCED_SHUTDOWN"
SYSTEM_CRITICAL_PROCESS_TERMINATION = "SYSTEM_CRITICAL_PROCESS_TERMINATION"

#: Calling back a shutdown that has not happened yet.
#:
#: AUTO, and deliberately so. Stopping a destructive thing is not itself
#: destructive, and putting a confirmation between the person and "no, wait"
#: would gate the one action that most needs to be instant. The asymmetry is
#: the point: hard to start, trivial to stop.
POWER_CANCEL = "POWER_CANCEL"

#: The old name, kept so nothing that still imports it breaks. New code uses
#: the specific category.
POWER_ACTION = "POWER_ACTION"
SECRET_READ = "SECRET_READ"

#: The multi-step objective run book (Phase 3). Starting, reading, pausing,
#: resuming or cancelling a run changes only durable rows Friday wrote
#: itself, and every transition is recorded as an event. Self-owned state,
#: the same shape as REMINDER - and stopping a job is instant, the same
#: asymmetry as POWER_CANCEL: hard to start, trivial to stop.
OBJECTIVE_CONTROL = "OBJECTIVE_CONTROL"

DEFAULT_POLICY: dict[str, str] = {
    READ_LOCAL_SAFE: AUTO,
    MEMORY_READ: AUTO,
    WEB_SEARCH: AUTO,
    SAFE_APP_OPEN: AUTO,
    MEMORY_WRITE: AUTO,
    APP_CLOSE: ASK,        # closing an app can lose unsaved work
    DEVICE_SETTING: AUTO,  # volume/brightness are trivially reversible
    CLIPBOARD_WRITE: ASK,  # overwrites something the user put there
    # Vision is separated into two categories on purpose. Both default to AUTO
    # because the user invokes them by asking ("look at this"), and every
    # capture leaves an artifact on disk so nothing happens unseen. They are
    # split so a user who wants the camera gated can set CAMERA_CAPTURE to ASK
    # without also gating screenshots.
    # Play, pause and skip are trivially reversible by the user, who is
    # sitting right there and can hear the result.
    MEDIA_CONTROL: AUTO,
    SCREEN_CAPTURE: AUTO,
    CAMERA_CAPTURE: AUTO,
    # Setting a reminder is the thing the user just asked for, and cancelling
    # one only removes something ADA created. Registering a scheduled task is
    # a system change, but a narrow and self-owned one.
    REMINDER: AUTO,
    BROWSER_CONTROL: AUTO,  # opening/reading a page is browsing
    # Driving clicks and keystrokes on live sites can log in, post, purchase.
    # It is the single most powerful thing in Phase 1 and is never automatic.
    BROWSER_AUTOMATION: ASK,
    FILE_WRITE: ASK,
    COMMAND_EXECUTION: ASK,
    DELETE: ASK,
    # Asking an application to close is a question the application gets to
    # answer, and it can say no. That is what makes it ASK rather than
    # CONFIRM - and what makes it a lie if the implementation kills instead.
    GRACEFUL_PROCESS_CLOSE: ASK,
    # Not ASK. FULL autonomy turns ASK into a yes, and "shut down" carried
    # out on a misheard or misrouted request is not recoverable by another
    # tool call. Every one of these ends something that cannot be un-ended.
    FORCE_PROCESS_TERMINATION: CONFIRM,
    SESSION_LOCK: CONFIRM,
    SLEEP: CONFIRM,
    HIBERNATE: CONFIRM,
    SHUTDOWN: CONFIRM,
    RESTART: CONFIRM,
    FORCED_SHUTDOWN: CONFIRM,
    POWER_CANCEL: AUTO,
    # The run book is Friday's own rows; driving it is what the user asked
    # for, and pausing or stopping it is never gated.
    OBJECTIVE_CONTROL: AUTO,
    # Never, at any autonomy level. Friday does not end the operating system,
    # itself, or what it is running inside.
    SYSTEM_CRITICAL_PROCESS_TERMINATION: DENY,
    POWER_ACTION: CONFIRM,
    SECRET_READ: DENY,
}

#: Categories a session approval can never upgrade. Escalating these requires
#: changing configuration deliberately, not saying "yes" mid-conversation.
NON_APPROVABLE = frozenset({SECRET_READ, SYSTEM_CRITICAL_PROCESS_TERMINATION})

#: The categories that end something. Used by the provenance gate: an
#: objective Friday read somewhere rather than heard from the person may not
#: reach any of these, whatever the autonomy setting says.
DESTRUCTIVE = frozenset({
    # Closing is in here, and it is the one worth arguing about. An
    # application gets to refuse WM_CLOSE, so closing is not irreversible the
    # way a restart is - which is the whole reason it is ASK rather than
    # CONFIRM. But a page that can close every open application can still cost
    # somebody an afternoon, and no page has any business asking. The tier a
    # capability sits in is about how much a *person's* mistake costs; this
    # set is about what a *page* may reach, and they are different questions.
    GRACEFUL_PROCESS_CLOSE,
    FORCE_PROCESS_TERMINATION, SESSION_LOCK, SLEEP, HIBERNATE, SHUTDOWN,
    RESTART, FORCED_SHUTDOWN, SYSTEM_CRITICAL_PROCESS_TERMINATION,
    POWER_ACTION,
})


def provenance_verdict(tool_id: str, provenance: str) -> Verdict | None:
    """
    Whether where the objective came from rules this out. None if it does not.

    Deliberately **not** a parameter on `PolicyEngine.decide`. That function
    takes a tool id and nothing else, structurally, so that no preference,
    persona or memory can ever reach an authorization decision - and the
    argument "but this parameter is one of the safe ones" is exactly the
    reasoning that rule exists to refuse. The next one would be
    `user_trust_level`, with an equally good story.

    So this is a separate step, run before `decide`, that can only ever
    narrow. It cannot grant anything: the best it returns is None, meaning
    "no objection from me, go and ask the real question".

    A page that says "restart the computer" is text. It is refused here,
    before any confirmation exists, because no answer to that question could
    make it allowed and putting it in front of the person would be theatre.
    """
    if provenance == "PERSON":
        return None
    category = TOOL_CATEGORIES.get(tool_id)
    if category is None or category not in DESTRUCTIVE:
        return None
    return Verdict(
        tool_id, category, DENY,
        f"the objective came from something Friday read rather than from "
        f"you, and {category} is not reachable that way")

# ---------------------------------------------------------------------------
# Autonomy
#
# The guarded defaults above were a mistake in practice. ASK exists so a human
# can say yes - but there is deliberately no self-approval tool (the agent
# could call it unprompted), so in conversation there was no way to say yes at
# all. The agent asked "shall I proceed?", the user said "yes", and the gate
# was still shut. It asked four times in a row. A gate with no key is not a
# safety feature, it is a hang.
#
# So FULL is the default: this is the user's own machine, they issue the
# command, and containment still comes from the places it actually belongs -
# the filesystem jail, the proof-of-work contract, and the fact that every
# action is recorded. DENY is untouched, because that tier is a refusal
# rather than a question.
#
# GUARDED keeps the original behaviour for anyone who wants it, and remains
# fully tested.
# ---------------------------------------------------------------------------

FULL = "full"
GUARDED = "guarded"
AUTONOMY_MODES = (FULL, GUARDED)

DEFAULT_AUTONOMY = os.getenv("ADA_AUTONOMY", FULL).strip().lower()


def resolve_policy(mode: str) -> dict[str, str]:
    """The policy table for an autonomy mode."""
    if mode not in AUTONOMY_MODES:
        raise ValueError(f"unknown autonomy mode {mode!r}; known: {list(AUTONOMY_MODES)}")
    if mode == GUARDED:
        return dict(DEFAULT_POLICY)
    # FULL: every question becomes a yes. Refusals stay refusals, and so do
    # the things that need a human specifically - CONFIRM passes through
    # untouched, which is the whole point of it being a separate decision
    # rather than a stricter flavour of ASK.
    return {category: (AUTO if decision == ASK else decision)
            for category, decision in DEFAULT_POLICY.items()}

#: tool id -> category. A tool with no entry is ASK by default: unknown means
#: unaudited, and unaudited must not mean allowed.
TOOL_CATEGORIES: dict[str, str] = {
    'system.get_info': READ_LOCAL_SAFE,
    'system.list_processes': READ_LOCAL_SAFE,
    'system.resource_usage': READ_LOCAL_SAFE,
    'system.wifi_status': READ_LOCAL_SAFE,
    'system.battery': READ_LOCAL_SAFE,
    'system.disks': READ_LOCAL_SAFE,
    'system.displays': READ_LOCAL_SAFE,
    'system.network': READ_LOCAL_SAFE,
    'windows.list': READ_LOCAL_SAFE,
    'windows.focus': DEVICE_SETTING,
    'windows.minimize': DEVICE_SETTING,
    'windows.restore': DEVICE_SETTING,
    'windows.maximize': DEVICE_SETTING,
    'windows.arrange': DEVICE_SETTING,
    'audio.sessions': READ_LOCAL_SAFE,
    'audio.session_volume': DEVICE_SETTING,
    'audio.session_mute': DEVICE_SETTING,
    'audio.master_volume': DEVICE_SETTING,
    'process.find': READ_LOCAL_SAFE,
    'process.close': GRACEFUL_PROCESS_CLOSE,
    'process.terminate': FORCE_PROCESS_TERMINATION,
    'power.lock': SESSION_LOCK,
    'power.sleep': SLEEP,
    'power.hibernate': HIBERNATE,
    'power.shutdown': SHUTDOWN,
    'power.restart': RESTART,
    'power.force_shutdown': FORCED_SHUTDOWN,
    'power.force_restart': FORCED_SHUTDOWN,
    'power.cancel': POWER_CANCEL,
    'apps.list_known': READ_LOCAL_SAFE,
    'apps.open': SAFE_APP_OPEN,
    'apps.focus': SAFE_APP_OPEN,
    'apps.close': GRACEFUL_PROCESS_CLOSE,
    'volume.get': READ_LOCAL_SAFE,
    'volume.set': DEVICE_SETTING,
    'brightness.get': READ_LOCAL_SAFE,
    'brightness.set': DEVICE_SETTING,
    'clipboard.read': READ_LOCAL_SAFE,
    'clipboard.write': CLIPBOARD_WRITE,
    'objectives.start': OBJECTIVE_CONTROL,
    'objectives.status': OBJECTIVE_CONTROL,
    'objectives.list': OBJECTIVE_CONTROL,
    'objectives.pause': OBJECTIVE_CONTROL,
    'objectives.resume': OBJECTIVE_CONTROL,
    'objectives.cancel': OBJECTIVE_CONTROL,
    'objectives.history': OBJECTIVE_CONTROL,
    'music.search': WEB_SEARCH,
    'music.play': MEDIA_CONTROL,
    'music.play_mood': MEDIA_CONTROL,
    'music.pause': MEDIA_CONTROL,
    'music.resume': MEDIA_CONTROL,
    'music.stop': MEDIA_CONTROL,
    'music.next': MEDIA_CONTROL,
    'music.current': READ_LOCAL_SAFE,
    'vision.screen_capture': SCREEN_CAPTURE,
    'vision.inspect_screen': SCREEN_CAPTURE,
    'vision.camera_frame': CAMERA_CAPTURE,
    'vision.inspect_camera': CAMERA_CAPTURE,
    'reminders.create': REMINDER,
    'reminders.list': READ_LOCAL_SAFE,
    'reminders.cancel': REMINDER,
    'product.process': FILE_WRITE,
    'product.export': FILE_WRITE,
    'product.retry': FILE_WRITE,
    'product.status': READ_LOCAL_SAFE,
    'documents.extract': READ_LOCAL_SAFE,
    'documents.inspect': READ_LOCAL_SAFE,
    'product.result': READ_LOCAL_SAFE,
    'automations.create': REMINDER,
    'automations.delete': REMINDER,
    'automations.run': REMINDER,
    'automations.list': READ_LOCAL_SAFE,
    'automations.history': READ_LOCAL_SAFE,
    'files.read': READ_LOCAL_SAFE,
    'files.roots': READ_LOCAL_SAFE,
    'files.list': READ_LOCAL_SAFE,
    'files.info': READ_LOCAL_SAFE,
    'files.search': READ_LOCAL_SAFE,
    'files.create': FILE_WRITE,
    'files.write': FILE_WRITE,
    'files.edit': FILE_WRITE,
    'files.copy': FILE_WRITE,
    'files.move': FILE_WRITE,
    'files.recycle': FILE_WRITE,
    'web.search': WEB_SEARCH,
    'web.fetch': WEB_SEARCH,
    'web.news': WEB_SEARCH,
    'youtube.find_channel': WEB_SEARCH,
    'youtube.channel_details': WEB_SEARCH,
    'youtube.recent_videos': WEB_SEARCH,
    'youtube.video_details': WEB_SEARCH,
    'workbench.write': FILE_WRITE,
    'workbench.preview': SAFE_APP_OPEN,
    'workbench.list': READ_LOCAL_SAFE,
    'workbench.stop': SAFE_APP_OPEN,
    'identity.profiles': READ_LOCAL_SAFE,
    'identity.open_url': SAFE_APP_OPEN,
    'web.answer': WEB_SEARCH,
    'web.crawl': WEB_SEARCH,
    'web.deep_research': WEB_SEARCH,
    'browser.open': BROWSER_CONTROL,
    'browser.navigate': BROWSER_CONTROL,
    'browser.inspect': BROWSER_CONTROL,
    'browser.close': BROWSER_CONTROL,
    'browser.automate': BROWSER_AUTOMATION,
    'get_world_news': WEB_SEARCH,
    'get_world_finance_news': WEB_SEARCH,
    'search_web': WEB_SEARCH,
    'fetch_url': WEB_SEARCH,
    'get_current_time': READ_LOCAL_SAFE,
    'get_system_info': READ_LOCAL_SAFE,
    'format_json': READ_LOCAL_SAFE,
    'word_count': READ_LOCAL_SAFE,
    'open_world_monitor': SAFE_APP_OPEN,
    'world_monitor.open': BROWSER_CONTROL,
    'open_finance_world_monitor': SAFE_APP_OPEN,
    'memory.recall': MEMORY_READ,
    'memory.search': MEMORY_READ,
    'memory.project_context': MEMORY_READ,
    'memory.projects_list': MEMORY_READ,
    'memory.project_resume': MEMORY_READ,
    'memory.session_recap': MEMORY_READ,
    'memory.remember': MEMORY_WRITE,
    'memory.record_decision': MEMORY_WRITE,
    'ada.ask': MEMORY_WRITE,
    'hermes.delegate': COMMAND_EXECUTION,
    'hermes.status': READ_LOCAL_SAFE,
    'hermes.steer': OBJECTIVE_CONTROL,
    'hermes.interrupt': OBJECTIVE_CONTROL,
    'hermes_delegate': COMMAND_EXECUTION,
    'hermes_status': READ_LOCAL_SAFE,
    'hermes_steer': OBJECTIVE_CONTROL,
    'hermes_interrupt': OBJECTIVE_CONTROL,
    'connector_list': READ_LOCAL_SAFE,
    'connector_describe': READ_LOCAL_SAFE,
    'connector_connect': COMMAND_EXECUTION,
    'connector_verify': READ_LOCAL_SAFE,
    'connector_smoke': COMMAND_EXECUTION,
    'connector_status': READ_LOCAL_SAFE,
    'connector_repair': COMMAND_EXECUTION,
    'capability_families': READ_LOCAL_SAFE,
    'capability_providers': READ_LOCAL_SAFE,
    'capability_health': READ_LOCAL_SAFE,
    'capability_processes': READ_LOCAL_SAFE,
    'capability_use': COMMAND_EXECUTION,
    'brain_recall': READ_LOCAL_SAFE,
    'brain_remember': MEMORY_WRITE,
    'brain_entity': READ_LOCAL_SAFE,
    'brain_forget': MEMORY_WRITE,
    'browser_page_observe': WEB_SEARCH,
    'secrets_begin_entry': READ_LOCAL_SAFE,
    'secrets_complete_entry': READ_LOCAL_SAFE,
    'secrets_list': READ_LOCAL_SAFE,
    'policy_snapshot': READ_LOCAL_SAFE,
    'policy_set': OBJECTIVE_CONTROL,
    'spend_gate': READ_LOCAL_SAFE,
    'spend_envelope_store': OBJECTIVE_CONTROL,
    'contract_pending_questions': READ_LOCAL_SAFE,
    'contract_record': MEMORY_WRITE,
    'operation_create': MEMORY_WRITE,
    'operation_status': READ_LOCAL_SAFE,
    'operation_assign': MEMORY_WRITE,
    'operation_update': MEMORY_WRITE,
    'skill_capture': MEMORY_WRITE,
    'skill_list': READ_LOCAL_SAFE,
    'memory.record_utterance': MEMORY_WRITE,
    'memory.forget': DELETE,
    'profile.learn': MEMORY_WRITE,
    'profile.get': MEMORY_READ,
    'profile.explain': MEMORY_READ,
    'profile.resolve': MEMORY_WRITE,
    'secrets.read': SECRET_READ,
    'secrets.list': SECRET_READ,
    'env.read_secret': SECRET_READ,
}

UNKNOWN_TOOL_DECISION = ASK


@dataclass(frozen=True)
class Verdict:
    tool_id: str
    category: str
    decision: str
    reason: str

    @property
    def allowed(self) -> bool:
        return self.decision == AUTO

    @property
    def needs_approval(self) -> bool:
        return self.decision in (ASK, CONFIRM)

    @property
    def needs_confirmation(self) -> bool:
        """A human has to say yes, and autonomy will not say it for them."""
        return self.decision == CONFIRM

    @property
    def denied(self) -> bool:
        return self.decision == DENY


class PolicyError(PermissionError):
    """A tool call was refused by policy."""


class PolicyEngine:
    def __init__(self, overrides: dict[str, str] | None = None,
                 *, autonomy: str | None = None) -> None:
        self.autonomy = (autonomy or DEFAULT_AUTONOMY).strip().lower()
        self._policy = resolve_policy(self.autonomy)
        for category, decision in (overrides or {}).items():
            if category not in DEFAULT_POLICY:
                raise ValueError(f"unknown policy category {category!r}")
            if decision not in DECISIONS:
                raise ValueError(f"unknown decision {decision!r}")
            self._policy[category] = decision
        self._session_approvals: set[str] = set()

    # -- the whole API takes a tool id and nothing else ---------------------

    def category_of(self, tool_id: str) -> str | None:
        return TOOL_CATEGORIES.get(tool_id)

    def decide(self, tool_id: str) -> Verdict:
        category = self.category_of(tool_id)
        if category is None:
            return Verdict(
                tool_id, "UNKNOWN", UNKNOWN_TOOL_DECISION,
                "tool has no declared policy category; unaudited is not allowed",
            )

        decision = self._policy[category]

        # A session approval settles an ASK and never a CONFIRM.
        #
        # It used to settle both, which made one yes cover every later call to
        # that tool for the rest of the session - "confirmed power actions for
        # the next ten minutes", which is the thing the CONFIRM tier exists to
        # rule out. A confirmation authorises one action on one target with
        # one set of arguments, once; that binding lives in
        # `friday.confirmation` and cannot be expressed by a set of tool ids.
        #
        # CONFIRM is still not DENY. It is granted per action, by a person, at
        # the moment of the action - just not in advance and not in bulk.
        if decision == ASK and tool_id in self._session_approvals:
            return Verdict(tool_id, category, AUTO, "approved earlier this session")

        reasons = {
            AUTO: f"{category} is auto-allowed",
            ASK: f"{category} requires explicit approval",
            CONFIRM: (f"{category} needs the boss to say yes out loud - "
                      f"autonomy does not grant this one"),
            DENY: f"{category} is denied by policy",
        }
        return Verdict(tool_id, category, decision, reasons[decision])

    def approve_for_session(self, tool_id: str) -> None:
        """
        Record that the *user* approved this tool for the session.

        Callers must only invoke this in response to a real human answer.
        Categories in NON_APPROVABLE cannot be approved this way.
        """
        category = self.category_of(tool_id)
        if category is None:
            # You cannot meaningfully approve what has no declared category -
            # nobody knows what it does. This forces TOOL_CATEGORIES to be the
            # single registry rather than an optional annotation.
            raise PolicyError(
                f"{tool_id}: no declared policy category; add it to "
                "TOOL_CATEGORIES before it can be approved"
            )
        if category in NON_APPROVABLE:
            raise PolicyError(
                f"{tool_id}: {category} cannot be approved in-session; "
                "change configuration deliberately instead"
            )
        if self._policy.get(category) == DENY:
            raise PolicyError(f"{tool_id}: {category} is DENY and cannot be approved")
        if self._policy.get(category) == CONFIRM:
            raise PolicyError(
                f"{tool_id}: {category} is CONFIRM and cannot be approved in "
                f"advance. It is granted one action at a time, bound to the "
                f"exact target and arguments - see friday.confirmation"
            )
        self._session_approvals.add(tool_id)

    def revoke(self, tool_id: str) -> None:
        self._session_approvals.discard(tool_id)

    def require(self, tool_id: str) -> Verdict:
        """Raise unless the tool may run right now."""
        verdict = self.decide(tool_id)
        if not verdict.allowed:
            raise PolicyError(f"{tool_id}: {verdict.reason}")
        return verdict

    def describe(self) -> list[dict]:
        return [
            {"category": category, "decision": decision,
             "approvable": category not in NON_APPROVABLE}
            for category, decision in self._policy.items()
        ]

    @property
    def asks_for_anything(self) -> bool:
        """
        True if any category can still stop and ask.

        In FULL this must be False. A tool that asks with no way to answer is
        the failure this mode exists to remove.
        """
        return ASK in self._policy.values()


#: Process-wide default. Constructed with no user data by design.
default_engine = PolicyEngine()
