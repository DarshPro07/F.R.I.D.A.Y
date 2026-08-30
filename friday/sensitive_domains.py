"""
The banking/payment observation block.

    A PAGE ON A FINANCIAL DOMAIN IS NEVER PLACED IN MODEL CONTEXT.

The rule comes from the governance pack (docs/security/, action
`banking_site_model_observation: DENY`) and it has to be code rather than
markdown, because the thing it constrains is not a person - it is every code
path that turns a URL into text, a screenshot or DOM state that a model will
read. Those paths are: `web.fetch`, `browser.open/navigate/inspect`,
`browser.automate` (screenshots every turn), and the research crawler.

What this is, honestly:

    A SUFFIX LIST IS NOT A CLASSIFIER.

This blocks *known* financial domains by registrable-domain suffix, plus
anything the user adds through `FRIDAY_SENSITIVE_DOMAINS`. An unknown bank is
not blocked - fail-open is the only honest default for a list, and saying so
here beats implying coverage the module does not have. The list is curated
toward domains this machine's user actually banks with (India-first) plus the
global payment processors whose checkout pages carry card fields.

The verdict string starts with `BLOCKED_SENSITIVE_DOMAIN` so callers, tests
and transcripts can recognise the refusal class without parsing prose.

The guard sits at the *observation* boundary, not the network boundary:
`netguard` decides whether a URL may be fetched at all; this decides whether
what came back may be shown to a model. Different questions, different
modules, deliberately.
"""

from __future__ import annotations

import os
from urllib.parse import urlsplit

#: The refusal class marker. Callers and tests match on this prefix.
MARKER = "BLOCKED_SENSITIVE_DOMAIN"

#: Kill switch, on by default. Turning it off is a deliberate configuration
#: act, never something a model or a page can do.
ENV_ENABLED = "FRIDAY_BROWSER_SENSITIVE_DOMAIN_GUARD"

#: Comma-separated extra registrable domains, e.g. "mybank.example,other.in".
ENV_EXTRA = "FRIDAY_SENSITIVE_DOMAINS"

#: Registrable domains whose pages are financial by nature. Matching is by
#: suffix on the hostname's dot-separated labels, so `netbanking.hdfcbank.com`
#: is caught by `hdfcbank.com` while `hdfcbank.com.evil.example` is not.
SENSITIVE_DOMAINS: frozenset[str] = frozenset({
    # India - major retail banks
    "hdfcbank.com", "icicibank.com", "onlinesbi.sbi", "sbi.co.in",
    "axisbank.com", "kotak.com", "pnbindia.in", "bankofbaroda.in",
    "unionbankofindia.co.in", "canarabank.com", "idfcfirstbank.com",
    "yesbank.in", "indusind.com", "federalbank.co.in", "rblbank.com",
    "aubank.in", "bandhanbank.com",
    # India - payments / UPI / wallets
    "paytm.com", "phonepe.com", "payzapp.in", "bhimupi.org.in",
    "npci.org.in", "mobikwik.com", "freecharge.in",
    # Global banks commonly reachable from anywhere
    "chase.com", "bankofamerica.com", "wellsfargo.com", "citibank.com",
    "citi.com", "hsbc.com", "hsbc.co.in", "barclays.co.uk", "sc.com",
    "dbs.com", "usbank.com", "capitalone.com", "ally.com",
    # Payment processors / checkout hosts
    "paypal.com", "stripe.com", "checkout.com", "razorpay.com",
    "payu.in", "payumoney.com", "ccavenue.com", "billdesk.com",
    "instamojo.com", "adyen.com", "braintreegateway.com", "2checkout.com",
    "worldpay.com", "authorize.net", "squareup.com",
    # Card networks' account portals
    "americanexpress.com", "discover.com",
    # Brokerages / trading (account pages carry holdings and order forms)
    "zerodha.com", "kite.trade", "upstox.com", "groww.in",
    "angelone.in", "icicidirect.com", "hdfcsec.com", "fidelity.com",
    "schwab.com", "etrade.com", "robinhood.com", "interactivebrokers.com",
})


def enabled() -> bool:
    """Whether the guard is in force. Read at call time, not import time."""
    raw = (os.getenv(ENV_ENABLED) or "true").strip().lower()
    return raw not in ("0", "false", "no", "off")


def _extra() -> frozenset[str]:
    raw = os.getenv(ENV_EXTRA) or ""
    return frozenset(
        d.strip().lower().lstrip(".")
        for d in raw.split(",") if d.strip()
    )


def _host_of(url: str) -> str:
    try:
        return (urlsplit((url or "").strip()).hostname or "").lower()
    except ValueError:
        return ""


def is_sensitive(url: str) -> bool:
    """Whether this URL's host is on the financial list."""
    host = _host_of(url)
    if not host:
        return False
    domains = SENSITIVE_DOMAINS | _extra()
    labels = host.split(".")
    # suffix match on label boundaries: hdfcbank.com matches
    # netbanking.hdfcbank.com but never hdfcbank.com.evil.example
    candidates = {".".join(labels[i:]) for i in range(len(labels))}
    return bool(candidates & domains)


def refusal(url: str) -> str:
    """
    Why this URL's content may not enter model context, or "" if it may.

    The message names the host rather than echoing the full URL, because the
    URL's path and query on a banking site can themselves carry account data
    and the refusal will be read by a model.
    """
    if not enabled():
        return ""
    if not is_sensitive(url):
        return ""
    host = _host_of(url)
    return (f"{MARKER}: {host} is a banking/payment domain and its page "
            f"content is never shown to a model. Nothing was read. Open it "
            f"yourself in your own browser; Friday will not observe it.")
