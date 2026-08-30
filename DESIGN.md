# Design

The visual world of the Friday control room, as it actually ships.

Mode: **Operate**. The owner is mid-task, usually talking, often looking at a
second monitor. Nothing here is for a visitor.

This file replaced an earlier spec for a cyan HUD with per-category node colours.
That design was built and rejected: decorative, not interactive, and the colour
carried no meaning the owner cared about. What follows is the design that
survived contact.

## World

Black, and one colour on it.

The centre is Friday's core: a rotating orb of shells, orbiting debris and
drifting code fragments, ported from
[SAGAR-TAMANG/ultron-by-sagar-builds](https://github.com/SAGAR-TAMANG/ultron-by-sagar-builds)
(MIT, pinned `a65306f5a956`). The palette is that project's and nothing else's —
amber on black. Everything in the room is a tint of the same hue, so the only
things that ever read as a different colour are alerts.

The room is a HUD, not a dashboard. Panels have no boxes and no shadows; they
are text blocks placed at the corners of a dark field, separated by hairlines
and by space. If a thing does not need a border to be legible, it does not get
one.

## Tokens

```
--bg      #000000   the field
--ink     #ffcc66   primary text
--amber   #ffaa30   the system's colour: labels, meters, focus, the core
--mid     #dd7700   secondary text
--dim     #884400   tertiary, de-emphasis
--faint   #553300   hairlines at their quietest
--alert   #ff5533   alerts, conflicts, a blocked action. The only other hue.
--rule    rgba(amber,.28)   --rule-2  rgba(amber,.14)
--wash    rgba(20,10,0,.55) --solid   #0a0500
```

`--g` carries the accent as raw channels (`255,170,48`) so any element can mix
its own alpha against it without a second token.

Three full-bleed overlays sit above the page and below the UI: a vignette, a
grain plate, and 2px scanlines at 30% — enough to make the black feel like a
display rather than a background.

## Type

**IBM Plex Sans** for anything a person reads as a sentence. **IBM Plex Mono**
for labels, numbers, identifiers and every piece of machine state — tabular
figures throughout, so digits do not dance as they update.

Micro-labels are mono, 10–11px, uppercase, tracked wide (`.2em`–`.34em`). That
tracking is the room's signature: it is what makes a two-word label read as an
instrument marking instead of a heading.

Body sits at 12–14px. The only large type is data: a tier count at 30px, a stat
at 22px. Nothing is large because it is a title.

## Shape and depth

Radius 3px on controls, 50% on the mic and the sound toggle, 0 everywhere else.
No panel shadows. Depth comes from the vignette and from the core's bloom, never
from a drop shadow under a card.

The memory graph and the capture box are framed by eight small corner rules
rather than a border — the frame marks the instrument without enclosing it.

## Layout

Four views behind text tabs in the top bar; no chrome around them.

- **Core** — the orb full-bleed. Objective and organisation at the left, memory
  summary at the right, gesture hints at the bottom left, the capture box and
  its side readout at the bottom right, the voice dock centred at the bottom.
- **Control room** — two editorial columns of hairline-separated sections.
  Objective, approvals, command deck, schedule, memory, and the check-up.
- **Organisation** — a lit spine (You → Friday → Hermes, each with live status),
  then divisions as cards with a strength meter that expand in place to their
  roster, then the capability map in columns.
- **Memory** — four tiers as instruments (count, gauge, source, feed state),
  what Friday is actually given for the current objective, the 3D graph, and the
  vault as a two-pane reader.

## Motion

The core never stops. It animates for as long as the page is open; there is no
switch, because a still core reads as a dead system, and because the orb is how
speech is expressed.

- Voice drives it directly: amplitude swells the whole sphere and brightens the
  bloom; spectral centroid (how fast and high the voice is) drives rotation and
  chromatic aberration.
- Recognition is one authored moment: the capture box glides from the centre of
  the lock screen into its corner slot over 820ms while the lock text fades and
  the core pulses once.
- Everything else is state feedback at 150–180ms.

Under load the renderer sheds resolution before it sheds anything visible, and
never below 1:1 — a blurred core reads as broken, not as fast. Chromatic
aberration is the last thing to go.

## States

Every control has default / hover / focus-visible / active / disabled.

Panels load as skeletons, never spinners — the one exception is the boot ring,
which is honest about being a wait. Empty states say what will appear there.
Errors name the call that failed and the fix.

The room has two whole-page states. **Locked**: the core is dimmed behind the
lock screen, every panel is faded out and inert, and the only live things are
the camera and its explanation. **Open**: panels ease in on a stagger, the dock
takes focus, the mic comes up on its own.

## The dock

Voice only. There is no text field — the mic, the level meter and the sound
toggle are the entire interface, because typing at Friday was never the point.

The meter is 24 bars driven by the real spectrum, at 30Hz. When she speaks, the
island's seven-bar visualiser moves instead, so it is always clear which
direction the audio is going.

## Accessibility

WCAG AA contrast on the substrate. Keyboard focus everywhere, with a visible
ring. The 3D graph has a text adjacency list beside it. `prefers-reduced-motion`
collapses transitions and the graph's auto-rotation; the core is exempt by the
owner's explicit instruction, and that is a deliberate departure, not an
oversight.

## Pinned upstreams

`three@0.185.1` (SRI-pinned), `3d-force-graph@1.73.4` (SRI-pinned),
`@vladmandic/face-api@1.7.15` — vendored locally, along with its weights, so the
gate never waits on a CDN to recognise a face.
