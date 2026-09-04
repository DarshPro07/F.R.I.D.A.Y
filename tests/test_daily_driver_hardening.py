"""The two behavioural gaps between 'supervised' and 'daily driver':

  item 4 - a skill-shaped request (diagram, report, expert review) is surfaced
           to the fabric families instead of coming back empty and getting
           answered from the model's own head;
  item 6 - the worker rides out a longer link outage than the datacenter
           default, so a home-connection blip does not leave a dead assistant.

Both are unit-testable here; the remaining part of each (does the model *prefer*
the family? does the link actually recover live?) needs a live voice session.
"""

import agent_friday as A


# --- item 4: skill-shaped requests reach the fabric ------------------------

def test_skill_shaped_requests_surface_a_fabric_family():
    assert "presentation" in A._family_hints("make me a diagram of this system")
    assert "presentation" in A._family_hints("build a slide deck")
    assert "writing" in A._family_hints("write a report on our sales")
    assert "research" in A._family_hints("research fusion energy in depth")
    assert "roles" in A._family_hints("give me an expert review of this PR")
    assert "scraping" in A._family_hints("scrape that website for prices")


def test_plain_core_tool_requests_get_no_family_hint():
    # A core-tool request must not be pushed at the fabric - especially the
    # file phrasings that share a verb with the writing skill.
    assert A._family_hints("open a web page") == []
    assert A._family_hints("what is playing") == []
    assert A._family_hints("delete that file") == []
    assert A._family_hints("write a file to disk") == []
    assert A._family_hints("read that file") == []


# --- item 6: the reconnect ceiling is raised for a home connection ---------

def test_worker_rides_out_a_longer_outage_than_the_datacenter_default():
    # The framework default is 16; a worker that exits after a blip is a dead
    # assistant until someone restarts it by hand.
    assert A.WORKER_MAX_RETRY >= 32
    opts = A.worker_options()
    assert opts.max_retry == A.WORKER_MAX_RETRY


if __name__ == "__main__":  # ponytail: one runnable check without pytest
    test_skill_shaped_requests_surface_a_fabric_family()
    test_plain_core_tool_requests_get_no_family_hint()
    test_worker_rides_out_a_longer_outage_than_the_datacenter_default()
    print("daily-driver hardening: item 4 + item 6 checks pass")
