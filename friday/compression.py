"""
The one place context is compressed (PRD v7 CTX-007, §16 payload classes, §18
Compression Coordinator).

Compressing context is the moment a fact goes missing. So nothing shortens a
payload on its own account: it is classified first, and the class says what may
be done to it. An exact literal, an approval, a policy, a constraint, security
evidence, a patch or an identifier - or any payload that carries a critical
fact line (`compaction.CriticalFactSet`) - is LOSSLESS_REQUIRED and passes raw.
A payload nobody can classify is treated the same way.

Every lossy step writes its source to the artifact vault, addressed by the
sha256 of the bytes, and returns a frozen `Transformation` record: the hash
going in, the hash coming out, the class, the action, the backend, the step it
stacks on and a pointer to the original. `recover` turns any transformed hash
back into the exact original bytes, however many steps were stacked.

It fails closed. A lossy request for a LOSSLESS_REQUIRED payload raises
`compaction.LossyCompaction`. A backend that raises, a stage that drops a fact
its input carried, a vault that cannot write or whose entry no longer matches
its hash returns the payload raw with the refusal recorded. A summarizer
(CTX-008's Caveman, later ones) joins through `register_backend`; it never
runs on a payload the class does not allow.

Assumed (the PRD text was not in the repository): the four classes differ in
how far a payload may be reduced - RECOVERABLE_LOSSY_ALLOWED may be folded to
an artifact stub, SUMMARIZABLE may also be summarized, DROP_AFTER_EXTRACTION
may also be dropped once its facts are extracted. Revisit if §16 says otherwise.
"""
from __future__ import annotations

import contextlib
import hashlib
import logging
import os
import re
from dataclasses import asdict, dataclass, fields
from pathlib import Path
from typing import Callable, Iterable, Sequence

from friday import compaction, config, contracts

log = logging.getLogger(__name__)

LOSSLESS_REQUIRED = "LOSSLESS_REQUIRED"
RECOVERABLE_LOSSY_ALLOWED = "RECOVERABLE_LOSSY_ALLOWED"
SUMMARIZABLE = "SUMMARIZABLE"
DROP_AFTER_EXTRACTION = "DROP_AFTER_EXTRACTION"
PAYLOAD_CLASSES = (LOSSLESS_REQUIRED, RECOVERABLE_LOSSY_ALLOWED, SUMMARIZABLE,
                   DROP_AFTER_EXTRACTION)

PASS_RAW = "PASS_RAW"
SUMMARIZE = "SUMMARIZE"
FOLD_TO_ARTIFACT = "FOLD_TO_ARTIFACT"
DROP = "DROP_AFTER_EXTRACTION"
ACTIONS = (PASS_RAW, SUMMARIZE, FOLD_TO_ARTIFACT, DROP)

#: What each class permits, least lossy first.
ALLOWED = {
    LOSSLESS_REQUIRED: (PASS_RAW,),
    RECOVERABLE_LOSSY_ALLOWED: (PASS_RAW, FOLD_TO_ARTIFACT),
    SUMMARIZABLE: (PASS_RAW, SUMMARIZE, FOLD_TO_ARTIFACT),
    DROP_AFTER_EXTRACTION: ACTIONS,
}

#: A payload tagged with any of these is LOSSLESS_REQUIRED whatever it holds.
LOSSLESS_TAGS = frozenset({"exact_literal", "approval", "policy", "constraint",
                           "security_evidence", "patch", "identifier"})

#: How a recovery pointer starts; the rest is the sha256 of the original.
POINTER = "vault:sha256:"

_DIGEST = re.compile(r"[0-9a-f]{64}")

#: A backend turns a payload into a shorter text. It may raise; that is a
#: refusal, never a crash and never a partial result.
Backend = Callable[[str], str]


class ArtifactError(contracts.ContractError):
    """The vault cannot give back, or safely take, an artifact."""


def sha256(data: str | bytes) -> str:
    return hashlib.sha256(_encode(data)).hexdigest()


def _encode(data: str | bytes) -> bytes:
    return data if isinstance(data, bytes) else data.encode("utf-8", "surrogatepass")


def default_root() -> Path:
    """Friday's own directory for originals; `FRIDAY_ARTIFACT_VAULT` moves it.
    Never the database, never the secrets vault."""
    explicit = os.getenv("FRIDAY_ARTIFACT_VAULT", "").strip()
    return Path(explicit).expanduser() if explicit else config.DATA_DIR / "compression_vault"


class ArtifactVault:
    """Content-addressed originals, and the links from what replaced them.

    `blobs/<aa>/<sha256>` holds the exact bytes; `links/<transformed>-<origin>`
    says which original a transformed artifact came from. Every read and every
    re-write checks the bytes against the name, so an entry that was edited
    after the fact is an error, never a quiet substitute.
    """

    def __init__(self, root: str | Path | None = None):
        self._root = Path(root) if root else None

    @property
    def root(self) -> Path:
        return self._root or default_root()

    @staticmethod
    def _digest(value: str) -> str:
        digest = (value or "").strip().lower()
        if digest.startswith(POINTER):
            digest = digest[len(POINTER):]
        if not _DIGEST.fullmatch(digest):
            raise ArtifactError("not a sha256 hash: %r" % (value or "")[:80])
        return digest

    def _blob(self, digest: str) -> Path:
        return self.root / "blobs" / digest[:2] / digest

    def put(self, data: bytes) -> str:
        """Store `data`; returns its sha256. An entry already there must still
        match its name."""
        digest = sha256(data)
        path = self._blob(digest)
        if path.exists():
            self._verified(path, digest)
            return digest
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name("%s.tmp-%d" % (path.name, os.getpid()))
        try:
            tmp.write_bytes(data)
            os.replace(tmp, path)
        finally:
            with contextlib.suppress(FileNotFoundError):
                tmp.unlink()
        self._verified(path, digest)
        return digest

    def get(self, handle: str) -> bytes:
        """The exact bytes stored under `handle` (a hash or a pointer)."""
        digest = self._digest(handle)
        path = self._blob(digest)
        if not path.exists():
            raise ArtifactError("no artifact sha256:%s in the vault" % digest)
        return self._verified(path, digest)

    @staticmethod
    def _verified(path: Path, digest: str) -> bytes:
        data = path.read_bytes()
        if sha256(data) != digest:
            log.error("compression vault: entry %s no longer matches its hash", digest)
            raise ArtifactError("vault entry sha256:%s does not match its hash" % digest)
        return data

    def link(self, transformed: str, origin: str) -> None:
        path = self.root / "links" / ("%s-%s" % (self._digest(transformed), self._digest(origin)))
        path.parent.mkdir(parents=True, exist_ok=True)
        path.touch()

    def origins(self, transformed: str) -> list[str]:
        """The originals recorded for a transformed hash."""
        digest = self._digest(transformed)
        folder = self.root / "links"
        if not folder.is_dir():
            return []
        return sorted(p.name.split("-", 1)[1] for p in folder.glob(digest + "-*"))


@dataclass(frozen=True)
class Transformation:
    """What happened to one payload at one step, and how to get it back.

    `source_sha256` is what this step took in and `transformed_sha256` what it
    gave out; `parent_sha256` is the transformed hash of the step it stacks on
    ("" for the first) and `origin_sha256` the original the whole stack began
    from. `action` is what was done and `requested` what was asked; they differ
    when it was refused (`refusal` says why) or the backend changed nothing.
    `recovery` points at the original ("" when nothing was stored).
    """

    payload_class: str
    action: str
    requested: str
    backend: str
    source_sha256: str
    transformed_sha256: str
    origin_sha256: str
    parent_sha256: str
    recovery: str
    refusal: str
    source_chars: int
    transformed_chars: int

    def to_dict(self) -> dict:
        return asdict(self)


def carried(payload: str, facts: compaction.CriticalFactSet) -> list[str]:
    """The critical lines `payload` holds, spacing aside."""
    lost = set(compaction.missing(payload, facts))
    return [line for line in facts.lines() if line.strip() and line not in lost]


def _held(payload: str, facts: compaction.CriticalFactSet) -> compaction.CriticalFactSet:
    """The facts `payload` carries, as a set a result can be validated against."""
    lost = set(compaction.missing(payload, facts))
    return compaction.CriticalFactSet(**{
        f.name: tuple(line for line in getattr(facts, f.name) if line not in lost)
        for f in fields(facts)})


def _tag(tag: str) -> str:
    return re.sub(r"[\s-]+", "_", (tag or "").strip().lower())


def classify(payload: str, *, facts: compaction.CriticalFactSet | None = None,
             tags: Iterable[str] = (), hint: str = "") -> str:
    """The class of `payload`. Carrying a critical fact line, or a lossless tag,
    decides it; otherwise `hint` (the caller's knowledge of what the payload
    is) does. No usable hint is not a license: it is LOSSLESS_REQUIRED."""
    if not isinstance(payload, str):
        return LOSSLESS_REQUIRED
    if facts is not None and carried(payload, facts):
        return LOSSLESS_REQUIRED
    if LOSSLESS_TAGS & {_tag(t) for t in tags}:
        return LOSSLESS_REQUIRED
    return hint if hint in PAYLOAD_CLASSES else LOSSLESS_REQUIRED


def _stub(verb: str, origin: str, chars: int) -> str:
    """What a fold or a drop leaves: a marker naming the original, so its hash
    is the original's and no other payload's. A caller that wants nothing of a
    dropped payload in its context discards the marker."""
    return "[%s %d chars; recover %s%s]" % (verb, chars, POINTER, origin)


class CompressionCoordinator:
    """Classifies, transforms, records and keeps every original recoverable."""

    def __init__(self, vault: ArtifactVault | None = None,
                 backends: dict[str, Backend] | None = None):
        self.vault = vault or ArtifactVault()
        self._backends: dict[str, Backend] = dict(backends or {})

    def register(self, name: str, backend: Backend) -> None:
        if not (name or "").strip() or not callable(backend):
            raise contracts.ContractError("a backend has a name and is callable")
        self._backends[name] = backend

    def transform(self, payload: str, action: str, *, backend: str = "",
                  facts: compaction.CriticalFactSet | None = None,
                  tags: Iterable[str] = (), hint: str = "",
                  parent: Transformation | None = None) -> tuple[str, Transformation]:
        """Apply `action` to `payload` if its class allows it. Returns the text
        that replaces it and the record. A refusal (backend or vault failure, a
        lost fact) returns the payload itself and a PASS_RAW record carrying the
        reason; asking a lossy action of a LOSSLESS_REQUIRED payload raises."""
        if action not in ACTIONS:
            raise contracts.ContractError("unknown compression action %r" % (action,))
        if not isinstance(payload, str):
            raise contracts.ContractError("a compression payload is text")
        cls = classify(payload, facts=facts, tags=tags, hint=hint)
        if action not in ALLOWED[cls]:
            if cls == LOSSLESS_REQUIRED:
                raise compaction.LossyCompaction(
                    ["%s payload cannot be %s; it passes raw" % (cls, action)])
            raise contracts.ContractError("%s is not allowed for a %s payload" % (action, cls))
        source = sha256(payload)
        if action == PASS_RAW:
            return payload, self._record(cls, PASS_RAW, action, backend, payload, payload,
                                         parent=parent)
        origin = parent.origin_sha256 if parent is not None and parent.recovery else source
        out, reason = self._apply(action, backend, payload, origin)
        if reason is None and facts is not None:
            try:
                compaction.validate(out, _held(payload, facts))
            except compaction.LossyCompaction as exc:
                reason = "the %s dropped %d critical fact(s)" % (action, len(exc.missing))
        if reason is not None:
            return self._refuse(cls, action, backend, payload, parent, reason)
        if out == payload:
            return payload, self._record(cls, PASS_RAW, action, backend, payload, payload,
                                         parent=parent)
        try:
            if origin == source:
                self.vault.put(_encode(payload))
            else:
                self.vault.get(origin)
            self.vault.link(sha256(out), origin)
        except (OSError, ArtifactError) as exc:
            return self._refuse(cls, action, backend, payload, parent,
                                "the vault refused: %s" % exc)
        return out, self._record(cls, action, action, backend, payload, out, parent=parent,
                                 origin=origin, recovery=POINTER + origin)

    def stack(self, payload: str, steps: Sequence[tuple[str, str]], *,
              facts: compaction.CriticalFactSet | None = None,
              tags: Iterable[str] = (), hint: str = "") -> tuple[str, list[Transformation]]:
        """Run `(action, backend)` steps one after another, each on the last
        one's output, as one stack. Every stage is checked against the facts and
        the finished stack is checked again against the original; the stack stops
        at the first refusal, leaving the last good text."""
        tags = tuple(tags)
        cls = classify(payload, facts=facts, tags=tags, hint=hint)
        out, records, parent = payload, [], None
        for action, backend in steps:
            out, parent = self.transform(out, action, backend=backend, facts=facts, tags=tags,
                                         hint=cls, parent=parent)
            records.append(parent)
            if parent.refusal:
                break
        if facts is not None:
            compaction.validate(out, _held(payload, facts))
        return out, records

    def recover(self, handle: str) -> bytes:
        """The exact original bytes behind a transformed hash or a pointer."""
        digest = ArtifactVault._digest(handle)
        origins = self.vault.origins(digest)
        if len(origins) > 1:
            raise ArtifactError("sha256:%s came from %d originals; recover one by its own "
                                "pointer" % (digest, len(origins)))
        return self.vault.get(origins[0] if origins else digest)

    def _apply(self, action: str, backend: str, payload: str,
               origin: str) -> tuple[str, str | None]:
        if action == DROP:
            return _stub("dropped", origin, len(payload)), None
        if action == FOLD_TO_ARTIFACT:
            return _stub("folded", origin, len(payload)), None
        fn = self._backends.get(backend)
        if fn is None:
            return payload, "no %s backend named %r is registered" % (action, backend)
        try:
            out = fn(payload)
        except Exception as exc:  # a backend is third-party: any failure is a refusal
            log.warning("compression backend %s failed: %s", backend, type(exc).__name__)
            return payload, "backend %r failed: %s" % (backend, type(exc).__name__)
        if not isinstance(out, str):
            return payload, "backend %r returned %s, not text" % (backend, type(out).__name__)
        return out, None

    def _refuse(self, cls, action, backend, payload, parent, reason):
        log.warning("compression refused (%s %s): %s", cls, action, reason)
        return payload, self._record(cls, PASS_RAW, action, backend, payload, payload,
                                     parent=parent, refusal=reason)

    @staticmethod
    def _record(cls: str, done: str, requested: str, backend: str, source: str, out: str, *,
                parent: Transformation | None, origin: str = "", recovery: str = "",
                refusal: str = "") -> Transformation:
        src = sha256(source)
        return Transformation(
            payload_class=cls, action=done, requested=requested, backend=backend,
            source_sha256=src, transformed_sha256=sha256(out),
            origin_sha256=origin or (parent.origin_sha256 if parent is not None else src),
            parent_sha256=parent.transformed_sha256 if parent is not None else "",
            recovery=recovery, refusal=refusal,
            source_chars=len(source), transformed_chars=len(out))


_DEFAULT = CompressionCoordinator()


def default() -> CompressionCoordinator:
    """The coordinator the runtime shares: one registry of backends, one vault."""
    return _DEFAULT


def register_backend(name: str, backend: Backend) -> None:
    _DEFAULT.register(name, backend)


def recover(handle: str) -> bytes:
    """The exact original bytes behind a transformed hash or a recovery pointer."""
    return _DEFAULT.recover(handle)
