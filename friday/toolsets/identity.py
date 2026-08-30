"""
Which of his accounts is this for?

"Open YouTube" has twelve possible answers on this machine and five different
Google identities behind them. Opening a blank automation browser answers none
of them, and opening the wrong one is worse than asking.

Order of resolution, and it is deliberately boring:

    he named one                    -> use it
    a service preference exists     -> use it, and say which
    otherwise                       -> the profile he was last in
    two candidates score equally    -> ask, once, and remember the answer

The remembered answer is a stored preference, which is a personalisation, not
an authorisation. It decides *which* account, never *whether* an action is
allowed - that is the policy engine's job and it does not get delegated here.
"""

from __future__ import annotations

from friday import browser_profiles as BP
from friday import contracts as c
from friday.policy import PolicyEngine, default_engine
from friday.store import FACT, Store
from friday.toolsets.web import _gate

EXECUTION_SCOPE = "user_device"

#: Where a service preference is kept. One subject per service, so "which
#: account for Gmail" survives a restart and is answered without asking again.
PREFERENCE_SUBJECT = "identity.default.{service}"


def _scoped(payload: dict) -> dict:
    return {"execution_scope": EXECUTION_SCOPE, **payload}


def remembered(store: Store, service: str) -> str:
    if not service:
        return ""
    rows = store.recall(PREFERENCE_SUBJECT.format(service=service.lower()))
    return str(rows[0]["value"]) if rows else ""


def remember_choice(store: Store, service: str, profile: BP.Profile) -> None:
    store.remember(
        PREFERENCE_SUBJECT.format(service=service.lower()),
        profile.email or profile.directory, kind=FACT,
        source=f"he chose {profile.label} for {service}",
        scope="preferences")


def browser_profiles(
    run: c.Run, *, engine: PolicyEngine = default_engine,
) -> c.ActionResult:
    """Every browser profile on this machine, and who is signed into it."""
    tool_id = "identity.profiles"
    blocked = _gate(run, tool_id, engine)
    if blocked:
        return blocked
    started = c.started(run.run_id, tool_id)

    profiles = BP.all_profiles()
    if not profiles:
        return run.record(c.failed(
            started, "no browser profiles found - is Chrome or Edge installed?"))

    active = BP.last_used()
    return run.record(c.succeeded(
        started,
        output=_scoped({
            "count": len(profiles),
            "profiles": [p.as_dict() for p in profiles],
            "active": active.as_dict() if active else None,
        }),
        verification=c.Verification(
            method="browser_local_state",
            evidence=f"{len(profiles)} profile(s) from the browsers' own "
                     f"metadata; active is "
                     f"{(active.email or active.name) if active else 'unknown'}",
        ),
    ))


def open_in_browser(
    run: c.Run, url: str, *, account: str = "", service: str = "",
    store: Store | None = None, engine: PolicyEngine = default_engine,
) -> c.ActionResult:
    """
    Open a URL in the browser profile he actually uses.

    Returns PARTIAL and lists the candidates when the account is ambiguous -
    which is a question, not a failure, and the caller should ask it.
    """
    tool_id = "identity.open_url"
    blocked = _gate(run, tool_id, engine)
    if blocked:
        return blocked
    started = c.started(run.run_id, tool_id)

    if not (url or "").strip():
        return run.record(c.failed(started, "no url given"))

    hint = account.strip()
    source = "named"
    if not hint and service and store is not None:
        hint = remembered(store, service)
        source = f"remembered for {service}"
    if not hint:
        source = "the profile he was last in"

    profile, alternatives = BP.resolve(hint)
    if profile is None:
        return run.record(c.partial(
            started,
            f"more than one account fits {hint or 'that'} - which one?",
            output=_scoped({
                "url": url, "needs_choice": True,
                "candidates": [p.as_dict() for p in alternatives],
            })))

    ok, message = BP.open_url(url, profile)
    if not ok:
        return run.record(c.failed(started, message))

    if service and store is not None and account.strip():
        # He named it, so that becomes the answer next time.
        try:
            remember_choice(store, service, profile)
        except Exception:
            pass

    return run.record(c.succeeded(
        started,
        output=_scoped({"url": url, "profile": profile.as_dict(),
                        "chosen_because": source}),
        verification=c.Verification(
            method="browser_launched",
            evidence=f"{message}; chosen because: {source}",
        ),
    ))
