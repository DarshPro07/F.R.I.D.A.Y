# Owner directive 2026-10-05: hermes-gadget-sdk - a Device Fabric behind FRIDAY

Pasted by the owner, extracted verbatim from the session transcript. Recorded as PRD v7
amendment A14 (docs/PRD_V7.md section 108): DEVICE-001..008, ranked P2 so it does not
interrupt the core burn-down, as the directive asks. The repository is a Capability
Marketplace candidate (HIGH_VALUE_CANDIDATE); nothing is cloned or pinned until DEVICE-001
is built.

---

Yes — **this one is very relevant to the “real-world JARVIS” direction**. I would use it, but again **behind FRIDAY**, not as a visible Hermes product.

`hermes-gadget-sdk` is basically a bridge between an agent and **physical devices**: ESP32-S3 boards, Raspberry Pi/Linux devices, microphones/speakers, displays, buttons, sensors, LEDs/relays, and custom hardware actions. It already has a desktop simulator, pairing, voice/text interaction, device actions described with JSON Schema, sensor/event reporting, browser-based flashing, update/rollback paths, and end-to-end tests against Hermes. [Adolanium](https://adolanium.github.io/hermes-gadget-sdk/?utm_source=chatgpt.com)

My classification would be:

```yaml
repository: Adolanium/hermes-gadget-sdk

decision: HIGH_VALUE_WRAP_AND_ADAPT

role:
  - physical device bridge
  - voice terminal
  - sensor/event input
  - hardware action output
  - ESP32 / Raspberry Pi edge device
  - future robotics / workshop / wearable interface

does_not_replace:
  - FRIDAY Core
  - Capability Fabric
  - Policy Engine
  - Memory
  - Model Gateway
```

## Why it matters for FRIDAY

Right now FRIDAY mainly exists inside:

```text
PC
browser
apps
files
voice
```

This repo gives us the start of:

```text
                    FRIDAY
                       │
                  Device Fabric
                       │
        ┌──────────────┼───────────────┐
        ▼              ▼               ▼
      ESP32        Raspberry Pi      Desktop
        │              │               │
   sensors/buttons   camera/audio    simulator
   relays/LEDs       GPIO/devices
```

That means later you can build things like:

> “Friday, when someone enters the room, tell me.”

> “Friday, turn the workshop light on.”

> “Friday, read the temperature from the suit.”

> “Friday, put this status on the wrist display.”

> “Friday, listen through this desk device and answer me.”

This is much closer to the physical JARVIS vision.

## The most useful architecture in the repo

The device can declare **actions dynamically**. An action has a name, description and JSON-Schema parameters, and the agent can discover and call it. For example the docs show a relay action that takes an `on: boolean` argument. Sensors can also publish values, and devices can emit events that optionally trigger an agent turn. [Adolanium](https://adolanium.github.io/hermes-gadget-sdk/docs/porting.html?utm_source=chatgpt.com)

That's almost exactly our Capability Fabric.

So instead of exposing:

```text
gadget_action
```

directly to models, I would translate it:

```text
Device advertises:

relay.set
temperature_c
door.opened

        ↓

FRIDAY Device Adapter

        ↓

Capability Registry

hardware.relay.set
sensor.temperature.read
event.door.opened
```

Then FRIDAY's normal routing/policy applies.

---

# Add a `Device Fabric`

I would formalize:

```text
                      FRIDAY
                         │
                    Device Fabric
                         │
         ┌───────────────┼────────────────┐
         ▼               ▼                ▼
     Gadget SDK         MQTT          Home Assistant
         │               │                │
     ESP32/Pi         IoT devices       Smart home
         │
         ▼
    future robots
    wearables
    workshop
    car
    suit
```

`hermes-gadget-sdk` becomes one provider:

```text
DeviceProvider:
    HermesGadgetAdapter
```

FRIDAY never becomes dependent on it.

---

# Voice terminal = very useful

A small ESP32/Pi can become a physical FRIDAY terminal:

```text
button
→ microphone recording
→ FRIDAY
→ response
→ speaker/display
```

The repo already supports speech integration through the Hermes host and can run microphones/speakers on boards or Linux devices. [Adolanium](https://adolanium.github.io/hermes-gadget-sdk/docs/connect-hermes.html?utm_source=chatgpt.com)

So you could eventually have:

```text
Bedroom FRIDAY
Desk FRIDAY
Workshop FRIDAY
Car FRIDAY
Wearable FRIDAY
```

all talking to the **same canonical FRIDAY brain**.

Not five independent assistants.

---

# Raspberry Pi is especially interesting

The Linux client can run headlessly on a Pi, keep a persistent device identity, reconnect, expose GPIO outputs, use USB audio, emit events, and run as a dedicated system service. The current Pi path is marked experimental, so we'd benchmark it rather than call it production-ready immediately. [Adolanium](https://adolanium.github.io/hermes-gadget-sdk/docs/linux.html?utm_source=chatgpt.com)

This could give us:

```text
Mac mini / laptop
      ↓
FRIDAY Core
      ↓ Wi-Fi/LAN
Pi in workshop
      ↓
camera / mic / GPIO / sensors
```

No frontier model needs to run on the Pi.

That's important for cheap hardware.

---

# Even better: device actions should inherit FRIDAY policy

Suppose device exposes:

```text
motor.start
door.unlock
heater.set
relay.power
```

We should **not** let the model call them just because the device advertised them.

Use:

```text
Device action
    ↓
Capability Registry
    ↓
risk classification
    ↓
PolicyEngine
    ↓
approval / lease
    ↓
execute
```

Example:

```text
led.set
→ LOW RISK
→ automatic

door.unlock
→ PHYSICAL_SECURITY
→ confirmation

motor.start
→ PHYSICAL_HAZARD
→ stronger policy
```

That becomes very important once FRIDAY moves beyond software.

---

# Device pairing is useful, but we should wrap it

The current system displays a pairing code and requires approval before the device becomes trusted. [Adolanium](https://adolanium.github.io/hermes-gadget-sdk/docs/connect-hermes.html?utm_source=chatgpt.com)

Keep that pattern:

```text
NEW DEVICE
    ↓
UNPAIRED
    ↓
display/physical pairing code
    ↓
owner approval
    ↓
PAIRED_UNVERIFIED
    ↓
capability inventory
    ↓
policy assignment
    ↓
ACTIVE
```

Don't automatically trust any gadget on the LAN.

And for remote/network use, the project itself recommends `wss://` outside a trusted local network and protects device identity/state separately. [Adolanium](https://adolanium.github.io/hermes-gadget-sdk/docs/linux.html?utm_source=chatgpt.com)

---

# OTA/update/rollback is another valuable pattern

The SDK already packages firmware, creates hashes/manifests, supports updates, and preserves prior releases for rollback on Linux. Its release pipeline also generates SHA256 sums and keeps third-party notices with artifacts. [Adolanium](https://adolanium.github.io/hermes-gadget-sdk/docs/development.html?utm_source=chatgpt.com)

That's useful for our Technology Scout / Capability Forge philosophy.

FRIDAY could eventually do:

```text
Device firmware update available
        ↓
verify signature/hash
        ↓
sandbox/compat test
        ↓
update one test device
        ↓
health probe
        ↓
rollout

failure
→ rollback
```

---

# The simulator makes adoption safer

This is one of the best things about the repo.

The same device core can run in a desktop simulator before touching physical hardware. [Adolanium](https://adolanium.github.io/hermes-gadget-sdk/docs/desktop.html?utm_source=chatgpt.com)

So Capability Forge can evaluate it like:

```text
clone/quarantine
     ↓
build simulator
     ↓
pair simulated device
     ↓
test actions
     ↓
test sensors/events
     ↓
test voice
     ↓
test reconnect
     ↓
ONLY THEN
physical hardware
```

Excellent for FRIDAY.

---

# I would NOT install this into active P0 work right now

This is important.

It is highly relevant, but unless a current PRD P0 specifically requires physical devices, put it into:

```text
Capability Marketplace
status = HIGH_VALUE_CANDIDATE
```

Then after the current core P0 burn-down, build:

## `DEVICE-001 — Device Fabric`

and use this repo as the first backend.

Otherwise we again risk:

```text
new cool repo
→ interrupt P0
→ expand scope
→ build never finishes
```

We already decided to avoid that.

---

# Requirements I would add

### `DEVICE-001 — Device Fabric`

FRIDAY SHALL provide a provider-neutral interface for paired physical devices that expose sensors, events, displays, audio and actions.

### `DEVICE-002 — Dynamic Device Capabilities`

Devices MAY advertise capabilities dynamically, but they SHALL enter the Capability Registry and Policy Engine before model access.

### `DEVICE-003 — Device Identity`

Every device SHALL have a persistent identity and explicit pairing/trust lifecycle.

### `DEVICE-004 — Physical Action Policy`

Physical-world actions SHALL receive risk classifications independent of the model or device-provided description.

### `DEVICE-005 — Edge Voice Terminal`

FRIDAY SHALL support lightweight edge devices acting as microphone/speaker/display terminals while inference and canonical memory remain with FRIDAY Core.

### `DEVICE-006 — Sensor/Event Ingestion`

FRIDAY SHALL accept state and event streams from approved devices and route them into Objective OS/proactive routines according to policy.

### `DEVICE-007 — Device Update/Rollback`

Device firmware/runtime updates SHALL be integrity-checked, testable and rollback-capable.

### `DEVICE-008 — Device Simulator`

A new device integration SHOULD prove core behavior in a simulator/emulator before physical promotion when a simulator is available.

---

# Capability Forge decision

When FRIDAY eventually receives this GitHub URL itself, I want it to conclude something like:

```yaml
source:
  Adolanium/hermes-gadget-sdk

classification:
  - hardware_sdk
  - device_gateway
  - voice_terminal
  - iot
  - firmware
  - raspberry_pi
  - esp32

overlap:
  pc_control: low
  voice: medium
  capability_fabric: complementary

new_value:
  physical_device_bridge: very_high

decision:
  WRAP_AND_EXTEND

extract:
  - device protocol
  - pairing
  - simulator
  - sensor/event system
  - action schema
  - firmware/update pipeline

user_visible_brand:
  FRIDAY

backend_provenance:
  retained
```

So yes: **I would absolutely keep this one.**

OpenMuse/OpenDots are helping FRIDAY get **eyes/UI/workers**.

Cua/OpenShell help FRIDAY get **hands and secure execution**.

`hermes-gadget-sdk` helps FRIDAY get **a body in the physical world**.

That makes it one of the more strategically relevant repos you've shared for the long-term JARVIS vision.
