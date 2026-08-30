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

    async def consume():
        stream = A.FridayAgent.tts_node(object(), chunks(original), object())
        return [frame async for frame in stream]
    assert asyncio.run(consume()) == []
    assert 'http' not in ''.join(captured)
    assert 'status' in ''.join(captured)
    assert original[0].startswith('See **status**')