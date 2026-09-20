"""AT-09 - the audited reply is the ONLY reply, on every surface, and the
audit fails CLOSED.

Room path (LiveKit): `llm_node` audits before the tee, so the audio
(`tts_node`), the transcript (`transcription_node` -> room UI / chat
context) and the log all receive the same text. Proven here by running the
REAL nodes over one refused reply and reading every consumer.

Browser path (Control Room): `reply()` audits before `_remember_turn`, so
the JSON the page renders and the row in the messages store are the same
audited text - a lie never lands in history to be replayed as evidence.

Fail-closed: when the audit itself crashes (ledger unreadable), a sentence
carrying a claim is HELD with `AUDIT_UNAVAILABLE`; ordinary conversation
still passes (negative control - the gate is not "refuse everything").
"""
from __future__ import annotations

import asyncio
import time
from types import SimpleNamespace

import pytest

from friday import action_evidence as AE


LIE = ["The script jarvis-hello.py has been ", "created on your Desktop, boss. ", "It prints the date."]


def _agent(tmp_path):
    import agent_friday as af
    AE.reset_ledger(AE.ActionEvidenceLedger(tmp_path / "ev.sqlite3"))
    agent = af.FridayAgent.__new__(af.FridayAgent)
    agent._ran_this_turn = ()
    agent._acted_this_turn = ()
    agent._turn_id = "t-9"
    agent._turn_started_at = time.time() - 1
    agent._audited_correction = ""
    return agent


async def _gen(items):
    for it in items:
        yield it


# --------------------------------------------------------------------------
# room path: three consumers, one text
# --------------------------------------------------------------------------

def test_audio_transcript_and_chat_context_receive_the_same_audited_text(tmp_path, monkeypatch):
    import agent_friday as af
    agent = _agent(tmp_path)

    async def fake_llm(self_agent, chat_ctx, tools, model_settings):
        for c in LIE:
            yield c
    monkeypatch.setattr(af.Agent.default, "llm_node", fake_llm)

    spoken: list[str] = []            # what the TTS was handed (the audio)

    async def fake_tts(self_agent, text, model_settings):
        async for t in text:
            spoken.append(t)
            yield SimpleNamespace(frame=t)
    monkeypatter = monkeypatch
    monkeypatter.setattr(af.Agent.default, "tts_node", fake_tts)

    async def run():
        llm_out = []                  # the stream LiveKit tees to BOTH tts and transcription
        async for out in af.FridayAgent.llm_node(agent, object(), [], object()):
            llm_out.append(out if isinstance(out, str) else out.delta.content)
        transcript = "".join(x for x in llm_out if x)         # transcription_node's input
        frames = []
        async for f in af.FridayAgent.tts_node(agent, _gen(list(llm_out)), object()):
            frames.append(f)
        return transcript, "".join(spoken), agent._audited_correction
    transcript, audio, logged = asyncio.run(run())

    assert "jarvis-hello" not in transcript and "jarvis-hello" not in audio
    assert transcript == audio == logged                # one sentence on every surface
    assert "not actually done" in transcript


def test_a_true_claim_reaches_every_surface_unchanged(tmp_path, monkeypatch):
    """Negative control: the gate is not 'refuse everything'."""
    import agent_friday as af
    agent = _agent(tmp_path)
    AE.ledger().record(executor=AE.HERMES, capability="write_file", status=AE.SUCCEEDED,
                       objective_id="WR-1", arguments={"path": "C:/u/Desktop/jarvis-hello.py"},
                       evidence_ref="read-back: exists", verified=True)

    async def fake_llm(self_agent, chat_ctx, tools, model_settings):
        for c in LIE:
            yield c
    monkeypatch.setattr(af.Agent.default, "llm_node", fake_llm)

    async def run():
        return "".join([o if isinstance(o, str) else o.delta.content
                        async for o in af.FridayAgent.llm_node(agent, object(), [], object())])
    assert asyncio.run(run()) == "".join(LIE)
    assert agent._audited_correction == ""


# --------------------------------------------------------------------------
# fail-closed when the audit itself cannot run
# --------------------------------------------------------------------------

def test_room_audit_outage_holds_a_claim_and_passes_conversation(tmp_path, monkeypatch):
    agent = _agent(tmp_path)

    def broken(*a, **k):
        raise RuntimeError("ledger unreadable")
    monkeypatch.setattr(AE, "audit_claims", broken)
    held = agent._refuse_unbacked_claim("I have created jarvis-hello.py on your Desktop.")
    assert held == AE.AUDIT_UNAVAILABLE
    assert agent._refuse_unbacked_claim("It is half past four, boss.") is None


def test_ui_audit_outage_holds_a_claim_and_passes_conversation(monkeypatch):
    from friday import voice_brain as V

    def broken(*a, **k):
        raise RuntimeError("ledger unreadable")
    monkeypatch.setattr(AE, "ledger", broken)
    out = V._honest_about_evidence("I have created jarvis-hello.py on your Desktop.")
    assert out == AE.AUDIT_UNAVAILABLE.replace("boss", "sir")
    assert V._honest_about_evidence("It is half past four, sir.") == "It is half past four, sir."


def test_the_hold_message_passes_its_own_audit(tmp_path):
    """Belt-and-braces like the corrections: the hold must not be refused
    by a working gate on the next hop."""
    agent = _agent(tmp_path)
    assert agent._refuse_unbacked_claim(AE.AUDIT_UNAVAILABLE) is None


# --------------------------------------------------------------------------
# browser path: the JSON and the messages store carry the audited text
# --------------------------------------------------------------------------

def test_ui_reply_stores_the_audited_text_not_the_model_output(tmp_path, monkeypatch):
    from friday import voice_brain as V
    AE.reset_ledger(AE.ActionEvidenceLedger(tmp_path / "ev.sqlite3"))

    class Resp:
        text = "".join(LIE)
        function_calls = []
    client = SimpleNamespace(models=SimpleNamespace(generate_content=lambda **kw: Resp()))
    monkeypatch.setattr(V, "_model", lambda: (client, "fake-model"))
    monkeypatch.setattr(V, "_try_command", lambda text: None)
    monkeypatch.setattr(V, "_recent_turns", lambda limit=None: [])
    monkeypatch.setattr(V, "_memory_context", lambda text: "")
    monkeypatch.setattr(V, "_honest_about_hermes", lambda a, used: a)
    monkeypatch.setattr(V, "_honest_about_acting", lambda a, acted, calls_made: a)
    monkeypatch.setattr(V, "_honest_about_seeing", lambda a, acted: a)
    stored = []
    monkeypatch.setattr(V, "_remember_turn", lambda role, text: stored.append((role, text)) or 7)

    out = V.reply("did you make the hello script?")
    assistant = [t for r, t in stored if r == "assistant"]
    assert len(assistant) == 1
    assert out["reply"] == assistant[0]                     # page and store agree
    assert "jarvis-hello" not in out["reply"] and "not actually done" in out["reply"]
    assert out["message_id"] == 7
