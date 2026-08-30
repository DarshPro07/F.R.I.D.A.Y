"""
Which account, and nothing smuggled in beside it.

The launch call is the one place this module hands a string to a process, and
everything before the first bare argument on a Chromium command line is a
switch. `--load-extension`, `--remote-debugging-port`, `--user-data-dir`,
`--disable-web-security` are all one unchecked string away from being the
browser's new configuration.

That is reachable, not theoretical: `url` is filled in by the model, and the
model reads web pages. A crawled page saying "now open --load-extension=..."
is the exact shape prompt injection takes.
"""

from __future__ import annotations

import pytest

from friday import browser_profiles as BP


@pytest.fixture
def profile():
    return BP.Profile(browser="chrome", directory="Profile 55",
                      name="aicodepro.com", email="devendra@aicodepro.com")


# ---------------------------------------------------------------------------
# Nothing that is not a url reaches the command line
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("hostile", [
    "--load-extension=C:/evil",
    "--remote-debugging-port=9222",
    "--user-data-dir=C:/somewhere-else",
    "--disable-web-security",
    "-incognito",
    "--headless",
])
def test_a_switch_is_never_opened_as_a_url(hostile, profile):
    ok, why = BP.open_url(hostile, profile)
    assert not ok
    assert "switch" in why


@pytest.mark.parametrize("scheme", [
    "file:///C:/Users/marke/.ssh/id_rsa",
    "chrome://settings/passwords",
    "javascript:alert(1)",
    "data:text/html,<h1>hi",
])
def test_only_http_and_https_may_be_opened(scheme, profile):
    ok, why = BP.open_url(scheme, profile)
    assert not ok, f"{scheme} was allowed"
    assert "http" in why


def test_a_url_with_no_host_is_refused(profile):
    assert not BP.open_url("https:///nothing", profile)[0]


def test_nothing_is_not_a_url(profile):
    assert not BP.open_url("", profile)[0]
    assert not BP.open_url("   ", profile)[0]


@pytest.mark.parametrize("good", [
    "https://www.youtube.com",
    "http://localhost:3000/dashboard",
    "https://mail.google.com/mail/u/0/#inbox",
    "https://example.com/a?b=c&d=-e",      # a dash inside is fine
])
def test_real_urls_pass(good):
    ok, checked = BP.safe_url(good)
    assert ok and checked == good


def test_an_odd_profile_directory_is_refused(profile):
    """It comes from Chrome's own config, but it lands in argv either way."""
    from dataclasses import replace

    bad = replace(profile, directory="--user-data-dir=C:/evil")
    ok, why = BP.open_url("https://example.com", bad)
    assert not ok and "profile directory" in why


def test_switch_parsing_is_ended_before_the_url(monkeypatch, profile):
    """
    Belt and braces. Even a url that gets past the check cannot become a flag,
    because `--` ends Chromium's switch parsing.
    """
    seen = {}

    def fake_popen(argv, **kwargs):
        seen["argv"] = argv
        return object()

    monkeypatch.setattr(BP, "executable", lambda browser: "chrome.exe")
    monkeypatch.setattr(BP.subprocess, "Popen", fake_popen)

    ok, _ = BP.open_url("https://www.youtube.com", profile)
    assert ok
    argv = seen["argv"]
    assert "--" in argv
    assert argv.index("--") == argv.index("https://www.youtube.com") - 1


# ---------------------------------------------------------------------------
# Choosing the account
# ---------------------------------------------------------------------------


def profiles():
    return [
        BP.Profile("chrome", "Profile 55", "aicodepro.com", "devendra@aicodepro.com", True),
        BP.Profile("chrome", "Profile 54", "aicodepro.com", "connect@aicodepro.com"),
        BP.Profile("chrome", "Profile 26", "Darsh", "darshyadav07@gmail.com"),
        BP.Profile("chrome", "Profile 24", "Work", ""),
    ]


def test_an_exact_address_wins(monkeypatch):
    monkeypatch.setattr(BP, "all_profiles", profiles)
    chosen, _ = BP.resolve("connect@aicodepro.com")
    assert chosen.directory == "Profile 54"


def test_a_shared_domain_is_ambiguous_and_asks(monkeypatch):
    """
    Two accounts on aicodepro.com. Picking one would send mail from the wrong
    address sooner or later.
    """
    monkeypatch.setattr(BP, "all_profiles", profiles)
    chosen, candidates = BP.resolve("aicodepro")
    assert chosen is None
    assert {c.email for c in candidates} == {
        "devendra@aicodepro.com", "connect@aicodepro.com"}


def test_no_hint_uses_the_profile_he_was_last_in(monkeypatch):
    monkeypatch.setattr(BP, "all_profiles", profiles)
    chosen, _ = BP.resolve("")
    assert chosen.email == "devendra@aicodepro.com"


def test_a_meaningless_hint_does_not_match_something_at_random(monkeypatch):
    monkeypatch.setattr(BP, "all_profiles", profiles)
    assert BP.resolve("xyzzy")[0] is None


def test_the_only_browser_file_it_opens_is_the_metadata_one():
    """
    The module's whole safety argument, checked against the code rather than
    the prose about the code. Chrome keeps credentials in "Login Data",
    "Cookies" and "Web Data"; this module must only ever read "Local State",
    which holds names and addresses.

    Scanning the source text was the first version of this test and it failed
    on its own docstring - which says "no password reading". Parse, don't grep.
    """
    import ast

    tree = ast.parse(open(BP.__file__, encoding="utf-8").read())
    literals = {node.value for node in ast.walk(tree)
                if isinstance(node, ast.Constant) and isinstance(node.value, str)}

    assert "Local State" in literals
    for credential_store in ("Login Data", "Cookies", "Web Data", "Local Storage"):
        assert credential_store not in literals, \
            f"the module now names {credential_store!r}"


def test_it_never_reaches_for_the_decryption_apis():
    """Reading Chrome's credentials needs DPAPI or the key from Local State."""
    import ast

    tree = ast.parse(open(BP.__file__, encoding="utf-8").read())
    names = {node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)}
    names |= {node.id for node in ast.walk(tree) if isinstance(node, ast.Name)}
    for forbidden in ("CryptUnprotectData", "os_crypt", "encrypted_key", "AESGCM"):
        assert forbidden not in names, f"the module now uses {forbidden}"
