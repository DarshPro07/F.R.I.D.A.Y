"""Execution ownership and pre-dispatch single-flight controls."""
from __future__ import annotations
import hashlib
import json
import re
from dataclasses import dataclass
from typing import Any, Callable, NoReturn, cast
from livekit.agents.llm.mcp import MCPServerHTTP
from livekit.agents.llm.tool_context import function_tool
from livekit.agents.voice.events import RunContext
from friday import capabilities
from friday.autolearn import payload
from friday.continuity import AttemptReservation, ContinuityManager, PortionClaim
from friday.runtime_metrics import RuntimeTelemetry
BOUND = 'BOUND'
ASSOCIATED = 'ASSOCIATED'
DUPLICATE = 'DUPLICATE'
REVOKED = 'REVOKED'
ANNOUNCE_THEN_ACT = 'ANNOUNCE_THEN_ACT'
ACK_AND_ACT = 'ACK_AND_ACT'
ACT_THEN_CONFIRM = 'ACT_THEN_CONFIRM'
SILENT_BACKGROUND = 'SILENT_BACKGROUND'
_SOUND_PRODUCING = frozenset({'music_resume', 'music_play', 'spotify_play', 'music_play_mood', 'spotify_resume'})
_FAST_ACTIONS = frozenset({'open_world_monitor', 'apps_open', 'apps_focus', 'open_finance_world_monitor', 'browser_open'})
_ACKNOWLEDGEMENTS = {ANNOUNCE_THEN_ACT: 'One moment, boss.', ACK_AND_ACT: 'On it.'}


class StaleExecutionOwner(RuntimeError):
    """A speech handle is not authorized for the current durable claim."""
    pass


def canonical_execution_key(task_id: str, tool_name: str, arguments: dict) -> str:
    if not isinstance(arguments, dict):
        raise TypeError('tool arguments must be an object')
    encoded = json.dumps({'task_id': task_id, 'tool': tool_name, 'arguments': arguments}, sort_keys=True, separators=(',', ':'), ensure_ascii=True, default=str).encode('utf-8')
    return hashlib.sha256(encoded).hexdigest()


@dataclass(frozen=True)
class OwnerDecision:
    state: str
    speech_id: str
    reason: str = ''


@dataclass(frozen=True)
class ExecutionOwner:
    claim: PortionClaim
    speech_id: str
    speech_handle: object | None = None
    state: str = BOUND


@dataclass(frozen=True)
class ToolAuthorization:
    claim: PortionClaim
    speech_id: str
    tool_name: str
    arguments: dict
    execute: bool
    status: str
    reservation: AttemptReservation | None = None


@dataclass(frozen=True)
class ActionPresentationPlan:
    tool_name: str
    mode: str
    acknowledgement: str = ''
    sound_producing: bool = False


class ActionPresentationPolicy:
    """Select deterministic speech/action ordering from the concrete tool."""

    def __init__(self,
                 acknowledgement: Callable[[str, str], str] | None = None) -> None:
        self._acknowledgement = acknowledgement

    def _line(self, mode: str, tool_name: str) -> str:
        if self._acknowledgement is not None:
            return self._acknowledgement(mode, tool_name)
        return _ACKNOWLEDGEMENTS[mode]

    def plan(self, tool_name: str, arguments: dict) -> ActionPresentationPlan:
        del arguments
        if tool_name in _SOUND_PRODUCING:
            return ActionPresentationPlan(tool_name, ANNOUNCE_THEN_ACT, self._line(ANNOUNCE_THEN_ACT, tool_name), sound_producing=True)
        if tool_name in _FAST_ACTIONS:
            return ActionPresentationPlan(tool_name, ACK_AND_ACT, self._line(ACK_AND_ACT, tool_name))
        if not _is_side_effecting(tool_name):
            return ActionPresentationPlan(tool_name, SILENT_BACKGROUND)
        return ActionPresentationPlan(tool_name, ACT_THEN_CONFIRM)

    async def present(self, context: RunContext, plan: ActionPresentationPlan, telemetry: RuntimeTelemetry) -> None:
        if not plan.acknowledgement:
            return
        handle = context.session.say(plan.acknowledgement, allow_interruptions=False, add_to_chat_ctx=False)
        telemetry.mark('ack_started', speech_id=str(getattr(handle, 'id', '') or ''), tool_name=plan.tool_name)
        if plan.mode == ANNOUNCE_THEN_ACT:
            await handle.wait_for_playout()
            telemetry.mark('ack_played_out', speech_id=str(getattr(handle, 'id', '') or ''), tool_name=plan.tool_name)


def _is_side_effecting(tool_name: str) -> bool:
    declared = capabilities.by_id(tool_name)
    if declared is None:
        return True
    return declared.side_effect != 'read'


def _result_body(raw: object) -> dict:
    body = payload(raw)
    if isinstance(body, dict):
        return body
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        try:
            decoded = json.loads(raw)
        except (TypeError, ValueError):
            return {}
        if isinstance(decoded, dict):
            if decoded.get('type') == 'text' and isinstance(decoded.get('text'), str):
                try:
                    nested = json.loads(decoded['text'])
                except (TypeError, ValueError):
                    return decoded
                return nested if isinstance(nested, dict) else decoded
            return decoded
    return {}


def _milestone_part(value: object) -> str:
    text = re.sub('[^a-z0-9]+', '-', str(value or '').lower()).strip('-')
    return text[:80]


def narration_milestone(tool_name: str, raw_result: object, *, task_id: str) -> str:
    body = _result_body(raw_result)
    if body.get('status') != 'succeeded' or body.get('may_claim_completion') is not True:
        return ''
    output = body.get('output') if isinstance(body.get('output'), dict) else {}
    state_by_tool = {'apps_open': ('app', 'opened'), 'music_play': ('music', 'playing'), 'music_play_mood': ('music', 'playing'), 'music_next': ('music', 'playing'), 'music_pause': ('music', 'paused'), 'music_resume': ('music', 'resumed'), 'music_stop': ('music', 'stopped'), 'spotify_open': ('spotify', 'opened'), 'spotify_play': ('spotify', 'playing'), 'spotify_pause': ('spotify', 'paused'), 'spotify_resume': ('spotify', 'resumed')}
    state = state_by_tool.get(tool_name)
    if state is None:
        return ''
    domain, transition = state
    track = output.get('now_playing') or output.get('track') or output.get('stopped')
    if isinstance(track, dict):
        identity = track.get('video_id') or track.get('id') or track.get('title')
    elif domain == 'app':
        identity = output.get('app')
    else:
        identity = output.get('uri') or output.get('title') or domain
    normalized = _milestone_part(identity) or 'verified'
    return f"{domain}:{transition}:{normalized}:{task_id}"


def _annotate_narration(raw_result: object, allowed: bool) -> object:
    if not isinstance(raw_result, str):
        return raw_result
    try:
        outer = json.loads(raw_result)
    except (TypeError, ValueError):
        return raw_result
    if not isinstance(outer, dict):
        return raw_result
    if outer.get('type') == 'text' and isinstance(outer.get('text'), str):
        try:
            body = json.loads(outer['text'])
        except (TypeError, ValueError):
            return raw_result
        if not isinstance(body, dict):
            return raw_result
        body['narration_allowed'] = allowed
        outer['text'] = json.dumps(body, separators=(',', ':'), default=str)
        return json.dumps(outer, separators=(',', ':'), default=str)
    outer['narration_allowed'] = allowed
    return json.dumps(outer, separators=(',', ':'), default=str)


class RunExecutionArbiter:
    """Bind a claim to one generated speech and guard its tool dispatches."""

    def __init__(self, manager: ContinuityManager, *, active_claim: Callable[[], PortionClaim | None], telemetry: RuntimeTelemetry | None = None, on_tool_settled: Callable[[PortionClaim, tuple[str, ...]], None] | None = None) -> None:
        self.manager = manager
        self._active_claim = active_claim
        self.telemetry = telemetry or RuntimeTelemetry()
        self._on_tool_settled = on_tool_settled
        self.owner = None
        self._revoked_speech_ids = set()

    def bind_speech(self, claim: PortionClaim, speech_handle, *, source: str) -> OwnerDecision:
        speech_id = str(getattr(speech_handle, 'id', '') or '')
        if source != 'generate_reply':
            return OwnerDecision(ASSOCIATED, speech_id, 'runtime presentation')
        if not speech_id:
            raise ValueError('generated speech requires an id')
        if speech_id in self._revoked_speech_ids:
            try:
                speech_handle.interrupt(force=True)
            finally:
                self.telemetry.increment('duplicate_owners_refused')
            return OwnerDecision(DUPLICATE, speech_id, 'speech ownership was revoked')
        if self.owner is None or self.owner.state == REVOKED:
            self.owner = ExecutionOwner(claim=claim, speech_id=speech_id, speech_handle=speech_handle)
            return OwnerDecision(BOUND, speech_id)
        if self._same_claim(self.owner.claim, claim) and self.owner.speech_id == speech_id:
            return OwnerDecision(BOUND, speech_id, 'already bound')
        try:
            speech_handle.interrupt(force=True)
        finally:
            self.telemetry.increment('duplicate_owners_refused')
        return OwnerDecision(DUPLICATE, speech_id, 'another speech owns this portion')

    def authorize_owner(self, context, *, expected_tool: str = '') -> PortionClaim:
        active = self._active_claim()
        speech_handle = getattr(context, 'speech_handle', None)
        speech_id = str(getattr(speech_handle, 'id', '') or '')
        function_call = getattr(context, 'function_call', None)
        call_id = str(getattr(function_call, 'call_id', '') or '')
        call_name = str(getattr(function_call, 'name', '') or '')
        if active is None or not speech_id:
            self.reject_stale('no active execution owner')
        if speech_id in self._revoked_speech_ids:
            self.reject_stale('speech ownership was revoked')
        if function_call is not None and (not call_id or not call_name):
            self.reject_stale('tool invocation has no function-call identity')
        if expected_tool and call_name and call_name not in {expected_tool, 'use_capability'}:
            self.reject_stale(f"function call {call_name!r} cannot invoke {expected_tool!r}")
        if self.owner is None or self.owner.state == REVOKED:
            self.owner = ExecutionOwner(claim=active, speech_id=speech_id, speech_handle=speech_handle)
            self.telemetry.mark('owner_bound_at_tool', run_id=active.run_id, portion_id=active.portion_id, speech_id=speech_id, tool_name=expected_tool or call_name)
            return active
        if not self._same_claim(active, self.owner.claim) or speech_id != self.owner.speech_id:
            self.reject_stale('speech handle does not own the current portion')
        return active

    def reject_stale(self, message: str) -> NoReturn:
        self.telemetry.increment('stale_owner_rejections')
        raise StaleExecutionOwner(message)

    def authorize_tool(self, context, tool_name: str, arguments: dict) -> ToolAuthorization:
        claim = self.authorize_owner(context, expected_tool=tool_name)
        if not _is_side_effecting(tool_name):
            return ToolAuthorization(claim, self.owner.speech_id, tool_name, dict(arguments), True, 'authorized')
        reservation = self.manager.reserve_attempt(claim, task_id=claim.task_id, idempotency_key=canonical_execution_key(claim.task_id, tool_name, arguments))
        if not reservation.execute:
            self.telemetry.increment('duplicate_calls_prevented')
        return ToolAuthorization(claim=claim, speech_id=self.owner.speech_id, tool_name=tool_name, arguments=dict(arguments), execute=reservation.execute, status=reservation.status, reservation=reservation)

    def settle_tool(self, authorization: ToolAuthorization, raw_result: object) -> None:
        active = self._active_claim()
        if active is None or not self._same_claim(active, authorization.claim):
            if authorization.reservation is not None and authorization.execute:
                self.manager.settle_abandoned_attempt(authorization.claim, authorization.reservation.attempt_id, error='result arrived after execution ownership was revoked')
            self.reject_stale('tool result belongs to a revoked portion')
        body = _result_body(raw_result)
        evidence_refs = ()
        if body.get('may_claim_completion') is True and body.get('verification'):
            result_run = str(body.get('run_id', '') or '')
            if result_run:
                evidence_refs = (result_run,)
        self._record_action(authorization.claim, evidence_refs)
        reservation = authorization.reservation
        if reservation is None or not authorization.execute:
            return
        status = str(body.get('status', '') or '').lower()
        if status == 'succeeded' and body.get('may_claim_completion') is True:
            result_ref = evidence_refs[0] if evidence_refs else hashlib.sha256(repr(raw_result).encode('utf-8', 'replace')).hexdigest()
            settled = 'succeeded'
            error = ''
        elif status in {'blocked', 'cancelled'}:
            settled, result_ref, error = 'cancelled', '', str(body.get('error', '') or '')
        elif status in {'failed', 'partial'}:
            settled, result_ref, error = 'failed', '', str(body.get('error', '') or status)
        else:
            settled, result_ref, error = ('unknown', '', 'tool result was not a claimable ActionResult')
        self.manager.settle_attempt(authorization.claim, reservation.attempt_id, status=settled, result_ref=result_ref, error=error)

    def settle_exception(self, authorization: ToolAuthorization, error: BaseException, *, dispatched: bool) -> None:
        if authorization.reservation is not None and authorization.execute:
            active = self._active_claim()
            if active is None or not self._same_claim(active, authorization.claim):
                self.manager.settle_abandoned_attempt(authorization.claim, authorization.reservation.attempt_id, error=f"late {type(error).__name__}: {error}")
                return
            self.manager.settle_attempt(authorization.claim, authorization.reservation.attempt_id, status='unknown' if dispatched else 'failed', error=f"{type(error).__name__}: {error}")
            self._record_action(authorization.claim, ())

    def reserve_result_narration(self, authorization: ToolAuthorization, raw_result: object) -> bool | None:
        milestone = narration_milestone(authorization.tool_name, raw_result, task_id=authorization.claim.task_id)
        if not milestone:
            return
        allowed = self.manager.reserve_narration(authorization.claim, milestone_key=milestone, speech_id=authorization.speech_id)
        if not allowed:
            self.telemetry.increment('duplicate_narrations_prevented')
        return allowed

    def _record_action(self, claim: PortionClaim, evidence_refs: tuple[str, ...]) -> None:
        self.manager.record_actions(claim, count=1, evidence_refs=evidence_refs)
        if self._on_tool_settled is not None:
            self._on_tool_settled(claim, evidence_refs)

    def revoke(self, claim: PortionClaim) -> None:
        if self.owner is not None and self._same_claim(self.owner.claim, claim):
            self._revoked_speech_ids.add(self.owner.speech_id)
            self.owner = ExecutionOwner(claim=claim, speech_id=self.owner.speech_id, speech_handle=self.owner.speech_handle, state=REVOKED)

    @staticmethod
    def _same_claim(left: PortionClaim, right: PortionClaim) -> bool:
        return left.run_id == right.run_id and left.portion_id == right.portion_id and left.wake_generation == right.wake_generation and left.lease_token == right.lease_token
    same_claim = _same_claim


class GuardedMCPServerHTTP(MCPServerHTTP):
    """Preserve MCP schemas while enforcing ownership before dispatch."""

    def __init__(self, *args, arbiter: RunExecutionArbiter, telemetry: RuntimeTelemetry | None = None, ownerless_tools: frozenset[str] = frozenset({'profile_get', 'profile_learn_from_turn'}), presentation_policy: ActionPresentationPolicy | None = None, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._arbiter = arbiter
        self._runtime_telemetry = telemetry or arbiter.telemetry
        self._ownerless_tools = ownerless_tools
        self._presentation_policy = presentation_policy or ActionPresentationPolicy()

    def _make_function_tool(self, name: str, description: str | None, input_schema: dict[str, Any], meta: dict[str, Any] | None):
        original = super()._make_function_tool(name, description, input_schema, meta)

        async def guarded(raw_arguments: dict[str, Any], context: RunContext = cast(RunContext, None)) -> Any:
            if context is None:
                if name not in self._ownerless_tools:
                    self._arbiter.reject_stale(f"{name} requires an active speech owner")
                return await original(raw_arguments)
            self._arbiter.authorize_owner(context, expected_tool=name)
            plan = self._presentation_policy.plan(name, raw_arguments)
            await self._presentation_policy.present(context, plan, self._runtime_telemetry)
            authorization = self._arbiter.authorize_tool(context, name, raw_arguments)
            if not authorization.execute:
                return json.dumps({'status': authorization.status, 'duplicate_prevented': True, 'may_claim_completion': False})
            self._runtime_telemetry.mark('tool_started', run_id=authorization.claim.run_id, portion_id=authorization.claim.portion_id, speech_id=authorization.speech_id, tool_name=name)
            try:
                result = await original(raw_arguments)
            except BaseException as exc:
                self._arbiter.settle_exception(authorization, exc, dispatched=True)
                raise
            self._arbiter.settle_tool(authorization, result)
            self._runtime_telemetry.increment('tool_calls_settled')
            if _result_body(result).get('status') == 'succeeded':
                self._runtime_telemetry.increment('successful_tool_calls')
            narration_allowed = self._arbiter.reserve_result_narration(authorization, result)
            if narration_allowed is not None:
                result = _annotate_narration(result, narration_allowed)
            self._runtime_telemetry.mark('tool_settled', run_id=authorization.claim.run_id, portion_id=authorization.claim.portion_id, speech_id=authorization.speech_id, tool_name=name, status=str(_result_body(result).get('status', 'unknown') or 'unknown'))
            return result
        return function_tool(guarded, raw_schema=dict(original.info.raw_schema))