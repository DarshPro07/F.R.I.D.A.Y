"""
Task complexity classification benchmark (PRD v3.1 FR-002).

Acceptance: >=95% routing agreement on a labelled set, and trivial tasks
never route to a coding executor. The labels below were written BEFORE the
classifier was run against them (PRD 7.3: criteria precede execution).
Every row is a realistic Friday request; the class is the one the PRD's
own use-case table (3.3) and risk model (4.11) imply.
"""
from __future__ import annotations

from friday import execution_economics as ee
from friday import task_class as TC

LABELLED: list[tuple[str, str]] = [
    # TRIVIAL - one deterministic device/media action (UC-01 first half)
    ("open spotify", TC.TRIVIAL),
    ("pause the music", TC.TRIVIAL),
    ("play the next song", TC.TRIVIAL),
    ("mute", TC.TRIVIAL),
    ("lock the screen", TC.TRIVIAL),
    ("open chrome", TC.TRIVIAL),
    ("what time is it", TC.TRIVIAL),
    ("set the volume to 40", TC.TRIVIAL),
    ("take a screenshot", TC.TRIVIAL),
    ("close notepad", TC.TRIVIAL),
    ("launch the calculator", TC.TRIVIAL),
    ("minimize this window", TC.TRIVIAL),
    # SIMPLE - one call or a direct answer
    ("how much free memory do i have", TC.SIMPLE),
    ("what is the capital of france", TC.SIMPLE),
    ("is my wifi connected", TC.SIMPLE),
    ("what do you remember about my backend", TC.SIMPLE),
    ("how many files are in the downloads folder", TC.SIMPLE),
    ("what is the battery level", TC.SIMPLE),
    ("tell me a fact about python", TC.SIMPLE),
    ("who are you", TC.SIMPLE),
    ("where is the file config.yaml", TC.SIMPLE),
    ("what did we decide about the database", TC.SIMPLE),
    # STANDARD - bounded multi-step in one domain (UC-05 light, UC-10)
    ("research the best static site generators and summarise the top three", TC.STANDARD),
    ("open youtube and play my saved playlist", TC.STANDARD),
    ("create a note called groceries, then open it", TC.STANDARD),
    ("read this pdf and extract the invoice totals", TC.STANDARD),
    ("compare the two pricing pages and tell me the differences", TC.STANDARD),
    ("write me a short email draft to the landlord about the leak", TC.STANDARD),
    ("look up the latest version of fastapi and its release date", TC.STANDARD),
    ("check whether this computer looks healthy and open paint", TC.STANDARD),
    ("summarise the meeting notes in this folder", TC.STANDARD),
    ("translate this paragraph to hindi", TC.STANDARD),
    ("update the product description draft in my store", TC.STANDARD),
    ("review the readme and suggest improvements", TC.STANDARD),
    ("rename the photos in this folder by date", TC.STANDARD),
    ("find me one current technology story", TC.STANDARD),
    # COMPLEX - cross-file implementation with verification (UC-02, UC-03)
    ("find why the app crashes after login, fix it and prove it works", TC.COMPLEX),
    ("refactor the payment module into smaller services with tests", TC.COMPLEX),
    ("implement the export feature end to end and add tests", TC.COMPLEX),
    ("migrate the database layer from sqlite to postgres", TC.COMPLEX),
    ("build a storefront draft for my print on demand idea", TC.COMPLEX),
    ("challenge my print-on-demand idea; if viable, build the launch plan", TC.COMPLEX),
    ("add a feature that lets users upload avatars, across the api and the ui", TC.COMPLEX),
    ("figure out why the tests are flaky and fix the root cause", TC.COMPLEX),
    ("build the integration between the crm and the billing pipeline", TC.COMPLEX),
    ("make a 45-second product reel from these assets with a storyboard and review", TC.COMPLEX),
    ("research this market and build the whole go-to-market plan", TC.COMPLEX),
    ("fix the bug in the scheduler and add a regression test", TC.COMPLEX),
    # LONG_RUNNING - recurring / open-ended (UC-07)
    ("every morning check whether our production health changed and alert me only if it matters", TC.LONG_RUNNING),
    ("keep monitoring the build and tell me when it goes green", TC.LONG_RUNNING),
    ("run the crawl overnight and summarise in the morning", TC.LONG_RUNNING),
    ("daily, collect the new reviews and file them", TC.LONG_RUNNING),
    ("watch for price drops on this listing", TC.LONG_RUNNING),
    ("schedule a weekly summary of my open issues", TC.LONG_RUNNING),
    # CRITICAL - destructive / financial / publishing / security (UC-04 save, UC-08, UC-12)
    ("delete all the old backups permanently", TC.CRITICAL),
    ("prepare the launch post and publish it after i approve", TC.CRITICAL),
    ("buy the domain and set up dns", TC.CRITICAL),
    ("send the email to the whole customer list", TC.CRITICAL),
    ("deploy the new build to production", TC.CRITICAL),
    ("rotate the api key for the payment provider", TC.CRITICAL),
    ("audit my staging domain for exposed services with nmap", TC.CRITICAL),
    ("change the firewall rules on the server", TC.CRITICAL),
    ("uninstall the old antivirus", TC.CRITICAL),
    ("transfer money to the supplier account", TC.CRITICAL),
]


def test_every_class_is_represented():
    seen = {label for _, label in LABELLED}
    assert seen == set(TC.TASK_CLASSES)


def test_routing_agreement_is_at_least_95_percent():
    results = [(text, expected, TC.classify(text).task_class) for text, expected in LABELLED]
    wrong = [(t, e, g) for t, e, g in results if e != g]
    agreement = 1 - len(wrong) / len(results)
    assert agreement >= 0.95, (
        f"{agreement:.1%} agreement; disagreements:\n  "
        + "\n  ".join(f"{t!r}: expected {e}, got {g}" for t, e, g in wrong))


def test_trivial_and_simple_never_reach_a_coding_executor():
    """FR-002 acceptance clause two, and PRD 1.5 'unnecessary specialist
    invocations < 10% of simple tasks' - here it is zero by construction."""
    executor_routes = {ee.HERMES_SINGLE, ee.HERMES_MULTI, ee.HERMES_DEEP}
    for text, expected in LABELLED:
        if expected in (TC.TRIVIAL, TC.SIMPLE):
            got = TC.classify(text)
            assert TC.route_for(got.task_class) not in executor_routes, (text, got)


def test_critical_wins_over_every_other_signal():
    c = TC.classify("every morning delete the old backups permanently")
    assert c.task_class == TC.CRITICAL
    assert TC.risk_tier_for(c.task_class) == "R3"
    c = TC.classify("open the admin password vault")
    assert c.task_class == TC.CRITICAL


def test_classification_is_explainable_and_serialisable():
    c = TC.classify("find why the app crashes after login, fix it and prove it works")
    d = c.to_dict()
    assert d["task_class"] == TC.COMPLEX
    assert d["route"] == ee.HERMES_SINGLE
    assert d["risk_tier"] == "R1"
    assert d["signals"] and d["reason"]


def test_empty_request_is_simple_not_an_error():
    assert TC.classify("").task_class == TC.SIMPLE
