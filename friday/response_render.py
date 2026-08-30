"""Speech-only cleanup and bounded, project-anchored phrasing variation."""
from __future__ import annotations
import json
import re
from collections.abc import AsyncIterable, AsyncIterator, Sequence
from datetime import datetime
from pathlib import Path
from friday.config import DATA_DIR
STYLE_STATE_PATH = DATA_DIR / 'response_style.json'
_URL = re.compile('(?:https?://|www\\.)\\S+', re.I)
_MARKDOWN_LINK = re.compile('!?\\[([^\\]]*)\\]\\((?:https?://|www\\.)[^)]+\\)', re.I)
_INLINE_CODE = re.compile('`[^`]*`')
_TABLE_RULE = re.compile('^\\s*\\|?(?:\\s*:?-{3,}:?\\s*\\|)+\\s*$', re.M)
_LIST_PREFIX = re.compile('(?m)^\\s*(?:#{1,6}\\s+|[-*+]\\s+|\\d+[.)]\\s+)')
_SPACE = re.compile('\\s+')
_OPENING = re.compile('[^a-z0-9 ]+')


def speech_text(markdown: str) -> str:
    """Convert substantive markdown to natural speech without URLs or code."""
    text = _MARKDOWN_LINK.sub(lambda match: match.group(1), markdown or '')
    text = _URL.sub('', text)
    text = _INLINE_CODE.sub('', text)
    text = _TABLE_RULE.sub('', text)
    text = _LIST_PREFIX.sub('', text)
    text = re.sub('[*_~]+', '', text)
    text = text.replace('|', ', ')
    text = re.sub('<[^>]+>', '', text)
    text = _SPACE.sub(' ', text).strip(' ,')
    return text


async def render_speech_stream(chunks: AsyncIterable[str]) -> AsyncIterator[str]:
    """Clean stream boundaries without emitting partial URLs or fenced code."""
    buffer = ''
    in_code = False
    async for chunk in chunks:
        buffer += str(chunk)
        while True:
            fence = buffer.find('```')
            if in_code:
                if fence < 0:
                    buffer = buffer[-2:]
                    break
                buffer = buffer[fence + 3:]
                in_code = False
                continue
            if fence >= 0:
                cleaned = speech_text(buffer[:fence])
                if cleaned:
                    yield cleaned + ' '
                buffer = buffer[fence + 3:]
                in_code = True
                continue
            boundary = re.search('\\n|(?<=[.!?])\\s+', buffer)
            if boundary is None:
                break
            end = boundary.end()
            cleaned = speech_text(buffer[:end])
            buffer = buffer[end:]
            if cleaned:
                yield cleaned + ' '
    if not in_code:
        cleaned = speech_text(buffer)
        if cleaned:
            yield cleaned


def normalize_opening(line: str) -> str:
    words = _OPENING.sub(' ', line.lower()).split()
    return ' '.join(words[:4])


class ResponseStyleState:
    """Persist only bounded style choices, never conversation content."""

    def __init__(self, path: Path = STYLE_STATE_PATH) -> None:
        self.path = path
        self.last_greeting = ''
        self.recent_openings = []
        self._load()

    def _load(self) -> None:
        try:
            body = json.loads(self.path.read_text(encoding='utf-8'))
        except (OSError, ValueError, TypeError):
            return
        self.last_greeting = str(body.get('last_greeting', '') or '')[:240]
        openings = body.get('recent_openings', [])
        if isinstance(openings, list):
            self.recent_openings = [str(value)[:80] for value in openings[-4:]]

    def _save(self) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self.path.with_suffix(self.path.suffix + '.tmp')
            temporary.write_text(json.dumps({'last_greeting': self.last_greeting, 'recent_openings': self.recent_openings[-4:]}, indent=2), encoding='utf-8')
            temporary.replace(self.path)
        except OSError:
            pass

    def choose_line(self, key: str, options: Sequence[str]) -> str:
        del key
        if not options:
            return ''
        selected = next((line for line in options if normalize_opening(line) not in self.recent_openings), options[0])
        opening = normalize_opening(selected)
        self.recent_openings = (self.recent_openings + [opening])[-4:]
        self._save()
        return selected

    def acknowledgement(self, mode: str, tool_name: str) -> str:
        del tool_name
        if mode == 'ANNOUNCE_THEN_ACT':
            options = ('One moment.', 'Coming right up.', 'Let me set that up.', "I'll get that started.", 'Right away.')
        else:
            options = ('On it.', 'Right away.', 'Consider it handled.', "I'll take care of that.", 'Leave it with me.')
        return self.choose_line(mode, options)

    def greeting(self, now: datetime, *, active_run: bool = False) -> str:
        if active_run:
            options = ('Welcome back... I still have the active task in hand.', "You're back... The active work is still tracked.", 'Back again... I still have it.')
        elif now.hour < 5 or now.hour >= 22:
            options = ("Hey, boss... You're up late... What are you up to?", "Still up, boss... What's on your mind?", 'Late one tonight... What are we working on?', 'Hey... What needs attention?')
        elif now.hour < 12:
            options = ('Morning... Where should we start?', "Good morning... What's first?", 'Ready when you are... What are we working on?')
        elif now.hour < 17:
            options = ('Good afternoon... What do you need?', "Afternoon... What's first?", "I'm here... What are we tackling?")
        else:
            options = ('Good evening... What are you up to?', 'Evening... What should we take on?', "Back again... What's next?")
        selected = next((line for line in options 
if line != self.last_greeting 
if normalize_opening(line) not in self.recent_openings), next((line for line in options if line != self.last_greeting), options[0]))
        self.last_greeting = selected
        self.recent_openings = (self.recent_openings + [normalize_opening(selected)])[-4:]
        self._save()
        return selected