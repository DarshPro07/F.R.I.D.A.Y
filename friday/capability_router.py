"""
Dynamic toolsets: put a dozen tools in front of the model, not seventy-four.

§18 said not to expose every tool permanently. It was ignored, and the bill
came due: 74 tools meant ~22,700 characters of JSON schema on every single
request, roughly 7,600 tokens of pure overhead before the user had said
anything. Gemini began returning empty completions - `FinishReason.STOP` with
no content, four retries, then `failed to generate LLM completion`.

So the model starts with a core set - the things asked for constantly - plus
two proxy tools. When it needs to read a file it searches for "read a file",
gets `files_read` back with its arguments, and calls it through
`use_capability`. The payload never grows: one round trip buys any of the
sixty tools it cannot see.

The split is by observed frequency, not by tidiness. "Open Spotify", "play
something", "what's using my RAM" and "remember this" happen in almost every
conversation, so they are core and cost one turn. The long tail costs two,
which is the right trade for a voice assistant.

`GROUPS` survives the change and still earns its keep: it gives search a
purpose line to match against, and the test that every registered tool sits in
exactly one group is what stops a new capability becoming unreachable.
`enable()` remains for that grouping, and for anything that wants a whole area
at once.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from friday import capabilities
from friday import semantics



#: "Do it again" is the one phrasing that should still reach a START tool
#: after this conversation has already started something. Without it, the
#: demotion below would make "process the catalogue again from scratch"
#: unreachable, which is a worse bug than the one it fixes.
_STARTING_OVER = re.compile(
    r"\b(?:again|from scratch|re-?process|re-?run it all|start over|"
    r"fresh|another|new one|redo)\b", re.I)


#: Words that are three letters or longer and still carry no intent.
#:
#: Not a general stopword list - a list of the words that produced a wrong
#: answer. "reprocess that catalogue" matched `automations_delete` on the
#: single shared word "that", which was half of that tool's two-word example
#: phrase "disable that" and therefore scored 50%. Length was the only filter,
#: and length is not meaning.
_EMPTY_WORDS = frozenset("""
the that this those these and but for with from you your yours its it's was
were are being have has had did does done doing all any some out off now one
two get got put can could will would should about what which when how why who
whom there here then than into onto over under again just only very much more
most such same each every both few own too also very
set put make take give
""".split())


def _content(text: str) -> set[str]:
    return {w for w in re.split(r"[^a-z0-9]+", text.lower())
            if len(w) > 2 and w not in _EMPTY_WORDS}


def _phrase_score(query: str, phrases) -> int:
    """
    How well a request matches example phrasings, by shared content words.

    Word overlap rather than substring: "retry those failures" and "retry the
    network failures" share the words that matter and share no substring long
    enough to notice.
    """
    if not phrases:
        return 0
    wanted = _content(query)
    if not wanted:
        return 0
    best = 0
    for phrase in phrases:
        # Count of shared words, not the ratio to the phrase's length.
        #
        # The ratio made short phrases violent: `automations_history` listed
        # "every morning" as a negative, which reduces to {morning}, so any
        # request mentioning a morning matched it 1/1 and was repelled by
        # sixty points - including "what happened this morning", its own best
        # example, which fell out of the results entirely. Counting instead
        # means one shared word is a nudge and three are a decision, which is
        # what a shared word is actually worth.
        shared = _content(phrase) & wanted
        best = max(best, min(10, 4 * len(shared)))
    return best

#: Always available. Frequent enough that a round-trip to enable them would be
#: worse than the tokens they cost.
CORE_TOOLS: tuple[str, ...] = (
    'get_current_time',
    'system_get_info',
    'system_resource_usage',
    'system_list_processes',
    'apps_open',
    'open_in_browser',
    'music_play',
    'music_pause',
    'music_resume',
    'music_stop',
    'power_cancel',
    'projects_list',
    'project_resume',
    'web_search',
    'web_fetch',
    'web_answer',
    # Friday's flagship. The persona (agent_friday.py SYSTEM_PROMPT) is built
    # around "brief me": it calls these four directly and immediately, and
    # rule 1 forbids the model from naming a tool or doing capability
    # discovery first. So they have to be reachable without a
    # search_capabilities -> use_capability round-trip, which is the exact
    # thing CORE_TOOLS exists for. Live test on 2026-08-29: news lived only in
    # the `news` group, the group was never enabled, and the model's direct
    # get_world_news call came back "unknown AI function" - Friday fell back to
    # "news feed's unresponsive, boss." `web_news` stays grouped as the generic
    # backend; these four are the ones the persona invokes by name.
    'get_world_news',
    'get_world_finance_news',
    'open_world_monitor',
    'open_finance_world_monitor',
    'memory_recall',
    'memory_search',
    'memory_remember',
    'capability_families',
    # The fabric bridge belongs in core beside capability_families: families
    # tells the model what outcomes exist, capability_use does one. A live
    # regression (2026-08-29) showed the fabric skills - prompt-engineering,
    # diagrams, scientific methods, expert review - were reachable but never
    # reached, because capability_use sat in a group behind a discovery hop
    # the model would not take when it could just answer from its own LLM.
    # Core makes the fabric one call away; the persona says when to prefer it.
    'capability_use',
)

#: group -> tool names. Every non-core tool belongs to exactly one group.
GROUPS: dict[str, tuple[str, ...]] = {
    "files": (
        "files_read", "files_write", "files_create", "files_edit", "files_copy", "files_move",
        "files_recycle", "files_delete", "files_list", "files_info", "files_search", "files_roots",
    ),
    "screen": (
        "screen_point", "desktop_plan", "desktop_step", "desktop_stop",
    ),
    "browser": (
        "browser_open", "browser_navigate", "browser_inspect", "browser_close",
        "browser_automate", "browser_act",
    ),
    "research": ("web_crawl", "web_deep_research"),
    "documents": ("documents_extract", "documents_inspect"),
    "hardware": ("system_battery", "system_disks", "system_displays", "system_network"),
    "windows": (
        "windows_list", "windows_focus", "windows_minimize", "windows_restore",
        "windows_maximize", "windows_arrange",
    ),
    "audio": (
        "audio_sessions", "audio_session_volume", "audio_session_mute", "audio_master_volume",
    ),
    "brightness": ("brightness_get", "brightness_set"),
    "processes": ("process_close", "process_terminate"),
    "power": ("power_lock", "power_sleep", "power_hibernate", "power_shutdown", "power_restart"),
    "executor": ("ada_ask",),
    "hermes": ("hermes_delegate", "hermes_status", "hermes_steer", "hermes_interrupt"),
    "model_gateway": ("model_providers", "model_infer", "model_usage"),
    "adversarial": ("decision_deliberate", "change_review"),
    "selfdev": ("selfdev_run", "selfdev_promote", "selfdev_rollback", "selfdev_status"),
    "governor": ("system_pressure", "system_diagnostics"),
    "observability": ("objective_trace",),
    "connectors": (
        "connector_list", "connector_describe", "connector_connect", "connector_verify",
        "connector_smoke", "connector_status", "connector_repair",
    ),
    "brain": ("brain_recall", "brain_remember", "brain_entity", "brain_forget"),
    "browser_policy": (
        "browser_page_observe", "secrets_begin_entry", "secrets_complete_entry", "secrets_list",
    ),
    "operating_policy": (
        "policy_snapshot", "policy_set", "spend_gate", "spend_envelope_store",
        "contract_pending_questions", "contract_record",
    ),
    "operations": (
        "operation_create", "operation_status", "operation_assign", "operation_update",
        "skill_capture", "skill_list",
    ),
    "identity": ("browser_profiles",),
    "workbench": ("workbench_write", "workbench_preview", "workbench_list", "workbench_stop"),
    "youtube": (
        "youtube_find_channel", "youtube_channel_details", "youtube_recent_videos",
        "youtube_video_details",
    ),
    "vision": (
        "vision_inspect_camera", "vision_inspect_screen", "vision_camera_frame",
        "vision_screen_capture",
    ),
    "music": ("music_search", "music_next", "music_play_mood", "music_current"),
    "reminders": ("reminders_create", "reminders_list", "reminders_cancel"),
    "automations": (
        "automations_create", "automations_list", "automations_run", "automations_history",
        "automations_delete",
    ),
    "schedules": (
        "schedules_create", "schedules_list", "schedules_run", "schedules_history",
        "schedules_delete",
    ),
    "products": (
        "product_process", "product_status", "product_result", "product_retry", "product_runs",
        "product_export",
    ),
    "computer": (
        "apps_close", "apps_focus", "apps_list_known", "volume_get", "volume_set",
        "clipboard_read", "clipboard_write", "system_wifi_status",
    ),
    # The four world/finance brief-and-monitor tools moved to CORE_TOOLS: the
    # persona calls them directly and they must not need a group enable first.
    # `web_news` stays here as the generic, parameterised news backend a
    # deliberate use_capability("news") call reaches.
    "news": (
        "web_news",
    ),
    "profile": (
        "profile_learn_from_turn", "profile_get", "profile_explain", "profile_open_conflicts",
        "profile_resolve_conflict",
    ),
    "memory_extra": (
        "memory_forget", "memory_record_decision", "memory_project_context",
        "memory_session_recap", "memory_record_utterance",
        "memory_provenance", "memory_export",
    ),
    "objectives": (
        "objective_start", "objective_status", "objective_list", "objective_pause",
        "objective_resume", "objective_cancel", "objective_history",
    ),
    "capabilities": (
        "capability_providers", "capability_health", "capability_processes",
        "capability_reload", "capability_manifest",
    ),
    "utils": ("format_json", "word_count", "get_system_info"),
}

#: What each group is for, in the words the model will match against.
GROUP_PURPOSE: dict[str, str] = {
    'capabilities': 'what kinds of work Friday can take on right now, which upstream provider is behind one, and whether it is healthy',
    'files': 'read, write, edit, search or list files and folders',
    'browser': 'open a web page, read it, or drive a site by clicking',
    'research': 'read whole pages, compare sources, research a topic deeply',
    'screen': 'point at where to click, or take the mouse and do it (asks first)',
    'documents': 'read a pdf, word document, spreadsheet, slide deck or zip',
    'hardware': 'battery, disks and free space, screens, network adapters',
    'windows': 'what windows are open, and moving, snapping or minimising one',
    'audio': 'what is making sound, and how loud one app or the whole machine is',
    'brightness': 'how bright the screen is, and changing it',
    'processes': 'asking a running program to close, or ending one that will not',
    'power': 'locking, sleeping, hibernating, shutting down or restarting the computer',
    'executor': 'answer a development question from project decisions',
    'hermes': 'delegate a sustained engineering task to the Hermes agent, check on it, steer it, or stop it',
    'model_gateway': 'ask which model providers Hermes can broker, run one inference-only request on a stronger model, or read gateway token usage',
    'selfdev': 'improve Friday itself through the gated loop: sandbox a candidate change, test, review, benchmark, promote on approval, roll back',
    'adversarial': 'deliberate a high-impact decision with proposer, contrarian, failure analyst, evidence checker and judge roles, or have an independent reviewer check a change against its claim',
    'governor': 'why Friday is running fewer workers or browsers right now: resource pressure level, active workers, queue depth; the one diagnostics view',
    'observability': 'the full trace of what happened to an objective: tool calls, workers, model calls, policy decisions, latency, retries, errors, verification',
    'connectors': 'connect, verify, repair or list AI providers and other connectors so the boss never configures Hermes by hand',
    'brain': 'the shared Friday/Hermes knowledge brain: recall what we already know, save a durable verified fact, look up an entity card, expire a superseded fact',
    'browser_policy': 'read a web page under the banking/secret policy gate, or connect an API key/credential safely',
    'operating_policy': 'what am I allowed to do automatically, record a permission the boss granted, check or authorize spending, run the first-run setup questions',
    'operations': 'create or track a persistent business operation, its work items and progress, or capture a reusable procedure as a skill',
    'identity': 'which browser profiles and google accounts exist',
    'workbench': 'build a website or page and show it to him in his browser',
    'youtube': 'look up a youtube channel, its uploads, views and video details',
    'vision': 'look through the camera or at the screen',
    'music': "search music, skip tracks, play by mood, see what's playing",
    'reminders': 'set, list or cancel reminders',
    'automations': 'make something happen on a schedule, every day or every N minutes, without being asked each time - and check afterwards whether it ran',
    'schedules': 'schedule a whole objective to run once or on a repeat with budgets, permissions and a delivery channel, or a monitor that only speaks up when its condition is met - and see every firing',
    'products': 'process a catalogue or spreadsheet of items, then report what happened to each one',
    'computer': 'close or focus apps, volume, clipboard, wifi',
    'news': 'world and finance headlines, and the monitor dashboards',
    'profile': 'what ADA has learned about the user, and correcting it',
    'memory_extra': 'forget, project context, session recap, decisions',
    'objectives': 'start a multi-step job Friday runs on its own, and check on, pause, resume or stop it',
    'utils': 'format JSON, count words, agent runtime info',
}


def group_of(tool_name: str) -> str | None:
    for group, names in GROUPS.items():
        if tool_name in names:
            return group
    return None


def catalogue() -> str:
    """One line per group, for the model to choose from."""
    return "\n".join(
        f"  {group}: {GROUP_PURPOSE.get(group, '')}" for group in sorted(GROUPS)
    )


def unassigned(tool_names: list[str]) -> list[str]:
    """Tools that are neither core nor in a group - they would be unreachable."""
    known = set(CORE_TOOLS)
    for names in GROUPS.values():
        known |= set(names)
    return sorted(set(tool_names) - known)


@dataclass
class Router:
    """
    Tracks which groups are live and produces the tool list for update_tools.

    `all_tools` maps tool name -> the MCPTool object handed over by the
    MCPToolset after setup.
    """

    all_tools: dict[str, object] = field(default_factory=dict)
    enabled: set[str] = field(default_factory=set)
    #: group -> what was started in it this conversation, newest last. This is
    #: the router's whole notion of "where we are": once a catalogue has been
    #: processed, "retry those" means the run that exists, not a new one.
    #: Deliberately per-conversation and in memory - durable run lookup is
    #: friday/runcontext.py's job, over the database, and answers a different
    #: question.
    started: dict[str, list[str]] = field(default_factory=dict)

    def load(self, tools) -> None:
        """Take the tools an MCPToolset fetched."""
        self.all_tools = {}
        for tool in tools:
            name = getattr(getattr(tool, "info", None), "name", None)
            if name:
                self.all_tools[name] = tool

    @property
    def known_names(self) -> list[str]:
        return sorted(self.all_tools)

    def active_names(self) -> list[str]:
        names = [n for n in CORE_TOOLS if n in self.all_tools]
        for group in self.enabled:
            names += [n for n in GROUPS.get(group, ()) if n in self.all_tools]
        # Anything unassigned stays available rather than becoming unreachable.
        names += unassigned(self.known_names)
        return sorted(set(names))

    def active_tools(self) -> list:
        return [self.all_tools[name] for name in self.active_names()
                if name in self.all_tools]

    def enable(self, group: str) -> tuple[bool, str]:
        """Returns (changed, message)."""
        key = (group or "").strip().lower()
        if key == "all":
            self.enabled = set(GROUPS)
            return True, f"enabled every group ({len(self.all_tools)} tools)"
        if key not in GROUPS:
            return False, (
                f"unknown group {group!r}. Available:\n{catalogue()}"
            )
        if key in self.enabled:
            available = [n for n in GROUPS[key] if n in self.all_tools]
            return False, f"{key} is already enabled ({', '.join(available)})"
        self.enabled.add(key)
        added = [n for n in GROUPS[key] if n in self.all_tools]
        return True, f"enabled {key}: {', '.join(added)}"

    def describe(self) -> dict:
        return {
            "total_tools": len(self.all_tools),
            "active_tools": len(self.active_names()),
            "enabled_groups": sorted(self.enabled),
            "available_groups": sorted(set(GROUPS) - self.enabled),
        }

    # -- search: the proxy half -------------------------------------------

    def _schema_of(self, name: str) -> dict:
        tool = self.all_tools.get(name)
        raw = getattr(getattr(tool, "info", None), "raw_schema", None) or {}
        return raw if isinstance(raw, dict) else {}

    def describe_tool(self, name: str) -> dict:
        raw = self._schema_of(name)
        params = (raw.get("parameters") or {}).get("properties") or {}
        required = set((raw.get("parameters") or {}).get("required") or [])
        return {
            "capability": name,
            "group": group_of(name) or "core",
            "what_it_does": (raw.get("description") or "").strip().split("\n")[0][:180],
            "arguments": {
                key: {
                    "type": spec.get("type", "string"),
                    "required": key in required,
                }
                for key, spec in params.items()
            },
        }

    def search(self, query: str, *, limit: int = 6) -> list[dict]:
        """
        Find capabilities by what the user wants to do.

        Deliberately keyword scoring rather than embeddings: it needs no model
        call, adds no latency to a voice turn, and the tool names and
        descriptions here are already written in the words a request uses.

        Keyword scoring, but no longer only static. `routing_memory.prior()`
        adds what this request shape taught us the last time it was asked -
        the boss's corrections first, settled shadow outcomes second - so a
        sentence that was mis-routed once stops being mis-routed identically
        forever. The metadata still decides; the prior decides the ties.

        Words alone are not enough inside one domain, though. Every product
        tool is named "product_something" and described in terms of
        catalogues, so "retry the network failures" scores about the same
        against all six - and one run in four the model picked
        `product_process` and reprocessed the whole catalogue. Two things
        separate them, and neither is vocabulary:

          negative examples   phrases that mean a DIFFERENT tool. These carry
                              more signal than the positives, because the
                              positives all overlap.
          operation kind      START begins work; FOLLOW_UP, RECOVERY, EXPORT
                              and CANCEL act on work that already exists. Once
                              this conversation has started something, the
                              follow-ups are what comes next and starting
                              again is the unusual request - unless the words
                              say otherwise, which `_STARTING_OVER` catches.
        """
        lowered = (query or "").lower()
        # What this request shape taught us last time. Read once per search,
        # not once per candidate: the module caches, but a dict lookup in the
        # loop below is still cheaper than trusting that.
        from friday import routing_memory
        learned = routing_memory.prior(lowered)
        # The same content filter the phrase scorer uses, and for the same
        # reason twice over. Two-letter tokens were admitted and matched as
        # SUBSTRINGS: "at", from "every morning at seven", is inside
        # navigate, create, status and format, so a request about a daily
        # schedule ranked `browser_navigate` first.
        terms = _content(lowered)
        if not terms:
            return []
        starting_over = bool(_STARTING_OVER.search(lowered))

        wanted = semantics.for_request(lowered)
        about = semantics.target_for_request(lowered)

        scored: list[tuple[int, str]] = []
        for name in self.all_tools:
            raw = self._schema_of(name)
            haystack_name = name.lower().replace("_", " ")
            haystack_desc = (raw.get("description") or "").lower()
            purpose = GROUP_PURPOSE.get(group_of(name) or "", "").lower()
            meta = capabilities.by_id(name)

            score = 0
            for term in terms:
                if term in haystack_name.split():
                    score += 10          # a word of the tool's own name
                elif term in haystack_name:
                    score += 6           # part of it
                if term in haystack_desc:
                    score += 3
                if term in purpose:
                    score += 1

            if meta is not None:
                positive = _phrase_score(lowered, meta.intent_examples)
                negative = _phrase_score(lowered, meta.negative_examples)
                score += positive * 4
                # A negative only counts for the amount by which the request
                # looks MORE like the other tool. Weighted flatly, any word
                # appearing on both lists repelled: "morning" is in both
                # "do this every morning" and "what happened this morning",
                # so `automations_create` scored itself out of existence for
                # "do this every morning at seven".
                if negative > positive:
                    score -= (negative - positive) * 6
                if meta.operation_kind in capabilities.NEEDS_EXISTING_WORK:
                    # Only meaningful once something has been started - and
                    # the wrong answer entirely when he has just said "again",
                    # which is a request to do the work, not to look at it.
                    if starting_over:
                        score -= 6
                    else:
                        score += 8 if self.started.get(group_of(name) or "") else -4
                elif meta.operation_kind == "START":
                    # "Again" is not a weaker signal than "process this", it
                    # is a stronger one: it names work this conversation has
                    # already done, and the router knows which. Without the
                    # bonus, "run the whole catalogue again" lost to
                    # `automations_run` on the bare word "run".
                    started_here = bool(self.started.get(group_of(name) or ""))
                    if starting_over:
                        score += 10 if started_here else 0
                    else:
                        score -= 8 if started_here else 0
            operation, _target = semantics.for_capability(name)
            score += semantics.target_affinity(about, name)
            # The learned prior, applied last and additively. A correction the
            # boss gave for this exact shape is worth more than a tool-name
            # word match and less than a confident intent example, so it wins
            # the close calls and loses to a request that plainly says
            # otherwise. See friday/routing_memory.py for the weights.
            score += learned.get(name, 0)
            if wanted and wanted != semantics.READ and operation == wanted:
                score += 12
            # Known bias: a description match scores the same whether the
            # description is one line or forty, so a verbose tool can outrank
            # a precise one on an incidental word. `automations_create` beat
            # `music_next` for "skip this track" purely because its docstring
            # happened to contain "skipped" and "this". The docstring was
            # shortened rather than the scorer changed - but if this happens a
            # third time, normalise by length instead of rewording again.
            # `> 0`, not truthiness: the lifecycle adjustments can take a tool
            # negative, and a negative score is truthy. Nonsense queries
            # started returning six results, every one of them there because
            # it had been penalised for being a follow-up with nothing to
            # follow.
            if score > 0:
                scored.append((score, name))

        if wanted:
            plausible = [pair for pair in scored
                         if semantics.compatible(wanted, pair[1])]
            if plausible:
                scored = plausible

        scored.sort(key=lambda pair: (-pair[0], pair[1]))
        return [self.describe_tool(name) for _, name in scored[:limit]]

    def invocable(self, name: str):
        """The tool object for a name, or None."""
        return self.all_tools.get(name)

    def note_used(self, name: str) -> None:
        """
        Record that a capability ran, so the next turn ranks differently.

        Only START tools change anything: they are what turns "process this"
        into "there is now a run to talk about". Reads and follow-ups leave
        the picture as they found it.
        """
        meta = capabilities.by_id(name)
        group = group_of(name)
        if meta is None or group is None or meta.operation_kind != "START":
            return
        self.started.setdefault(group, []).append(name)
