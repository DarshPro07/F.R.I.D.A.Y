"""
What code is actually running, so a test can refuse to test the wrong build.

This exists because of an hour lost to a lie. `agent_friday.py dev` printed
`registered worker` four times while the process id never changed, because
LiveKit's dev watcher re-registers without re-importing modules already in
`sys.modules`. Separately, `server.py` had been running since 13:23 while the
MCP tools it was meant to serve were written at 23:57.

A live test asked "what am I working on?", got "I don't have a record of
that", and it read as a routing failure. The routing was fine. The capability
was registered, gated, in CORE_TOOLS and correct - it simply did not exist in
the process being talked to.
"""
from __future__ import annotations
from friday import build_identity as B


def test_a_build_knows_what_it_is_made_of():
    build = B.current(refresh=True)
    assert build.registry_hash and build.registry_hash != 'unknown'
    assert build.capabilities > 100
    assert build.pid > 0


def test_the_registry_hash_follows_the_capabilities_not_the_files():
    """
    Over ids rather than file contents, deliberately: it answers "does this
    process know about the same abilities". A comment change must not
    invalidate a running server; a new capability must.
    """
    first = B.registry_hash()
    assert first == B.registry_hash()
    assert len(first) == 12


def test_a_process_matches_itself():
    same, why = B.current(refresh=True).matches(B.expected())
    assert same, why


def test_a_stale_registry_is_caught_even_at_the_same_commit():
    """
    The case that cost the hour. A process running across an edit reports the
    *new* commit - git reads the tree, not memory - so the commit alone would
    have said everything was fine.
    """
    mine = B.current(refresh=True)
    stale = B.Build(commit=mine.commit, registry_hash='0000deadbeef', capabilities=mine.capabilities - 2, started_at=0.0, pid=1)
    same, why = mine.matches(stale)
    assert not same
    assert 'registry' in why
    assert 'running across an edit' in why


def test_the_registry_is_checked_before_the_commit():
    """Order matters: the registry is the half that catches a stale server."""
    mine = B.current(refresh=True)
    both_wrong = B.Build(commit='ffffff', registry_hash='0000deadbeef', started_at=0.0, pid=1)
    _same, why = mine.matches(both_wrong)
    assert 'registry' in why, 'the commit mismatch masked the registry one'


def test_a_dirty_tree_is_reported_rather_than_claimed_clean():
    """
    A developer's working copy is the usual case here, and a commit alone
    would claim more than it knows.
    """
    build = B.current(refresh=True)
    assert isinstance(build.dirty, bool)


def test_it_says_how_long_it_has_been_up():
    """"Ten hours ago" is the fact that makes a stale process obvious."""
    assert B.current(refresh=True).age_seconds >= 0
    assert 'up' in B.describe()


def test_describe_is_one_readable_line():
    line = B.describe()
    assert '\n' not in line
    assert 'commit' in line and 'registry' in line and 'capabilities' in line