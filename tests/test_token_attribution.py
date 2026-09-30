import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import token_attribution as ta  # noqa: E402


def _asst(mid, ts, blocks, usage):
    return {"type": "assistant", "timestamp": ts,
            "message": {"id": mid, "content": blocks, "usage": usage}}


def _user(ts, content):
    return {"type": "user", "timestamp": ts, "message": {"content": content}}


def _u(i, cc, cr, o):
    return {"input_tokens": i, "cache_creation_input_tokens": cc,
            "cache_read_input_tokens": cr, "output_tokens": o}


def _write(tmp_path):
    rows = [
        {"type": "mode"},
        _user("2026-01-01T00:00:00Z", "hello"),  # 5 chars
        _asst("m1", "2026-01-01T00:10:00Z",
              [{"type": "text", "text": "abcd"}], _u(1, 100, 0, 10)),
        _asst("m1", "2026-01-01T00:10:00Z",  # same message, second block, same usage
              [{"type": "tool_use", "id": "t1", "name": "Bash",
                "input": {"command": "ls -la"}}], _u(1, 100, 0, 10)),
        _user("2026-01-01T00:20:00Z",
              [{"type": "tool_result", "tool_use_id": "t1", "content": "x" * 400}]),
        _asst("m2", "2026-01-01T00:30:00Z",
              [{"type": "tool_use", "id": "t2", "name": "Read",
                "input": {"file_path": "/a/b.py"}}], _u(2, 0, 100, 20)),
        _user("2026-01-01T00:40:00Z",
              [{"type": "tool_result", "tool_use_id": "t2",
                "content": [{"type": "text", "text": "y" * 100},
                            {"type": "text", "text": "z" * 100}]},
               {"type": "text", "text": "<system-reminder>ab</system-reminder>"}]),
        _asst("m3", "2026-01-01T01:00:00Z",
              [{"type": "text", "text": "done!"}], _u(3, 0, 200, 30)),
    ]
    p = tmp_path / "s.jsonl"
    p.write_text("\n".join(json.dumps(r) for r in rows) + "\nnot json\n", encoding="utf-8")
    return p


def test_totals_and_attribution(tmp_path):
    r = ta.aggregate(ta.load_entries(_write(tmp_path)))
    assert r["turns"] == 3
    assert r["usage"] == {"input_tokens": 6, "cache_creation_input_tokens": 100,
                          "cache_read_input_tokens": 300, "output_tokens": 60}
    assert r["total_tokens"] == 466
    assert r["wall_seconds"] == 3600
    assert r["tokens_per_hour"] == 466
    a = r["attribution_chars"]
    assert a["tool_results_by_tool"]["Bash"] == {"results": 1, "chars": 400}
    assert a["tool_results_by_tool"]["Read"] == {"results": 1, "chars": 200}
    assert a["assistant_text"] == 4 + 5
    assert a["user_text"] == 5 + len("<system-reminder>ab</system-reminder>")
    top = r["top_tool_results"]
    assert [(t["tool"], t["chars"], t["hint"]) for t in top] == [
        ("Bash", 400, "ls -la"), ("Read", 200, "/a/b.py")]


def test_since_window_and_cli_json(tmp_path, capsys):
    p = _write(tmp_path)
    out = tmp_path / "o.json"
    rc = ta.main([str(p), "--since", "2026-01-01T00:25:00Z", "--json", str(out)])
    assert rc == 0
    text = capsys.readouterr().out
    assert "APPROXIMATE" in text and len(text.splitlines()) <= 20
    rep = json.loads(out.read_text(encoding="utf-8"))
    assert rep["turns"] == 2
    assert rep["attribution_chars"]["tool_results_by_tool"].get("Bash") is None
