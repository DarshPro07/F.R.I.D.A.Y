"""
Make something, check it works, and put it in front of him.

    "Create a coffee shop website and show me."

The asked-for version of this was a Sandpack preview embedded in Friday's chat
UI. Friday has no chat UI - no package.json, no HTML, no frontend of any kind.
It is a voice agent and an MCP server, and he talks to it. Building a React
app to host a preview would be a different project, and the preview is not the
valuable part anyway.

What is valuable, and what this does:

    generate      real files in a real folder he keeps
    validate      it is checked before he is told it exists
    show          served on loopback and opened in HIS Chrome, signed in,
                  with his extensions and his devtools
    iterate       "make the hero darker" edits the same project and he
                  refreshes

The browser he already uses is a better preview surface than anything that
could be embedded: it is the browser the site will actually be viewed in.

## Serving rather than file://

`open_in_browser` refuses `file://`, deliberately - it was one of the ways the
argv-injection fix closed local file disclosure. That refusal stays. A static
server on 127.0.0.1 is better regardless: relative paths resolve, `fetch`
works, and the page behaves the way it will on a real host.

The server is lazy. Nothing listens until a preview is asked for, and it stops
on request or at process exit.
"""

from __future__ import annotations

import http.server
import logging
import os
import re
import socketserver
import threading
from dataclasses import dataclass, field
from pathlib import Path

from friday import contracts as c
from friday.policy import PolicyEngine, default_engine
from friday.toolsets.web import _gate

logger = logging.getLogger("friday-agent.workbench")

EXECUTION_SCOPE = "local_machine"

#: Projects live under the workbench root, one folder each, and they persist.
#: A generated site he cannot find again afterwards is a demo, not a tool.
ROOT = Path(os.getenv("ADA_WORKBENCH", "data/workbench")).resolve()

#: A project name becomes a directory name, so it is constrained rather than
#: sanitised - a name that needs sanitising is a name worth refusing.
SAFE_NAME = re.compile(r"^[a-z0-9][a-z0-9_-]{0,48}$")

#: What a browser can be pointed at. Anything else is a file, not a page.
ENTRY_CANDIDATES = ("index.html", "index.htm")

#: Loopback only. This serves whatever was generated, and generated content is
#: not something to expose to the network.
HOST = "127.0.0.1"


def _scoped(payload: dict) -> dict:
    return {"execution_scope": EXECUTION_SCOPE, **payload}


def project_dir(name: str) -> Path:
    return ROOT / name


def contained(name: str, relative: str) -> Path | None:
    """
    Resolve a path inside a project, or None if it escapes.

    Same resolve-then-contain rule as the filesystem jail: check the resolved
    path, because "a/../../b" only shows its intent after resolution.
    """
    base = project_dir(name).resolve()
    try:
        target = (base / relative).resolve()
    except (OSError, ValueError):
        return None
    if target == base or base in target.parents:
        return target
    return None


# ---------------------------------------------------------------------------
# The preview server - lazy, loopback, one per project
# ---------------------------------------------------------------------------


@dataclass
class Preview:
    name: str
    port: int
    server: object = None
    thread: object = None

    @property
    def url(self) -> str:
        return f"http://{HOST}:{self.port}/"


_previews: dict[str, Preview] = {}
_lock = threading.Lock()


class _QuietHandler(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *args) -> None:  # noqa: D102 - silence the console
        pass


def start_preview(name: str) -> Preview:
    """Serve a project on a free loopback port. Idempotent per project."""
    with _lock:
        existing = _previews.get(name)
        if existing is not None:
            return existing

        directory = str(project_dir(name))

        class Handler(_QuietHandler):
            def __init__(self, *args, **kwargs):
                super().__init__(*args, directory=directory, **kwargs)

        # Port 0: the OS picks a free one. Guessing a port is how two projects
        # end up fighting over 8080.
        server = socketserver.TCPServer((HOST, 0), Handler)
        server.daemon_threads = True
        port = server.server_address[1]
        thread = threading.Thread(target=server.serve_forever, daemon=True,
                                  name=f"workbench-{name}")
        thread.start()

        preview = Preview(name=name, port=port, server=server, thread=thread)
        _previews[name] = preview
        logger.info("workbench: serving %s at %s", name, preview.url)
        return preview


def stop_preview(name: str) -> bool:
    with _lock:
        preview = _previews.pop(name, None)
    if preview is None:
        return False
    try:
        preview.server.shutdown()
        preview.server.server_close()
    except Exception:
        logger.exception("could not stop the preview for %s", name)
    return True


def stop_all() -> int:
    for name in list(_previews):
        stop_preview(name)
    return len(_previews)


# ---------------------------------------------------------------------------
# Validation - said to work before he is told it works
# ---------------------------------------------------------------------------


@dataclass
class Check:
    ok: bool
    problems: list[str] = field(default_factory=list)
    entry: str = ""
    files: list[str] = field(default_factory=list)


def validate(name: str) -> Check:
    """
    Is this actually a page a browser can open?

    Not a full HTML validator - a browser will render broken markup happily.
    This catches the failures that make a preview useless: no entry point, an
    empty file, a stylesheet or script that points at nothing.
    """
    base = project_dir(name)
    if not base.is_dir():
        return Check(False, [f"no project called {name!r}"])

    files = sorted(str(p.relative_to(base)).replace("\\", "/")
                   for p in base.rglob("*") if p.is_file())
    if not files:
        return Check(False, ["the project is empty"], files=files)

    entry = next((e for e in ENTRY_CANDIDATES if (base / e).is_file()), "")
    if not entry:
        return Check(False, ["no index.html, so there is nothing to open"],
                     files=files)

    html = (base / entry).read_text(encoding="utf-8", errors="replace")
    problems = []
    if not html.strip():
        problems.append(f"{entry} is empty")
    if "<body" not in html.lower():
        problems.append(f"{entry} has no <body>")

    # A missing local stylesheet is the difference between "a website" and
    # "unstyled text", and it is invisible until he looks at it.
    for reference in re.findall(r'(?:href|src)\s*=\s*["\']([^"\':#]+)["\']', html):
        if reference.startswith(("http://", "https://", "//", "data:", "mailto:")):
            continue
        if not (base / reference.lstrip("/")).exists():
            problems.append(f"{entry} references {reference!r}, which is missing")

    return Check(not problems, problems, entry=entry, files=files)


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------


async def workbench_write(
    run: c.Run, project: str, path: str, content: str, *,
    engine: PolicyEngine = default_engine,
) -> c.ActionResult:
    """Create or replace one file in a project."""
    tool_id = "workbench.write"
    blocked = _gate(run, tool_id, engine)
    if blocked:
        return blocked
    started = c.started(run.run_id, tool_id)

    name = (project or "").strip().lower()
    if not SAFE_NAME.match(name):
        return run.record(c.failed(
            started, f"{project!r} is not a usable project name - lowercase "
                     f"letters, digits, dashes and underscores"))
    target = contained(name, path)
    if target is None:
        return run.record(c.failed(
            started, f"{path!r} would land outside the project"))

    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    written = target.read_text(encoding="utf-8")

    return run.record(c.succeeded(
        started,
        output=_scoped({"project": name, "path": path,
                        "bytes": len(written.encode("utf-8")),
                        "dir": str(project_dir(name))}),
        verification=c.Verification(
            method="file_read_back",
            evidence=f"{target} holds {len(written)} chars after writing"),
    ))


async def workbench_preview(
    run: c.Run, project: str, *, engine: PolicyEngine = default_engine,
) -> c.ActionResult:
    """
    Check the project renders, serve it, and return the URL to open.

    Validation comes first on purpose. Opening a browser at a blank page and
    saying "here it is" is the shape of claim this codebase exists to prevent.
    """
    tool_id = "workbench.preview"
    blocked = _gate(run, tool_id, engine)
    if blocked:
        return blocked
    started = c.started(run.run_id, tool_id)

    name = (project or "").strip().lower()
    if not SAFE_NAME.match(name):
        return run.record(c.failed(started, f"{project!r} is not a project name"))

    check = validate(name)
    if not check.ok:
        return run.record(c.failed(
            started, "not ready to show: " + "; ".join(check.problems)))

    preview = start_preview(name)

    # Fetch it back. The server answering is the evidence - "it started" is
    # not the same as "it serves the page".
    import httpx

    try:
        response = httpx.get(preview.url, timeout=10)
        served = len(response.content)
        status = response.status_code
    except Exception as exc:
        stop_preview(name)
        return run.record(c.failed(
            started, f"the preview server did not answer: {type(exc).__name__}: {exc}"))

    if status != 200 or served == 0:
        stop_preview(name)
        return run.record(c.failed(
            started, f"the preview answered {status} with {served} bytes"))

    return run.record(c.succeeded(
        started,
        output=_scoped({"project": name, "url": preview.url, "port": preview.port,
                        "entry": check.entry, "files": check.files,
                        "dir": str(project_dir(name))}),
        verification=c.Verification(
            method="served_and_fetched",
            evidence=f"{preview.url} returned {status} with {served} bytes of "
                     f"{check.entry}; {len(check.files)} file(s) in the project"),
    ))


async def workbench_list(
    run: c.Run, project: str = "", *, engine: PolicyEngine = default_engine,
) -> c.ActionResult:
    """Projects, or the files in one."""
    tool_id = "workbench.list"
    blocked = _gate(run, tool_id, engine)
    if blocked:
        return blocked
    started = c.started(run.run_id, tool_id)

    if not project.strip():
        ROOT.mkdir(parents=True, exist_ok=True)
        projects = sorted(p.name for p in ROOT.iterdir() if p.is_dir())
        return run.record(c.succeeded(
            started,
            output=_scoped({"projects": projects, "count": len(projects),
                            "root": str(ROOT),
                            "previewing": sorted(_previews)}),
            verification=c.Verification(
                method="directory_listing",
                evidence=f"{len(projects)} project(s) under {ROOT}")))

    name = project.strip().lower()
    check = validate(name)
    if not check.files:
        return run.record(c.failed(started, f"no project called {name!r}"))
    return run.record(c.succeeded(
        started,
        output=_scoped({"project": name, "files": check.files,
                        "entry": check.entry, "ready": check.ok,
                        "problems": check.problems,
                        "dir": str(project_dir(name))}),
        verification=c.Verification(
            method="directory_listing",
            evidence=f"{len(check.files)} file(s); "
                     f"{'ready to show' if check.ok else '; '.join(check.problems)}")))


async def workbench_stop(
    run: c.Run, project: str, *, engine: PolicyEngine = default_engine,
) -> c.ActionResult:
    """Stop serving a project. The files stay."""
    tool_id = "workbench.stop"
    blocked = _gate(run, tool_id, engine)
    if blocked:
        return blocked
    started = c.started(run.run_id, tool_id)

    name = (project or "").strip().lower()
    stopped = stop_preview(name)
    return run.record(c.succeeded(
        started,
        output=_scoped({"project": name, "was_running": stopped}),
        verification=c.Verification(
            method="preview_stopped",
            evidence=f"{name} {'was serving and is now stopped' if stopped else 'was not serving'}")))
