"""
friday/access.py -- the face gate. Nothing works until the owner is seen.

The rule the owner set: until his face is verified, Friday is locked -- no
chat, no tasks, no Hermes -- and everything that touches the locked system is
logged. This module enforces that on the SERVER, so the lock is real even if
the page is bypassed:

  * recognition happens in the browser (face-api descriptors, 128 floats);
  * MATCHING happens here, against descriptors enrolled on this machine;
  * a match issues a session cookie; every /api/* and the event stream require
    it, or answer 423 Locked and write a line to data/access_log.jsonl;
  * only the page itself, its static assets and /api/auth/* are reachable
    while locked, because the camera has to run somewhere.

Enrolment: with no owner on file the first enrolment is open (it is the
owner's own PC, and there is nobody else to ask); after that, changing the
enrolled face needs an unlocked session. Descriptors are stored as numbers,
never images. Set FRIDAY_FACE_GATE=0 to disable the gate entirely (tests do).
"""
from __future__ import annotations

import hashlib
import hmac
import json
import math
import os
import secrets
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OWNER_PATH = Path(os.getenv("FRIDAY_OWNER_FACE", str(ROOT / "data" / "owner_face.json")))
LOG_PATH = Path(os.getenv("FRIDAY_ACCESS_LOG", str(ROOT / "data" / "access_log.jsonl")))
THRESHOLD = float(os.getenv("FRIDAY_FACE_THRESHOLD", "0.5"))   # face-api euclidean; 0.6 is lenient
SESSION_HOURS = float(os.getenv("FRIDAY_SESSION_HOURS", "12"))
COOKIE = "friday_session"
GATE_ENABLED = os.getenv("FRIDAY_FACE_GATE", "1") != "0"
DESCRIPTOR_LEN = 128
MAX_DESCRIPTORS = int(os.getenv("FRIDAY_FACE_MAX", "64"))   # a wide sample of the owner: angles, light, glasses
OPEN_PREFIXES = ("/health", "/ui/", "/api/auth/", "/api/camera")
PIN_PATH = Path(os.getenv("FRIDAY_OWNER_PIN", str(ROOT / "data" / "owner_pin.json")))
AUTH_MODE = os.getenv("FRIDAY_AUTH_MODE", "face").lower()    # "face" | "pin" (--password)
PIN_ITERATIONS = 240_000
PIN_MAX_TRIES = 5
PIN_COOLDOWN = 300.0

_sessions: dict[str, float] = {}
_lock = threading.Lock()
_pin_fails: list[float] = []
_cam_hold = {"at": 0.0}                 # last heartbeat from a Friday page that HAS the camera
CAM_HOLD_TTL = 45.0


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# ---------------------------------------------------------------------------
# audit log
# ---------------------------------------------------------------------------

def log(event: dict):
    """Append one JSON line. Never raises: the log must not take Friday down."""
    try:
        LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        with LOG_PATH.open("a", encoding="utf-8") as f:
            f.write(json.dumps({"at": _now(), **event}, ensure_ascii=False) + "\n")
    except OSError:
        pass


def recent_log(limit=50):
    try:
        lines = LOG_PATH.read_text(encoding="utf-8").splitlines()[-limit:]
        return [json.loads(l) for l in lines if l.strip()]
    except (OSError, ValueError):
        return []


# ---------------------------------------------------------------------------
# enrolment
# ---------------------------------------------------------------------------

def _valid(descriptor):
    try:
        vals = [float(x) for x in descriptor]
    except (TypeError, ValueError):
        return None
    if len(vals) != DESCRIPTOR_LEN or any(math.isnan(v) or math.isinf(v) for v in vals):
        return None
    return vals


def load_owner():
    try:
        d = json.loads(OWNER_PATH.read_text(encoding="utf-8"))
        return [v for v in (d.get("descriptors") or []) if _valid(v)]
    except (OSError, ValueError):
        return []


def enrolled():
    return bool(load_owner())


def enrol(descriptor, label="owner"):
    vals = _valid(descriptor)
    if vals is None:
        return {"ok": False, "error": "a descriptor is %d numbers" % DESCRIPTOR_LEN}
    with _lock:
        current = load_owner()
        current.append(vals)
        OWNER_PATH.parent.mkdir(parents=True, exist_ok=True)
        OWNER_PATH.write_text(json.dumps({"label": label, "descriptors": current[-MAX_DESCRIPTORS:],
                                          "enrolled_at": _now()}), encoding="utf-8")
    log({"kind": "enrol", "label": label, "count": len(current)})
    return {"ok": True, "count": len(current)}


# ---------------------------------------------------------------------------
# the PIN -- the way in when the camera is not Friday's to take
#
# A PIN is weaker than a face: a PIN proves knowledge, a face proves presence.
# So the PIN is not an alternative, it is a fallback. It only opens the door
# when the camera genuinely cannot be used -- another app holds it, the machine
# has none, or the owner started Friday with --password. While the camera is
# free, the PIN is refused even if it is correct.
# ---------------------------------------------------------------------------

def _hash_pin(pin: str, salt: bytes) -> str:
    return hashlib.pbkdf2_hmac("sha256", pin.encode("utf-8"), salt, PIN_ITERATIONS).hex()


def has_pin() -> bool:
    try:
        d = json.loads(PIN_PATH.read_text(encoding="utf-8"))
        return bool(d.get("hash") and d.get("salt"))
    except (OSError, ValueError):
        return False


def set_pin(pin):
    pin = str(pin or "").strip()
    if len(pin) < 4 or len(pin) > 32:
        return {"ok": False, "error": "the PIN needs 4 to 32 characters"}
    if pin.isdigit() and len(set(pin)) == 1:
        return {"ok": False, "error": "not a repeated digit"}
    salt = secrets.token_bytes(16)
    PIN_PATH.parent.mkdir(parents=True, exist_ok=True)
    PIN_PATH.write_text(json.dumps({"salt": salt.hex(), "hash": _hash_pin(pin, salt),
                                    "iterations": PIN_ITERATIONS, "set_at": _now()}), encoding="utf-8")
    log({"kind": "pin_set"})
    return {"ok": True}


def note_camera_hold():
    """A Friday page is looking through the camera right now."""
    _cam_hold["at"] = time.time()


def camera_held_by_friday():
    return time.time() - _cam_hold["at"] < CAM_HOLD_TTL


def pin_allowed():
    """
    (allowed, reason). A PIN proves knowledge; a face proves presence. So the
    PIN opens the door only when no face could have been checked at all.

    The subtle case, and the one that matters: Friday's own window holds the
    camera, so the OS reports "a browser is using the camera" -- which would
    look like grounds for a PIN while face recognition is in fact working
    perfectly one window over. A page that has the camera says so with a
    heartbeat, and that veto outranks everything except --password.
    """
    if AUTH_MODE == "pin":
        return True, "started with --password"
    if camera_held_by_friday():
        return False, "a Friday window has the camera -- your face is the way in, there"
    try:
        from friday import camera
        cam = camera.status()
    except Exception:  # noqa: BLE001
        return True, "cannot see the camera state"
    if cam["yield_to"] or cam["busy"]:
        return True, cam["why"] or "the camera is in use"
    return False, "the camera is free -- your face is the way in"


def verify_pin(pin):
    allowed, reason = pin_allowed()
    if not allowed:
        log({"kind": "pin_refused", "reason": reason})
        return {"ok": False, "error": reason, "refused": True}
    if not has_pin():
        return {"ok": False, "error": "no PIN set", "needs_pin": True}
    now = time.time()
    with _lock:
        _pin_fails[:] = [t for t in _pin_fails if now - t < PIN_COOLDOWN]
        if len(_pin_fails) >= PIN_MAX_TRIES:
            wait = int(PIN_COOLDOWN - (now - _pin_fails[0]))
            log({"kind": "pin_locked_out", "seconds_left": wait})
            return {"ok": False, "error": "too many attempts, wait %ds" % wait, "locked_out": wait}
    d = json.loads(PIN_PATH.read_text(encoding="utf-8"))
    ok = hmac.compare_digest(_hash_pin(str(pin or ""), bytes.fromhex(d["salt"])), d["hash"])
    if not ok:
        with _lock:
            _pin_fails.append(now)
        log({"kind": "pin_rejected", "reason": reason})
        return {"ok": False, "error": "wrong PIN", "tries_left": PIN_MAX_TRIES - len(_pin_fails)}
    token = secrets.token_urlsafe(24)
    with _lock:
        _pin_fails.clear()
        _sessions[token] = now + SESSION_HOURS * 3600
    log({"kind": "unlock_pin", "reason": reason})
    return {"ok": True, "token": token, "via": "pin"}


# ---------------------------------------------------------------------------
# verification + sessions
# ---------------------------------------------------------------------------

def distance(a, b):
    return math.sqrt(sum((x - y) ** 2 for x, y in zip(a, b)))


def verify(descriptor):
    vals = _valid(descriptor)
    if vals is None:
        return {"ok": False, "error": "bad descriptor"}
    owner = load_owner()
    if not owner:
        return {"ok": False, "error": "no owner enrolled", "enrolled": False}
    best = min(distance(vals, o) for o in owner)
    ok = best <= THRESHOLD
    out = {"ok": ok, "distance": round(best, 4), "threshold": THRESHOLD, "enrolled": True}
    if ok:
        token = secrets.token_urlsafe(24)
        with _lock:
            _sessions[token] = time.time() + SESSION_HOURS * 3600
        out["token"] = token
        log({"kind": "unlock", "distance": out["distance"]})
    else:
        log({"kind": "face_rejected", "distance": out["distance"]})
    return out


def session_ok(token):
    if not token:
        return False
    with _lock:
        exp = _sessions.get(token)
        if exp is None:
            return False
        if exp < time.time():
            _sessions.pop(token, None)
            return False
        return True


def lock(token):
    with _lock:
        _sessions.pop(token, None)
    log({"kind": "lock"})
    return {"ok": True}


def status(token=None):
    allowed, reason = pin_allowed()
    return {"gate": GATE_ENABLED, "enrolled": enrolled(),
            "locked": GATE_ENABLED and not session_ok(token),
            "mode": "blue" if (not GATE_ENABLED or session_ok(token)) else "yellow",
            "threshold": THRESHOLD, "auth_mode": AUTH_MODE,
            "pin_set": has_pin(), "pin_allowed": allowed, "pin_reason": reason}


def is_open(path):
    return path == "/" or any(path.startswith(p) for p in OPEN_PREFIXES)


def cookie_value(headers):
    raw = ""
    for k, v in headers:
        if k == b"cookie":
            raw = v.decode("latin-1")
            break
    for part in raw.split(";"):
        if "=" in part:
            k, v = part.strip().split("=", 1)
            if k == COOKIE:
                return v
    return ""


class GateMiddleware:
    """ASGI: 423 + log for anything not open while no valid session exists."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        kind = scope["type"]
        # A WebSocket is a task channel as much as a POST is: the speech socket
        # streams the microphone to a paid recogniser. Until the first socket
        # route existed this branch only ever saw "http" and "lifespan", so the
        # gap was latent; it is closed here rather than in each handler, so a
        # future socket cannot forget to be locked.
        if kind not in ("http", "websocket") or not GATE_ENABLED:
            return await self.app(scope, receive, send)
        path = scope.get("path", "")
        if is_open(path) or session_ok(cookie_value(scope.get("headers", []))):
            return await self.app(scope, receive, send)
        client = scope.get("client") or ("", 0)
        ua = ""
        for k, v in scope.get("headers", []):
            if k == b"user-agent":
                ua = v.decode("latin-1")[:120]
        log({"kind": "blocked", "path": path,
             "method": scope.get("method", "") if kind == "http" else "WEBSOCKET",
             "client": client[0], "ua": ua})
        if kind == "websocket":
            # Deny at the handshake, before accept, so nothing upstream ever
            # sees the connection. 1008 is "policy violation".
            await receive()                          # the websocket.connect event
            await send({"type": "websocket.close", "code": 1008})
            return
        body = json.dumps({"locked": True, "error": "face verification required"}).encode()
        await send({"type": "http.response.start", "status": 423,
                    "headers": [(b"content-type", b"application/json"),
                                (b"content-length", str(len(body)).encode())]})
        await send({"type": "http.response.body", "body": body})
