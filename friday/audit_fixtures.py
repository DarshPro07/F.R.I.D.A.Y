"""
What an audit may safely call a capability with, and what it may not.

The registry says a capability exists and `capability_runtime` says it can be
reached. Neither says what to pass it, and 60 of the 113 runnable capabilities
take a required argument. Calling them with nothing produces a `TypeError`
that the runtime honestly reports as a failed task - and "files_read takes
(run, path)" is a fact about the audit, not about `files_read`. An audit that
reports its own missing fixtures as broken capabilities is worse than no audit.

So arguments are declared per capability, not derived per parameter name.
Deriving them would say "anything taking `path` is safe with a temp file",
which is true for `files_read` and false for `product_process`, where a text
file is not a catalogue and the resulting failure would be the fixture's fault
wearing the capability's name.

The table is deliberately conservative and deliberately incomplete. A
capability that is not in it reports `FIXTURE_REQUIRED`, which is a true
statement with a name, and the count of them is asserted in the tests so it
shrinks on purpose rather than growing by accident.

Two rules the entries are chosen against:

    owned         a mutating call touches only what this audit made, in a
                  directory it created - never the boss's files, never a real
                  window, never a real memory
    cheap         a network call that costs money or minutes is not worth an
                  audit; `web_search` is in, `web_deep_research` is out
"""

from __future__ import annotations

import pathlib

from friday.fsjail import DEFAULT_WORKSPACE

#: Why a runnable capability was not exercised. Not a failure: an audit that
#: cannot honestly build the argument says so.
FIXTURE_REQUIRED = "FIXTURE_REQUIRED"

#: The text a fixture file is created with. Recognisable in a stray temp
#: directory, and long enough that `word_count` and `documents_extract` have
#: something to find.
SAMPLE = ("Friday audit fixture. This file exists only so a capability can "
          "be exercised against something this run owns.\n")


class Workspace:
    """A directory this audit made, for the capabilities that write.

    Inside the file jail's own workspace, not the system temp directory. The
    first real audit put its fixtures in `%TEMP%` and every file capability
    refused the path - correctly, because `%TEMP%` is not a permitted root -
    so six capabilities reported a refusal that was the audit's fault.

    Every mutating fixture gets its own file. Sharing one would make the
    audit order-dependent - `files_recycle` would delete what `files_read`
    was about to open - and the children of a group are deliberately
    unordered.
    """

    DEFAULT_ROOT = pathlib.Path(DEFAULT_WORKSPACE) / "audit-fixtures"

    def __init__(self, root: pathlib.Path | None = None) -> None:
        self.root = pathlib.Path(root or self.DEFAULT_ROOT)
        self.root.mkdir(parents=True, exist_ok=True)

    def file(self, name: str, *, create: bool = True) -> str:
        """A path in this workspace, with the file made if it should exist.

        `create=False` means the capability under audit is the one that makes
        it, so a leftover from the last audit is removed first - otherwise
        `files_create` would be audited against a file that already existed,
        which is a different call with a different answer.
        """
        path = self.root / name
        if create:
            path.write_text(SAMPLE, encoding="utf-8")
        elif path.exists():
            path.unlink()
        return str(path)


def arguments_for(capability_id: str, workspace: Workspace) -> dict | None:
    """
    Arguments this capability can honestly be audited with, or None.

    None means `FIXTURE_REQUIRED` - no dishonest guess is made, because a
    guess that fails is indistinguishable in the report from a capability
    that is broken.
    """
    builder = _TABLE.get(capability_id)
    if builder is None:
        return None
    return builder(workspace)


def _own(name: str, *, create: bool = True):
    """A fixture whose only argument is a path to a file this run owns."""
    return lambda workspace: {"path": workspace.file(name, create=create)}


#: capability id -> arguments. Read as: "this exact call is safe here."
_TABLE = {
    # -- files: everything against the audit's own directory ---------------
    "files_read": _own("read.txt"),
    "files_info": _own("info.txt"),
    "files_create": _own("created.txt", create=False),
    "files_write": lambda w: {"path": w.file("written.txt", create=False),
                              "content": SAMPLE},
    "files_edit": lambda w: {"path": w.file("edited.txt"),
                             "old": "fixture", "new": "fixture (edited)"},
    "files_copy": lambda w: {"source": w.file("copy-source.txt"),
                             "destination": w.file("copy-target.txt",
                                                   create=False)},
    "files_move": lambda w: {"source": w.file("move-source.txt"),
                             "destination": w.file("move-target.txt",
                                                   create=False)},
    # Recycling is reversible and the file is one this run made moments ago.
    "files_recycle": _own("recycle-me.txt"),

    # -- cheap network reads -----------------------------------------------
    "web_search": lambda _w: {"query": "what time is it in London"},
    "music_search": lambda _w: {"query": "daft punk"},
    "youtube_find_channel": lambda _w: {"query": "nasa"},
}

#: Deliberately absent, and why - so the gaps are a decision on the record
#: rather than an oversight somebody has to rediscover.
#:
#:   apps_*, windows_*, process_*   would move or close the boss's real
#:                                  windows, and no window is audit-owned
#:   audio_*, volume_*, music_play  audible, and changes a real setting
#:   browser_*, open_in_browser     opens real pages in the real browser
#:   memory_forget, memory_remember mutate durable state about the boss
#:   memory_record_*, workbench_*   the same, for projects and code
#:   automations_*, reminders_*     create or cancel real scheduled work
#:   web_deep_research, web_crawl   minutes and money per call
#:   product_process                a text file is not a catalogue; the
#:                                  failure would be the fixture's fault
#:   objective_start                would start a second durable run inside
#:                                  the one auditing it
NOT_AUDITABLE_HERE = (
    "a fixture for this would have to touch something the audit does not own"
)
