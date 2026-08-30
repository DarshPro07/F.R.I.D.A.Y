/*
 * Friday Companion - the browser half of the bridge.
 *
 * Connects out to Friday on loopback and waits to be asked. It never
 * initiates: every command originates from Friday, and every reply is page
 * data that Friday treats as untrusted.
 *
 * TWO MODES, on purpose:
 *
 *   attended   activeTab. Clicking the toolbar icon grants this tab, this
 *              time. Nothing at all is possible without that gesture.
 *   granted    an origin the user approved on the options page. Works while
 *              they are away, which activeTab structurally cannot do - it
 *              needs a gesture every single time.
 *
 * The difference is visible rather than buried, because "Friday may act on
 * this site unattended" deserves to be a decision.
 *
 * Two things this will not do, whatever it is asked:
 *   - touch chrome:// or edge:// pages, which hold settings and saved passwords
 *   - type into a password field. Those are his to fill.
 */

/*
 * LIFETIME - the bug this design exists to avoid.
 *
 * An MV3 service worker is ephemeral. Chrome terminates it after inactivity,
 * and setTimeout dies with it - so "reconnect on close" reconnects exactly
 * once and then, when the worker is eventually killed, nothing ever brings it
 * back. Friday cannot dial in either: a loopback server cannot open a socket
 * into a sleeping extension. Unattended work would stop silently, which is
 * the worst way for it to stop.
 *
 * So the policy is state-aware rather than one-size:
 *
 *   no task            let the worker sleep. An alarm wakes it periodically to
 *                      see whether Friday is there. Alarms survive worker
 *                      termination; timers do not.
 *   task running       heartbeat under Chrome's 30s idle window. WebSocket
 *                      activity keeps the worker alive (Chrome 116+), which is
 *                      why minimum_chrome_version says 116.
 *   task finished      stop the heartbeat and let it sleep again.
 *
 * Friday declares the task state; the extension does not guess it. Keeping a
 * worker alive forever "just in case" is the thing Chrome asks extensions not
 * to do, and it would cost battery for no reason.
 */

const FRIDAY = "ws://127.0.0.1:8791";
const WAKE_ALARM = "friday-wake";
const WAKE_PERIOD_MINUTES = 1;      // the floor Chrome allows
const HEARTBEAT_MS = 20000;         // comfortably inside the 30s idle window

/*
 * THE LEASE - what stops a Friday crash from pinning the worker awake forever.
 *
 * A plain boolean "task running" has a silent failure: Friday sends
 * session.begin, then dies before session.end, and the extension stays awake
 * for the rest of the browser's life burning battery on a run that no longer
 * exists.
 *
 * So a run holds a LEASE that expires unless Friday keeps renewing it. If
 * Friday goes away, the lease lapses on its own and the worker sleeps.
 *
 * Two rules that make it mean anything:
 *
 *   the heartbeat does NOT renew the lease. If it did, the extension would be
 *   certifying its own task forever - the keepalive would be its own excuse
 *   for existing.
 *
 *   the lease lives in chrome.storage.session, not a global. Globals die with
 *   the worker, and a lease that vanishes when the worker naps is not a lease.
 *   storage.session survives termination and clears on browser restart, which
 *   is exactly right: after a restart the extension must assume NOTHING about
 *   running work and wait for Friday to say.
 */
const LEASE_KEY = "friday_lease";
const DEFAULT_LEASE_MS = 60000;

/*
 * THE TRACE - diagnostics, not a feature.
 *
 * "Test 14 failed" is not a finding. A trace turns it into a sequence you can
 * read, and the field that does most of the work is workerInstance: it is
 * minted fresh when this script is evaluated, so a NEW id in the log is direct
 * evidence that Chrome terminated the worker and started another. Without it,
 * worker death has to be inferred from silence.
 *
 * Never recorded: the token, DOM text, typed values, cookies, passwords. The
 * trace says WHAT happened and to which origin, never what was on the page.
 */
const TRACE_KEY = "friday_trace";
const TRACE_LIMIT = 200;
const workerInstance = Math.random().toString(36).slice(2, 8);

/*
 * What each event may carry. Enforced at emit time, not by inspecting the
 * source: a test can prove nobody WROTE `trace(..., {password})`, but it
 * cannot prove a variable named `reason` will never hold something sensitive
 * at runtime. Anything not named here is dropped, so a future edit cannot
 * widen the trace by accident.
 */
const TRACE_FIELDS = {
  "worker.started":    ["extension_id"],
  "ws.open":           [],
  "ws.paired":         [],
  "ws.closed":         ["code", "reason_class"],
  "ws.unreachable":    [],
  "lease.begin":       ["run_id", "lease_ms"],
  "lease.renew":       ["run_id", "lease_ms"],
  "lease.end":         ["run_id"],
  "lease.expired":     [],
  "heartbeat":         ["run_id", "lease_in_ms"],
  "alarm.fired":       ["socket_open"],
  "permission.check":  ["origin", "granted"]
};

let socket = null;
let attendedTabId = null;
let heartbeat = null;

async function trace(event, detail) {
  // An unknown event still gets a line - silence would hide a bug - but it
  // carries nothing, because nothing has been vetted for it.
  const allowed = TRACE_FIELDS[event] || [];
  const entry = {
    at: new Date().toISOString(),
    worker: workerInstance,
    event: TRACE_FIELDS[event] ? event : "unknown"
  };
  for (const field of allowed) {
    if (detail && detail[field] !== undefined) entry[field] = detail[field];
  }

  const stored = await chrome.storage.session.get(TRACE_KEY);
  const entries = stored[TRACE_KEY] || [];
  entries.push(entry);
  await chrome.storage.session.set({
    [TRACE_KEY]: entries.slice(-TRACE_LIMIT)
  });
}

trace("worker.started", { extension_id: chrome.runtime.id });

// ---------------------------------------------------------------------------
// Connection
// ---------------------------------------------------------------------------

async function pairingToken() {
  const stored = await chrome.storage.local.get("token");
  return (stored.token || "").trim();
}

async function connect() {
  const secret = await pairingToken();
  if (!secret) return; // not paired yet - the options page explains how

  try {
    socket = new WebSocket(FRIDAY);
  } catch (e) {
    // Friday is not listening. Do nothing: the alarm will try again, and a
    // timer here would both fail to survive worker termination and reference
    // a retry constant that no longer exists.
    trace("ws.unreachable");
    socket = null;
    return;
  }

  socket.onopen = () => {
    trace("ws.open");
    socket.send(JSON.stringify({ token: secret }));   // the value is never traced
  };

  socket.onmessage = async (event) => {
    let message;
    try { message = JSON.parse(event.data); } catch (e) { return; }
    if (message.friday === "paired") {
      trace("ws.paired");
      chrome.action.setBadgeText({ text: "ok" });
      chrome.action.setBadgeBackgroundColor({ color: "#1a7f37" });
      return;
    }
    if (!message.command) return;
    const reply = await run(message.command, message.params || {});
    socket.send(JSON.stringify(Object.assign({ id: message.id }, reply)));
  };

  socket.onclose = (event) => {
    // The close reason is free text from the other end. Classified rather than
    // copied: the useful part is WHICH refusal it was, and a raw string is a
    // place for something unvetted to ride along.
    const code = event && event.code;
    const reason_class =
      code === 4403 ? "origin-refused"
      : code === 4401 ? "token-refused"
      : code === 4400 ? "no-greeting"
      : code === 1000 ? "normal"
      : "other";
    trace("ws.closed", { code: code, reason_class: reason_class });
    socket = null;
    stopHeartbeat();
    chrome.action.setBadgeText({ text: "" });
    // No setTimeout here on purpose - it would not survive the worker being
    // terminated. The alarm is what brings us back.
  };
  socket.onerror = () => { try { socket.close(); } catch (e) {} };
}

// ---------------------------------------------------------------------------
// Staying alive only while it matters
// ---------------------------------------------------------------------------

async function readLease() {
  const stored = await chrome.storage.session.get(LEASE_KEY);
  return stored[LEASE_KEY] || null;
}

async function writeLease(lease) {
  if (lease) await chrome.storage.session.set({ [LEASE_KEY]: lease });
  else await chrome.storage.session.remove(LEASE_KEY);
}

/** The lease if it is still valid; otherwise null, and it is cleared. */
async function liveLease() {
  const lease = await readLease();
  if (!lease) return null;
  if (Date.now() > lease.expires_at) {
    await writeLease(null);
    return null;
  }
  return lease;
}

function startHeartbeat() {
  if (heartbeat !== null) return;
  heartbeat = setInterval(async () => {
    // Checked every tick: an expired lease stops the keepalive without anyone
    // having to tell it to. That is the failsafe for Friday dying mid-run.
    const lease = await liveLease();
    if (!lease) {
      trace("lease.expired");
      stopHeartbeat();
      chrome.action.setBadgeText({ text: socket ? "ok" : "" });
      return;
    }
    if (socket && socket.readyState === WebSocket.OPEN) {
      // Traffic keeps the worker alive; the content is irrelevant, so this is
      // the smallest thing that counts as activity. It carries no run id
      // BECAUSE it must not be able to renew anything.
      socket.send(JSON.stringify({ heartbeat: true }));
      trace("heartbeat", { run_id: lease.run_id,
                           lease_in_ms: lease.expires_at - Date.now() });
    }
  }, HEARTBEAT_MS);
}

function stopHeartbeat() {
  if (heartbeat !== null) {
    clearInterval(heartbeat);
    heartbeat = null;
  }
}

// Alarms survive service-worker termination, which is the whole point.
// persistAcrossSessions is Chrome 150+; this browser is older, so the alarm is
// checked and recreated on every startup path rather than assumed to exist.
async function ensureWakeAlarm() {
  const existing = await chrome.alarms.get(WAKE_ALARM);
  if (!existing) {
    await chrome.alarms.create(WAKE_ALARM, { periodInMinutes: WAKE_PERIOD_MINUTES });
  }
}

chrome.alarms.onAlarm.addListener(async (alarm) => {
  if (alarm.name !== WAKE_ALARM) return;
  await trace("alarm.fired", { socket_open: !!socket });
  await ensureWakeAlarm();
  if (!socket || socket.readyState === WebSocket.CLOSED) connect();
  // Woken with a live lease and no heartbeat? The worker was terminated
  // mid-run. Resume keeping it awake.
  if (await liveLease()) startHeartbeat();
});

async function startup() {
  await ensureWakeAlarm();
  connect();
}

chrome.runtime.onStartup.addListener(startup);
chrome.runtime.onInstalled.addListener(startup);
startup();

chrome.action.onClicked.addListener((tab) => {
  attendedTabId = tab.id;
  chrome.action.setBadgeText({ text: "on", tabId: tab.id });
  if (!socket) connect();
});

// ---------------------------------------------------------------------------
// Permission - whether a command may touch a tab at all
// ---------------------------------------------------------------------------

async function mayTouch(tab) {
  if (!tab || !tab.url) return { ok: false, error: "no tab" };
  if (tab.url.startsWith("chrome://") || tab.url.startsWith("edge://") ||
      tab.url.startsWith("about:")) {
    return { ok: false, error: "browser settings pages are out of bounds" };
  }
  if (attendedTabId === tab.id) return { ok: true, mode: "attended" };

  let origin;
  try {
    origin = new URL(tab.url).origin + "/*";
  } catch (e) {
    return { ok: false, error: "that tab has no usable origin" };
  }
  // Asked of Chrome at execution time, every time. A cached answer would go on
  // saying yes after the user revoked the origin, which is the one thing a
  // permission check must never do.
  const granted = await chrome.permissions.contains({ origins: [origin] });
  await trace("permission.check", { origin: origin, granted: granted });
  if (granted) return { ok: true, mode: "granted" };

  return {
    ok: false,
    error: "Friday has no permission for " + origin + ". Click the Friday " +
           "icon on this tab to attend it once, or approve the site in the " +
           "extension options for unattended work."
  };
}

async function currentTab() {
  const tabs = await chrome.tabs.query({ active: true, lastFocusedWindow: true });
  return tabs[0];
}

async function inPage(tabId, fn, args) {
  const results = await chrome.scripting.executeScript({
    target: { tabId: tabId }, func: fn, args: args || []
  });
  return results && results[0] ? results[0].result : null;
}

// ---------------------------------------------------------------------------
// The things that run inside the page. Kept small and readable on purpose:
// everything they return is untrusted input on Friday's side.
// ---------------------------------------------------------------------------

function readPage() {
  return {
    title: document.title,
    url: location.href,
    text: document.body ? document.body.innerText.slice(0, 20000) : ""
  };
}

function observeAccount() {
  const text = document.body ? document.body.innerText : "";
  const handles = new Set();
  const emails = new Set();
  const handleMatches = text.match(/@[A-Za-z0-9._]{2,30}/g) || [];
  const emailMatches = text.match(/[\w.+-]+@[\w-]+\.[\w.]{2,}/g) || [];
  handleMatches.forEach((h) => handles.add(h));
  emailMatches.forEach((e) => emails.add(e));
  return {
    url: location.href,
    host: location.host,
    handles: Array.from(handles).slice(0, 10),
    emails: Array.from(emails).slice(0, 10),
    looks_signed_out: /log ?in|sign ?in/i.test(text.slice(0, 4000))
  };
}

function interactive(want) {
  const norm = (s) => (s || "").trim().toLowerCase();
  const target = norm(want);
  const nodes = Array.prototype.slice.call(document.querySelectorAll(
    "a,button,input,textarea,select,[role=button],[role=link],[role=tab]"));
  return nodes.map((el, i) => ({
    index: i,
    tag: el.tagName.toLowerCase(),
    role: el.getAttribute("role") || "",
    type: el.type || "",
    name: norm(el.innerText || el.value || el.getAttribute("aria-label") ||
               el.getAttribute("placeholder") || el.getAttribute("title")),
    visible: !!(el.offsetWidth || el.offsetHeight)
  })).filter((e) => e.visible && e.name && (!target || e.name.indexOf(target) >= 0))
     .slice(0, 25);
}

function act(kind, want, value) {
  const norm = (s) => (s || "").trim().toLowerCase();
  const target = norm(want);
  const nodes = Array.prototype.slice.call(document.querySelectorAll(
    "a,button,input,textarea,select,[role=button],[role=link],[role=tab]"));
  let match = null;
  for (const el of nodes) {
    const name = norm(el.innerText || el.value || el.getAttribute("aria-label") ||
                      el.getAttribute("placeholder") || el.getAttribute("title"));
    if (name && name.indexOf(target) >= 0 && (el.offsetWidth || el.offsetHeight)) {
      match = el;
      break;
    }
  }
  if (!match) return { found: false };
  if (kind === "type" && match.type === "password") {
    return { found: true, refused: "password fields are yours to fill, not mine" };
  }
  if (kind === "click") {
    match.click();
    return { found: true, acted: "clicked", name: norm(match.innerText) };
  }
  match.focus();
  match.value = value;
  match.dispatchEvent(new Event("input", { bubbles: true }));
  match.dispatchEvent(new Event("change", { bubbles: true }));
  return { found: true, acted: "typed" };
}

// ---------------------------------------------------------------------------
// Commands
// ---------------------------------------------------------------------------

async function run(command, params) {
  try {
    if (command === "hello") {
      return {
        ok: true,
        extension: chrome.runtime.getManifest().version,
        // Chrome's own view of the id, so a mismatch with what Friday pinned
        // is visible as data rather than as a connection that silently never
        // happens.
        extension_id: chrome.runtime.id,
        task_active: !!(await liveLease())
      };
    }

    if (command === "session.begin") {
      // A lease, not a flag. If Friday dies without ending it, it lapses.
      if (!params.run_id) return { ok: false, error: "session.begin needs a run_id" };
      const ms = Number(params.lease_ms) > 0 ? Number(params.lease_ms)
                                             : DEFAULT_LEASE_MS;
      await writeLease({ run_id: params.run_id, expires_at: Date.now() + ms });
      await trace("lease.begin", { run_id: params.run_id, lease_ms: ms });
      startHeartbeat();
      chrome.action.setBadgeText({ text: "run" });
      return { ok: true, run_id: params.run_id, lease_ms: ms };
    }

    if (command === "session.renew") {
      const lease = await liveLease();
      if (!lease) {
        return { ok: false, error: "no live lease to renew" };
      }
      if (lease.run_id !== params.run_id) {
        // A late renewal from a finished run must not resurrect itself over
        // the run that replaced it.
        return { ok: false, error: "that run is not the active one",
                 active_run: lease.run_id };
      }
      const ms = Number(params.lease_ms) > 0 ? Number(params.lease_ms)
                                             : DEFAULT_LEASE_MS;
      await writeLease({ run_id: lease.run_id, expires_at: Date.now() + ms });
      await trace("lease.renew", { run_id: lease.run_id, lease_ms: ms });
      startHeartbeat();
      return { ok: true, run_id: lease.run_id, lease_ms: ms };
    }

    if (command === "session.end") {
      const lease = await readLease();
      if (lease && params.run_id && lease.run_id !== params.run_id) {
        return { ok: false, error: "that run is not the active one",
                 active_run: lease.run_id };
      }
      await writeLease(null);
      await trace("lease.end", { run_id: params.run_id || null });
      stopHeartbeat();
      chrome.action.setBadgeText({ text: socket ? "ok" : "" });
      return { ok: true, keeping_awake: false };
    }

    if (command === "trace.dump") {
      const stored = await chrome.storage.session.get(TRACE_KEY);
      const lease = await readLease();
      const alarm = await chrome.alarms.get(WAKE_ALARM);
      const permissions = await chrome.permissions.getAll();
      return {
        ok: true,
        worker: workerInstance,
        extension_id: chrome.runtime.id,
        socket_open: !!(socket && socket.readyState === WebSocket.OPEN),
        heartbeat_running: heartbeat !== null,
        lease: lease,
        alarm_exists: !!alarm,
        granted_origins: permissions.origins || [],
        attended_tab: attendedTabId,
        entries: stored[TRACE_KEY] || []
      };
    }

    if (command === "session.status") {
      const lease = await liveLease();
      return { ok: true, active_run: lease ? lease.run_id : null,
               expires_in_ms: lease ? lease.expires_at - Date.now() : null };
    }

    if (command === "tabs.list") {
      const tabs = await chrome.tabs.query({});
      return { ok: true, tabs: tabs.map((t) => ({
        id: t.id, title: t.title, url: t.url, active: t.active
      })) };
    }

    if (command === "tabs.current") {
      const tab = await currentTab();
      if (!tab) return { ok: false, error: "no active tab" };
      return { ok: true, tab: { id: tab.id, title: tab.title, url: tab.url } };
    }

    if (command === "tabs.focus") {
      let tab;
      if (params.tab_id) {
        tab = await chrome.tabs.get(params.tab_id);
      } else {
        const found = await chrome.tabs.query({ url: params.match });
        tab = found[0];
      }
      if (!tab) return { ok: false, error: "no matching tab" };
      await chrome.tabs.update(tab.id, { active: true });
      await chrome.windows.update(tab.windowId, { focused: true });
      return { ok: true, tab: { id: tab.id, title: tab.title, url: tab.url } };
    }

    if (command === "nav.open") {
      const tab = await chrome.tabs.create({ url: params.url, active: true });
      return { ok: true, tab: { id: tab.id, url: tab.url } };
    }

    const tab = await currentTab();
    const allowed = await mayTouch(tab);
    if (!allowed.ok) return allowed;

    if (command === "page.read") {
      const page = await inPage(tab.id, readPage);
      return Object.assign({ ok: true, mode: allowed.mode }, page);
    }

    if (command === "account.observe") {
      const observed = await inPage(tab.id, observeAccount);
      return { ok: true, mode: allowed.mode, observed: observed };
    }

    if (command === "page.find") {
      const elements = await inPage(tab.id, interactive, [params.name || ""]);
      return { ok: true, mode: allowed.mode, elements: elements };
    }

    if (command === "page.click" || command === "page.type") {
      const kind = command === "page.click" ? "click" : "type";
      const done = await inPage(tab.id, act,
                                [kind, params.name || "", params.text || ""]);
      if (!done || !done.found) {
        return { ok: false, error: "nothing on the page matches " +
                                   JSON.stringify(params.name || "") };
      }
      if (done.refused) return { ok: false, error: done.refused };
      return { ok: true, mode: allowed.mode, acted: done.acted, url: tab.url };
    }

    return { ok: false, error: "unknown command " + command };
  } catch (e) {
    return { ok: false, error: String(e && e.message ? e.message : e) };
  }
}
