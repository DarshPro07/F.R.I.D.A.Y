"""
Durable memory of "this provider/model is capped until X".

A CAPPED diagnosis (see `friday/provider_diagnostics.py`) names a provider
that cannot answer for a while, not a broken request. This is the record
that lets the next delegation route AROUND it instead of hammering the
same capped provider on every retry - JSON under DATA_DIR, same shape as
`data/autonomy.json`, so it survives process restarts.
"""
from __future__ import annotations

import json
import threading
from datetime import datetime
from pathlib import Path

from friday.config import DATA_DIR

COOLDOWNS_FILE = Path(DATA_DIR) / "provider_cooldowns.json"

# ponytail: one process-wide lock, not per-key - cooldowns are rare writes
# (a cap event) against frequent reads (every plan_delegation); a single
# lock is not a contention problem at this rate.
_lock = threading.Lock()


def _load() -> dict:
    try:
        return json.loads(COOLDOWNS_FILE.read_text(encoding="utf-8"))
    except OSError:
        return {}                       # no file yet: nothing is cooling
    except ValueError as exc:
        # A torn or corrupt file must not silently forget every cooldown -
        # that is a capped provider retried into its own window (review,
        # 2026-09-04). Say so; the next mark() rewrites it whole.
        import logging
        logging.getLogger("friday.cooldowns").warning(
            "cooldown file unreadable, treating as empty: %s", exc)
        return {}


def _save(data: dict) -> None:
    """Atomic: a reader never sees a half-written file."""
    import os
    COOLDOWNS_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = COOLDOWNS_FILE.with_suffix(COOLDOWNS_FILE.suffix + ".tmp")
    tmp.write_text(json.dumps(data), encoding="utf-8")
    os.replace(tmp, COOLDOWNS_FILE)


def _key(provider: str, model: str) -> str:
    return f"{provider}\x1f{model}"


def mark(provider: str, model: str, until: str, reason: str = "") -> None:
    """Record that (provider, model) is capped until the ISO timestamp `until`."""
    with _lock:
        data = _load()
        data[_key(provider, model)] = {"until": until, "reason": reason}
        _save(data)


def active(now: datetime | None = None) -> dict[tuple[str, str], str]:
    """{(provider, model): until_iso} for cooldowns not yet expired."""
    now = now or datetime.now()
    with _lock:
        data = _load()
    out: dict[tuple[str, str], str] = {}
    for key, entry in data.items():
        provider, _, model = key.partition("\x1f")
        until = entry.get("until", "") if isinstance(entry, dict) else ""
        try:
            expires = datetime.fromisoformat(until)
        except ValueError:
            continue
        if expires > now:
            out[(provider, model)] = until
    return out


def clear() -> None:
    with _lock:
        _save({})
