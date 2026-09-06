from __future__ import annotations
import asyncio
import inspect
from datetime import datetime, timezone
from friday.response_render import ResponseStyleState, normalize_opening, render_speech_stream, speech_text


async def chunks(values):
    for value in values:
        yield value


async def rendered(values):
    return ''.join([part async for part in render_speech_stream(chunks(values))])


def test_speech_renderer_removes_markdown_tables_urls_and_code_across_chunks():
    source = ['## Update\n| Name | Link |\n|---|---|\n| Alpha | https://exa', 'mple.com/private |\nThe result is **ready**.\n```py\npri', "nt('do not speak')\n```\nDone."]
    spoken = asyncio.run(rendered(source))
    assert 'Update' in spoken
    assert 'Alpha' in spoken
    assert 'result is ready' in spoken
    assert 'Done' in spoken
    for forbidden in ('http', '|', '```', 'print', '**'):
        assert forbidden not in spoken
    assert source[0].startswith('## Update')


def test_inline_links_keep_the_label_without_reading_the_url():
    spoken = speech_text('Read [the report](https://example.com/report), not `rm -rf` or www.example.com.')
    assert 'the report' in spoken
    assert 'http' not in spoken and 'www' not in spoken
    assert 'rm -rf' not in spoken


def test_greeting_uses_context_and_does_not_repeat_after_reopen(tmp_path):
    path = tmp_path / 'style.json'
    now = datetime(2026, 8, 19, 23, 30, tzinfo=timezone.utc)
    first = ResponseStyleState(path).greeting(now)
    second = ResponseStyleState(path).greeting(now)
    active = ResponseStyleState(path).greeting(now, active_run=True)
    assert first != second
    assert 'late' in (first + second).lower() or 'awake' in (first + second).lower()
    assert 'active' in active.lower() or 'objective' in active.lower()


def test_late_night_greeting_restores_the_requested_paused_line(tmp_path):
    greeting = ResponseStyleState(tmp_path / 'style.json').greeting(datetime(2026, 8, 19, 23, 30, tzinfo=timezone.utc))
    assert greeting == "Hey, boss... You're up late... What are you up to?"


def test_morning_greetings_pause_and_rotate_before_reuse(tmp_path):
    path = tmp_path / 'style.json'
    now = datetime(2026, 8, 20, 8, 30, tzinfo=timezone.utc)
    greetings = [ResponseStyleState(path).greeting(now) for _ in range(3)]
    assert len(set(greetings)) == 3
    assert all(('...' in greeting for greeting in greetings))
    assert 'Morning... Where should we start?' in greetings


def test_default_voice_rate_is_natural_and_still_configurable(monkeypatch):
    import agent_friday as A
    monkeypatch.delenv('TTS_SPEED', raising=False)
    assert A.session_config()['tts_speed'] == 1.0
    assert inspect.signature(A.providers.build_tts).parameters['speed'].default == 1.0
    monkeypatch.setenv('TTS_SPEED', '0.92')
    assert A.session_config()['tts_speed'] == 0.92


def test_acknowledgement_opening_does_not_repeat_within_previous_four(tmp_path):
    state = ResponseStyleState(tmp_path / 'style.json')
    lines = [state.acknowledgement('ACK_AND_ACT', 'apps_open') for _ in range(8)]
    openings = [normalize_opening(line) for line in lines]
    for index, opening in enumerate(openings):
        assert opening not in openings[max(0, index - 4):index]
    assert sum(('boss' in line.lower() for line in lines)) <= 1


class _Turn:
    """Minimal stand-in for FridayAgent: only what the gate reads."""

    def __init__(self, acted=()):
        self._acted_this_turn = tuple(acted)

    _refuse_unbacked_claim = None       # bound below from the real class



def test_friday_tts_node_cleans_only_the_tts_stream(monkeypatch):
    import agent_friday as A
    captured = []

    async def fake_tts(agent, text, model_settings):
        async for part in text:
            captured.append(part)
        if False:
            yield
    monkeypatch.setattr(A.Agent.default, 'tts_node', fake_tts)
    original = ['See **status** at https://exa', 'mple.com. All good.']

    # tts_node now runs the completion gate, which reads `_acted_this_turn`
    # off the agent - so `self` has to be an object that has it, not a bare
    # `object()`. Nothing here claims completion, so the gate is a no-op.
    agent = _Turn(("desktop_plan",))
    agent._refuse_unbacked_claim = A.FridayAgent._refuse_unbacked_claim.__get__(agent)

    async def consume():
        stream = A.FridayAgent.tts_node(agent, chunks(original), object())
        return [frame async for frame in stream]
    assert asyncio.run(consume()) == []
    assert 'http' not in ''.join(captured)
    assert 'status' in ''.join(captured)
    assert original[0].startswith('See **status**')

# ---------------------------------------------------------------------------
# The spoken completion gate (the voice twin of TestTheCompletionGate).
#
# 2026-09-06: on the browser path Friday said "I have opened the Start Menu for
# you, sir" having called nothing at all. On THIS path the same sentence is
# spoken aloud, so the gate sits in tts_node - on the first flush only, because
# buffering the whole answer would cost the time-to-first-word this node exists
# to protect.
# ---------------------------------------------------------------------------

def _spoken(acted, said):
    import agent_friday as A
    captured = []

    async def fake_tts(agent, text, model_settings):
        async for part in text:
            captured.append(part)
        if False:
            yield

    agent = _Turn(acted)
    # The real method, bound to the stand-in: the gate under test, not a copy.
    agent._refuse_unbacked_claim = A.FridayAgent._refuse_unbacked_claim.__get__(agent)

    async def consume():
        import agent_friday as AA
        old = AA.Agent.default.tts_node
        AA.Agent.default.tts_node = fake_tts
        try:
            stream = AA.FridayAgent.tts_node(agent, chunks(said), object())
            return [f async for f in stream]
        finally:
            AA.Agent.default.tts_node = old

    asyncio.run(consume())
    return "".join(captured)


def test_an_unbacked_spoken_claim_is_replaced_before_it_is_synthesised():
    out = _spoken((), ["I have opened the Start Menu ", "for you, sir. Anything else?"])
    assert "not actually done that" in out.lower()
    assert "start menu" not in out.lower()


def test_a_spoken_claim_backed_by_a_real_action_is_spoken_unchanged():
    """The negative case: she DID act, so she may say so."""
    out = _spoken(("desktop_plan",), ["I have opened the Start Menu ", "for you, sir."])
    assert "start menu" in out.lower()
    assert "not actually done" not in out.lower()


def test_an_ordinary_spoken_answer_is_never_gated():
    out = _spoken((), ["It is half past four, sir. ", "The rain has stopped."])
    assert "half past four" in out.lower()
    assert "not actually done" not in out.lower()


def test_a_plan_is_not_an_action_on_the_voice_path_either():
    """`desktop_plan` proposes; `desktop_step` acts. `ownership.is_read_only`
    already draws that line, so `use_capability` records only the second - but
    assert it, because the browser path had this exact bug (2026-09-06: a plan
    licensed "I've opened the Start Menu")."""
    from friday import ownership
    assert ownership.is_read_only("desktop_plan"), (
        "a plan must not count as an action, or it can back a false claim")
    assert not ownership.is_read_only("desktop_step")


def test_the_contraction_the_live_model_used_is_caught_when_spoken():
    out = _spoken((), ["I've opened the Start Menu ", "for you, sir."])
    assert "not actually done that" in out.lower()
