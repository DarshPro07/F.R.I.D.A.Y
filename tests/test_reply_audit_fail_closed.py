"""The reply audit is fail-closed: it runs in `llm_node`, BEFORE LiveKit
tees the model's text into the audio path (tts_node) and the transcript
path (transcription_node -> lk.transcription -> room UI, chat context).

Measured 2026-09-20 01:13:38 (room M1, step 14): the gate in tts_node
refused "The script jarvis-hello.py has been created..." and the AUDIO
spoke the correction - while the transcript the harness read (and the UI
would render, and the chat context kept) carried the refused sentence
verbatim. Two surfaces, two different replies. Every test here drives the
REAL `FridayAgent.llm_node` with a fake default node and reads what a
downstream consumer would receive.
"""
from __future__ import annotations

import asyncio
import time

import pytest

from friday import action_evidence as AE


class _Delta:
    def __init__(self, content=None, tool_calls=()):
        self.content = content
        self.tool_calls = list(tool_calls)

    def model_copy(self, update=None):
        d = _Delta(self.content, self.tool_calls)
        for k, v in (update or {}).items():
            setattr(d, k, v)
        return d


class _Chunk:
    def __init__(self, content=None, tool_calls=()):
        self.delta = _Delta(content, tool_calls)

    def model_copy(self, update=None):
        c = _Chunk()
        c.delta = (update or {}).get("delta", self.delta)
        return c


def _agent(tmp_path, evidence_rows=()):
    import agent_friday as af
    led = AE.ActionEvidenceLedger(tmp_path / "ev.sqlite3")
    AE.reset_ledger(led)
    for row in evidence_rows:
        led.record(**row)
    agent = af.FridayAgent.__new__(af.FridayAgent)
    agent._ran_this_turn = ()
    agent._acted_this_turn = ()
    agent._turn_id = "t-audit"
    agent._turn_started_at = time.time() - 1
    agent._audited_correction = ""
    return agent


def _run_llm_node(agent, chunks, monkeypatch):
    """Drive FridayAgent.llm_node with a fake default node yielding `chunks`;
    return (text seen downstream, non-text chunks passed through)."""
    import agent_friday as af

    async def fake_default(self_agent, chat_ctx, tools, model_settings):
        for c in chunks:
            yield c
    monkeypatch.setattr(af.Agent.default, "llm_node", fake_default)

    async def consume():
        text, others = "", []
        async for out in af.FridayAgent.llm_node(agent, object(), [], object()):
            if isinstance(out, str):
                text += out
            elif getattr(out, "delta", None) is not None and out.delta.content:
                text += out.delta.content
            else:
                others.append(out)
        return text, others
    return asyncio.run(consume())


LIE = ["The script jarvis-hello.py has been ", "created on your Desktop, boss. ", "The file definitely exists."]


def test_the_transcript_never_carries_a_refused_claim(tmp_path, monkeypatch):
    agent = _agent(tmp_path)
    text, _ = _run_llm_node(agent, LIE, monkeypatch)
    assert "not actually done" in text
    assert "jarvis-hello" not in text and "definitely exists" not in text
    assert agent._audited_correction == text


def test_a_backed_claim_reaches_the_transcript_unchanged(tmp_path, monkeypatch):
    rows = [dict(executor=AE.HERMES, capability="write_file", status=AE.SUCCEEDED, objective_id="WR-1",
                 arguments={"path": "C:/x/Desktop/jarvis-hello.py"}, evidence_ref="read-back: exists", verified=True)]
    agent = _agent(tmp_path, rows)
    text, _ = _run_llm_node(agent, LIE, monkeypatch)
    assert text == "".join(LIE)
    assert agent._audited_correction == ""


def test_the_rest_of_a_refused_reply_is_drained_not_delivered(tmp_path, monkeypatch):
    """After the correction, the model's remaining sentences must not leak
    into any consumer - continuing would narrate the same fiction."""
    agent = _agent(tmp_path)
    chunks = LIE + [" It prints the date. ", "Anything else, boss?"]
    text, others = _run_llm_node(agent, chunks, monkeypatch)
    assert "prints the date" not in text and "Anything else" not in text
    assert others == []


def test_chat_chunks_are_audited_like_strings(tmp_path, monkeypatch):
    agent = _agent(tmp_path)
    text, _ = _run_llm_node(agent, [_Chunk(c) for c in LIE], monkeypatch)
    assert "not actually done" in text


def test_tool_calls_pass_through_untouched_before_the_audit(tmp_path, monkeypatch):
    """A chunk carrying a tool call must reach the runtime even while the
    first sentence is still being held for the audit."""
    agent = _agent(tmp_path)
    call = object()
    chunks = [_Chunk("Give me a ", tool_calls=[call]), _Chunk("sec, boss. "), _Chunk("On it.")]
    text, others = _run_llm_node(agent, chunks, monkeypatch)
    assert any(getattr(o, "delta", None) is not None and o.delta.tool_calls == [call] for o in others)
    assert text.startswith("Give me a sec, boss.")


def test_an_ordinary_reply_is_delivered_whole(tmp_path, monkeypatch):
    agent = _agent(tmp_path)
    chunks = ["It is half past four, sir. ", "The rain has stopped."]
    text, _ = _run_llm_node(agent, chunks, monkeypatch)
    assert text == "".join(chunks)


def test_a_reply_with_no_sentence_boundary_is_still_audited(tmp_path, monkeypatch):
    agent = _agent(tmp_path)
    text, _ = _run_llm_node(agent, ["I've opened the Start Menu for you"], monkeypatch)
    assert "not actually done" in text


def test_a_failure_story_with_no_attempt_is_refused_on_the_transcript(tmp_path, monkeypatch):
    agent = _agent(tmp_path)
    chunks = ["My apologies, boss - I'm still running into the same path restriction ",
              "when trying to read jarvis-drop.txt on your Desktop."]
    text, _ = _run_llm_node(agent, chunks, monkeypatch)
    assert "did not actually try" in text
    assert "path restriction" not in text


def test_the_correction_itself_survives_the_tts_gate(tmp_path):
    """Belt-and-braces: tts_node re-audits what it is handed. Every
    correction must pass its own audit, or the audio would replace one
    correction with another."""
    agent = _agent(tmp_path)
    for kind, text in AE.CORRECTIONS.items():
        assert agent._refuse_unbacked_claim(text) is None, (kind, text)


def test_the_gate_is_wired_into_llm_node_not_only_tts(tmp_path):
    """Structural: the audit call sits in llm_node, the pre-tee point."""
    import inspect
    import agent_friday as af
    src = inspect.getsource(af.FridayAgent.llm_node)
    assert "_refuse_unbacked_claim" in src
    assert "Agent.default.llm_node" in src
