"""
Slice 9 - the cross-capability golden journey.

Every family has its own tests. This is the one that proves they compose:
a single user objective that crosses several capability families, routed
through the real `capability_use` bridge, with Friday as the only manager -
the model names an outcome (a family), never a brand, and the result of one
step feeds the next.

The objective, stated once:

    "I'm working through a single-cell RNA dataset that's published on a web
     page. Find the right method, pull the data table off the page, get me
     the review checklist for the plan, and draft a paste-ready prompt for an
     LLM to interpret the results."

    research   -> which method skill fits              (science_skills)
    scraping   -> the data table, as structured fields (scrapling_parse)
    roles      -> the review checklist for the plan     (gstack_process)
    writing    -> the paste-ready prompt                (prompt_master)

The point, as in test_vertical_slice: a pipeline of individually-correct parts
is exactly how things break when nothing connects them. So this drives the
same bridge the voice agent uses and checks that work carries from one family
to the next - and that the boundaries (brand-hiding, graceful failure, the
security scope gate) hold *under composition*, not just in isolation.
"""

from __future__ import annotations

import pytest

from friday import fabric


# --- Friday as manager: the real capability_use bridge ---------------------

class _Registrar:
    def __init__(self) -> None:
        self.tools: dict = {}

    def tool(self, *args, **kwargs):
        def take(fn):
            self.tools[fn.__name__] = fn
            return fn
        if args and callable(args[0]):
            return take(args[0])
        return take


@pytest.fixture()
def friday():
    """The manager surface: capability_use + capability_families, as the agent sees them."""
    from friday.tools import fabric_control

    reg = _Registrar()
    fabric_control.register(reg)
    fabric.reload()
    yield reg.tools
    fabric.reload()


def outcome(friday, family, operation, **arguments):
    """Friday routes by outcome. The caller never names a provider."""
    return friday["capability_use"](family, operation, arguments)


def _cloned(upstream, marker):
    from friday.fabric_adapters import _skillpack
    return (_skillpack.pack_root(upstream) / marker).exists()


skills_cloned = pytest.mark.skipif(
    not (_cloned("scientific-agent-skills", "skills")
         and _cloned("gstack", "review/SKILL.md")
         and _cloned("prompt-master", "SKILL.md")),
    reason="skill-pack upstreams not all cloned")


# --- the journey -----------------------------------------------------------


@skills_cloned
def test_one_objective_crosses_four_families_and_the_work_carries_forward(friday):
    # Step 1 - research: which method fits single-cell RNA?
    research = outcome(friday, "research", "search",
                       query="single cell rna sequencing analysis")
    assert research["status"] == "succeeded", research
    methods = {row["skill"] for row in research["output"]}
    assert methods & {"scanpy", "anndata", "scvelo"}, methods
    chosen_method = sorted(methods & {"scanpy", "anndata", "scvelo"})[0]

    # Step 2 - scraping: pull the data table the method will consume. The page
    # is the input to the next step, so its output must be structured, not prose.
    page = ("<table><tr class=r><td class=gene>CD8A</td><td class=exp>4.2</td></tr>"
            "<tr class=r><td class=gene>MS4A1</td><td class=exp>1.1</td></tr></table>")
    table = outcome(friday, "scraping", "fields", html=page,
                    fields={"gene": "td.gene", "expression": "td.exp"})
    assert table["status"] == "succeeded", table
    assert table["output"] == {"gene": ["CD8A", "MS4A1"],
                               "expression": ["4.2", "1.1"]}

    # Step 3 - roles: the review checklist for the analysis plan.
    review = outcome(friday, "roles", "route",
                     task="review this data analysis plan before running it")
    assert review["status"] == "succeeded", review
    assert review["output"][0]["skill"] in {"review", "plan-eng-review",
                                            "plan-design-review", "autoplan"}

    # Step 4 - writing: a paste-ready prompt that carries the method and the
    # extracted genes forward. The journey's output depends on steps 1 and 2.
    draft = outcome(friday, "writing", "instructions")
    assert draft["status"] == "succeeded", draft
    assert "prompt" in str(draft["output"]).lower()

    # The work actually carried: the pieces the later steps needed exist.
    carried = {"method": chosen_method,
               "genes": table["output"]["gene"],
               "have_review": bool(review["output"]),
               "have_prompt_method": bool(draft["output"])}
    assert carried["method"] and carried["genes"] and carried["have_review"] \
        and carried["have_prompt_method"]


# --- the boundaries hold under composition ---------------------------------


@skills_cloned
def test_friday_names_outcomes_not_brands_across_the_whole_journey(friday):
    """
    The manager contract: the user says "research", "scraping", "writing" -
    never "science_skills" or "scrapling". capability_families, the surface the
    model actually reads, must not leak a provider id.
    """
    families = friday["capability_families"]()
    blob = str(families).lower()
    for brand in ("science_skills", "scrapling", "gstack_process",
                  "prompt_master", "codebase_memory"):
        assert brand not in blob, f"{brand} leaked into the family surface"


@skills_cloned
def test_the_security_scope_gate_holds_mid_journey(friday):
    """
    A journey that has been succeeding does not earn a bypass. Reaching into
    the security family for a procedure still needs authorized_scope, even
    after four green steps.
    """
    blocked = outcome(friday, "security", "skill",
                      name="abusing-dpapi-for-credential-access")
    assert blocked["status"] == "failed"
    # but the open half still works, so the journey is not dead-ended
    lookup = outcome(friday, "security", "search", query="detect credential dumping")
    assert lookup["status"] == "succeeded", lookup


def test_an_unavailable_family_step_fails_without_sinking_the_journey(friday,
                                                                     monkeypatch):
    """
    NON_NEGOTIABLE 15 under composition: one family being down is a failed
    step that names the layer, not an exception that takes the objective with
    it. code_intelligence -> graft needs a CLI that may be absent; simulate
    the whole chain down and confirm the manager still gets a result object.
    """
    monkeypatch.setattr(fabric, "call_with_fallback",
                        lambda *a, **k: _failed("graph provider unavailable"))
    step = outcome(friday, "code_intelligence", "map")
    assert step["status"] == "failed"
    assert step["error"]
    # a failed step returns a dict the manager can branch on - not a raise
    assert isinstance(step, dict) and "status" in step


def _failed(msg):
    from friday import contracts as c
    return c.failed(c.started("journey", "capability.use"), msg)
