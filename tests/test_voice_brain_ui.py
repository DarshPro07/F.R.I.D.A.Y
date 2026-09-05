"""The UI voice brain (friday/voice_brain.py) - the path behind the browser mic.

The LiveKit agent got its fixes in tests/test_voice_pipeline.py; the boss
talks to Friday through the browser when the machine is struggling, and that
path has its own prompt, history replay and tool list. Both are covered now.

Observed live (2026-09-02, UI path):
  - "search for me foreign market Trend" -> research/search -> skill NAMES ->
    "the research capability is failing". The UI brain had no web search.
  - that excuse was replayed as history on every later turn until she opened
    a new session with it, unprompted, to "hey what's up".
  - her voice changed: ui_server switched to Deepgram by itself because a key
    was present in .env; the LiveKit agent still spoke as OpenAI 'nova'.
"""
from __future__ import annotations

import os

import pytest

from friday import voice_brain as V


# ---------------------------------------------------------------------------
# the web is reachable from the spoken path
# ---------------------------------------------------------------------------

def test_the_web_family_is_offered_to_the_model():
    fams = V._surface()
    assert "web" in fams
    assert {"search", "answer", "news"} <= fams["web"]
    assert "web" in V._capability_menu()


def test_web_search_needs_a_query_and_says_so():
    out = V._run_capability("web", "search", {})
    assert "error" in out and "query" in out["error"]
    # the error names the fix, not a dead end
    assert "needs" in out["error"]


def test_unknown_web_operation_lists_the_real_ones():
    out = V._run_capability("web", "browse", {"query": "x"})
    assert "error" in out
    for op in ("answer", "news", "search"):
        assert op in out["error"]


def test_web_search_runs_through_the_guarded_toolset(monkeypatch):
    """A search must go through friday.toolsets.web (netguard, breaker,
    policy) and come back compacted for speech: title/url/snippet only."""
    from friday import contracts as c
    from friday.toolsets import web as W

    seen = {}

    async def fake_search(run, query, *, limit=8, engine=None):
        seen["query"], seen["limit"] = query, limit
        prior = c.started(run.run_id, "web.search")
        return c.succeeded(
            prior,
            verification=c.Verification(method="fake", evidence="2 hits"),
            output={"results": [
                {"title": "T1", "url": "https://a.example/", "snippet": "s" * 500,
                 "html": "<junk>" * 100},
                {"title": "T2", "url": "https://b.example/", "snippet": "two"},
            ]})

    monkeypatch.setattr(W, "web_search", fake_search)
    out = V._run_capability("web", "search", {"query": "foreign market trends"})
    assert "result" in out, out
    assert seen == {"query": "foreign market trends", "limit": 6}
    assert "T1" in out["result"] and "T2" in out["result"]
    assert "<junk>" not in out["result"]          # compacted for speech
    assert len(out["result"]) <= 2500


def test_a_failed_search_is_an_error_not_an_invented_result(monkeypatch):
    from friday import contracts as c
    from friday.toolsets import web as W

    async def dead(run, query, *, limit=8, engine=None):
        return c.failed(c.started(run.run_id, "web.search"),
                        "no search provider answered")

    monkeypatch.setattr(W, "web_search", dead)
    out = V._run_capability("web", "search", {"query": "anything"})
    assert out == {"error": "no search provider answered"}


# ---------------------------------------------------------------------------
# history replay: her own excuses are not fed back to her
# ---------------------------------------------------------------------------

POISON = [
    "My apologies, sir. It seems the research skill is still encountering an issue. "
    "I am unable to directly access e-commerce market trends at this moment.",
    "Sir, the situation remains unchanged. I am still unable to directly search for "
    "foreign market trends due to an issue with the 'research' capability, and I "
    "cannot view your screen as the 'vision' capability is unavailable.",
    "My apologies, sir. It seems I am unable to directly view your screen at this "
    "moment. The 'vision' capability is not currently available.",
    "I believe you're asking what was going on with the research skill, sir. It "
    "appears there's a temporary issue with that specific capability.",
    "Sir, I did try the research skill again, but it seems to be consistently "
    "failing. It states that \"'' is not an offered skill\" which is not very helpful.",
    "Sir, I cannot directly help you with \"travel\" as a command. My current "
    "capabilities do not include booking or planning travel.",
    "I am investigating the active capabilities, sir. I will report back when I "
    "have a clearer picture.",
    "Sir, I understand you've acknowledged the current limitations. I've initiated "
    "an audit of my capabilities to get a clearer picture.",
]

REAL = [
    "Noted, sir.",
    "Just a quiet day so far, sir. My systems are all nominal.",
    "I can, sir. What topic would you like me to research?",
    "Online. GBrain available, Hermes degraded, 169 tools, RAM 94.3%.",
    "The error on line 3 is a typo, sir - a missing colon after the def.",
    "I've searched for foreign market trends, sir. Reuters, Investing.com and CNBC "
    "all have current coverage; the IMF publishes a daily Global Markets Monitor.",
    "The research on that is thin, but the Reuters piece is the one to read.",
    "I can see a few things that might be considered errors. The lighting is uneven "
    "and the top of your head is cut off.",
    "Search returned eight results; the top one is State Street's 2026 outlook.",
]


@pytest.mark.parametrize("text", POISON)
def test_a_capability_excuse_is_recognised(text):
    assert V.is_stale_excuse(text), text


@pytest.mark.parametrize("text", REAL)
def test_a_real_answer_is_not_mistaken_for_an_excuse(text):
    assert not V.is_stale_excuse(text), text


def test_replay_drops_her_excuses_but_keeps_what_the_boss_said(monkeypatch):
    rows = [
        {"role": "user", "content": "hey what's going on"},
        {"role": "assistant", "content": POISON[0]},
        {"role": "user", "content": "try again"},
        {"role": "assistant", "content": POISON[1]},
        {"role": "user", "content": "what is your status"},
        {"role": "assistant", "content": REAL[3]},
        {"role": "user", "content": "the current question (dropped: it is the live turn)"},
    ]

    class FakeStore:
        def recent_messages(self, limit=30):
            return rows[-limit:]

    import friday.toolsets.memory as M
    monkeypatch.setattr(M, "store", lambda: FakeStore())

    turns = V._recent_turns()
    texts = [t for _, t in turns]
    assert POISON[0] not in texts and POISON[1] not in texts
    assert "hey what's going on" in texts and "try again" in texts
    assert REAL[3] in texts
    assert turns[0][0] == "user"                    # Gemini needs user-first
    assert "the current question" not in " ".join(texts)


def test_replay_never_opens_on_a_model_turn(monkeypatch):
    rows = [
        {"role": "assistant", "content": REAL[0]},   # orphaned model turn first
        {"role": "user", "content": "hi"},
        {"role": "assistant", "content": REAL[1]},
        {"role": "user", "content": "live turn"},
    ]

    class FakeStore:
        def recent_messages(self, limit=30):
            return rows[-limit:]

    import friday.toolsets.memory as M
    monkeypatch.setattr(M, "store", lambda: FakeStore())
    turns = V._recent_turns()
    assert turns and turns[0] == ("user", "hi")


# ---------------------------------------------------------------------------
# the prompt: history is not evidence, tool errors are not for him
# ---------------------------------------------------------------------------

def test_persona_tells_her_history_is_not_evidence_and_to_retry():
    p = V.PERSONA.lower()
    assert "not evidence" in p
    assert "try again" in p
    assert "never read an error string aloud" in p
    assert "search the web" in p


def test_tool_description_routes_world_questions_to_web_not_research():
    d = V._capability_tool().function_declarations[0].description.lower()
    assert "web/search" in d
    assert "skill names" in d and "never facts" in d


# ---------------------------------------------------------------------------
# screen access + PC control from the browser path
# ---------------------------------------------------------------------------

def test_desktop_family_is_offered():
    fams = V._surface()
    assert {"plan", "step", "stop", "point"} <= fams["desktop"]
    d = V._capability_tool().function_declarations[0].description.lower()
    assert "desktop/plan" in d and "desktop/stop" in d


def test_a_forbidden_task_is_refused_before_the_screen_is_read(monkeypatch):
    import json
    from friday.toolsets import vision
    monkeypatch.setattr(vision, "capture_screen",
                        lambda **k: (_ for _ in ()).throw(AssertionError("captured")))
    out = V._run_capability("desktop", "plan", {"task": "type my bank password"})
    d = json.loads(out["result"])
    assert d["result"] == "refused"


def test_a_step_without_an_approved_plan_does_nothing():
    import json
    out = V._run_capability("desktop", "step", {"nonce": "forged"})
    assert json.loads(out["result"])["result"] == "no_plan"


def test_stop_is_never_gated():
    import json
    out = V._run_capability("desktop", "stop", {})
    assert json.loads(out["result"])["status"] == "succeeded"


def test_a_plan_returns_a_nonce_and_touches_nothing(monkeypatch):
    import json
    from friday.toolsets import desktop as D
    from friday.toolsets import vision

    class Frame:
        pass
    monkeypatch.setattr(vision, "capture_screen", lambda **k: Frame())
    monkeypatch.setattr(D, "_propose", lambda frame, task: {
        "possible": True,
        "steps": [{"action": "click", "target": "Start button", "say": "click Start"}]})
    clicked = []
    monkeypatch.setattr(D, "_precheck", lambda *a, **k: None)
    # Below DANGEROUS a plan waits for a yes; DANGEROUS (the default since
    # 2026-09-03 18:00) runs it - covered in tests/test_autonomy_and_selfcheck.py.
    from friday import policy
    monkeypatch.setattr(policy, "default_engine", policy.PolicyEngine(autonomy=policy.FULL))
    out = V._run_capability("desktop", "plan", {"task": "open the start menu"})
    d = json.loads(out["result"])
    assert d["result"] == "planned" and d["confirm"]["nonce"]
    assert d["plan"] == ["1. click Start"]
    assert clicked == []


# ---------------------------------------------------------------------------
# one voice on both paths
# ---------------------------------------------------------------------------

def test_ui_voice_is_openai_nova_unless_opted_out(monkeypatch):
    """Her voice changed under the owner because a DEEPGRAM key in .env
    flipped the UI to Aura while the LiveKit agent stayed on nova."""
    from friday import ui_server as u
    monkeypatch.setenv("DEEPGRAM_API_KEY", "present")
    monkeypatch.delenv("TTS_PROVIDER", raising=False)
    assert u._tts_provider() == "openai"
    monkeypatch.setenv("TTS_PROVIDER", "deepgram")
    assert u._tts_provider() == "deepgram"


def test_ui_and_livekit_agree_on_voice_and_speed_defaults():
    import inspect

    from friday import providers, ui_server as u
    assert 'voice="nova"' in inspect.getsource(providers.build_tts)
    src = inspect.getsource(u._openai_tts)
    assert 'os.getenv("TTS_VOICE", "nova")' in src
    assert 'os.getenv("TTS_SPEED", "1.0")' in src
    assert os.getenv("TTS_SPEED", "1.0")  # the shared knob exists


def test_reply_speaks_even_when_the_tool_loop_ends_without_words(monkeypatch):
    """Round cap reached with the model still calling tools -> she must still
    say something from what she found, never "..." (2026-09-03 live: two
    roles reads, then an empty reply)."""
    from types import SimpleNamespace
    from google.genai import types
    import friday.voice_brain as V

    calls = {"n": 0}

    class Resp:
        def __init__(self, text, fcalls, content):
            self.text = text
            self.function_calls = fcalls
            self.candidates = [SimpleNamespace(content=content)]

    class Models:
        def generate_content(self, model, contents, config):
            calls["n"] += 1
            if getattr(config, "tools", None):
                fc = types.FunctionCall(name="use_capability", args={
                    "family": "roles", "operation": "agents", "arguments": {}})
                return Resp("", [fc], types.Content(
                    role="model", parts=[types.Part(function_call=fc)]))
            return Resp("Two sentences, sir.", [], None)

    client = SimpleNamespace(models=Models())
    monkeypatch.setattr(V, "_model", lambda: (client, "fake-model"))
    monkeypatch.setattr(V, "_try_command", lambda text: None)
    monkeypatch.setattr(V, "_recent_turns", lambda limit=None: [])
    monkeypatch.setattr(V, "_memory_context", lambda text: "")
    monkeypatch.setattr(V, "_remember_turn", lambda role, text: None)
    monkeypatch.setattr(V, "_run_capability", lambda fam, op, args: {"result": "ok"})

    out = V.reply("act as a scrum master and open the sprint")

    assert out["reply"] == "Two sentences, sir."
    # first call + one per round + the forced, tool-free answer
    assert calls["n"] == V._MAX_TOOL_ROUNDS + 2
    assert out["used_capabilities"] == ["roles"] * V._MAX_TOOL_ROUNDS


# ---------------------------------------------------------------------------
# work/status - "what's running", read on demand from the same digest the
# room speaks from (S3b)
# ---------------------------------------------------------------------------

def test_work_family_is_offered_with_a_status_op():
    fams = V._surface()
    assert fams.get("work") == {"status"}


def test_work_status_reports_nothing_running_with_no_active_runs(monkeypatch):
    from friday import progress_digest as pd
    monkeypatch.setattr(pd, "gather", lambda sup, **k: [])
    import friday.tools.hermes_control as hc
    monkeypatch.setattr(hc, "supervisor", lambda: object())
    out = V._run_capability("work", "status", {})
    assert out["result"] == "nothing running right now"


def test_whats_running_phrase_triggers_work_status_without_a_model(monkeypatch):
    monkeypatch.setattr(V, "_run_work", lambda op, args: {"result": "did 2 tools, last: editing policy.py"})
    out = V._try_command("what's running right now?")
    assert out is not None
    assert out["action"] == "work.status"
    assert out["used_capabilities"] == ["work"]
    assert "editing policy.py" in out["reply"]
