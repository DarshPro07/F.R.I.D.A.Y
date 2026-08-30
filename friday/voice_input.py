"""Keep LiveKit microphone state aligned with actual audio processing."""
from __future__ import annotations
from friday.runtime_metrics import RuntimeTelemetry


def _is_microphone(publication) -> bool:
    source = getattr(publication, 'source', None)
    return str(source).lower().endswith('microphone')


class VoiceInputGate:
    """Detach audio input when every observed microphone publication is muted."""

    def __init__(self, session, telemetry: RuntimeTelemetry) -> None:
        self.session = session
        self.telemetry = telemetry
        self._room = None
        self._seen_microphone = False

    @property
    def accepting_audio(self) -> bool:
        return bool(self.session.input.audio_enabled)

    def set_muted(self, muted: bool) -> None:
        self.session.input.set_audio_enabled(not muted)
        self.telemetry.mark('voice_input_state', status='muted' if muted else 'listening')

    def attach(self, room) -> None:
        self._room = room
        for event_name in ('track_muted', 'track_unmuted', 'track_published', 'track_unpublished', 'participant_connected', 'participant_disconnected'):
            room.on(event_name, self._sync_from_room)
        self._sync_from_room()

    def _sync_from_room(self, *args) -> None:
        del args
        if self._room is None:
            return
        publications = []
        for participant in getattr(self._room, 'remote_participants', {}).values():
            tracks = getattr(participant, 'track_publications', {})
            publications.extend((publication for publication in tracks.values() if _is_microphone(publication)))
        if publications:
            self._seen_microphone = True
            self.set_muted(all((bool(getattr(item, 'muted', False)) for item in publications)))
            return
        if self._seen_microphone:
            self.set_muted(True)

    def describe(self) -> dict:
        return {'audio_input_enabled': self.accepting_audio, 'microphone_state': 'listening' if self.accepting_audio else 'muted'}