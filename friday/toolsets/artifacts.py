"""
friday/toolsets/artifacts.py -- a large tool payload becomes a file, and the
context gets a reference (PRD v7 section 19.5: raw output ratio).

A tool that hands the model eighteen thousand characters of crawled page body
spends the next prompt's whole budget on material the model reads once. The
payload is not noise - it is the research, or the file the boss asked about -
so it is never dropped: it is written under `ARTIFACTS_DIR` and the result
carries a preview, the size, the digest and the path. The next prompt stays
small and nothing is lost; a tool that needs the rest reads the path.

One owner for the rule. Every tool that can produce a large payload calls
`offload` here rather than writing its own copy somewhere else, because a
second artifact store drifts from the first and neither can be trusted to
hold the whole corpus.

Failure keeps the payload. If there is no artifacts directory, or the jail
refuses the write, the payload stays inline with `inline_reason` saying why -
a silent drop would be a tool that lies about what it read.
"""

from __future__ import annotations

import hashlib
import os
import re
from dataclasses import dataclass
from pathlib import Path

from friday.fsjail import FileJail, JailError

#: Over this many characters a payload is a file, not context. Below it the
#: reference costs more than the text it replaces.
OFFLOAD_CHARS = 1500

#: What stays inline when a payload is offloaded: enough for the model to tell
#: whether it wants the rest, not enough to be the rest.
PREVIEW_CHARS = 400

_SLUG = re.compile(r"[^a-z0-9._-]+")
_SUFFIX = re.compile(r"^\.[a-z0-9]{1,8}$")

#: Leaving `base` out of `offload` means Friday's own artifacts directory;
#: passing `base=None` means the caller has no store and the payload stays
#: inline. `None` cannot carry both meanings, so "not given" is its own value.
_DEFAULT_BASE = object()


def _limit(name: str, default: int) -> int:
    """A positive integer from the environment, or the default."""
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        value = int(str(raw).strip())
    except ValueError:
        return default
    return value if value > 0 else default


def threshold() -> int:
    return _limit("FRIDAY_ARTIFACT_OFFLOAD_CHARS", OFFLOAD_CHARS)


def preview_budget() -> int:
    return _limit("FRIDAY_ARTIFACT_PREVIEW_CHARS", PREVIEW_CHARS)


def artifacts_dir() -> Path | None:
    """Friday's own output directory, asked for at call time so a test (and
    `golden_live_runtime`) can point it somewhere disposable."""
    try:
        from friday.config import ARTIFACTS_DIR
    except Exception:  # noqa: BLE001 - an unconfigured install keeps payloads inline
        return None
    return ARTIFACTS_DIR


@dataclass(frozen=True)
class Offload:
    """What a caller may put in context, and where the rest of it lives.

    `text` is the whole payload when it stayed inline and a preview when it
    did not; `chars` and `digest` describe the whole payload either way, so a
    caller can report the real size without carrying the real bytes.
    """

    text: str
    chars: int
    digest: str
    artifact_path: str = ""
    offloaded: bool = False
    inline_reason: str = ""

    def fields(self, key: str = "text") -> dict:
        """The keys a tool output carries for this payload.

        Size keys stay with the caller: `files_read`'s `chars` counts the file
        and a crawled page's `chars` counts the page before its per-source cap,
        and neither means the same thing as this payload's length.
        """
        out = {key: self.text, "digest": self.digest, "offloaded": self.offloaded}
        if self.offloaded:
            out["artifact_path"] = self.artifact_path
        elif self.inline_reason:
            out["inline_reason"] = self.inline_reason
        return out


def _slug(label: str) -> str:
    cleaned = _SLUG.sub("-", (label or "").lower()).strip("-.")
    return cleaned[:40] or "payload"


def _ext(suffix: str) -> str:
    candidate = (suffix or "").lower()
    return candidate if _SUFFIX.match(candidate) else ".txt"


def _preview(text: str, budget: int, chars: int) -> str:
    """The head of the payload, saying out loud that it is only the head."""
    if budget <= 0:
        return f"[{chars} chars offloaded to artifact_path]"
    head = text[:budget]
    cut = head.rfind(" ")
    if cut > budget // 2:
        head = head[:cut]
    return f"{head}\n\n[... {chars} chars in total; the full text is at artifact_path]"


def _write(payload: str, base: Path, *, label: str, suffix: str) -> str:
    """The full payload under `base`, named for its own content.

    Content-addressed, so two sources that crawled the same page share one
    file and a repeated research pass does not grow the directory. The write
    goes through a jail rooted at `base`: the shape, reserved-name, denylist
    and containment checks still apply, and `base` is created if it is new.
    """
    digest = hashlib.sha256(payload.encode("utf-8", "surrogatepass")).hexdigest()
    name = f"{_slug(label)}-{digest[:16]}{_ext(suffix)}"
    target = FileJail(roots=(Path(base),)).resolve(str(Path(base) / name))
    if not target.exists():
        target.write_text(payload, encoding="utf-8", newline="\n")
    return str(target)


def recall(item: dict, key: str = "text", *, limit: int | None = None) -> str:
    """The text an `Offload.fields` item stands for, for a caller that has to
    reason over the page itself rather than its preview.

    An offloaded item is read back from the artifact store (at most `limit`
    characters of it); an inline one is returned as it is. Only a file inside
    `artifacts_dir()` is ever opened - the path is a field of a tool result,
    and a result is not trusted to name any file it likes. When the artifact
    cannot be read the preview is returned, which says it is only the head.
    """
    shown = str(item.get(key) or "")
    path, root = item.get("artifact_path"), artifacts_dir()
    if not (item.get("offloaded") and path and root):
        return shown
    try:
        target = FileJail(roots=(Path(root),)).resolve(str(path))
        with target.open(encoding="utf-8") as handle:
            return handle.read(limit) if limit else handle.read()
    except (JailError, OSError, UnicodeDecodeError):
        return shown


def offload(
    payload: str, *, label: str = "payload", suffix: str = ".txt",
    threshold_chars: int | None = None,
    base: Path | None | object = _DEFAULT_BASE,
    preview_chars: int | None = None,
) -> Offload:
    """Keep `payload` inline, or write it out and return a reference to it."""
    text = payload or ""
    chars = len(text)
    digest = hashlib.sha256(text.encode("utf-8", "surrogatepass")).hexdigest()[:16]
    limit = threshold() if threshold_chars is None else max(1, int(threshold_chars))

    if chars <= limit:
        return Offload(text=text, chars=chars, digest=digest)

    root = artifacts_dir() if base is _DEFAULT_BASE else base
    if root is None:
        return Offload(text=text, chars=chars, digest=digest,
                       inline_reason="no artifacts directory is configured")
    try:
        path = _write(text, Path(root), label=label, suffix=suffix)
    except (JailError, OSError) as exc:
        return Offload(text=text, chars=chars, digest=digest,
                       inline_reason=f"could not write the artifact: {exc}")

    budget = preview_budget() if preview_chars is None else max(0, int(preview_chars))
    return Offload(text=_preview(text, budget, chars), chars=chars, digest=digest,
                   artifact_path=path, offloaded=True)
