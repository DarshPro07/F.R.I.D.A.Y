"""FR-PRV-001: classification vocabulary, the deterministic classifier, and
the routing rule - proven independently of any prompt or gateway call."""
from __future__ import annotations

from friday import data_class as DC
from friday import privacy

# Assembled at run time: the source carries no provider-shaped key for a
# push-protection scanner, while the code under test still sees the whole shape.
FAKE_KEY = "sk-" + "abcdefghijklmnopqrstuvwx"
FAKE_PRIVATE_KEY_HEADER = "-----BEGIN RSA " + "PRIVATE KEY-----"
FAKE_AWS_KEY_ID = "AKIA" + "ABCDEFGHIJKLMNOP"


def test_classes_are_ordered_least_to_most_sensitive():
    assert DC.CLASSES == (DC.PUBLIC, DC.INTERNAL, DC.PERSONAL, DC.CUSTOMER_PII,
                          DC.CONFIDENTIAL, DC.SECRET, DC.LOCAL_ONLY)


def test_classify_public():
    assert DC.classify("this is a PUBLIC announcement") == DC.PUBLIC
    assert DC.classify("nothing special here at all") == DC.PUBLIC


def test_classify_internal():
    assert DC.classify("INTERNAL roadmap notes for the team") == DC.INTERNAL


def test_classify_personal():
    assert DC.classify("call me at 555-234-6789") == DC.PERSONAL
    assert DC.classify("DOB: 1990-01-01") == DC.PERSONAL


def test_classify_customer_pii():
    assert DC.classify("card 4111 1111 1111 1111") == DC.CUSTOMER_PII
    assert DC.classify("ssn 219-09-9999") == DC.CUSTOMER_PII
    assert DC.classify("aadhaar 1234 5678 9012") == DC.CUSTOMER_PII
    assert DC.classify("contact Jane Doe at jane.doe@example.com") == DC.CUSTOMER_PII


def test_classify_confidential():
    assert DC.classify("CONFIDENTIAL: Q3 merger terms") == DC.CONFIDENTIAL


def test_classify_secret():
    assert DC.classify("api_key: " + FAKE_KEY) == DC.SECRET
    assert DC.classify(FAKE_PRIVATE_KEY_HEADER) == DC.SECRET
    assert DC.classify("aws " + FAKE_AWS_KEY_ID) == DC.SECRET


def test_classify_local_only():
    assert DC.classify("LOCAL_ONLY: keep this on the machine") == DC.LOCAL_ONLY


def test_hint_can_only_raise_never_lower():
    # The text alone is SECRET; a "public" hint must not launder it.
    text = "api_key: " + FAKE_KEY
    assert DC.classify(text, hint=DC.PUBLIC) == DC.SECRET
    # A plain text with a CUSTOMER_PII hint is raised, not ignored.
    assert DC.classify("nothing structural here", hint=DC.CUSTOMER_PII) == DC.CUSTOMER_PII


# -- routing rule table -----------------------------------------------------


def test_secret_and_local_only_refuse_off_a_local_backend():
    for cls in (DC.SECRET, DC.LOCAL_ONLY):
        d = DC.decision(cls, backend="anthropic", mode=privacy.OPEN)
        assert d.verdict == "REFUSE"


def test_secret_and_local_only_allow_on_a_local_backend():
    for cls in (DC.SECRET, DC.LOCAL_ONLY):
        d = DC.decision(cls, backend="ollama", mode=privacy.OPEN)
        assert d.verdict == "ALLOW"


def test_customer_pii_and_confidential_redact_on_cloud():
    for cls in (DC.CUSTOMER_PII, DC.CONFIDENTIAL, DC.PERSONAL):
        d = DC.decision(cls, backend="anthropic", mode=privacy.OPEN)
        assert d.verdict == "REDACT"


def test_public_and_internal_allow_anywhere():
    for cls in (DC.PUBLIC, DC.INTERNAL):
        assert DC.decision(cls, backend="anthropic", mode=privacy.OPEN).verdict == "ALLOW"
        assert DC.decision(cls, backend="ollama", mode=privacy.OPEN).verdict == "ALLOW"


def test_private_only_mode_refuses_rather_than_redacts():
    """PRIVATE_ONLY's promise is nothing leaves the machine - a redact-class
    is refused, not minimised, when the mode is on."""
    d = DC.decision(DC.CUSTOMER_PII, backend="anthropic", mode=privacy.PRIVATE_ONLY)
    assert d.verdict == "REFUSE"


# -- redact -------------------------------------------------------------


def test_redact_removes_and_counts():
    text = "card 4111 1111 1111 1111 belongs to a customer"
    cleaned, n = DC.redact(text, DC.CUSTOMER_PII)
    assert n >= 1
    assert "4111" not in cleaned
    assert "[REDACTED-CARD]" in cleaned


def test_redact_removes_email_and_name_pair():
    text = "contact Jane Doe at jane.doe@example.com please"
    cleaned, n = DC.redact(text, DC.CUSTOMER_PII)
    assert n >= 2
    assert "jane.doe@example.com" not in cleaned
    assert "Jane Doe" not in cleaned
