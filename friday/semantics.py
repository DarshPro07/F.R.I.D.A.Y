"""
What a capability *does*, and to *what*, as machine vocabulary.

Routing was lexical, and lexical routing has a specific failure that this
codebase has now hit five times: two capabilities share the user's words and
the wrong one wins. The sharpest example is not subtle.

    "open Paint"              wants apps_open
    "what windows are open?"  wants windows_list

Both contain "open". No amount of example phrasing settles it, because the
word really is in both sentences - and the second one is a *question about*
something being open, not an instruction to open anything. What separates them
is not vocabulary:

    apps_open      OPEN  an APPLICATION
    windows_list   LIST  some WINDOWs

So the request is read for an operation and a target too, and text similarity
only ranks candidates that are already structurally plausible. A LIST tool
cannot win an OPEN request by having better example sentences, which is
exactly what happened.

The vocabulary is derived from the registry rather than invented for it. The
verbs below are the ones Friday's capability ids already end in - list, search,
open, create, write, close, pause, resume, run - and the targets are the
domains they already begin with.
"""

from __future__ import annotations

import re
READ = 'READ'
LIST = 'LIST'
SEARCH = 'SEARCH'
OPEN = 'OPEN'

CREATE = 'CREATE'
UPDATE = 'UPDATE'
MOVE = 'MOVE'
DELETE = 'DELETE'
CONTROL = 'CONTROL'
EXECUTE = 'EXECUTE'
START = 'START'
CANCEL = 'CANCEL'
FOLLOW_UP = 'FOLLOW_UP'
RECOVERY = 'RECOVERY'
EXPORT = 'EXPORT'
MUTATE = 'MUTATE'

OPERATIONS = (
    READ,
    LIST,
    SEARCH,
    OPEN,
    CREATE,
    UPDATE,
    MOVE,
    DELETE,
    CONTROL,
    EXECUTE,
    START,
    CANCEL,
    FOLLOW_UP,
    RECOVERY,
    EXPORT,
    MUTATE,
)

TARGETS = ('SYSTEM',
 'PROCESS',
 'APPLICATION',
 'WINDOW',
 'FILE',
 'DOCUMENT',
 'WEB',
 'BROWSER',
 'AUDIO',
 'MEDIA',
 'AUTOMATION',
 'REMINDER',
 'MEMORY',
 'PROFILE',
 'PRODUCT',
 'VISION',
 'DISPLAY',
 'POWER',
 'OBJECTIVE',
 'DEVELOPMENT',
 'CLIPBOARD',
 'WORKBENCH',
 'CAPABILITY',
 'NONE')

_TARGET_BY_PREFIX = {'files': 'FILE',
 'documents': 'DOCUMENT',
 'workbench': 'WORKBENCH',
 'system': 'SYSTEM',
 'process': 'PROCESS',
 'apps': 'APPLICATION',
 'windows': 'WINDOW',
 'power': 'POWER',
 'brightness': 'DISPLAY',
 'volume': 'AUDIO',
 'audio': 'AUDIO',
 'music': 'MEDIA',
 'youtube': 'MEDIA',
 'web': 'WEB',
 'browser': 'BROWSER',
 'open': 'BROWSER',
 'get': 'WEB',
 'automations': 'AUTOMATION',
 'reminders': 'REMINDER',
 'memory': 'MEMORY',
 'profile': 'PROFILE',
 'product': 'PRODUCT',
 'vision': 'VISION',
 'objective': 'OBJECTIVE',
 'ada': 'DEVELOPMENT',
 'clipboard': 'CLIPBOARD',
 'capability': 'CAPABILITY',
 'format': 'NONE',
 'word': 'NONE'}


_OPERATION_BY_SUFFIX = {
    'list': LIST,
    'runs': LIST,
    'history': LIST,
    'sessions': LIST,
    'profiles': LIST,
    'known': LIST,
    'displays': LIST,
    'disks': LIST,
    'search': SEARCH,
    'find': SEARCH,
    'channel': SEARCH,
    'open': OPEN,
    'launch': OPEN,
    'monitor': OPEN,
    'preview': OPEN,
    'create': CREATE,
    'record': CREATE,
    'capture': CREATE,
    'frame': CREATE,
    'write': UPDATE,
    'edit': UPDATE,
    'set': UPDATE,
    'volume': UPDATE,
    'mute': UPDATE,
    'arrange': MOVE,
    'move': MOVE,
    'copy': MOVE,
    'minimize': MOVE,
    'maximize': MOVE,
    'restore': MOVE,
    'focus': CONTROL,
    'recycle': DELETE,
    'delete': DELETE,
    'terminate': DELETE,
    'close': DELETE,
    'pause': CONTROL,
    'resume': CONTROL,
    'stop': CONTROL,
    'next': CONTROL,
    'play': CONTROL,
    'lock': CONTROL,
    'sleep': CONTROL,
    'hibernate': CONTROL,
    'shutdown': CONTROL,
    'restart': CONTROL,
    'cancel': CANCEL,
    'run': EXECUTE,
    'process': EXECUTE,
    'retry': RECOVERY,
    'export': EXPORT,
    'start': START,
    'status': FOLLOW_UP,
    'result': FOLLOW_UP,
    'current': READ,
    'info': READ,
    'get': READ,
    'details': READ,
    'read': READ,
    'fetch': READ,
    'inspect': READ,
    'recap': READ,
    'context': READ,
    'explain': READ,
    'battery': READ,
    'usage': READ,
    'news': READ,
    'forget': DELETE,
    'remember': CREATE,
    'recall': READ,
    'ask': READ,
    'automate': EXECUTE,
    'navigate': OPEN,
    'extract': READ,
    'roots': LIST,
    'network': READ,
    'answer': READ,
    'crawl': SEARCH,
    'research': SEARCH,
    'videos': LIST,
    'learn': UPDATE,
    'resolve': UPDATE,
    'observe': READ,
    'snapshot': READ,
    'questions': READ,
    'gate': READ,
    'entry': UPDATE,
    'update': UPDATE,
    'assign': CREATE,
    'authorize': CREATE,
    'store': CREATE,
    'describe': READ,
    'connect': EXECUTE,
    'verify': READ,
    'repair': RECOVERY,
    'smoke': EXECUTE,
    'entity': READ,
    'families': LIST,
    'providers': LIST,
    'processes': LIST,
    'health': FOLLOW_UP,
    'use': START,
}

_OVERRIDES: dict[str, tuple[str, str]] = {
    'apps_close': (CONTROL, 'APPLICATION'),
    'process_close': (CONTROL, 'PROCESS'),
    'browser_close': (CONTROL, 'BROWSER'),
    'windows_close': (CONTROL, 'WINDOW'),
    'music_play': (OPEN, 'MEDIA'),
    'music_play_mood': (OPEN, 'MEDIA'),
    'projects_list': (LIST, 'MEMORY'),
    'project_resume': (FOLLOW_UP, 'MEMORY'),
    'objective_start': (START, 'OBJECTIVE'),
    'objective_cancel': (CANCEL, 'OBJECTIVE'),
    'objective_pause': (CONTROL, 'OBJECTIVE'),
    'objective_resume': (CONTROL, 'OBJECTIVE'),
    'objective_status': (FOLLOW_UP, 'OBJECTIVE'),
    'objective_list': (LIST, 'OBJECTIVE'),
    'objective_history': (LIST, 'OBJECTIVE'),
    'hermes_delegate': (START, 'DEVELOPMENT'),
    'hermes_steer': (CONTROL, 'DEVELOPMENT'),
    'hermes_interrupt': (CANCEL, 'DEVELOPMENT'),
    'hermes_status': (FOLLOW_UP, 'DEVELOPMENT'),
    # An imperative: "rescan for providers" tells Friday to do something to the
    # runtime, not to answer a question. Left to default it became READ and was
    # unroutable by any instruction - the exact failure test_semantics guards.
    'capability_reload': (CONTROL, 'CAPABILITY'),
    'browser_page_observe': (READ, 'WEB'),
    'format_json': (UPDATE, 'NONE'),
    'word_count': (READ, 'NONE'),
    'get_current_time': (READ, 'SYSTEM'),
    'get_system_info': (READ, 'SYSTEM'),
}
_DEFAULT = (READ, 'SYSTEM')


def _derive(capability_id: str) -> tuple[str | None, str]:
    """The rules alone, with None where no verb rule matched."""
    parts = capability_id.split("_")
    target = _TARGET_BY_PREFIX.get(parts[0], "SYSTEM")
    for part in reversed(parts[1:] or parts):
        if part in _OPERATION_BY_SUFFIX:
            return _OPERATION_BY_SUFFIX[part], target
    return _OPERATION_BY_SUFFIX.get(parts[0]), target


def for_capability(capability_id: str) -> tuple[str, str]:
    """`(operation, target)` for a registered capability."""
    if capability_id in _OVERRIDES:
        return _OVERRIDES[capability_id]
    operation, target = _derive(capability_id)
    return operation or _DEFAULT[0], target


def defaulted(capability_id: str) -> bool:
    """
    Whether this capability got its operation by falling through.

    Falling through is not neutral: the default is READ, READ is
    informational, and an informational capability cannot satisfy an
    imperative. A capability that lands here is unroutable by any instruction
    and nothing says so out loud - which is how memory_forget got lost. A test
    asserts this is false for every registered capability, so a new capability
    with an unfamiliar verb forces a decision instead of disappearing.
    """
    return (capability_id not in _OVERRIDES
            and _derive(capability_id)[0] is None)
_QUESTION = re.compile('^\\s*(what|which|how many|how much|where|when|is|are|do i|have i)\\b|\\?\\s*$', re.IGNORECASE)

_REQUEST_VERBS = {
    'open': OPEN,
    'launch': OPEN,
    'play': OPEN,
    'show': LIST,
    'list': LIST,
    'find': SEARCH,
    'search': SEARCH,
    'look': SEARCH,
    'research': SEARCH,
    'check': READ,
    'inspect': READ,
    'verify': READ,
    'tell': READ,
    'read': READ,
    'create': CREATE,
    'write': CREATE,
    'save': CREATE,
    'edit': UPDATE,
    'change': UPDATE,
    'set': UPDATE,
    'update': UPDATE,
    'rename': UPDATE,
    'lower': UPDATE,
    'raise': UPDATE,
    'move': MOVE,
    'snap': MOVE,
    'minimise': MOVE,
    'minimize': MOVE,
    'maximise': MOVE,
    'maximize': MOVE,
    'copy': MOVE,
    'unmaximise': MOVE,
    'unmaximize': MOVE,
    'restore': MOVE,
    'resize': MOVE,
    'shrink': MOVE,
    'enlarge': MOVE,
    'delete': DELETE,
    'remove': DELETE,
    'bin': DELETE,
    'recycle': DELETE,
    'clean': DELETE,
    'clear': DELETE,
    'kill': DELETE,
    'forget': DELETE,
    'pause': CONTROL,
    'resume': CONTROL,
    'stop': CONTROL,
    'skip': CONTROL,
    'mute': CONTROL,
    'focus': CONTROL,
    'close': CONTROL,
    'quit': CONTROL,
    'lock': CONTROL,
    'shut': CONTROL,
    'run': EXECUTE,
    'retry': RECOVERY,
    'export': EXPORT,
    'cancel': CANCEL,
}

_PHRASAL_VERBS = {
    ('bring', 'up'): OPEN,
    ('pull', 'up'): OPEN,
    ('fire', 'up'): OPEN,
    ('start', 'up'): OPEN,
    ('look', 'up'): SEARCH,
    ('dig', 'up'): SEARCH,
    ('throw', 'away'): DELETE,
    ('clear', 'out'): DELETE,
}
_FILLER = frozenset({'a',
           'an',
           'can',
           'could',
           'friday',
           'just',
           'me',
           'my',
           'now',
           'please',
           'the',
           'would',
           'you'})


def for_request(text: str) -> str | None:
    """
    The operation a sentence is asking for, or None when it is not clear.

    None is a real answer and the common one. This is a *narrowing* signal:
    when it fires it removes structurally wrong candidates, and when it does
    not the ranking behaves exactly as it did before.
    """
    lowered = (text or "").strip().lower()
    if not lowered:
        return None
    if _QUESTION.search(lowered):
        return READ
    words = [word for word in re.findall(r"[a-z']+", lowered)
             if word not in _FILLER]
    if not words:
        return None
    for particle in words[1:3]:
        found = _PHRASAL_VERBS.get((words[0], particle))
        if found:
            return found
    return _REQUEST_VERBS.get(words[0])
_INFORMATIONAL = frozenset({READ, LIST, SEARCH, FOLLOW_UP})
_NEIGHBOURS = {
    OPEN: {OPEN, CREATE, EXECUTE},
    CREATE: {CREATE, UPDATE, OPEN, EXPORT},
    UPDATE: {UPDATE, CREATE, MOVE, CONTROL},
    MOVE: {MOVE, UPDATE, CONTROL},
    DELETE: {DELETE, CONTROL, CANCEL},
    CONTROL: {CONTROL, UPDATE, CANCEL, DELETE},
    EXECUTE: {EXECUTE, START, RECOVERY},
    CANCEL: {CANCEL, CONTROL},
    RECOVERY: {RECOVERY, EXECUTE},
    EXPORT: {EXPORT, CREATE},
    START: {START, OPEN, EXECUTE},
}


def compatible(request_operation: str | None, capability_id: str) -> bool:
    """
    Whether this capability could plausibly serve that request.

    Incompatible is the useful half. `windows_list` is LIST, an imperative
    "open Paint" is OPEN, and LIST is not in OPEN's neighbourhood - so no
    amount of shared vocabulary lets it win.
    """
    if request_operation is None:
        return True
    operation, _target = for_capability(capability_id)
    if request_operation in _INFORMATIONAL:
        return operation in _INFORMATIONAL
    return operation in _NEIGHBOURS.get(request_operation, {request_operation})

_TARGET_NOUNS = {'file': 'FILE',
 'files': 'FILE',
 'folder': 'FILE',
 'directory': 'FILE',
 'document': 'DOCUMENT',
 'documents': 'DOCUMENT',
 'window': 'WINDOW',
 'windows': 'WINDOW',
 'app': 'APPLICATION',
 'apps': 'APPLICATION',
 'application': 'APPLICATION',
 'program': 'APPLICATION',
 'process': 'PROCESS',
 'automation': 'AUTOMATION',
 'automations': 'AUTOMATION',
 'reminder': 'REMINDER',
 'reminders': 'REMINDER',
 'song': 'MEDIA',
 'music': 'MEDIA',
 'track': 'MEDIA',
 'playing': 'MEDIA',
 'playback': 'MEDIA',
 'video': 'MEDIA',
 'volume': 'AUDIO',
 'sound': 'AUDIO',
 'audio': 'AUDIO',
 'browser': 'BROWSER',
 'tab': 'BROWSER',
 'page': 'WEB',
 'website': 'WEB',
 'site': 'WEB',
 'url': 'WEB',
 'news': 'WEB',
 'web': 'WEB',
 'internet': 'WEB',
 'online': 'WEB',
 'product': 'PRODUCT',
 'products': 'PRODUCT',
 'catalogue': 'PRODUCT',
 'catalog': 'PRODUCT',
 'screen': 'VISION',
 'screenshot': 'VISION',
 'camera': 'VISION',
 'objective': 'OBJECTIVE',
 'run': 'OBJECTIVE',
 'brightness': 'DISPLAY',
 'clipboard': 'CLIPBOARD',
 'disk': 'SYSTEM',
 'disks': 'SYSTEM',
 'cpu': 'SYSTEM',
 'note': 'FILE',
 'notes': 'FILE',
 'computer': 'SYSTEM',
 'machine': 'SYSTEM',
 'pc': 'SYSTEM',
 'laptop': 'SYSTEM',
 'ram': 'SYSTEM',
 'memory': 'SYSTEM',
 'system': 'SYSTEM',
 'drive': 'SYSTEM',
 'drives': 'SYSTEM',
 'network': 'SYSTEM',
 'adapter': 'SYSTEM',
 'adapters': 'SYSTEM',
 'interface': 'SYSTEM',
 'interfaces': 'SYSTEM',
 'battery': 'SYSTEM',
 'story': 'WEB',
 'article': 'WEB',
 'headline': 'WEB',
 'headlines': 'WEB'}


def target_for_request(text: str) -> str | None:
    """
    The kind of thing a sentence is about, or None.

    Weaker than `for_request` and used as a weight rather than a filter: the
    operation is carried by grammar, the target only by vocabulary, and
    vocabulary is what was unreliable to begin with.
    """
    words = re.findall(r"[a-z]+", (text or "").lower())
    for word in words:
        if word in _TARGET_NOUNS:
            return _TARGET_NOUNS[word]
    try:
        from friday.apps import ALIASES
    except Exception:                                        # noqa: BLE001
        return None
    seen = set(words)
    for alias in ALIASES:
        parts = alias.split()
        if all(part in seen for part in parts):
            return "APPLICATION"
    return None

_TARGET_FAMILIES = (
    {'WEB', 'BROWSER'},
    {'MEDIA', 'AUDIO'},
    {'DOCUMENT', 'FILE', 'WORKBENCH'},
    {'APPLICATION', 'BROWSER', 'PROCESS', 'WINDOW'},
    {'DISPLAY', 'POWER', 'PROCESS', 'SYSTEM'},
)


def target_affinity(wanted: str | None, capability_id: str) -> int:
    """
    How much the target agrees, as a weight: full credit for the same kind,
    partial for the same family, nothing for unrelated.

    Deliberately no penalty. A negative pushes capabilities down on the
    strength of a vocabulary guess, and the guesses here are mine - the
    request only ever names its target in passing.
    """
    if not wanted:
        return 0
    target = for_capability(capability_id)[1]
    if target == wanted:
        return 10
    if any(wanted in family and target in family
           for family in _TARGET_FAMILIES):
        return 4
    return 0
