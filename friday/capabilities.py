"""
Capability metadata for every MCP tool.

This is deliberately not a permission system yet. It is the data a policy
engine will need later, declared now so no tool can be added without stating
what it touches. The immediately useful property: ``requires_edge`` marks the
tools that act on a physical machine and therefore break the moment the agent
is deployed to a container.

Kept as a plain table rather than decorators so the whole surface is readable
in one screen and testable without importing FastMCP.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass

# Where the work physically happens.
#   agent_runtime - inside the process running the tool (a container in cloud)
#   network       - an outbound call to a third party
#   user_device   - the human's actual computer
SCOPES = ("agent_runtime", "network", "user_device")

# What the call does to the world.
#   none            - pure computation
#   read            - observes without changing anything
#   write           - changes persistent state
#   external_action - causes something visible outside the process
SIDE_EFFECTS = ("none", "read", "write", "external_action")

# Where the call sits in the life of a piece of work.
#
# Keyword similarity alone cannot tell "process this catalogue" from "retry
# the failures on it" - both are about processing a catalogue, and one run in
# four the model picked `product_process` for the second and reprocessed
# everything into a new run. What separates them is not their words, it is
# that one STARTs work and the other RECOVERs work that already exists.
#
#   START     begins something new, and usually creates a run
#   READ      observes; safe to call at any point, changes nothing
#   FOLLOW_UP inspects or continues work that already exists
#   RECOVERY  retries or repairs an existing run
#   MUTATE    changes existing state
#   EXPORT    emits a result somewhere
#   CANCEL    stops something
OPERATION_KINDS = ("START", "READ", "FOLLOW_UP", "RECOVERY", "MUTATE",
                   "EXPORT", "CANCEL")

#: The kinds that only make sense once a run exists. Used by the router to
#: demote them when nothing is open, and to promote them when something is.
NEEDS_EXISTING_WORK = ("FOLLOW_UP", "RECOVERY", "EXPORT", "CANCEL")

# What kind of evidence exists for this capability - not how much code it has.
#
# "Tests green" and "verified" are different claims, and the gap between them
# is where a mock accepting our request gets recorded as a working printer.
# Every capability declares the strongest proof it actually has, and the
# default is the weakest, so nothing claims more by omission.
#
#   UNIT           the function is tested in isolation
#   CONTRACT       the protocol shape is tested against a specification
#   SIMULATED      tested against a stand-in for the real thing
#   LOCAL_REAL     tested against the real thing on this machine
#   DEVICE_REAL    tested against real external hardware
#   EXTERNAL_REAL  tested against a real third-party service
#
# A simulated `complete` event is not proof that plastic came out of a
# printer, and a discovery call that found zero devices is not proof that a
# lamp switched on.
VERIFICATION_SCOPES = ("UNIT", "CONTRACT", "SIMULATED", "LOCAL_REAL",
                       "DEVICE_REAL", "EXTERNAL_REAL")

#: The two that cannot be reached without something outside this machine.
#: Claiming either is a statement that real hardware or a real account
#: answered, and `test_capability_routing` requires each one to be listed
#: deliberately rather than arriving by copy-paste.
NEEDS_THE_WORLD = ("DEVICE_REAL", "EXTERNAL_REAL")

# What this costs if it happens when it should not have.
#
#   LOW           reversible by asking again - reads, volume, focus
#   MEDIUM        reversible with effort - moving a window, writing a file
#   HIGH          loses work that was not saved - closing, terminating
#   IRREVERSIBLE  cannot be undone by any later call - shutdown, restart
#
# This is declared beside the routing metadata rather than looked up, because
# the point is that somebody reading the capability can see what it costs
# without also holding the policy table in their head.
RISK_LEVELS = ("LOW", "MEDIUM", "HIGH", "IRREVERSIBLE")


@dataclass(frozen=True)
class Capability:
    id: str
    description: str
    execution_scope: str
    side_effect: str
    requires_edge: bool = False
    requires_auth: bool = False
    #: Defaults chosen so an unannotated capability behaves exactly as before:
    #: READ ranks neutrally, and neither creates nor requires a run.
    operation_kind: str = "READ"
    #: Phrases a request might use. Ranked against, so they are worth writing
    #: for anything whose name does not already contain the user's words.
    intent_examples: tuple[str, ...] = ()
    #: Phrases that mean a DIFFERENT capability. These carry more signal than
    #: the positives: "which products failed?" scores well against every tool
    #: with "product" in its name, and only a negative says which one is
    #: wrong.
    negative_examples: tuple[str, ...] = ()
    creates_run: bool = False
    #: The strongest evidence that exists for this today. Weakest by default.
    verification_scope: str = "UNIT"
    #: Content this capability is the specialist for - suffixes, or names a
    #: generic tool can recognise. A generic tool that meets one of these hands
    #: off rather than reporting that it cannot help; see `specialist_for`.
    handles_content: tuple[str, ...] = ()
    #: What it costs if it happens when it should not have.
    risk: str = "LOW"
    #: The id this capability is gated under in `policy.TOOL_CATEGORIES`.
    #:
    #: Set it where the two names differ. The table holds both shapes -
    #: `get_world_news` as it stands, `apps.close` dotted - so the lookup
    #: tries the id unchanged before trying the dotted form, and mangling a
    #: name that was already right is how a read of public headlines came back
    #: claiming it needed approval.
    policy_tool_id: str = ""

    @property
    def requires_approval(self) -> bool:
        """
        Whether a person has to say yes before this runs.

        Read from the policy table rather than stored, so the two cannot
        disagree: a field would be a second copy of a security decision, and
        second copies drift. Fails closed - an id policy has never heard of is
        treated as needing a yes, matching the table's own rule that unknown
        means unaudited and unaudited must not mean allowed.
        """
        from friday import policy                     # local: avoids a cycle

        for tool_id in self.policy_tool_ids():
            category = policy.TOOL_CATEGORIES.get(tool_id)
            if category is not None:
                return (policy.DEFAULT_POLICY.get(category, policy.ASK)
                        != policy.AUTO)
        return True

    def policy_tool_ids(self) -> tuple[str, ...]:
        """The ids to try, in order, when asking policy about this."""
        if self.policy_tool_id:
            return (self.policy_tool_id,)
        return (self.id, self.id.replace("_", ".", 1))

    def __post_init__(self) -> None:
        if self.execution_scope not in SCOPES:
            raise ValueError(f"{self.id}: bad execution_scope {self.execution_scope!r}")
        if self.side_effect not in SIDE_EFFECTS:
            raise ValueError(f"{self.id}: bad side_effect {self.side_effect!r}")
        if self.operation_kind not in OPERATION_KINDS:
            raise ValueError(
                f"{self.id}: bad operation_kind {self.operation_kind!r}")
        if self.verification_scope not in VERIFICATION_SCOPES:
            raise ValueError(
                f"{self.id}: bad verification_scope "
                f"{self.verification_scope!r}")
        if self.risk not in RISK_LEVELS:
            raise ValueError(f"{self.id}: bad risk {self.risk!r}")


_ALL = (
    Capability(
        id='get_world_news',
        description='Fetch global headlines from public RSS feeds.',
        execution_scope='network',
        side_effect='read',
    ),
    Capability(
        id='get_world_finance_news',
        description='Fetch finance and market headlines from public RSS feeds.',
        execution_scope='network',
        side_effect='read',
    ),
    Capability(
        id='web_search',
        intent_examples=(
            'search the web for this',
            'look this up online',
            'find me some links about that',
        ),
        negative_examples=(
            'search my project for a word',
            'what do you remember about this',
            'research this topic properly',
        ),
        description='Real web search via Brave, Tavily or DuckDuckGo.',
        execution_scope='network',
        side_effect='read',
    ),
    Capability(
        id='web_fetch',
        intent_examples=('fetch this url and tell me what it says', 'what does that link say'),
        negative_examples=('read these two web pages and compare them', 'what does this pdf say'),
        description='Fetch a URL and extract its readable text.',
        execution_scope='network',
        side_effect='read',
    ),
    Capability(
        id='workbench_write',
        description='Write a file into a workbench project the user keeps.',
        execution_scope='user_device',
        side_effect='write',
        requires_edge=True,
    ),
    Capability(
        id='workbench_preview',
        description='Validate a workbench project and serve it locally so it can be opened in a real browser.',
        execution_scope='user_device',
        side_effect='external_action',
        requires_edge=True,
    ),
    Capability(
        id='workbench_list',
        description='List workbench projects, or the files in one.',
        execution_scope='user_device',
        side_effect='read',
        requires_edge=True,
    ),
    Capability(
        id='workbench_stop',
        description="Stop serving a workbench project's preview.",
        execution_scope='user_device',
        side_effect='external_action',
        requires_edge=True,
    ),
    Capability(
        id='youtube_find_channel',
        intent_examples=('find that youtube channel', 'which channel is that exactly'),
        negative_examples=('list the running processes',),
        description='Find a YouTube channel by name or handle and pin down its exact id.',
        execution_scope='network',
        side_effect='read',
    ),
    Capability(
        id='youtube_channel_details',
        intent_examples=(
            'how many subscribers does that youtube channel have',
            'how big is that channel',
        ),
        negative_examples=('what has that channel uploaded recently', 'list the running processes'),
        description='Who a YouTube channel is: title, handle, subscribers, views, video count.',
        execution_scope='network',
        side_effect='read',
    ),
    Capability(
        id='youtube_recent_videos',
        intent_examples=(
            "list that channel's last five uploads with view counts",
            'what has that channel uploaded recently',
            'show me the newest videos from that channel',
        ),
        negative_examples=(
            'list the running processes',
            'list my automations',
            'how many subscribers does that channel have',
        ),
        description="A YouTube channel's newest uploads with dates and real view counts.",
        execution_scope='network',
        side_effect='read',
    ),
    Capability(
        id='youtube_video_details',
        description='Full detail for one YouTube video: description, tags, duration, views, likes, comments.',
        execution_scope='network',
        side_effect='read',
    ),
    Capability(
        id='documents_extract',
        description='Read a PDF, Word document, spreadsheet, slide deck or zip as text.',
        execution_scope='user_device',
        side_effect='read',
        requires_edge=True,
        intent_examples=(
            'what does this pdf say',
            'read this word document to me',
            'what is in that spreadsheet',
            'summarise this slide deck',
        ),
        negative_examples=(
            'read this text file',
            'open this csv',
            'what does this python file do',
            'read these two web pages and compare them',
            'read these urls properly',
        ),
        verification_scope='LOCAL_REAL',
        handles_content=('.pdf', '.docx', '.pptx', '.xlsx', '.xlsm', '.zip'),
    ),
    Capability(
        id='documents_inspect',
        description='How many pages, sheets or slides a document has, without reading it.',
        execution_scope='user_device',
        side_effect='read',
        requires_edge=True,
        operation_kind='READ',
        intent_examples=(
            'how many pages is this pdf',
            'what sheets are in that spreadsheet',
            'how long is this document',
        ),
        negative_examples=(
            'what does this pdf say',
            'read this document to me',
            'read these two web pages and compare them',
        ),
    ),
    Capability(
        id='system_battery',
        description='Battery charge, whether it is charging, and how long is left.',
        execution_scope='user_device',
        side_effect='read',
        requires_edge=True,
        verification_scope='LOCAL_REAL',
        intent_examples=('how much battery is left', 'am I plugged in', 'is this thing charging'),
        negative_examples=('how much memory is free', 'how much disk space is left'),
    ),
    Capability(
        id='system_disks',
        description='Every mounted volume with free space, not just the one this process runs from.',
        execution_scope='user_device',
        side_effect='read',
        requires_edge=True,
        verification_scope='LOCAL_REAL',
        intent_examples=(
            'how much disk space is left',
            'which drive is nearly full',
            'how much room is on my drives',
        ),
        negative_examples=('how much battery is left', 'what is using all my memory'),
    ),
    Capability(
        id='system_displays',
        description='How many screens are attached, their size, and which is primary.',
        execution_scope='user_device',
        side_effect='read',
        requires_edge=True,
        verification_scope='LOCAL_REAL',
        intent_examples=(
            'how many monitors do I have',
            'what resolution is my screen',
            'how many screens are attached',
        ),
        negative_examples=('look at my screen and tell me what is open', 'take a screenshot'),
    ),
    Capability(
        id='system_network',
        description='Which network adapters exist, which are up, and what addresses they hold.',
        execution_scope='user_device',
        side_effect='read',
        requires_edge=True,
        verification_scope='LOCAL_REAL',
        intent_examples=(
            'what network adapters do I have',
            'am I connected to anything',
            'which network interfaces are up',
        ),
        negative_examples=('what wifi am I on', 'search the web for this'),
    ),
    Capability(
        id='windows_list',
        description='What windows are open, where they are, and which is in front.',
        execution_scope='user_device',
        side_effect='read',
        requires_edge=True,
        verification_scope='LOCAL_REAL',
        intent_examples=(
            'what windows do I have open',
            'what have I got open at the moment',
            'which window is in front',
        ),
        negative_examples=('what apps are installed', 'look at my screen and tell me what it says'),
    ),
    Capability(
        id='windows_focus',
        description='Bring one window to the front, restoring it if minimized.',
        execution_scope='user_device',
        side_effect='external_action',
        requires_edge=True,
        verification_scope='LOCAL_REAL',
        operation_kind='MUTATE',
        intent_examples=(
            'bring that window to the front',
            'switch to the other chrome window',
            'put notepad in front',
        ),
        negative_examples=('open notepad', 'close that window'),
    ),
    Capability(
        id='windows_minimize',
        description='Put a window out of the way, reversibly.',
        execution_scope='user_device',
        side_effect='external_action',
        requires_edge=True,
        verification_scope='LOCAL_REAL',
        operation_kind='MUTATE',
        intent_examples=('get that window out of my way', 'minimise the browser', 'hide that window'),
        negative_examples=('close that window', 'quit the browser'),
    ),
    Capability(
        id='windows_restore',
        description='Bring a minimized or maximized window back to its ordinary size.',
        execution_scope='user_device',
        side_effect='external_action',
        requires_edge=True,
        verification_scope='LOCAL_REAL',
        operation_kind='MUTATE',
        intent_examples=('bring that window back', 'unmaximise the browser', 'restore that window'),
        negative_examples=('close that window', 'minimise the browser'),
    ),
    Capability(
        id='windows_maximize',
        description='Fill the screen with one window, reversibly.',
        execution_scope='user_device',
        side_effect='external_action',
        requires_edge=True,
        verification_scope='LOCAL_REAL',
        operation_kind='MUTATE',
        intent_examples=(
            'make that window full screen',
            'maximise the browser',
            'fill the screen with it',
        ),
        negative_examples=('minimise the browser', 'close that window'),
    ),
    Capability(
        id='windows_arrange',
        description='Snap a window to half the screen - left, right, top, bottom or full - and verify where it landed.',
        execution_scope='user_device',
        side_effect='external_action',
        requires_edge=True,
        verification_scope='LOCAL_REAL',
        operation_kind='MUTATE',
        intent_examples=(
            'put that window on the left half',
            'snap the browser to the right',
            'move notepad to the other side',
        ),
        negative_examples=('minimise the browser', 'close that window'),
    ),
    Capability(
        id='audio_sessions',
        description='What is making sound, how loud each one is, and which can be changed.',
        execution_scope='user_device',
        side_effect='read',
        requires_edge=True,
        verification_scope='LOCAL_REAL',
        intent_examples=(
            'what is making that noise',
            'what apps are playing sound',
            'which app is making sound right now',
        ),
        negative_examples=('what song is this', 'turn the volume down'),
    ),
    Capability(
        id='audio_session_volume',
        description="Set one application's volume, leaving everything else alone.",
        execution_scope='user_device',
        side_effect='external_action',
        requires_edge=True,
        verification_scope='LOCAL_REAL',
        operation_kind='MUTATE',
        intent_examples=(
            'lower spotify to 30 percent',
            'turn chrome down a bit',
            'make that app quieter',
        ),
        negative_examples=('turn the whole machine down', 'set the system volume to 30'),
    ),
    Capability(
        id='audio_session_mute',
        description='Mute or unmute one application.',
        execution_scope='user_device',
        side_effect='external_action',
        requires_edge=True,
        verification_scope='LOCAL_REAL',
        operation_kind='MUTATE',
        intent_examples=('mute the app that is playing music', 'silence chrome', 'unmute spotify'),
        negative_examples=('mute the whole machine', 'turn the volume down'),
    ),
    Capability(
        id='audio_master_volume',
        description='Set the master volume for the whole machine - every application at once.',
        execution_scope='user_device',
        side_effect='external_action',
        requires_edge=True,
        verification_scope='LOCAL_REAL',
        operation_kind='MUTATE',
        intent_examples=(
            'set the system volume to 30 percent',
            'turn the whole machine down',
            'master volume to 50',
        ),
        negative_examples=('lower spotify to 30 percent', 'mute the app that is playing music'),
    ),
    Capability(
        id='brightness_get',
        description='How bright the screen is, where the monitor exposes a control.',
        execution_scope='user_device',
        side_effect='read',
        requires_edge=True,
        verification_scope='LOCAL_REAL',
        intent_examples=('how bright is my screen', 'what is the screen brightness'),
        negative_examples=('turn the volume down', 'how much battery is left'),
    ),
    Capability(
        id='brightness_set',
        description='Set the screen brightness, refusing to go dark enough to be unrecoverable.',
        execution_scope='user_device',
        side_effect='external_action',
        requires_edge=True,
        verification_scope='LOCAL_REAL',
        operation_kind='MUTATE',
        intent_examples=('dim the screen a bit', 'set the brightness to 40', 'make the screen brighter'),
        negative_examples=('turn the volume down', 'lower spotify'),
    ),
    Capability(
        id='browser_profiles',
        description='List the browser profiles on this machine and which account each is signed into.',
        execution_scope='user_device',
        side_effect='read',
        requires_edge=True,
    ),
    Capability(
        id='open_in_browser',
        intent_examples=('open youtube', 'take me to that website', 'open this link in my browser'),
        negative_examples=('read these two web pages and compare them', 'search the web for this'),
        description='Open a URL in the real browser profile the user is signed into, not a blank automation window.',
        execution_scope='user_device',
        side_effect='external_action',
        requires_edge=True,
    ),
    Capability(
        id='ada_ask',
        description='Answer a development question from project decisions and stated facts, escalating to the user when nothing settles it. Called by a Claude Code run, not by ADA.',
        execution_scope='agent_runtime',
        side_effect='read',
    ),
    Capability(
        id='hermes_delegate',
        intent_examples=(
            'have hermes look at this project',
            'hermes inspect this project for architectural problems',
            'hand this coding task to the agent team',
            'get hermes to investigate the architecture',
        ),
        negative_examples=('open this file and read it to me', 'answer a quick question about python'),
        description='Hand one bounded engineering task to the Hermes agent: inspect or analyse a project or codebase, find architectural problems, implement multi-file changes, debug, investigate. Waits for the structured result.',
        execution_scope='agent_runtime',
        side_effect='external_action',
    ),
    Capability(
        id='hermes_status',
        intent_examples=('what is hermes doing right now', 'how is that delegated task going'),
        description="The durable status of Hermes work runs, from Friday's own records.",
        execution_scope='agent_runtime',
        side_effect='read',
    ),
    Capability(
        id='hermes_steer',
        intent_examples=('steer the hermes engineering run', 'redirect the delegated hermes task'),
        negative_examples=('what is hermes doing right now',),
        description='Course-correct a running Hermes engineering delegation without restarting it.',
        execution_scope='agent_runtime',
        side_effect='external_action',
    ),
    Capability(
        id='hermes_interrupt',
        intent_examples=('interrupt the hermes engineering run', 'abort the delegated hermes work'),
        negative_examples=('stop the music', 'cancel my reminder'),
        description='Stop a running Hermes engineering delegation; partial work is recorded.',
        execution_scope='agent_runtime',
        side_effect='external_action',
    ),
    Capability(
        id='web_answer',
        intent_examples=(
            'what is the latest version of that',
            'answer this with a citation',
            'look up a current fact',
        ),
        negative_examples=('what do you remember about this', 'search my project for a word'),
        description='Answer a current-facts question with Google grounding, cited, in one call. No local browser.',
        execution_scope='network',
        side_effect='read',
    ),
    Capability(
        id='web_crawl',
        description='Read several pages properly: main content only, as Markdown, in parallel.',
        execution_scope='network',
        side_effect='read',
        intent_examples=(
            'read these two web pages and compare them',
            'read these urls properly',
            'fetch and read these links',
        ),
        negative_examples=(
            'what does this pdf say',
            'read this file on my computer',
            'read this word document',
        ),
    ),
    Capability(
        id='web_deep_research',
        description='Search, read the results, rank them and return a budgeted corpus with citations.',
        execution_scope='network',
        side_effect='read',
        intent_examples=(
            'research this topic properly',
            'look into how people do this and compare sources',
            'do some real research on this for me',
        ),
        negative_examples=('what do you remember about this', 'search your memory for that'),
    ),
    Capability(
        id='web_news',
        description='Current headlines from public RSS feeds.',
        execution_scope='network',
        side_effect='read',
    ),
    Capability(
        id='browser_open',
        intent_examples=(
            'open a web page I can click around in',
            'drive that site for me',
            'open a page under automation so you can interact with it',
        ),
        negative_examples=(
            'open youtube',
            'take me to that website',
            'read these two web pages and compare them',
        ),
        description='Open a URL in a real browser and confirm the page loaded.',
        execution_scope='user_device',
        side_effect='external_action',
        requires_edge=True,
    ),
    Capability(
        id='browser_navigate',
        intent_examples=('go to another page in that browser', 'navigate to the next url'),
        negative_examples=('open youtube', 'search the web for this'),
        description='Navigate the open browser session to another URL.',
        execution_scope='user_device',
        side_effect='external_action',
        requires_edge=True,
    ),
    Capability(
        id='browser_inspect',
        intent_examples=('what is on the page you have open', 'read the page you are driving'),
        negative_examples=('what does this pdf say', 'look at the screen and tell me what is there'),
        description="Read the current browser page's url, title and text.",
        execution_scope='user_device',
        side_effect='read',
        requires_edge=True,
    ),
    Capability(
        id='browser_close',
        description='Close the browser session so Playwright is not left running.',
        execution_scope='user_device',
        side_effect='external_action',
        requires_edge=True,
        intent_examples=(
            'close the browser session',
            'finish up with the browser',
            'stop the automated browser',
        ),
        negative_examples=('close chrome', 'force close chrome', 'shut down the computer'),
    ),
    Capability(
        id='browser_automate',
        description='Drive the browser by clicking and typing. Requires approval.',
        execution_scope='user_device',
        side_effect='external_action',
        requires_edge=True,
        requires_auth=True,
    ),
    Capability(
        id='files_read',
        description='Read a text file inside the workspace roots.',
        execution_scope='user_device',
        side_effect='read',
        requires_edge=True,
        intent_examples=('read this text file', 'what does this csv contain', 'show me that json file'),
        negative_examples=(
            'what does this pdf say',
            'read this word document to me',
            'what is in that spreadsheet',
        ),
    ),
    Capability(
        id='files_list',
        description='List a directory inside the workspace roots.',
        execution_scope='user_device',
        side_effect='read',
        requires_edge=True,
        intent_examples=('show me my files', 'what is in that folder', 'list that directory'),
    ),
    Capability(
        id='files_info',
        description='Size, type and modification time of a path.',
        execution_scope='user_device',
        side_effect='read',
        requires_edge=True,
    ),
    Capability(
        id='files_roots',
        description='Which directories are readable and what stays protected.',
        execution_scope='user_device',
        side_effect='read',
        requires_edge=True,
    ),
    Capability(
        id='files_search',
        description='Find files by glob, optionally filtering by content.',
        execution_scope='user_device',
        side_effect='read',
        requires_edge=True,
        intent_examples=(
            'search my project for a word',
            'find the file that mentions this',
            'which files contain that text',
        ),
        negative_examples=('what do you remember about this', 'search the web for this'),
    ),
    Capability(
        id='files_create',
        description='Create a new file. Requires approval.',
        execution_scope='user_device',
        side_effect='write',
        requires_edge=True,
        requires_auth=True,
    ),
    Capability(
        id='files_write',
        description='Write a file, replacing it if present. Requires approval.',
        execution_scope='user_device',
        side_effect='write',
        requires_edge=True,
        requires_auth=True,
    ),
    Capability(
        id='files_edit',
        description='Replace a unique snippet in a file. Requires approval.',
        execution_scope='user_device',
        side_effect='write',
        requires_edge=True,
        requires_auth=True,
    ),
    Capability(
        id='files_copy',
        description='Copy a file within the workspace. Requires approval.',
        execution_scope='user_device',
        side_effect='write',
        requires_edge=True,
        requires_auth=True,
    ),
    Capability(
        id='files_move',
        description='Move a file within the workspace. Requires approval.',
        execution_scope='user_device',
        side_effect='write',
        requires_edge=True,
        requires_auth=True,
    ),
    Capability(
        id='memory_remember',
        intent_examples=('remember this for later', 'keep a note of that', 'do not forget this'),
        negative_examples=('remind me at six', 'what do you remember about this'),
        description='Store a durable memory with kind, source and confidence.',
        execution_scope='agent_runtime',
        side_effect='write',
    ),
    Capability(
        id='memory_recall',
        intent_examples=(
            'what do you remember about this',
            'what did I tell you about that',
            'what do you know about me',
        ),
        negative_examples=('search my project for a word', 'research this topic properly'),
        description='Look up memories for an exact subject.',
        execution_scope='agent_runtime',
        side_effect='read',
    ),
    Capability(
        id='memory_search',
        description='Search memories by keyword.',
        execution_scope='agent_runtime',
        side_effect='read',
        intent_examples=(
            'what do you remember about this',
            'have we talked about this before',
            'search your memory for that',
        ),
        negative_examples=(
            'search my project for a word',
            'which files contain that text',
            'research this topic properly',
            'search the web for this',
        ),
    ),
    Capability(
        id='memory_forget',
        description='Supersede a memory. Requires approval; history retained.',
        execution_scope='agent_runtime',
        side_effect='write',
        requires_auth=True,
    ),
    Capability(
        id='memory_record_decision',
        description='Record a project decision and its rationale.',
        execution_scope='agent_runtime',
        side_effect='write',
    ),
    Capability(
        id='memory_project_context',
        description='All memories and decisions recorded for a project.',
        execution_scope='agent_runtime',
        side_effect='read',
    ),
    Capability(
        id='projects_list',
        description='Every project on record, most recently touched first, with how many decisions and open questions each has.',
        execution_scope='agent_runtime',
        side_effect='read',
        operation_kind='READ',
        verification_scope='LOCAL_REAL',
        policy_tool_id='memory.projects_list',
        intent_examples=(
            'what am I working on',
            'what projects are active',
            'what are my projects',
            'list my projects',
            'show me my projects',
        ),
        negative_examples=('what tasks are running', 'what is this run doing', 'what did we decide'),
    ),
    Capability(
        id='project_resume',
        description='Pick a project up again: what was decided, what is still unanswered, what is in flight and what to do next.',
        execution_scope='agent_runtime',
        side_effect='read',
        operation_kind='FOLLOW_UP',
        verification_scope='LOCAL_REAL',
        policy_tool_id='memory.project_resume',
        intent_examples=(
            'continue that project',
            'pick up where we left off',
            'carry on with the lighthouse game',
            'where were we on that project',
            'what is left to do on it',
            'what is still unanswered on that',
        ),
        negative_examples=(
            'what projects are active',
            'start a new project',
            'what is playing',
            'continue the objective',
        ),
    ),
    Capability(
        id='memory_session_recap',
        description='Recent conversations and runs. Non-destructive.',
        execution_scope='agent_runtime',
        side_effect='read',
    ),
    Capability(
        id='memory_record_utterance',
        description='Store raw and normalized transcription separately.',
        execution_scope='agent_runtime',
        side_effect='write',
    ),
    Capability(
        id='reminders_create',
        intent_examples=(
            'remind me at six',
            'set a reminder for tomorrow morning',
            'nudge me about this later',
        ),
        negative_examples=('do this every morning without asking me', 'dim the screen a bit'),
        description='Schedule a reminder with the OS scheduler, verified registered.',
        execution_scope='user_device',
        side_effect='external_action',
        requires_edge=True,
    ),
    Capability(
        id='reminders_list',
        description='Pending reminders and whether the OS still holds each.',
        execution_scope='user_device',
        side_effect='read',
        requires_edge=True,
    ),
    Capability(
        id='reminders_cancel',
        description='Cancel a reminder and verify the task is gone.',
        execution_scope='user_device',
        side_effect='external_action',
        requires_edge=True,
    ),
    Capability(
        id='automations_create',
        description='Define a scheduled job: a trigger and a graph of steps.',
        execution_scope='user_device',
        side_effect='external_action',
        requires_edge=True,
        operation_kind='START',
        creates_run=True,
        intent_examples=(
            'do this every morning without asking me',
            'every day at seven',
            'each weekday at nine',
            'set up something that runs on a schedule',
        ),
        negative_examples=(
            'what happened this morning',
            'run that automation now',
            'turn that automation off',
        ),
    ),
    Capability(
        id='automations_list',
        description='Every automation, with armed read back from the OS scheduler.',
        execution_scope='user_device',
        side_effect='read',
        requires_edge=True,
        operation_kind='FOLLOW_UP',
        intent_examples=('list my automations', 'what jobs are scheduled', 'show me my automations'),
        negative_examples=('what happened this morning', 'run that automation now'),
    ),
    Capability(
        id='automations_run',
        description="Run an automation now, reporting every step's outcome.",
        execution_scope='user_device',
        side_effect='external_action',
        requires_edge=True,
        operation_kind='MUTATE',
        intent_examples=(
            'run that automation now',
            'trigger that job manually',
            'fire the automation right now',
        ),
        negative_examples=('do this every morning without asking me', 'what happened this morning'),
    ),
    Capability(
        id='automations_history',
        description='What automations actually did, per step, including unattended runs.',
        execution_scope='user_device',
        side_effect='read',
        requires_edge=True,
        operation_kind='FOLLOW_UP',
        intent_examples=(
            'what happened this morning',
            'when did that automation run',
            'how did that automation go this morning',
            "did last night's job go through",
            'how did the overnight schedule do',
        ),
        negative_examples=('do this every morning without asking me', 'set up an automation'),
    ),
    Capability(
        id='automations_delete',
        description='Delete an automation and verify its scheduled task is gone.',
        execution_scope='user_device',
        side_effect='external_action',
        requires_edge=True,
        operation_kind='CANCEL',
        intent_examples=('stop that automation', 'disable that automation', 'delete the scheduled job'),
        negative_examples=('what happened this morning', 'run that automation now'),
    ),
    Capability(
        id='product_process',
        description='Process a product catalogue through the pipeline, returning a run_id.',
        execution_scope='user_device',
        side_effect='write',
        requires_edge=True,
        operation_kind='START',
        creates_run=True,
        intent_examples=(
            'process this catalogue',
            'import these products',
            'run my product spreadsheet through',
            'run the whole catalogue again',
            'process the catalogue again from scratch',
        ),
        negative_examples=(
            'read this csv',
            'which products failed',
            'retry the network failures',
            'how did that job finish',
        ),
    ),
    Capability(
        id='product_status',
        description="How a catalogue run is doing - answers 'how did that job finish?'.",
        execution_scope='user_device',
        side_effect='read',
        requires_edge=True,
        operation_kind='FOLLOW_UP',
        intent_examples=(
            'how did that catalogue job finish',
            'is that catalogue still running',
            'did the catalogue job complete',
        ),
        negative_examples=('process this catalogue', 'which products failed'),
    ),
    Capability(
        id='product_result',
        description='What a run produced per product, including which stages failed.',
        execution_scope='user_device',
        side_effect='read',
        requires_edge=True,
        operation_kind='FOLLOW_UP',
        intent_examples=(
            'which products failed',
            'which products in the catalogue had problems',
            'what went wrong with those products',
            'show me the quarantined rows',
        ),
        negative_examples=('process this catalogue', 'retry the failures'),
    ),
    Capability(
        id='product_retry',
        description='Re-run only the products whose failure was worth retrying.',
        execution_scope='user_device',
        side_effect='write',
        requires_edge=True,
        operation_kind='RECOVERY',
        intent_examples=(
            'retry the network failures',
            'try those failed products again',
            'retry the failed rows',
            'have another go at the ones that broke',
        ),
        negative_examples=('process this catalogue', 'which products failed', 'start again from scratch'),
    ),
    Capability(
        id='product_runs',
        description='Find a catalogue run when you do not have its id - newest first.',
        execution_scope='user_device',
        side_effect='read',
        requires_edge=True,
        operation_kind='FOLLOW_UP',
        intent_examples=(
            'what catalogue jobs have run',
            'which catalogue run was that',
            'list recent product runs',
        ),
        negative_examples=('process this catalogue', 'run the whole catalogue again'),
    ),
    Capability(
        id='product_export',
        description="Write a run's finished products to a CSV file.",
        execution_scope='user_device',
        side_effect='write',
        requires_edge=True,
        operation_kind='EXPORT',
        intent_examples=(
            'export those products',
            'write the results to a csv',
            'save the finished products',
        ),
        negative_examples=('process this catalogue', 'which products failed'),
    ),
    Capability(
        id='vision_inspect_camera',
        description='Capture a webcam frame now and answer a question about it.',
        execution_scope='user_device',
        side_effect='read',
        requires_edge=True,
    ),
    Capability(
        id='vision_inspect_screen',
        intent_examples=(
            'look at the screen and tell me what is there',
            'what is on my screen',
            'describe what you can see on the display',
        ),
        negative_examples=(
            'how bright is my screen',
            'how many monitors do I have',
            'what windows do I have open',
        ),
        description='Capture the screen now and answer a question about it.',
        execution_scope='user_device',
        side_effect='read',
        requires_edge=True,
    ),
    Capability(
        id='vision_camera_frame',
        description='Take one webcam photo and save it; device released after.',
        execution_scope='user_device',
        side_effect='read',
        requires_edge=True,
    ),
    Capability(
        id='vision_screen_capture',
        description='Capture and save a screenshot.',
        execution_scope='user_device',
        side_effect='read',
        requires_edge=True,
    ),
    Capability(
        id='music_play',
        intent_examples=(
            'play something by daft punk',
            'put some music on',
            'play that song',
            'restart the song',
            'start that track again',
            'play it again from the beginning',
        ),
        negative_examples=(
            'what song is this',
            'lower spotify to 30 percent',
            'skip to the next track',
            'restart the computer',
            'reboot the machine',
            'restart this pc',
        ),
        description='Play any song by name. No account or subscription needed.',
        execution_scope='user_device',
        side_effect='external_action',
        requires_edge=True,
    ),
    Capability(
        id='music_play_mood',
        description='Play something matching a mood, chosen by search phrasing.',
        execution_scope='user_device',
        side_effect='external_action',
        requires_edge=True,
    ),
    Capability(
        id='music_search',
        description='Find songs without playing them.',
        execution_scope='network',
        side_effect='read',
    ),
    Capability(
        id='music_pause',
        intent_examples=(
            'pause the music',
            'stop the song for a moment',
            'hold the music',
            'suspend the music',
        ),
        negative_examples=(
            'mute the app that is playing music',
            'turn the volume down',
            'put the computer to sleep',
            'suspend the machine',
            'hibernate the pc',
        ),
        description='Pause playback by suspending the player process.',
        execution_scope='user_device',
        side_effect='external_action',
        requires_edge=True,
    ),
    Capability(
        id='music_resume',
        intent_examples=('carry on with the music', 'resume the song', 'start the music again'),
        negative_examples=('play something by daft punk', 'skip to the next track'),
        description='Resume playback from where it was paused.',
        execution_scope='user_device',
        side_effect='external_action',
        requires_edge=True,
    ),
    Capability(
        id='music_next',
        description='Skip to the next result from the last search.',
        execution_scope='user_device',
        side_effect='external_action',
        requires_edge=True,
    ),
    Capability(
        id='music_stop',
        intent_examples=(
            'stop the music',
            'turn the music off altogether',
            'shut down the music',
            'shut the music down',
            'kill the music',
        ),
        negative_examples=(
            'pause the music',
            'mute the app that is playing music',
            'shut down the computer',
            'turn off the pc',
            'power off this machine',
        ),
        description='Stop playback and shut the player down.',
        execution_scope='user_device',
        side_effect='external_action',
        requires_edge=True,
    ),
    Capability(
        id='music_current',
        description='What is playing and how far in, from the live player.',
        execution_scope='user_device',
        side_effect='read',
        requires_edge=True,
        intent_examples=('what song is this', "what's playing at the moment", 'what track is on'),
        negative_examples=('play something by daft punk', 'what windows do I have open'),
    ),
    Capability(
        id='profile_learn_from_turn',
        description='Learn durable facts about the user from a conversation turn.',
        execution_scope='agent_runtime',
        side_effect='write',
    ),
    Capability(
        id='profile_get',
        description='Everything known about the user, grouped by dimension.',
        execution_scope='agent_runtime',
        side_effect='read',
        intent_examples=(
            'what do you know about me',
            'what have you learned about me',
            'what is in my profile',
        ),
        negative_examples=('forget what you know about me', 'that is wrong about me'),
    ),
    Capability(
        id='profile_explain',
        description='Why ADA believes something, traced to the words it heard.',
        execution_scope='agent_runtime',
        side_effect='read',
    ),
    Capability(
        id='profile_open_conflicts',
        description='Contradictions waiting on a decision from the user.',
        execution_scope='agent_runtime',
        side_effect='read',
    ),
    Capability(
        id='profile_resolve_conflict',
        description='Settle a contradiction after the user has said which is right.',
        execution_scope='agent_runtime',
        side_effect='write',
    ),
    Capability(
        id='open_world_monitor',
        description='Open the World Monitor dashboard in a desktop browser.',
        execution_scope='user_device',
        side_effect='external_action',
        requires_edge=True,
    ),
    Capability(
        id='open_finance_world_monitor',
        description='Open the Finance World Monitor dashboard in a desktop browser.',
        execution_scope='user_device',
        side_effect='external_action',
        requires_edge=True,
    ),
    Capability(
        id='get_current_time',
        intent_examples=('what time is it', 'what is the date today', 'how late is it'),
        negative_examples=('how long did that take', 'set a reminder for six'),
        description='Current date and time of the agent runtime, ISO 8601.',
        execution_scope='agent_runtime',
        side_effect='read',
    ),
    Capability(
        id='get_system_info',
        description="Platform details of the agent runtime, not the user's PC.",
        execution_scope='agent_runtime',
        side_effect='read',
    ),
    Capability(
        id='format_json',
        description='Pretty-print a JSON string.',
        execution_scope='agent_runtime',
        side_effect='none',
    ),
    Capability(
        id='word_count',
        description='Count words, characters and lines in text.',
        execution_scope='agent_runtime',
        side_effect='none',
    ),
    Capability(
        id='system_get_info',
        intent_examples=(
            'what machine is this',
            'how much ram does this have',
            'what operating system am I on',
        ),
        negative_examples=('how much memory is being used right now', 'how much disk space is left'),
        description="OS, CPU, RAM and uptime of the user's own computer.",
        execution_scope='user_device',
        side_effect='read',
        requires_edge=True,
    ),
    Capability(
        id='system_list_processes',
        intent_examples=(
            'what is running on my computer',
            'what is using all the memory',
            'list the running processes',
        ),
        negative_examples=('what windows do I have open', 'what apps are playing sound'),
        description="Real running processes on the user's computer.",
        execution_scope='user_device',
        side_effect='read',
        requires_edge=True,
    ),
    Capability(
        id='system_resource_usage',
        intent_examples=(
            'how much memory is being used right now',
            'is the cpu busy',
            'what is my machine doing',
            'check my computer',
            'how is my computer doing',
            'is my machine healthy',
        ),
        negative_examples=(
            'what machine is this',
            'how much battery is left',
            'which of my drives is running out of space',
        ),
        description="Live CPU, memory and disk usage of the user's computer.",
        execution_scope='user_device',
        side_effect='read',
        requires_edge=True,
    ),
    Capability(
        id='system_wifi_status',
        description="Wi-Fi state, SSID and signal on the user's computer.",
        execution_scope='user_device',
        side_effect='read',
        requires_edge=True,
    ),
    Capability(
        id='apps_open',
        intent_examples=('open spotify', 'launch the calculator', 'start chrome for me'),
        negative_examples=('bring that window to the front', 'what apps are installed'),
        description='Launch an application and verify a matching process appeared.',
        execution_scope='user_device',
        side_effect='external_action',
        requires_edge=True,
    ),
    Capability(
        id='apps_close',
        description='Ask a named application to close, the way its close button does. It may prompt to save, and it may decline.',
        execution_scope='user_device',
        side_effect='external_action',
        requires_edge=True,
        requires_auth=True,
        operation_kind='MUTATE',
        verification_scope='LOCAL_REAL',
        risk='HIGH',
        policy_tool_id='apps.close',
        intent_examples=(
            'close chrome',
            'quit spotify',
            'close visual studio code',
            'quit the browser',
            'close that app',
        ),
        negative_examples=(
            'force close chrome',
            'kill it',
            'end that process',
            'shut down the computer',
            'restart this pc',
            'close that window',
            'stop the music',
        ),
    ),
    Capability(
        id='apps_focus',
        description='Bring an open window to the foreground.',
        execution_scope='user_device',
        side_effect='external_action',
        requires_edge=True,
    ),
    Capability(
        id='apps_list_known',
        description='Applications discoverable via registry and Start Menu.',
        execution_scope='user_device',
        side_effect='read',
        requires_edge=True,
    ),
    Capability(
        id='volume_get',
        description='Read master volume and mute state.',
        execution_scope='user_device',
        side_effect='read',
        requires_edge=True,
    ),
    Capability(
        id='volume_set',
        description='Set master volume, confirmed by read-back.',
        execution_scope='user_device',
        side_effect='write',
        requires_edge=True,
    ),
    Capability(
        id='clipboard_read',
        description="Read the user's clipboard contents.",
        execution_scope='user_device',
        side_effect='read',
        requires_edge=True,
    ),
    Capability(
        id='files_recycle',
        description='Send a file to the Recycle Bin, where it can be restored. Does not destroy it.',
        execution_scope='user_device',
        side_effect='write',
        requires_edge=True,
        requires_auth=True,
        operation_kind='MUTATE',
        verification_scope='LOCAL_REAL',
        risk='MEDIUM',
        policy_tool_id='files.recycle',
        intent_examples=(
            'delete that file',
            'get rid of that note',
            'bin that file',
            'remove the temporary file',
            'clean up that file',
        ),
        negative_examples=(
            'close that app',
            'force close chrome',
            'end that process',
            'shut down the computer',
            'delete that reminder',
            'cancel that automation',
            'clear the clipboard',
        ),
    ),
    Capability(
        id='clipboard_write',
        description='Replace clipboard contents. Requires approval.',
        execution_scope='user_device',
        side_effect='write',
        requires_edge=True,
        requires_auth=True,
    ),
    Capability(
        id='process_close',
        description='Ask a running program to close, the way its close button does. It may prompt to save, and it may decline.',
        execution_scope='user_device',
        side_effect='external_action',
        requires_edge=True,
        operation_kind='MUTATE',
        verification_scope='LOCAL_REAL',
        risk='HIGH',
        policy_tool_id='process.close',
        intent_examples=(
            'close notepad',
            'ask that program to close',
            'close the app that is not responding',
            'quit that application',
        ),
        negative_examples=(
            'force close it',
            'end that process',
            'kill it',
            'shut down the computer',
            'close that window',
            'stop the music',
        ),
    ),
    Capability(
        id='process_terminate',
        description='End a program that will not close. Anything unsaved in it is lost. Needs the boss to say yes to this exact one.',
        execution_scope='user_device',
        side_effect='external_action',
        requires_edge=True,
        requires_auth=True,
        operation_kind='MUTATE',
        verification_scope='LOCAL_REAL',
        risk='HIGH',
        policy_tool_id='process.terminate',
        intent_examples=(
            'force close it',
            'kill that process',
            'it is frozen, end it',
            'force quit chrome',
            'it is not responding, kill it',
        ),
        negative_examples=(
            'close chrome',
            'quit spotify',
            'shut down the computer',
            'restart the machine',
            'stop the music',
            'close that window',
        ),
    ),
    Capability(
        id='power_lock',
        description='Lock the screen. Nothing is lost; you sign in again.',
        execution_scope='user_device',
        side_effect='external_action',
        requires_edge=True,
        requires_auth=True,
        operation_kind='MUTATE',
        verification_scope='LOCAL_REAL',
        risk='MEDIUM',
        policy_tool_id='power.lock',
        intent_examples=(
            'lock my computer',
            'lock the screen',
            'lock this pc',
            'lock it, I am stepping away',
        ),
        negative_examples=(
            'turn the screen off',
            'dim the screen',
            'shut down the computer',
            'lock the door',
        ),
    ),
    Capability(
        id='power_sleep',
        description='Put the computer to sleep. Open applications stay as they are.',
        execution_scope='user_device',
        side_effect='external_action',
        requires_edge=True,
        requires_auth=True,
        operation_kind='MUTATE',
        verification_scope='CONTRACT',
        risk='IRREVERSIBLE',
        policy_tool_id='power.sleep',
        intent_examples=('put the computer to sleep', 'send this pc to sleep'),
        negative_examples=(
            'pause the music',
            'suspend the music',
            'turn the screen off',
            'set a sleep timer',
            'shut down the computer',
            'hibernate',
            'what machine is this',
            'what is my machine doing',
        ),
    ),
    Capability(
        id='power_hibernate',
        description='Hibernate the computer, if this machine supports it. Never silently replaced with sleep.',
        execution_scope='user_device',
        side_effect='external_action',
        requires_edge=True,
        requires_auth=True,
        operation_kind='MUTATE',
        verification_scope='CONTRACT',
        risk='IRREVERSIBLE',
        policy_tool_id='power.hibernate',
        intent_examples=('hibernate the computer', 'hibernate this pc'),
        negative_examples=(
            'put the computer to sleep',
            'pause the music',
            'shut down the computer',
            'what machine is this',
        ),
    ),
    Capability(
        id='power_shutdown',
        description='Turn this computer off. Unsaved work in every open application may be lost.',
        execution_scope='user_device',
        side_effect='external_action',
        requires_edge=True,
        requires_auth=True,
        operation_kind='MUTATE',
        verification_scope='CONTRACT',
        risk='IRREVERSIBLE',
        policy_tool_id='power.shutdown',
        intent_examples=(
            'shut down the computer',
            'turn off the pc',
            'power off this computer',
            'turn this computer off',
        ),
        negative_examples=(
            'shut down the music',
            'stop the music',
            'shut the music down',
            'close chrome',
            'shut down spotify',
            'turn off the lights',
            'mute everything',
            'close that window',
            'turn the screen off',
            'what machine is this',
            'what is my machine doing',
            'how much memory is being used',
        ),
    ),
    Capability(
        id='power_restart',
        description='Restart this computer. Unsaved work may be lost, and Friday will disconnect.',
        execution_scope='user_device',
        side_effect='external_action',
        requires_edge=True,
        requires_auth=True,
        operation_kind='MUTATE',
        verification_scope='CONTRACT',
        risk='IRREVERSIBLE',
        policy_tool_id='power.restart',
        intent_examples=('restart the computer', 'reboot', 'restart this pc', 'reboot this computer'),
        negative_examples=(
            'restart the song',
            'play that again',
            'restart the browser',
            'reopen chrome',
            'restart the automation',
            'restart spotify',
            'start that track again',
            'what machine is this',
            'what is my machine doing',
        ),
    ),
    Capability(
        id='power_cancel',
        description='Call back a shutdown or restart that has not happened yet.',
        execution_scope='user_device',
        side_effect='external_action',
        requires_edge=True,
        operation_kind='MUTATE',
        verification_scope='LOCAL_REAL',
        risk='LOW',
        policy_tool_id='power.cancel',
        intent_examples=(
            'cancel that',
            'stop the restart',
            'no, wait',
            'never mind, do not restart',
            'call back the shutdown',
        ),
        negative_examples=('cancel the reminder', 'cancel that automation', 'stop the music'),
    ),
    Capability(
        id='objective_start',
        description="Start a new multi-step objective: Friday plans it, compiles the plan, and runs every step on its own - no 'continue' needed. One job at a time: refuses if another objective is already active.",
        execution_scope='agent_runtime',
        side_effect='write',
        operation_kind='START',
        verification_scope='LOCAL_REAL',
        risk='LOW',
        policy_tool_id='objectives.start',
        intent_examples=(
            'handle this whole thing for me',
            'start a background job for this',
            'do these steps one after another',
            'run this job in the background',
            'take care of this whole task',
        ),
        negative_examples=('continue the song', 'what time is it', 'stop the music'),
    ),
    Capability(
        id='objective_status',
        description='Report how the active objective is doing: status, tasks done, failures, and whether Friday is waiting on you. Read-only.',
        execution_scope='agent_runtime',
        side_effect='none',
        operation_kind='FOLLOW_UP',
        verification_scope='LOCAL_REAL',
        risk='LOW',
        policy_tool_id='objectives.status',
        intent_examples=(
            'how far have you got',
            "what's the status of that job",
            'how is the objective going',
            'progress report',
            'are we done yet',
        ),
        negative_examples=('play some music', 'what time is it', 'stop the music'),
    ),
    Capability(
        id='objective_list',
        description="List the objective runs on record, most recent first, with each one's status. Read-only.",
        execution_scope='agent_runtime',
        side_effect='none',
        operation_kind='FOLLOW_UP',
        verification_scope='LOCAL_REAL',
        risk='LOW',
        policy_tool_id='objectives.list',
        intent_examples=(
            'what jobs do you have running',
            'list your active objectives',
            'what are you working on',
        ),
        negative_examples=('what time is it', 'play some music'),
    ),
    Capability(
        id='objective_pause',
        description='Pause the active objective: Friday stops executing immediately at the next step boundary and waits until told to resume. Nothing is cancelled.',
        execution_scope='agent_runtime',
        side_effect='write',
        operation_kind='FOLLOW_UP',
        verification_scope='LOCAL_REAL',
        risk='LOW',
        policy_tool_id='objectives.pause',
        intent_examples=('pause that job', 'hold off on that for now', 'put that on pause'),
        negative_examples=('pause the music', 'pause the video', 'stop the job'),
    ),
    Capability(
        id='objective_resume',
        description='Resume the paused objective: Friday picks up exactly where it stopped and keeps going on its own.',
        execution_scope='agent_runtime',
        side_effect='write',
        operation_kind='RECOVERY',
        verification_scope='LOCAL_REAL',
        risk='LOW',
        policy_tool_id='objectives.resume',
        intent_examples=('resume the objective', 'pick that back up', 'continue the job', 'unpause it'),
        negative_examples=('resume the song', 'continue explaining', 'keep going with the story'),
    ),
    Capability(
        id='objective_cancel',
        description='Stop the objective now: every unfinished step is interrupted and the run is recorded as cancelled.',
        execution_scope='agent_runtime',
        side_effect='write',
        operation_kind='CANCEL',
        verification_scope='LOCAL_REAL',
        risk='LOW',
        policy_tool_id='objectives.cancel',
        intent_examples=(
            'stop that job',
            'cancel the objective',
            'abort that run',
            'never mind the job',
            'forget the whole thing',
        ),
        negative_examples=(
            'stop the music',
            'cancel the reminder',
            'stop that countdown',
            'cancel that automation',
        ),
    ),
    Capability(
        id='objective_history',
        description='Past objectives: which runs finished, how each one went, and what failed. Read-only.',
        execution_scope='agent_runtime',
        side_effect='none',
        operation_kind='FOLLOW_UP',
        verification_scope='LOCAL_REAL',
        risk='LOW',
        policy_tool_id='objectives.history',
        intent_examples=(
            'what jobs have you done',
            'show me past objectives',
            'how did the last job go',
        ),
        negative_examples=('what time is it', 'play some music'),
    ),
    Capability(
        id='browser_page_observe',
        intent_examples=(
            'read that page under the safety policy',
            'observe the website content safely',
            'check what that page says',
        ),
        negative_examples=('open my bank account page', 'search the web for something'),
        description="Read a web page's text under Friday's browser policy: banking/payment domains are refused before capture, auth pages hand off to the boss, secret-shaped text is redacted. The sanctioned page-to-context path.",
        execution_scope='network',
        side_effect='read',
    ),
    Capability(
        id='secrets_begin_entry',
        intent_examples=(
            'connect my anthropic api key',
            'add a credential for openai',
            'set up the api key safely',
        ),
        negative_examples=('what api keys do i have',),
        description='Start a secure credential entry: opens a scratch file the boss types the secret into. Friday never sees the value - never ask for a key in chat.',
        execution_scope='user_device',
        requires_edge=True,
        side_effect='write',
    ),
    Capability(
        id='secrets_complete_entry',
        intent_examples=('i typed the key, finish connecting it', 'done - store the credential'),
        description='Finish a secure credential entry: encrypts the typed value, shreds the scratch file, returns metadata only.',
        execution_scope='user_device',
        requires_edge=True,
        side_effect='write',
    ),
    Capability(
        id='secrets_list',
        intent_examples=('which credentials are connected', 'list my api key aliases'),
        description='Connected credentials as metadata: alias, provider, purpose. Values never appear.',
        execution_scope='user_device',
        requires_edge=True,
        side_effect='read',
    ),
    Capability(
        id='policy_snapshot',
        intent_examples=('what are you allowed to do automatically', 'show your current permissions'),
        description='Current delegated-permission states per domain, plus the constitutional deny list.',
        execution_scope='agent_runtime',
        side_effect='read',
    ),
    Capability(
        id='policy_set',
        intent_examples=(
            'i trust you to publish without asking now',
            'stop asking before customer emails',
            'ask me first again before deploys',
        ),
        description='Record an explicit permission change the boss stated in conversation. Versioned and audited; constitutional classes are refused.',
        execution_scope='agent_runtime',
        side_effect='write',
    ),
    Capability(
        id='spend_gate',
        intent_examples=(
            'is that ad purchase inside the approved budget envelope',
            'may you spend 500 rupees on the campaign',
        ),
        negative_examples=('check my computer', 'how much memory is in use'),
        description='Pre-spend gate: AUTO inside an authorized envelope, CONFIRM otherwise. Called before any money moves.',
        execution_scope='agent_runtime',
        side_effect='read',
    ),
    Capability(
        id='spend_envelope_store',
        intent_examples=(
            'approve a 5000 rupee ad budget for the launch',
            'set up a spending envelope for meta ads',
        ),
        description='Store a spend envelope the boss confirmed: platform, caps, purpose. The record of confirmation, not a way around it.',
        execution_scope='agent_runtime',
        side_effect='write',
    ),
    Capability(
        id='contract_pending_questions',
        intent_examples=(
            'which operating contract questions remain',
            'is the first run configuration complete',
        ),
        description='Unanswered first-run operating-contract questions.',
        execution_scope='agent_runtime',
        side_effect='read',
    ),
    Capability(
        id='contract_record',
        intent_examples=('save my setup answers', 'record the operating contract'),
        description='Persist first-run contract answers; permission-shaped answers update live policy too.',
        execution_scope='agent_runtime',
        side_effect='write',
    ),
    Capability(
        id='operation_create',
        intent_examples=('create the watchco operation', 'set up a persistent business operation'),
        negative_examples=('have hermes look at this project', 'research watches for me'),
        description='Create a persistent organization-scale operation (after the boss confirms). Simple tasks never need this.',
        execution_scope='agent_runtime',
        side_effect='write',
    ),
    Capability(
        id='operation_status',
        intent_examples=(
            'how is the watch business operation going',
            'status of my persistent operation',
        ),
        description='Customer-shaped operation status: objective, work, progress, cost, decisions.',
        execution_scope='agent_runtime',
        side_effect='read',
    ),
    Capability(
        id='operation_assign',
        intent_examples=('add supplier research to the operation', 'assign that work item'),
        description='Add a work item to a persistent operation.',
        execution_scope='agent_runtime',
        side_effect='write',
    ),
    Capability(
        id='operation_update',
        intent_examples=('change the operation goal', 'update the budget for the operation'),
        description="Change a persistent operation's goal or confirmed budget.",
        execution_scope='agent_runtime',
        side_effect='write',
    ),
    Capability(
        id='skill_capture',
        intent_examples=('save that procedure as a skill candidate', 'remember how we launched resolve'),
        description='Capture a skill candidate after work that met a promotion criterion. Refused without criteria and evidence.',
        execution_scope='agent_runtime',
        side_effect='write',
    ),
    Capability(
        id='skill_list',
        intent_examples=('what skills have you learned', 'list skill candidates'),
        description='Skill candidates and validated skills, by state.',
        execution_scope='agent_runtime',
        side_effect='read',
    ),
    Capability(
        id='connector_list',
        intent_examples=('which ai providers can you connect to', 'list the available model connectors'),
        negative_examples=('check my computer', 'list my files'),
        description='Every AI provider/connector Hermes can reach: auth type, authenticated or not, models, and the one human step connecting would need.',
        execution_scope='agent_runtime',
        side_effect='read',
    ),
    Capability(
        id='connector_describe',
        intent_examples=(
            'describe the anthropic connector setup',
            'how would connecting openrouter work',
        ),
        description='Registry metadata, stored connection state (opaque refs only) and the human step for one connector.',
        execution_scope='agent_runtime',
        side_effect='read',
    ),
    Capability(
        id='connector_connect',
        intent_examples=(
            'connect claude to hermes',
            'jarvis connect an ai provider',
            'add openrouter as a model provider',
        ),
        negative_examples=('open this website', 'log into my bank'),
        description="Connect an AI provider through the correct auth flow (secure key window or official sign-in), configure Hermes, select the model, and verify. The user's only step is the identity/secret action itself.",
        execution_scope='agent_runtime',
        side_effect='external_action',
    ),
    Capability(
        id='connector_verify',
        intent_examples=('verify the claude connection now', 'did my provider sign-in land'),
        description='Re-check one connector against the live Hermes registry after a sign-in or an auth failure; the resume trigger for a run parked at an auth boundary.',
        execution_scope='agent_runtime',
        side_effect='read',
    ),
    Capability(
        id='connector_smoke',
        intent_examples=(
            'run the live check on the new provider',
            'prove the connected model actually answers',
        ),
        description='The READY gate: one real inference through the production Hermes path; verifies EFFECTIVE provider/model and fails on silent fallback. Only this promotes a connector to READY.',
        execution_scope='agent_runtime',
        side_effect='external_action',
    ),
    Capability(
        id='connector_status',
        intent_examples=('show the connector dashboard', 'which providers are connected right now'),
        description='All stored connections with status, default model, health and live authenticated state. No credential values, ever.',
        execution_scope='agent_runtime',
        side_effect='read',
    ),
    Capability(
        id='connector_repair',
        intent_examples=('repair the anthropic connector', 'fix the provider credentials problem'),
        negative_examples=('fix this bug in my code',),
        description='Automatic connector repair after a provider auth failure: inspect, distinguish stale credential from transient fault, re-open the right auth flow only if needed. Never restarts processes for a credential problem.',
        execution_scope='agent_runtime',
        side_effect='external_action',
    ),
    Capability(
        id='brain_recall',
        intent_examples=(
            'check the shared brain for what we know about this project',
            'recall our stored knowledge before researching again',
        ),
        negative_examples=('what did we talk about yesterday', 'remind me what you said earlier'),
        description='Durable facts + document snippets from the shared Friday/Hermes brain, packed server-side to a token budget. Evidence with provenance, never synthesis. Check before re-reading files or re-researching.',
        execution_scope='agent_runtime',
        side_effect='read',
    ),
    Capability(
        id='brain_remember',
        intent_examples=(
            'save that verified fact to the shared brain',
            'store this architecture decision durably for hermes too',
        ),
        negative_examples=('remember my name is tony', 'remember I prefer short answers'),
        description='One durable verified fact into the shared Friday/Hermes brain, provenance required. Refuses secret/banking content before ingestion. Not for personal preferences (memory_remember) or execution status.',
        execution_scope='agent_runtime',
        side_effect='write',
    ),
    Capability(
        id='brain_entity',
        intent_examples=(
            'show the shared brain entity card for hermes',
            'what does the shared brain hold on this company',
        ),
        negative_examples=('what do you know about me',),
        description='One known person/company/project card from the shared brain - zero model calls, sub-second.',
        execution_scope='agent_runtime',
        side_effect='read',
    ),
    Capability(
        id='brain_forget',
        intent_examples=(
            'expire that superseded fact from the shared brain',
            'the shared brain fact is wrong, expire it',
        ),
        negative_examples=('forget that', 'forget my preference'),
        description='Expire one shared-brain fact by id with an audit trail - for superseded or wrong facts.',
        execution_scope='agent_runtime',
        side_effect='write',
    ),
    Capability(
        id='capability_families',
        intent_examples=(
            'which capability families are available',
            'list your capability families and their status',
            'what kinds of work can you take on',
            'are any of your capabilities degraded or unavailable',
        ),
        negative_examples=(
            'what am i working on',
            'list my files',
            'which ai providers can you connect to',
            'what is using my ram',
        ),
        description='The kinds of work Friday can take on right now - coding, browser, research, memory, media and so on - and whether each is working. Names no internal tool or repository.',
        execution_scope='agent_runtime',
        side_effect='read',
    ),
    Capability(
        id='capability_providers',
        intent_examples=(
            'which tool did you actually use for that',
            'what is behind your code intelligence',
            'show the pinned versions and licences of your providers',
        ),
        negative_examples=(
            'which capability families are available',
            'which ai providers can you connect to',
        ),
        description='Diagnostic: the upstream implementation behind each capability family, its pinned commit, licence mode and whether it is running.',
        execution_scope='agent_runtime',
        side_effect='read',
    ),
    Capability(
        id='capability_health',
        intent_examples=('is the code intelligence provider healthy', 'probe that capability provider'),
        negative_examples=('how is my computer', 'check my connectors'),
        description='Probe one capability provider and report what it actually said, with the reason. Activates it if dormant.',
        execution_scope='agent_runtime',
        side_effect='read',
    ),
    Capability(
        id='capability_processes',
        intent_examples=(
            'is any capability provider running twice',
            'check for duplicate provider processes',
        ),
        negative_examples=('what is using my ram', 'list my processes'),
        description='Whether any capability provider that owns an OS process is running twice - the stale-restart case, as data.',
        execution_scope='agent_runtime',
        side_effect='read',
    ),
    Capability(
        id='capability_use',
        intent_examples=(
            'route this through the capability fabric',
            'use the capability family that fits and give me the result',
            'dispatch this to whichever capability provider suits it',
        ),
        negative_examples=(
            'which capability families are available',
            'which capability provider is behind that',
            'smoke test the connector',
            'run the automation',
        ),
        description='Do one piece of work through whichever provider in a capability family is cheapest, least risky and actually up, falling back down the chain if the first cannot answer.',
        execution_scope='agent_runtime',
        side_effect='write',
        operation_kind='START',
    ),
    Capability(
        id='capability_reload',
        intent_examples=(
            'rescan for new capability providers',
            'reload the capability fabric',
            'pick up the capability i just added without restarting',
        ),
        negative_examples=(
            'which capability families are available',
            'use the capability that fits',
            'restart friday',
        ),
        description='Re-scan friday/fabric_adapters for providers and drop the cached registry, so a newly added capability becomes reachable without a process restart. Reports what appeared or disappeared, by id.',
        execution_scope='agent_runtime',
        side_effect='read',
    ),
)

CAPABILITIES: dict[str, Capability] = {cap.id: cap for cap in _ALL}


def get(tool_id: str) -> Capability:
    if tool_id not in CAPABILITIES:
        raise KeyError(f"no capability declared for tool {tool_id!r}")
    return CAPABILITIES[tool_id]


def by_id(tool_id: str) -> Capability | None:
    """
    Like `get`, but None for an undeclared tool instead of raising.

    The router asks about every tool the MCP server offers, which is a
    slightly different set from what this file declares - a tool added
    upstream should degrade to "no routing metadata", not take the router
    down with a KeyError.
    """
    return CAPABILITIES.get(tool_id)


def specialist_for(content: str) -> Capability | None:
    """
    Which capability declares itself the specialist for this kind of content.

    The generic-tool escalation path. `files_read` is CORE - always visible,
    costing no search - so it answers first for anything phrased as "read
    this", and when it met a PDF it reported that it could not read binary
    files and stopped. `documents_extract` was ranked first by the router and
    never called, because the router only runs when the model searches, and it
    had no reason to.

    A dead end that names its successor turns that into a handoff. This is the
    lookup that keeps the pairing out of the generic tool: files_read asks the
    registry what reads a .pdf rather than importing the documents module and
    knowing the answer.
    """
    wanted = (content or "").strip().lower()
    if not wanted:
        return None
    for capability in _ALL:
        if wanted in (c.lower() for c in capability.handles_content):
            return capability
    return None


def requiring_edge() -> tuple[Capability, ...]:
    """Tools that act on a physical machine and cannot run in a container."""
    return tuple(cap for cap in _ALL if cap.requires_edge)


def as_dicts() -> list[dict]:
    return [asdict(cap) for cap in _ALL]
