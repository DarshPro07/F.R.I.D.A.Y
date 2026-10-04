"""
Is a configured credential usable?

The doctor used to answer "is the variable non-empty", which reports the
template values copied from .env.example, and keys the provider has revoked,
as configured. This module is the one place that tells the two apart:

    MISSING      unset or blank
    PLACEHOLDER  a template value that was never replaced
    REJECTED     the provider refused it (a bounded live auth probe), or the
                 value cannot be that provider's key (wrong format, no call made)
    VALID        the provider accepted it
    UNVERIFIED   set and not a template, but the provider could not be asked:
                 no probe exists for it, or the network did not answer

UNVERIFIED is deliberately not VALID. A credential's value is never printed,
logged or put into a returned detail string; only its name is.
"""

from __future__ import annotations

import json
import re
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from typing import Callable, NamedTuple
from urllib.parse import urlsplit

MISSING = "MISSING"
PLACEHOLDER = "PLACEHOLDER"
REJECTED = "REJECTED"
VALID = "VALID"
UNVERIFIED = "UNVERIFIED"

#: Every probe is bounded: a doctor on a dead network must still answer.
PROBE_TIMEOUT_S = 4.0

#: A value that is a template, not a key: runs of x (APIxxxx, sk-proj-xxxx),
#: "your"/"you" placeholders, <angle brackets>, and whitespace (a real key has
#: none; "you-google-api-key (from https://...)" is a sentence).
_PLACEHOLDER = re.compile(
    r"x{5,}|^you(r)?[-_ ]|your-project|<[^>]*>|\s|changeme|change-me|placeholder|"
    r"replace[-_ ]?me|^todo$|^example$",
    re.IGNORECASE)

_URL_SCHEMES = ("ws", "wss", "http", "https")

#: The shape a provider's key must have, checked before any call: a key in the
#: wrong format cannot be accepted, so asking the provider only adds a round
#: trip and a refusal. (name -> (required prefix, what the key is))
_FORMATS = {"GOOGLE_API_KEY": ("AIza", "a Gemini API key")}


class Verdict(NamedTuple):
    state: str
    detail: str


#: (name, value) -> a Verdict, or None when no probe exists for that name.
Probe = Callable[[str, str], "Verdict | None"]


def is_placeholder(value: str) -> bool:
    return bool(_PLACEHOLDER.search(value.strip()))


def classify_static(name: str, value: str | None) -> Verdict:
    """MISSING / PLACEHOLDER / REJECTED (malformed) / UNVERIFIED; never VALID."""
    text = (value or "").strip()
    if not text:
        return Verdict(MISSING, f"{name} is not set")
    if is_placeholder(text):
        return Verdict(PLACEHOLDER, f"{name} is still the template value")
    if name == "LIVEKIT_URL":
        parts = urlsplit(text)
        if parts.scheme not in _URL_SCHEMES or not parts.hostname:
            return Verdict(REJECTED, f"{name} is not a ws/wss/http/https URL")
    shape = _FORMATS.get(name)
    if shape and not text.startswith(shape[0]):
        return Verdict(REJECTED, f"{name} is not {shape[1]} (those start '{shape[0]}')")
    return Verdict(UNVERIFIED, f"{name} is set; not checked against the provider")


# ---------------------------------------------------------------------------
# Probes. Each returns REJECTED only on the provider's explicit refusal and
# VALID only on its explicit acceptance; every other outcome is UNVERIFIED.
# ---------------------------------------------------------------------------

_CODE = re.compile(r"[A-Za-z][A-Za-z0-9_.-]{0,63}")


def _reason(content) -> str:
    """The provider's machine-readable refusal code (API_KEY_INVALID,
    invalid_api_key, UNAUTHENTICATED), or "". Never its prose: OpenAI's
    message quotes a masked copy of the key, and a detail string must carry
    the variable's name and nothing of its value."""
    try:
        error = json.loads(content)["error"]
    except (TypeError, ValueError, KeyError):
        return ""
    if not isinstance(error, dict):
        return ""
    details = error.get("details")
    candidates = [d.get("reason") for d in details if isinstance(d, dict)] \
        if isinstance(details, list) else []
    for code in (*candidates, error.get("code"), error.get("status")):
        if isinstance(code, str) and _CODE.fullmatch(code):
            return code
    return ""


def _answer(name: str, status: int, refused: tuple[int, ...], reason: str = "") -> Verdict:
    if 200 <= status < 300:
        return Verdict(VALID, f"{name} was accepted by the provider")
    if status in refused:
        why = f", {reason}" if reason else ""
        return Verdict(REJECTED, f"{name} was rejected by the provider (HTTP {status}{why})")
    return Verdict(UNVERIFIED, f"{name}: the provider answered HTTP {status}; cannot tell")


def _unreachable(name: str, exc: Exception) -> Verdict:
    return Verdict(UNVERIFIED, f"{name}: the provider could not be reached ({type(exc).__name__})")


def _get(name: str, url: str, headers: dict, refused: tuple[int, ...],
         timeout: float, refusal_marker: bytes = b"") -> Verdict:
    from friday import netguard

    try:
        reply = netguard.fetch(url, timeout=timeout, headers=headers, max_bytes=65536)
    except Exception as exc:                                # noqa: BLE001
        return _unreachable(name, exc)
    status = reply["status"]
    if status == 400 and refusal_marker and refusal_marker in reply["content"]:
        return _answer(name, status, (400,), _reason(reply["content"]))
    return _answer(name, status, refused, _reason(reply["content"]) if status in refused else "")


def probe_openai(name: str, value: str, timeout: float = PROBE_TIMEOUT_S) -> Verdict:
    # 403 is a region block as often as a bad key, so only 401 is a refusal.
    return _get(name, "https://api.openai.com/v1/models",
                {"Authorization": f"Bearer {value}"}, (401,), timeout)


def probe_google(name: str, value: str, timeout: float = PROBE_TIMEOUT_S) -> Verdict:
    # Google answers an unknown key with 400 API_KEY_INVALID, a known one it
    # will not serve with 401/403.
    return _get(name, "https://generativelanguage.googleapis.com/v1beta/models?pageSize=1",
                {"x-goog-api-key": value}, (401, 403), timeout,
                refusal_marker=b"API_KEY_INVALID")


def probe_livekit(url: str, key: str, secret: str, timeout: float = PROBE_TIMEOUT_S) -> Verdict:
    """Sign a room-list token and ask the project's own API to accept it.

    The URL is the operator's own configuration (same trust as the secret it
    sits beside, and often a LAN or loopback server), so it is not run through
    netguard's private-address refusal; the request is POST, single hop,
    redirects refused, and bounded by `timeout`.
    """
    import httpx
    from livekit import api

    name = "LIVEKIT_API_KEY / LIVEKIT_API_SECRET"
    parts = urlsplit(url.strip())
    base = f"{'https' if parts.scheme in ('wss', 'https') else 'http'}://{parts.netloc}"
    try:
        token = (api.AccessToken(key, secret)
                 .with_grants(api.VideoGrants(room_list=True))
                 .with_ttl(timedelta(seconds=60)).to_jwt())
        with httpx.Client(timeout=timeout, follow_redirects=False) as client:
            reply = client.post(f"{base}/twirp/livekit.RoomService/ListRooms", json={},
                                headers={"Authorization": f"Bearer {token}"})
    except Exception as exc:                                # noqa: BLE001
        return _unreachable(name, exc)
    return _answer(name, reply.status_code, (401, 403))


#: Providers that have a probe. Anything else stays UNVERIFIED.
_PROBES: dict[str, Probe] = {"OPENAI_API_KEY": probe_openai, "GOOGLE_API_KEY": probe_google}


def live_probe(name: str, value: str) -> Verdict | None:
    """The probe for one single-value credential, or None when none exists."""
    probe = _PROBES.get(name)
    return probe(name, value) if probe else None


# ---------------------------------------------------------------------------
# Public entry points
# ---------------------------------------------------------------------------

def check_credential(name: str, value: str | None, *, probe: Probe | None = None) -> Verdict:
    """Static classification, then `probe(name, value)` when one is given.

    A probe that returns None (no probe for this name) or UNVERIFIED leaves
    the static verdict's UNVERIFIED in place; the probe's detail wins when it
    says why (unreachable, refused, accepted).
    """
    static = classify_static(name, value)
    if static.state != UNVERIFIED or probe is None:
        return static
    try:
        answered = probe(name, (value or "").strip())
    except Exception as exc:                                # noqa: BLE001
        return _unreachable(name, exc)
    return answered if answered is not None else static


def check_credentials(values: dict[str, str | None], *,
                      probe: Probe | None = None) -> dict[str, Verdict]:
    """check_credential for several names; probes run in parallel."""
    if not values:
        return {}
    with ThreadPoolExecutor(max_workers=min(4, len(values))) as pool:
        futures = {name: pool.submit(check_credential, name, value, probe=probe)
                   for name, value in values.items()}
        return {name: future.result() for name, future in futures.items()}


def check_livekit(url: str | None, key: str | None, secret: str | None, *,
                  live: bool = True, probe: Callable[[str, str, str], Verdict] | None = None
                  ) -> dict[str, Verdict]:
    """Per-variable static verdicts, upgraded by one live probe when all three
    are statically sound. Returned in LIVEKIT_URL / KEY / SECRET order."""
    verdicts = {"LIVEKIT_URL": classify_static("LIVEKIT_URL", url),
                "LIVEKIT_API_KEY": classify_static("LIVEKIT_API_KEY", key),
                "LIVEKIT_API_SECRET": classify_static("LIVEKIT_API_SECRET", secret)}
    if live and all(v.state == UNVERIFIED for v in verdicts.values()):
        try:
            answered = (probe or probe_livekit)(url.strip(), key.strip(), secret.strip())
        except Exception as exc:                            # noqa: BLE001
            answered = _unreachable("LIVEKIT_URL", exc)
        # A 401 condemns the key pair, not the URL that answered it.
        condemned = ("LIVEKIT_API_KEY", "LIVEKIT_API_SECRET") if answered.state == REJECTED \
            else tuple(verdicts)
        for name in condemned:
            verdicts[name] = answered
    return verdicts


def worst_livekit(verdicts: dict[str, Verdict]) -> Verdict:
    """The one verdict that decides the voice worker: the first of
    MISSING, PLACEHOLDER, REJECTED, UNVERIFIED, VALID that any variable has."""
    for state in (MISSING, PLACEHOLDER, REJECTED, UNVERIFIED):
        names = [n for n, v in verdicts.items() if v.state == state]
        if names:
            return Verdict(state, f"{state.lower()}: {', '.join(names)}")
    return Verdict(VALID, "LIVEKIT_URL / API_KEY / API_SECRET were accepted")
