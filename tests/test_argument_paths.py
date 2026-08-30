r"""
Windows paths through a JSON string argument.

`use_capability(capability, arguments)` takes its arguments as JSON text, and
the model writes the path the boss said: {"path": "E:\friday\gate.csv"}. `\f`
is a legal JSON escape, so nothing raises - `json.loads` returns
"E:<formfeed>riday<tab>ate.csv" and the tool fails on a path nobody typed.

Measured cost: the model's recovery is to call again with different escaping,
and when the second attempt worked, one "process this catalogue" left two
catalogue runs in the database.
"""

from __future__ import annotations

import json

import agent_friday as A


def through_json(raw_arguments: str) -> str:
    """Exactly what use_capability does to an argument, in the same order."""
    try:
        parsed = json.loads(raw_arguments)
    except json.JSONDecodeError:
        parsed = json.loads(A._escape_lone_backslashes(raw_arguments))
    return A._repair_windows_path(parsed["path"])


def test_the_escape_json_silently_accepts():
    """
    The dangerous half: nothing raises, and the path is wrong.

    Every backslash here is followed by a letter JSON knows, so json.loads
    succeeds and hands back control characters.
    """
    mangled = json.loads(r'{"path": "E:\build\notes.txt"}')["path"]
    assert "\b" in mangled and "\n" in mangled, "json stopped eating this"
    assert A._repair_windows_path(mangled) == r"E:\build\notes.txt"


def test_the_escape_json_refuses_outright():
    """
    The loud half, and the one the gate actually hit: `\\g` is not an escape
    JSON knows, so the whole call fails to parse and the model retries.
    """
    raw = r'{"path": "E:\friday\gate.csv"}'
    try:
        json.loads(raw)
        raise AssertionError("json started accepting this; the test is stale")
    except json.JSONDecodeError:
        pass
    assert through_json(raw) == r"E:\friday\gate.csv"


def test_every_escape_json_can_produce_comes_back():
    for raw, wanted in (
        (r'{"path": "E:\friday"}', "E:\\friday"),
        (r'{"path": "C:\build\notes.txt"}', "C:\\build\\notes.txt"),
        (r'{"path": "D:\reports\new\rows.csv"}', "D:\\reports\\new\\rows.csv"),
        (r'{"path": "E:\temp\backup"}', "E:\\temp\\backup"),
        (r'{"path": "E:\friday-tony-stark-demo-main\data\gate\a.csv"}',
         "E:\\friday-tony-stark-demo-main\\data\\gate\\a.csv"),
    ):
        assert through_json(raw) == wanted, raw


def test_an_escaped_quote_is_not_collateral_damage():
    raw = r'{"path": "E:\notes", "label": "he said \"go\""}'
    parsed = json.loads(A._escape_lone_backslashes(raw))
    assert parsed["label"] == 'he said "go"'


def test_a_correctly_escaped_path_is_left_alone():
    assert through_json(r'{"path": "E:\\friday\\gate.csv"}') == \
        r"E:\friday\gate.csv"


def test_a_forward_slash_path_is_left_alone():
    assert through_json('{"path": "E:/friday/gate.csv"}') == "E:/friday/gate.csv"


def test_file_content_with_real_newlines_is_never_touched():
    """
    The one argument where a control character belongs. A general repair
    would turn a written file's line breaks into the two characters
    backslash-n, which is why this only touches drive-letter paths.
    """
    content = "line one\nline two\ttabbed"
    assert A._repair_windows_path(content) == content


def test_prose_that_merely_mentions_a_drive_is_untouched():
    text = "the file is on E: somewhere\nhave a look"
    assert A._repair_windows_path(text) == text


def test_a_unix_path_is_untouched():
    assert A._repair_windows_path("/home/marke/notes.txt") == \
        "/home/marke/notes.txt"
