"""Semantic delivery vs voice rendering - separate decisions, one message.

The three cases from the Phase 1F contract:
  A. voice session (audio output attached)  -> spoken + in chat
  B/C. text-only session (no audio output)  -> in chat, no speech attempt
Plus: the mode check reads REAL session output state, never the request's
origin.
"""
import asyncio
import pytest
import agent_friday


class _Audio:
    def __init__(self, enabled=True):
        self.enabled = enabled


class _Output:
    def __init__(self, audio):
        self.audio = audio


class _Session:
    def __init__(self, audio):
        self.output = _Output(audio)
        self.say_calls = []

    async def say(self, text, **kwargs):
        self.say_calls.append((text, kwargs))


def test_voice_session_allows_speech():
    session = _Session(_Audio(enabled=True))
    assert agent_friday._session_allows_speech(session) is True


def test_disabled_audio_is_text_mode():
    session = _Session(_Audio(enabled=False))
    assert agent_friday._session_allows_speech(session) is False


def test_missing_audio_output_is_text_mode():
    session = _Session(None)
    assert agent_friday._session_allows_speech(session) is False


def test_broken_session_defaults_to_silent():
    class Hostile:
        @property
        def output(self):
            raise RuntimeError('no')
    assert agent_friday._session_allows_speech(Hostile()) is False


def test_delivery_routes_by_mode():
    voice = _Session(_Audio(enabled=True))
    via = asyncio.run(agent_friday.deliver_message(voice, 'findings'))
    assert via == 'say+audio'
    assert voice.say_calls[0][0] == 'findings'
    text_only = _Session(None)
    via = asyncio.run(agent_friday.deliver_message(text_only, 'findings'))
    assert via == 'say+text'
    assert text_only.say_calls[0][0] == voice.say_calls[0][0]