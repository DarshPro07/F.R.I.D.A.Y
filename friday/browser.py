"""
Browser primitives (PRD v3.1 FR-031, FR-033, FR-034, FR-035, FR-036, FR-037).

One deterministic layer between "Friday wants the browser to do X" and
Playwright, so that every primitive the PRD names (open, inspect,
navigate, click, type, scroll, select, upload, download, tabs, screenshot,
wait, verify) exists once, is policy-classified once, and runs inside the
same observe -> plan -> policy -> act -> observe -> verify loop.

The rules are data, not prose:

  * `PRIMITIVES`   - each primitive's policy category and whether it
                     changes page state.  Reads are BROWSER_CONTROL
                     (AUTO); state changes are BROWSER_AUTOMATION (ASK).
  * `EXTERNAL_WRITE` - purchase / publish / destructive / security-settings
                     intents.  FR-034: a valid session never authorises
                     these; they need the exact-action CONFIRM and are
                     detected from the page, not from the caller's word.
  * `human_verification(state)` - FR-035: a CAPTCHA / anti-bot / MFA page
                     is a HANDOFF, never something to get past.
  * `Profile kind`  - FR-033: ISOLATED (ephemeral context, no cookies) vs
                     AUTHORIZED (the user's persistent profile).  The
                     authorized profile's cookies are never exported to a
                     worker; a worker gets ISOLATED unless the objective's
                     approval named AUTHORIZED.
  * `observe()`     - FR-037: structured state first - URL, title,
                     accessibility-ish element table, form fields - and a
                     screenshot only as evidence, not as the input.

Playwright is driven through `Driver`, an injectable interface, so the
rule layer is tested without a browser and the live E2E test proves the
same code against real Chromium.
"""
from __future__ import annotations

import logging
import re
import shutil
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

from friday import browser_capability as bc
from friday import policy as P
from friday import sensitive_domains

logger = logging.getLogger("friday.browser")

# ---------------------------------------------------------------------------
# Primitives and their policy
# ---------------------------------------------------------------------------

OPEN, INSPECT, NAVIGATE, CLICK, TYPE, SCROLL, SELECT = (
    "open", "inspect", "navigate", "click", "type", "scroll", "select")
UPLOAD, DOWNLOAD, TABS, SCREENSHOT, WAIT, VERIFY = (
    "upload", "download", "tabs", "screenshot", "wait", "verify")

#: primitive -> (policy category, changes page state)
PRIMITIVES: dict[str, tuple[str, bool]] = {
    OPEN: (P.BROWSER_CONTROL, False),
    INSPECT: (P.BROWSER_CONTROL, False),
    NAVIGATE: (P.BROWSER_CONTROL, False),
    SCREENSHOT: (P.BROWSER_CONTROL, False),
    WAIT: (P.BROWSER_CONTROL, False),
    VERIFY: (P.BROWSER_CONTROL, False),
    TABS: (P.BROWSER_CONTROL, False),
    SCROLL: (P.BROWSER_CONTROL, False),
    CLICK: (P.BROWSER_AUTOMATION, True),
    TYPE: (P.BROWSER_AUTOMATION, True),
    SELECT: (P.BROWSER_AUTOMATION, True),
    UPLOAD: (P.BROWSER_AUTOMATION, True),
    DOWNLOAD: (P.BROWSER_AUTOMATION, True),
}

#: FR-034 - intents that a signed-in session never authorises. Detected on
#: the element about to be acted on and on the page URL; any hit lifts the
#: action to a CONFIRM (exact-action approval) regardless of session.
EXTERNAL_WRITE = {
    "purchase": (r"\b(buy now|place (your )?order|checkout|pay now|confirm (purchase|payment)|"
                 r"complete (purchase|order)|subscribe now|add payment)\b",
                 r"/(checkout|cart/pay|payment|billing|purchase|order/confirm)\b"),
    "publish": (r"\b(publish|post now|send tweet|tweet|share now|go live|submit post|"
                r"send message|send email|send)\b",
                r"/(compose|publish|post/new|tweet|status/update)\b"),
    "destructive": (r"\b(delete (account|repository|repo|project|everything|all)|"
                    r"permanently delete|remove all|wipe|factory reset|"
                    r"close (my )?account|deactivate)\b",
                    r"/(delete|destroy|deactivate|close-account)\b"),
    "security_settings": (r"\b(change password|reset password|two[- ]factor|2fa|security key|"
                          r"api key|access token|revoke|grant access|authorize app|"
                          r"recovery (code|email|phone))\b",
                          r"/(settings/security|security|password|tokens|api-keys|"
                          r"oauth/authorize|permissions)\b"),
}

#: FR-035 - human verification surfaces. Any of these on the page means
#: STOP and hand the tab to the person (or use an approved API path).
#: Structural signals (widget class names, challenge phrases, interstitial
#: titles) - not the bare word "captcha", which a help article can mention.
HUMAN_VERIFICATION = (
    r"\bg-recaptcha\b", r"\brecaptcha/api\b", r"\bh-captcha\b", r"\bhcaptcha\.com\b",
    r"\bcf-turnstile\b", r"\bcf-challenge\b", r"\bchallenge-platform\b",
    r"\bverify (you are|you're) (a )?human\b", r"\bare you a robot\b",
    r"\bi am not a robot\b", r"\bi'm not a robot\b",
    r"\bchecking your browser\b", r"\bjust a moment\.\.\.",
    r"\bcomplete the (security )?(check|challenge)\b", r"\bsolve the captcha\b",
    r"\benter the (captcha|characters) (you see|shown|below)\b",
    r"\bone[- ]time (code|password)\b", r"\bverification code\b",
    r"\benter the code (we )?sent\b", r"\bauthenticator app\b",
    r"\bpress and hold\b", r"\bprove you're human\b",
    # Passkey and hardware-key prompts need the person's own device or touch.
    r"\b(use|verify with|sign in with|continue with) (your |a )?pass ?key\b",
    r"\b(use|insert|touch|tap|connect) (your )?(hardware |usb |physical )?security key\b",
)

ISOLATED = "isolated"
AUTHORIZED = "authorized"
TEMPORARY = "temporary"
PROFILE_KINDS = (ISOLATED, AUTHORIZED, TEMPORARY)

#: The policy label a FR-034 external write carries: it is a CONFIRM-class
#: action whatever the primitive's own category says.
EXTERNAL_WRITE_POLICY = "EXTERNAL_WRITE_CONFIRM"

#: Argument keys that would carry authority if a caller could set them. A
#: step's arguments come from the model, and through the model from whatever
#: page it last read, so none of these is honoured from `args`: session
#: approval is the loop's own `approvals`, set by the runtime once the policy
#: engine has allowed the primitive, and an external write's exact-action
#: confirmation is the keyword `confirmed=True`, which only trusted code
#: passes after spending the person's yes (`friday.toolsets.web.browser_act`).
AUTHORITY_KEYS = frozenset({"confirmed", "approved", "nonce"})


@dataclass(frozen=True)
class ProfileChoice:
    """FR-033: which profile a session runs in, and why."""

    kind: str
    reason: str
    directory: str = ""      # the user-data dir (AUTHORIZED and TEMPORARY only)


def choose_profile(*, worker: str, authorized_by_approval: bool,
                   authorized_dir: str = "",
                   workspace_id: str | None = None,
                   temporary_by_approval: bool = False) -> ProfileChoice:
    """A worker runs ISOLATED unless the objective's approval explicitly
    named the authorized profile. Friday's own foreground session may use
    AUTHORIZED; the cookies never leave that context either way.

    FR-PRV-002 adds a second boundary on top of the approval one: an
    authorized profile directory belongs to the workspace whose approval
    named it, and `workspace_id` records that on the choice. A signed-in
    session is private state that happens to live in a browser rather than
    in the database, so "brand A's approval unlocked this profile" must not
    read as "any workspace may use it". A caller that hands a directory
    approved by one workspace to another gets ISOLATED and the reason says
    why - refused, not silently honoured.

    BROW-003: `temporary_by_approval` is the person's yes to a throwaway
    on-disk profile for this workspace: its own cookies and storage, no
    access to the signed-in profile, deleted by `discard_temporary_profile`
    when the session ends. Without that yes a profile is never temporary -
    the default stays the in-memory ISOLATED context.
    """
    from friday.workspace import resolve
    owner = resolve(workspace_id)
    if authorized_by_approval and authorized_dir:
        if not owns_profile_dir(authorized_dir, owner):
            return ProfileChoice(
                ISOLATED,
                f"the approved profile directory belongs to another workspace, "
                f"not {owner!r}; isolated instead (FR-PRV-002)")
        return ProfileChoice(AUTHORIZED, f"approval named the signed-in profile for {worker}",
                             authorized_dir)
    if authorized_by_approval and not authorized_dir:
        return ProfileChoice(ISOLATED, "approval named the signed-in profile but no "
                                       "profile directory is configured; isolated instead")
    if temporary_by_approval:
        return ProfileChoice(TEMPORARY, f"approval named a temporary profile for {worker}; "
                                        f"it is deleted when the session ends",
                             str(new_temporary_profile_dir(owner)))
    return ProfileChoice(ISOLATED, f"{worker} gets an ephemeral context: no cookies, "
                                   f"no signed-in state (FR-033)")


def workspace_profile_dir(workspace_id: str | None = None) -> Path:
    """The dedicated FRIDAY Chrome profile directory for a workspace.

    FR-BRW-002 allows a dedicated profile; FR-PRV-002 forbids sharing one
    across workspaces. Every workspace sits at the same depth under
    `browser/workspaces/`, including `default`: a layout where one tenant
    lives at the root is how a later "just read the browser directory"
    shortcut reads the first tenant's cookies.
    """
    from friday.config import STATE_ROOT
    from friday.workspace import resolve
    return STATE_ROOT / "browser" / "workspaces" / resolve(workspace_id)


def temporary_profiles_root(workspace_id: str | None = None) -> Path:
    """Where a workspace's throwaway profiles live (BROW-003). Kept beside
    `workspaces/` rather than inside a workspace's dedicated profile, so a
    temporary profile is never a sub-directory of a signed-in one."""
    from friday.config import STATE_ROOT
    from friday.workspace import resolve
    return STATE_ROOT / "browser" / "temporary" / resolve(workspace_id)


def new_temporary_profile_dir(workspace_id: str | None = None) -> Path:
    """A fresh, unique directory path for one temporary profile. Nothing is
    created: the browser makes it on launch, and every call is a new name so
    two sessions never share cookies."""
    return temporary_profiles_root(workspace_id) / uuid.uuid4().hex


def discard_temporary_profile(directory: str, workspace_id: str | None = None) -> bool:
    """Delete one temporary profile; True if it existed.

    This is the only deletion of a browser profile in the module, so it
    refuses anything that is not exactly one directory directly under this
    workspace's temporary root - never the dedicated profile, another
    workspace's temporary one, or the user's own Chrome profile. Refusal
    raises: a silent no-op would let a caller believe a signed-in profile had
    been cleaned up (or worse, that deleting it was fine).
    """
    root = temporary_profiles_root(workspace_id).resolve()
    candidate = Path(directory).resolve()
    if candidate.parent != root:
        raise ValueError(f"{str(directory)!r} is not a temporary profile of this "
                         f"workspace; refusing to delete it")
    if not candidate.exists():
        return False
    shutil.rmtree(candidate)
    return True


def owns_profile_dir(directory: str, workspace_id: str | None = None) -> bool:
    """True when `directory` is this workspace's own profile, or a profile
    outside FRIDAY's managed trees entirely.

    The second case is deliberate: the user's real Chrome profile is theirs,
    not a workspace's, and the owner's recorded direction is that FRIDAY
    mostly works in it. What this refuses is one workspace reaching into the
    dedicated or temporary profile FRIDAY created for another - the case the
    user never approved because they never saw it.
    """
    from friday.config import STATE_ROOT
    from friday.workspace import resolve
    owner = resolve(workspace_id)
    try:
        candidate = Path(directory).resolve()
        managed = [(STATE_ROOT / "browser" / name).resolve()
                   for name in ("workspaces", "temporary")]
    except OSError:
        return False
    for root in managed:
        if candidate.is_relative_to(root):
            return candidate.is_relative_to(root / owner)
    return True                           # not a FRIDAY-managed profile


# ---------------------------------------------------------------------------
# Structured observation (FR-037)
# ---------------------------------------------------------------------------


@dataclass
class Element:
    index: int
    tag: str
    role: str
    text: str
    name: str = ""            # name/id/aria-label
    href: str = ""
    kind: str = ""            # input type
    selector: str = ""


@dataclass
class PageState:
    """What the page IS, structurally. The screenshot path is evidence, not
    the reasoning input."""

    url: str
    title: str
    text: str
    elements: list[Element] = field(default_factory=list)
    forms: list[dict] = field(default_factory=list)
    tabs: list[dict] = field(default_factory=list)
    screenshot: str = ""
    observed_at: float = field(default_factory=time.time)
    perception: str = "structured"       # structured | screenshot_only
    redactions: int = 0
    #: DOM-level markers that text cannot show: verification widget class
    #: names and challenge iframes (FR-035 structural detection).
    markers: list[str] = field(default_factory=list)

    def find(self, target: str) -> Element | None:
        """Resolve a human target ('the Sign in button', 'Search box',
        '#login', 'a[href*=docs]') to one element. CSS goes straight
        through; text matches prefer exact, then prefix, then contains."""
        t = (target or "").strip()
        if not t:
            return None
        if t.startswith(("#", ".", "[")) or re.match(r"^[a-z]+(\[|\.|#|:)", t):
            return Element(index=-1, tag="", role="", text="", selector=t)
        low = re.sub(r"^(the |a |an )", "", t.lower())
        low = re.sub(r"\s+(button|link|field|box|input|tab|menu|checkbox)$", "", low)
        best: tuple[int, Element] | None = None
        for el in self.elements:
            hay = " ".join((el.text, el.name)).lower().strip()
            if not hay:
                continue
            score = 0
            if hay == low or el.text.lower().strip() == low:
                score = 100
            elif hay.startswith(low):
                score = 70
            elif low in hay:
                score = 50 - min(len(hay) - len(low), 30)
            if score and (best is None or score > best[0]):
                best = (score, el)
        return best[1] if best else None

    def to_dict(self) -> dict:
        return {"url": self.url, "title": self.title, "text": self.text[:4000],
                "elements": [e.__dict__ for e in self.elements[:200]],
                "forms": self.forms, "tabs": self.tabs, "screenshot": self.screenshot,
                "perception": self.perception, "redactions": self.redactions}


#: The JS that builds the structured element table: interactive elements
#: with role, text, name, href and a stable selector. Bounded to 300.
ELEMENT_SCRIPT = r"""
() => {
  const sel = ['a[href]', 'button', 'input', 'select', 'textarea', '[role=button]',
               '[role=link]', '[role=tab]', '[role=menuitem]', '[role=checkbox]',
               '[onclick]', 'summary'].join(',');
  const nodes = Array.from(document.querySelectorAll(sel));
  const out = [];
  const seen = new Set();
  const cssPath = (el) => {
    if (el.id) return '#' + CSS.escape(el.id);
    const parts = [];
    let cur = el;
    while (cur && cur.nodeType === 1 && parts.length < 5) {
      let part = cur.tagName.toLowerCase();
      if (cur.id) { parts.unshift('#' + CSS.escape(cur.id)); break; }
      const parent = cur.parentElement;
      if (parent) {
        const sibs = Array.from(parent.children).filter(c => c.tagName === cur.tagName);
        if (sibs.length > 1) part += ':nth-of-type(' + (sibs.indexOf(cur) + 1) + ')';
      }
      parts.unshift(part);
      cur = parent;
    }
    return parts.join(' > ');
  };
  for (const el of nodes) {
    if (out.length >= 300) break;
    const rect = el.getBoundingClientRect();
    if (rect.width === 0 && rect.height === 0) continue;
    const style = window.getComputedStyle(el);
    if (style.visibility === 'hidden' || style.display === 'none') continue;
    const text = (el.innerText || el.value || el.placeholder || '').trim().slice(0, 120);
    const name = (el.getAttribute('aria-label') || el.getAttribute('name') || el.id || el.getAttribute('title') || '').slice(0, 80);
    const selector = cssPath(el);
    if (seen.has(selector)) continue;
    seen.add(selector);
    out.push({tag: el.tagName.toLowerCase(),
              role: el.getAttribute('role') || (el.tagName === 'A' ? 'link' : el.tagName === 'BUTTON' ? 'button' : el.tagName.toLowerCase()),
              text, name, href: el.href || '', kind: el.type || '', selector});
  }
  const forms = Array.from(document.forms).slice(0, 20).map(f => ({
    action: f.action || '', method: (f.method || 'get').toLowerCase(),
    fields: Array.from(f.elements).slice(0, 30).map(e => ({name: e.name || e.id || '', type: e.type || e.tagName.toLowerCase()}))
  }));
  const markers = [];
  const widgetSel = ['.g-recaptcha', '.h-captcha', '.cf-turnstile', '#cf-challenge-running',
                     'iframe[src*="recaptcha"]', 'iframe[src*="hcaptcha"]',
                     'iframe[src*="turnstile"]', 'iframe[src*="challenges.cloudflare"]',
                     'script[src*="recaptcha/api"]', 'script[src*="hcaptcha.com"]',
                     'script[src*="challenge-platform"]'].join(',');
  for (const w of Array.from(document.querySelectorAll(widgetSel)).slice(0, 10)) {
    markers.push((w.className || '') + ' ' + (w.getAttribute('src') || '') + ' ' + (w.id || ''));
  }
  return {elements: out, forms, markers};
}
"""


def human_verification(state: PageState) -> str:
    """FR-035: the reason this page is a human-verification step, or ''.
    Reads the title, the visible text and the element table, plus the
    widget markers the page carries (`state.markers`, from the DOM)."""
    hay = " ".join((state.title, state.text[:6000], state.url, " ".join(state.markers),
                    " ".join(e.text + " " + e.name for e in state.elements[:100]))).lower()
    for pattern in HUMAN_VERIFICATION:
        if re.search(pattern, hay):
            return f"human verification present ({pattern.strip(chr(92) + 'b')})"
    return ""


def handoff_reason(state: PageState) -> str:
    """BROW-003: why this page still belongs to a person, or ''. Either a
    human-verification step (OTP, passkey, hardware key, captcha) or an
    authentication surface (a login/MFA URL). The same answer decides when a
    hand-off starts and when the person has finished it."""
    return human_verification(state) or (
        bc.AUTH_HANDOFF_REASON if bc.is_auth_url(state.url) else "")


def external_write_intent(state: PageState, element: Element | None, text: str = "") -> str:
    """FR-034: the external-write class this action falls in, or ''."""
    hay = " ".join(filter(None, (
        element.text if element else "", element.name if element else "", text))).lower()
    url = state.url.lower()
    for kind, (word_pattern, url_pattern) in EXTERNAL_WRITE.items():
        if hay and re.search(word_pattern, hay):
            return kind
        if element is not None and element.selector and re.search(url_pattern, url) \
                and re.search(r"\b(submit|confirm|continue|next|save|apply|ok)\b", hay):
            return kind
    return ""


# ---------------------------------------------------------------------------
# Driver seam
# ---------------------------------------------------------------------------


class Driver(Protocol):
    """What the primitives need from a browser. Playwright implements it in
    `PlaywrightDriver`; tests implement it in memory."""

    async def goto(self, url: str, timeout_ms: int) -> int | None: ...
    async def current(self) -> tuple[str, str]: ...            # url, title
    async def text(self) -> str: ...
    async def elements(self) -> dict: ...                       # ELEMENT_SCRIPT result
    async def click(self, selector: str) -> None: ...
    async def fill(self, selector: str, text: str, clear: bool) -> None: ...
    async def press(self, key: str) -> None: ...
    async def scroll(self, dx: int, dy: int) -> None: ...
    async def select(self, selector: str, value: str) -> list[str]: ...
    async def upload(self, selector: str, paths: list[str]) -> None: ...
    async def download(self, selector: str, into: str, timeout_ms: int) -> str: ...
    async def tabs(self) -> list[dict]: ...
    async def switch_tab(self, index: int) -> None: ...
    async def new_tab(self, url: str) -> int: ...
    async def close_tab(self, index: int) -> None: ...
    async def screenshot(self, path: str) -> str: ...
    async def wait_for(self, condition: str, value: str, timeout_ms: int) -> bool: ...


class PlaywrightDriver:
    """The real thing, over one Playwright BrowserContext."""

    def __init__(self, context, page) -> None:
        self.context = context
        self.page = page

    async def goto(self, url, timeout_ms):
        response = await self.page.goto(url, timeout=timeout_ms, wait_until="domcontentloaded")
        return response.status if response is not None else None

    async def current(self):
        try:
            title = await self.page.title()
        except Exception:  # noqa: BLE001
            title = ""
        return self.page.url, title

    async def text(self):
        try:
            return await self.page.inner_text("body")
        except Exception:  # noqa: BLE001
            return ""

    async def elements(self):
        try:
            return await self.page.evaluate(ELEMENT_SCRIPT)
        except Exception as exc:  # noqa: BLE001
            logger.debug("element script failed: %s", exc)
            return {"elements": [], "forms": []}

    async def click(self, selector):
        await self.page.click(selector, timeout=10000)

    async def fill(self, selector, text, clear):
        if clear:
            await self.page.fill(selector, text, timeout=10000)
        else:
            await self.page.type(selector, text, timeout=10000)

    async def press(self, key):
        await self.page.keyboard.press(key)

    async def scroll(self, dx, dy):
        await self.page.mouse.wheel(dx, dy)

    async def select(self, selector, value):
        return await self.page.select_option(selector, value, timeout=10000)

    async def upload(self, selector, paths):
        await self.page.set_input_files(selector, paths, timeout=10000)

    async def download(self, selector, into, timeout_ms):
        async with self.page.expect_download(timeout=timeout_ms) as info:
            await self.page.click(selector, timeout=10000)
        download = await info.value
        target = Path(into) / download.suggested_filename
        await download.save_as(str(target))
        return str(target)

    async def tabs(self):
        out = []
        for i, p in enumerate(self.context.pages):
            try:
                title = await p.title()
            except Exception:  # noqa: BLE001
                title = ""
            out.append({"index": i, "url": p.url, "title": title, "active": p is self.page})
        return out

    async def switch_tab(self, index):
        self.page = self.context.pages[index]
        await self.page.bring_to_front()

    async def new_tab(self, url):
        page = await self.context.new_page()
        if url:
            await page.goto(url, timeout=30000, wait_until="domcontentloaded")
        self.page = page
        return len(self.context.pages) - 1

    async def close_tab(self, index):
        page = self.context.pages[index]
        await page.close()
        if self.context.pages:
            self.page = self.context.pages[-1]

    async def screenshot(self, path):
        await self.page.screenshot(path=path, type="png")
        return path

    async def wait_for(self, condition, value, timeout_ms):
        try:
            if condition == "selector":
                await self.page.wait_for_selector(value, timeout=timeout_ms)
            elif condition == "url":
                await self.page.wait_for_url(f"**{value}**", timeout=timeout_ms)
            elif condition == "text":
                await self.page.wait_for_function(
                    "t => document.body && document.body.innerText.includes(t)",
                    arg=value, timeout=timeout_ms)
            elif condition == "load":
                await self.page.wait_for_load_state("networkidle", timeout=timeout_ms)
            else:
                return False
            return True
        except Exception:  # noqa: BLE001
            return False


# ---------------------------------------------------------------------------
# The loop
# ---------------------------------------------------------------------------


@dataclass
class Step:
    """One primitive, observed before and after, with the verify verdict."""

    primitive: str
    target: str = ""
    args: dict = field(default_factory=dict)
    policy: str = ""
    external_write: str = ""
    before: PageState | None = None
    after: PageState | None = None
    ok: bool = False
    detail: str = ""
    handoff: str = ""
    evidence: str = ""

    def to_dict(self) -> dict:
        return {"primitive": self.primitive, "target": self.target, "args": self.args,
                "policy": self.policy, "external_write": self.external_write,
                "before": {"url": self.before.url, "title": self.before.title} if self.before else None,
                "after": {"url": self.after.url, "title": self.after.title} if self.after else None,
                "ok": self.ok, "detail": self.detail, "handoff": self.handoff,
                "evidence": self.evidence}


class Refused(Exception):
    """The step was refused by policy/boundary before anything ran."""

    def __init__(self, step: Step, why: str) -> None:
        super().__init__(why)
        self.step = step


class Handoff(Exception):
    """The page needs a person (FR-035 human verification, or login)."""

    def __init__(self, step: Step, why: str) -> None:
        super().__init__(why)
        self.step = step


class Browser:
    """FR-036's loop over one driver: observe -> plan (policy) -> act ->
    observe -> verify. Every action has an observation checkpoint on both
    sides; there is no blind sequence."""

    def __init__(self, driver: Driver, *, profile: ProfileChoice,
                 shots_dir: str | Path | None = None,
                 approvals: dict[str, bool] | None = None) -> None:
        self.driver = driver
        self.profile = profile
        self.shots = Path(shots_dir) if shots_dir else None
        #: primitive -> a person said yes to state changes of this kind
        #: for this session (ASK). CONFIRM-class external writes are never
        #: settled here; they carry their own exact-action nonce.
        self.approvals = dict(approvals or {})
        self.steps: list[Step] = []
        self.last: PageState | None = None
        #: BROW-003: why the tab was last handed to a person, until `resume`
        #: sees the page no longer needs them. While set, a state-changing
        #: primitive re-reads the live page first instead of planning against
        #: the observation that stopped it.
        self.pending_handoff = ""

    # -- observe -----------------------------------------------------------

    async def observe(self, *, screenshot: bool = False) -> PageState:
        url, title = await self.driver.current()
        blocked = sensitive_domains.refusal(url)
        if blocked:
            # The observation boundary: nothing of the page is read.
            state = PageState(url=url, title="", text="", perception="blocked")
            self.last = state
            raise Refused(Step(INSPECT, url), blocked)
        raw = await self.driver.text()
        text, redactions = bc.redact_secrets(re.sub(r"\s+", " ", raw or "").strip())
        table = await self.driver.elements()
        elements = [Element(index=i, tag=e.get("tag", ""), role=e.get("role", ""),
                            text=bc.redact_secrets(e.get("text", ""))[0], name=e.get("name", ""),
                            href=e.get("href", ""), kind=e.get("kind", ""),
                            selector=e.get("selector", ""))
                    for i, e in enumerate(table.get("elements", []))]
        state = PageState(url=url, title=title, text=text, elements=elements,
                          forms=table.get("forms", []), redactions=redactions,
                          markers=[str(m) for m in table.get("markers", [])],
                          perception="structured" if elements or text else "screenshot_only")
        try:
            state.tabs = await self.driver.tabs()
        except Exception:  # noqa: BLE001
            state.tabs = []
        if screenshot and self.shots is not None:
            self.shots.mkdir(parents=True, exist_ok=True)
            path = self.shots / f"shot-{int(time.time() * 1000)}.png"
            try:
                state.screenshot = await self.driver.screenshot(str(path))
            except Exception as exc:  # noqa: BLE001
                logger.debug("screenshot failed: %s", exc)
        self.last = state
        return state

    # -- plan (policy) -----------------------------------------------------

    def plan(self, primitive: str, target: str = "", args: dict | None = None,
             *, state: PageState | None = None, confirmed: bool = False,
             net: dict | None = None) -> Step:
        """Classify one intended primitive against the CURRENT page. Raises
        Refused/Handoff; returns the Step with its policy decided.

        `confirmed` is the exact-action confirmation for an external write.
        It is a keyword rather than an argument so that nothing a model or a
        page writes into `args` can supply it (AUTHORITY_KEYS are dropped
        from the step's arguments, and never read)."""
        if primitive not in PRIMITIVES:
            raise Refused(Step(primitive, target), f"unknown primitive {primitive!r}")
        category, changes_state = PRIMITIVES[primitive]
        arguments = {key: value for key, value in (args or {}).items()
                     if key not in AUTHORITY_KEYS}
        step = Step(primitive=primitive, target=target, args=arguments,
                    policy=category, before=state or self.last)
        state = step.before

        if primitive in (OPEN, NAVIGATE):
            # `net`: a durable run's network scope (NET-001; netguard.run_scope).
            verdict = bc.classify_url(str(step.args.get("url", target)), net=net)
            if verdict.decision in (bc.BLOCK_SENSITIVE, bc.BLOCK_NETWORK):
                raise Refused(step, verdict.reason)
            if verdict.decision == bc.AUTH_HANDOFF:
                step.handoff = verdict.reason
            return step

        if state is None:
            raise Refused(step, "no observation yet: observe before acting")

        # FR-035: nothing acts through a human-verification page.
        hv = human_verification(state)
        if hv and changes_state:
            step.handoff = hv
            self.pending_handoff = hv
            raise Handoff(step, f"{hv}; hand the tab to the user or use an approved API path")

        if changes_state:
            element = state.find(target) if target else None
            if target and element is None:
                raise Refused(step, f"no element on the page matches {target!r}; "
                                    f"re-observe rather than guessing a coordinate")
            step.external_write = external_write_intent(
                state, element, str(step.args.get("text", "")))
            if step.external_write:
                # FR-034: session != authorization. Exact-action CONFIRM class,
                # named as such in the record.
                step.policy = EXTERNAL_WRITE_POLICY
                if not confirmed:
                    raise Refused(step, f"{step.external_write} action needs an exact-action "
                                        f"confirmation; being signed in does not authorise it")
            elif not self.approvals.get(primitive):
                raise Refused(step, f"{primitive} changes page state ({category}); "
                                    f"not approved for this session")
        return step

    # -- act + observe + verify -------------------------------------------

    async def act(self, step: Step, *, timeout_ms: int = 30000) -> Step:
        """Run one planned step, then re-observe and verify. Never raises for
        a page-level failure: `step.ok`/`step.detail` say what happened."""
        d = self.driver
        target = step.target
        element = step.before.find(target) if (step.before and target) else None
        selector = element.selector if element else target
        try:
            if step.primitive in (OPEN, NAVIGATE):
                status = await d.goto(str(step.args.get("url", target)), timeout_ms)
                step.detail = f"HTTP {status}"
                step.ok = status is None or status < 400
            elif step.primitive == INSPECT:
                step.ok = True
            elif step.primitive == CLICK:
                await d.click(selector)
                step.ok = True
            elif step.primitive == TYPE:
                await d.fill(selector, str(step.args.get("text", "")),
                             bool(step.args.get("clear", True)))
                if step.args.get("enter"):
                    await d.press("Enter")
                step.ok = True
            elif step.primitive == SCROLL:
                await d.scroll(int(step.args.get("dx", 0)), int(step.args.get("dy", 800)))
                step.ok = True
            elif step.primitive == SELECT:
                chosen = await d.select(selector, str(step.args.get("value", "")))
                step.ok = bool(chosen)
                step.detail = f"selected {chosen}"
            elif step.primitive == UPLOAD:
                paths = [str(p) for p in step.args.get("paths", [])]
                missing = [p for p in paths if not Path(p).is_file()]
                if missing:
                    step.ok, step.detail = False, f"missing file(s): {missing}"
                else:
                    await d.upload(selector, paths)
                    step.ok = True
            elif step.primitive == DOWNLOAD:
                into = str(step.args.get("into", "."))
                saved = await d.download(selector, into, timeout_ms)
                step.ok = Path(saved).is_file()
                step.detail = saved
            elif step.primitive == TABS:
                op = step.args.get("op", "list")
                if op == "new":
                    idx = await d.new_tab(str(step.args.get("url", "")))
                    step.detail = f"opened tab {idx}"
                elif op == "switch":
                    await d.switch_tab(int(step.args.get("index", 0)))
                elif op == "close":
                    await d.close_tab(int(step.args.get("index", 0)))
                step.ok = True
            elif step.primitive == SCREENSHOT:
                step.ok = True
            elif step.primitive == WAIT:
                step.ok = await d.wait_for(str(step.args.get("condition", "load")),
                                           str(step.args.get("value", "")), timeout_ms)
                step.detail = "condition met" if step.ok else "timed out"
            elif step.primitive == VERIFY:
                step.ok = True
        except Exception as exc:  # noqa: BLE001 - reported, never hidden
            step.ok, step.detail = False, f"{type(exc).__name__}: {exc}"

        # Observe after, always. A refusal here (sensitive redirect) is the
        # loop's own boundary firing, and the step records it.
        try:
            step.after = await self.observe(screenshot=(step.primitive == SCREENSHOT))
        except Refused as exc:
            step.ok, step.detail = False, str(exc)
            step.after = self.last
        if step.after is not None:
            hv = handoff_reason(step.after)
            if hv:
                step.handoff = hv
            # The live page is the truth about whether a person is still
            # needed, not the reason a step recorded on the way in (a login
            # URL that redirected to the inbox is no longer a hand-off).
            self.pending_handoff = hv
        else:
            self.pending_handoff = step.handoff
        self.verify(step)
        self.steps.append(step)
        return step

    def verify(self, step: Step) -> Step:
        """The observation checkpoint: what changed, and does it match the
        expectation the step carried (`expect` in args)?"""
        before, after = step.before, step.after
        if after is None:
            step.evidence = "no post-observation"
            step.ok = False
            return step
        parts = [f"url {after.url}"]
        if before is not None and before.url != after.url:
            parts.append(f"navigated from {before.url}")
        if step.primitive == SCREENSHOT:
            parts.append(f"screenshot {after.screenshot or 'failed'}")
            step.ok = bool(after.screenshot)
        expect = step.args.get("expect") or {}
        if isinstance(expect, dict) and expect:
            for key, want in expect.items():
                got = {"url": after.url, "title": after.title, "text": after.text}.get(key, "")
                if str(want).lower() not in str(got).lower():
                    step.ok = False
                    parts.append(f"expected {key} to contain {want!r}")
                else:
                    parts.append(f"{key} contains {want!r}")
        if step.handoff:
            parts.append(f"HANDOFF: {step.handoff}")
        step.evidence = "; ".join(parts)
        return step

    async def resume(self) -> PageState:
        """BROW-003: pick the session back up after a hand-off to a person.

        Re-reads the live page (through `observe`, so the sensitive-domain
        boundary and secret redaction apply to it like any other read). While
        the page still needs the person - the OTP/passkey/captcha step is
        still on screen, or the tab is still on a login URL - this raises
        `Handoff` and nothing runs. Once it does not, the hand-off is cleared
        and the fresh observation is returned, so the next primitive plans
        against what the person left behind rather than the page that stopped
        the loop. Friday never supplies the person's part: it only looks."""
        state = await self.observe()
        why = handoff_reason(state)
        if why:
            self.pending_handoff = why
            raise Handoff(Step(INSPECT, state.url, handoff=why, before=state),
                          f"{why}; still waiting for the person to finish")
        self.pending_handoff = ""
        return state

    async def run(self, primitive: str, target: str = "", args: dict | None = None,
                  *, timeout_ms: int = 30000, confirmed: bool = False,
                  net: dict | None = None) -> Step:
        """observe (if needed) -> plan -> act -> observe -> verify.
        `confirmed`: see `plan` - trusted callers only.

        A state-changing primitive after a hand-off first `resume`s: it
        proceeds only once the person has finished, on a fresh observation.
        Reads and navigation are not held, so the person can still be told
        what the page says and the tab can be moved on."""
        if self.pending_handoff and PRIMITIVES.get(primitive, ("", False))[1]:
            await self.resume()
        if self.last is None and primitive not in (OPEN, NAVIGATE, TABS):
            await self.observe()
        step = self.plan(primitive, target, args, confirmed=confirmed, net=net)
        return await self.act(step, timeout_ms=timeout_ms)

    def transcript(self) -> list[dict]:
        return [s.to_dict() for s in self.steps]
