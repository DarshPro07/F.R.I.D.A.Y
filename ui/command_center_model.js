// ACC-004: the Command Center graph model. Pure functions, no DOM, no I/O.
// State comes only from the ACC-001 event frames (friday/command_center.py
// to_agui, wrapped by ui_server._event_frame): STATE_SNAPSHOT replaces the
// map, STATE_DELTA patches it. Every other frame is informational, because
// to_agui always pairs a lifecycle frame with a STATE_DELTA.

const AGENTS = '/agents/';

const isAgent = (a) => a !== null && typeof a === 'object' && typeof a.run_id === 'string';

// frame: {event, data: JSON string of {v, type, run_id, at, payload}}.
// Returns a new Map on a state change, else the input map; never throws.
export function applyFrame(agents, frame) {
  let body;
  try {
    body = JSON.parse(frame && frame.data);
  } catch (err) {
    return agents; // malformed frame: untrusted input, keep the last good state
  }
  const payload = body && body.payload;
  if (!payload || typeof payload !== 'object') return agents;
  if (body.type === 'STATE_SNAPSHOT' && Array.isArray(payload.agents)) {
    return new Map(payload.agents.filter(isAgent).map((a) => [a.run_id, a]));
  }
  if (body.type === 'STATE_DELTA' && Array.isArray(payload.delta)) {
    const next = new Map(agents);
    for (const op of payload.delta) {
      if (!op || typeof op.path !== 'string' || !op.path.startsWith(AGENTS)) continue;
      // command_center.py does not RFC 6901-escape the run_id, so take it as-is.
      const key = op.path.slice(AGENTS.length);
      if ((op.op === 'add' || op.op === 'replace') && isAgent(op.value)) {
        next.set(op.value.run_id, op.value);
      } else if (op.op === 'remove') {
        next.delete(key);
      }
    }
    return next;
  }
  return agents;
}

const VISUAL = {
  queued: { icon: 'queue', animation: 'none' },
  running: { icon: 'run', animation: 'pulse' },
  verified: { icon: 'check', animation: 'once' }, // the renderer plays it on entry only
  blocked: { icon: 'stop', animation: 'none' },
  failed: { icon: 'x', animation: 'none' },
  cancelled: { icon: 'ban', animation: 'none' },
};

export function visualFor(agent, reducedMotion) {
  const known = Object.prototype.hasOwnProperty.call(VISUAL, agent.state);
  const v = known ? VISUAL[agent.state] : VISUAL.blocked;
  let detail = '';
  if (!known) detail = 'unknown state';
  else if (agent.state === 'blocked') detail = agent.reason || '';
  else if (agent.state === 'queued' && agent.position != null) detail = 'position ' + agent.position;
  return {
    icon: v.icon,
    label: String(agent.state),
    animation: reducedMotion ? 'none' : v.animation,
    detail,
  };
}

const byKey = (a, b) => (a < b ? -1 : a > b ? 1 : 0);

const sorted = (agents) => [...agents.values()].sort((a, b) => byKey(a.run_id, b.run_id));

export function toGraph(agents, reducedMotion) {
  const list = sorted(agents);
  const roles = [...new Set(list.map((a) => a.role))].sort(byKey);
  const nodes = [{ id: 'friday', type: 'friday' }]
    .concat(roles.map((role) => ({ id: 'team:' + role, type: 'team', data: { role } })))
    .concat(list.map((a) => ({
      id: a.run_id,
      type: 'agent',
      parentId: 'team:' + a.role,
      data: {
        role: a.role,
        model: a.model,
        state: a.state,
        reason: a.reason,
        position: a.position,
        visual: visualFor(a, reducedMotion),
      },
    })));
  const edges = list.map((a) => {
    const source = a.parent_run_id && agents.has(a.parent_run_id) ? a.parent_run_id : 'friday';
    return {
      id: source + '->' + a.run_id,
      source,
      target: a.run_id,
      animated: a.state === 'running' && !reducedMotion,
    };
  });
  return { nodes, edges };
}

export function runningIds(agents) {
  return sorted(agents).filter((a) => a.state === 'running').map((a) => a.run_id);
}
