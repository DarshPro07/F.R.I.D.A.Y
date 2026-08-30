"""MCP tools for the browser policy capability and the secret broker.

Thin adapters: policy logic lives in friday/browser_capability.py and
friday/secret_broker.py; this file is the production wiring that makes
them REACHABLE (the reachability invariant refuses modules without a
production caller, by design).
"""

from __future__ import annotations

import logging

from friday import browser_capability as bc
from friday import secret_broker as sb

logger = logging.getLogger("friday-agent")


def register(mcp):
    @mcp.tool()
    def browser_page_observe(url: str) -> dict:
        """
        Read a web page's content under Friday's browser policy.

        The policy gate runs BEFORE any capture: banking/payment domains
        return BLOCKED_SENSITIVE_DOMAIN with zero content, auth pages
        hand off to the boss for credentials/MFA, and secret-shaped text
        is redacted from anything returned. Use for QA and research on
        ordinary pages; this is the sanctioned page->context path.
        """
        def capture() -> str:
            # Reuse the existing governed fetch path (netguard inside).
            # fetch() returns {'url','status','headers','content','chain'};
            # only the TEXT is page content - headers stay out of model
            # space. REDIRECT GUARD (adversarial finding): netguard
            # validates each hop for SSRF only, so an allowed URL could
            # redirect into a financial domain. Every hop in the chain
            # re-passes the observation boundary before content returns.
            from friday import netguard, sensitive_domains
            out = netguard.fetch(url)
            if isinstance(out, dict):
                for hop in out.get("chain") or [out.get("url", url)]:
                    if sensitive_domains.enabled() and \
                            sensitive_domains.is_sensitive(hop):
                        raise bc.SensitiveRedirect(
                            sensitive_domains.refusal(hop))
                return str(out.get("content") or out.get("text")
                           or "")[:20000]
            return str(out)[:20000]

        try:
            return bc.observe_page(url, capture)
        except Exception as exc:                             # noqa: BLE001
            logger.exception("browser_page_observe failed")
            return {"status": "failed", "error": str(exc)[:500]}

    @mcp.tool()
    def secrets_begin_entry(alias: str, provider: str = "",
                            purpose: str = "") -> dict:
        """
        Start a secure credential entry: opens a scratch file the BOSS
        types the secret into. Friday never sees the value - after the
        boss says done, call secrets_complete_entry with the same alias.
        Never ask the boss to paste a key into chat.
        """
        try:
            path = sb.SecretBroker().scratch_file(alias)
            return {"status": "ready", "alias": alias,
                    "scratch_path": str(path), "provider": provider,
                    "purpose": purpose,
                    "instruction": ("Boss types the key into that file "
                                    "and saves; then complete the entry. "
                                    "The file is shredded after.")}
        except Exception as exc:                             # noqa: BLE001
            return {"status": "failed", "error": str(exc)[:500]}

    @mcp.tool()
    def secrets_complete_entry(alias: str, provider: str = "",
                               purpose: str = "") -> dict:
        """
        Finish a credential entry started with secrets_begin_entry:
        encrypts the typed value, shreds the scratch file, and returns
        METADATA ONLY (alias, provider, length). The value itself never
        appears anywhere a model can read.
        """
        try:
            return sb.SecretBroker().ingest_scratch(
                alias, provider=provider, purpose=purpose)
        except FileNotFoundError:
            return {"status": "failed",
                    "error": f"no entry in progress for alias {alias!r} - "
                             f"call secrets_begin_entry first"}
        except Exception as exc:                             # noqa: BLE001
            return {"status": "failed", "error": str(exc)[:500]}

    @mcp.tool()
    def secrets_list() -> dict:
        """Connected credentials as capability metadata: alias, provider,
        purpose. Values are never shown - they do not exist in model
        space."""
        try:
            return {"status": "succeeded",
                    "aliases": sb.SecretBroker().list_aliases()}
        except Exception as exc:                             # noqa: BLE001
            return {"status": "failed", "error": str(exc)[:500]}
