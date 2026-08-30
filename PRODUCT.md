# Product

F.R.I.D.A.Y (spoken to as "Jarvis" or "Friday"): a personal AI operating system that runs on the owner's own PC. The owner speaks an objective; Friday plans, routes, gates and reports; Hermes executes; a dynamic organisation of agent teams does the work; one shared memory is what everything learns from.

## Platform
web (browser-served control room on localhost; also usable from a LiveKit voice session, which must keep working unchanged)

## Stack
Python 3.11 Starlette UI server (`friday/ui_server.py`) over the existing Friday MCP server; a single no-build `ui/index.html` (vanilla HTML/CSS/JS, canvas, 3d-force-graph/three.js from a CDN). No bundler, no framework.

## Users
One person: the owner, a solo founder who runs several businesses through this system. Expert, impatient, uses it all day, often by voice while doing something else. Sole authority over every gate.

## Product Purpose
Replace "many tabs and a chatbot" with one voice layer and one screen: speak, and the right team runs the work, and the memory of it is readable afterwards.

## Positioning
Not a chat UI and not a dashboard for a team. A cockpit for one operator with a company of agents under him. Jarvis-from-Iron-Man is the explicit reference; the owner supplied HUD/holographic reference images and a "one-man company" org model.

## Operating Context
Desktop, dark room or evening use, second monitor, hours at a time. Machine RAM frequently above 90%: the UI must be light. Voice is always on: the owner talks over Friday (barge-in) and expects an unmute/mute toggle, not push-to-talk.

## Capabilities and Constraints
- 164 MCP tools, an objective engine, Hermes delegation, a gated persistent browser, a Command Deck, a Vault (memory as markdown), sight on request through the vision model (camera or screen).
- Non-negotiables (governance): Friday is the only control layer; Hermes is the execution engine; ONE memory (GBrain + ada.sqlite3), no duplicates; nothing destructive without a confirmation bound to the exact action; dry-run by default; upstreams pinned; LiveKit path stays intact.
- The gate: nothing answers until the owner's face is recognised. Enforced server-side, so it holds even if the page is bypassed, and every request made while locked is written to the access log. A PIN exists only as a fallback for when the camera genuinely cannot be used, and is refused whenever a face could be read instead — a PIN proves knowledge, a face proves presence.
- The camera is never taken from the owner. A meeting or a stream holding it means Friday does without.
- Escalation: Friday -> Hermes -> the owner ("Sir, should I build this?").

## Brand Commitments
Black substrate; amber as the system's own colour, taken from the ULTRON core the owner chose as the centre of the room and used for everything else so the room and its core are one object. Hazard red only for alerts, and nothing else gets a second hue — an earlier build gave node types and teams their own categorical colours and the owner rejected it: the colour carried no meaning he needed and the room read as decoration. No AI-purple, no gradients.

## Evidence on Hand
Everything on screen is read from the running system, so these numbers move. As of 2026-08-31: 214 active facts (172 earlier versions kept), 16 recorded contradictions, 12 shared-brain ledger entries, 18 agent divisions / 258 agents in the pinned agency-agents upstream, 63 vault pages, 24 enrolled face descriptors, real objective runs, real build identity.

## Product Principles
- Real over decorative: every node, number and agent on screen exists in the store or the upstream.
- Honest state: unavailable is shown as unavailable; queued is not "done".
- Interactive, not a poster: everything visible can be clicked into, dragged, or spoken to.
- The HUD is minimal; detail lives in Control Room, Agentic OS and Vault.

## Accessibility & Inclusion
WCAG AA contrast on the dark substrate; keyboard focus everywhere; a text adjacency list mirrors the 3D graph; reduced motion collapses the canvas to a static frame and voice visualisers to static bars.
