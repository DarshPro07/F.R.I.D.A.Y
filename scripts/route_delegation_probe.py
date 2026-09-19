"""Probe the delegation router as the live agent sees it (HERMES_DIR / HERMES_PYTHON from .env)."""
import os, pathlib, sys
root = pathlib.Path(__file__).resolve().parent.parent
for line in (root / ".env").read_text(encoding="utf-8").splitlines():
    if line.startswith(("HERMES_DIR=", "HERMES_PYTHON=")):
        k, v = line.split("=", 1)
        os.environ.setdefault(k, v.strip())
sys.path.insert(0, str(root))
from friday.hermes_bridge import _profile_model_default, profile_home, locate
print("locate:", bool(locate()), "profile_home:", profile_home("friday"))
print("profile default:", _profile_model_default())
import friday.execution_economics as ee
ee._tier_table.cache_clear(); ee.known_models.cache_clear()
print("tier table:", ee._tier_table())
for tier in ("economy", "standard", "deep"):
    print(" ", tier, ee.candidates(tier))
for t in ("add a one-line docstring to _busy in friday/desk.py, cheapest model",
          "refactor the auth middleware and rotate the session secret",
          "research the best approach to vector search for our memory tier and write a design note",
          "write a two-line python script that prints today's date to jarvis-hello.py on the desktop",
          "use gemini for this: summarise the README",
          "use gpt-5.6-terra for this one: write the tests",
          "send this to opus: design the plugin lifecycle"):
    p = ee.plan_delegation(t)
    print(f"  {t[:58]:58s} -> {p['level']}/{p['tier']} model={p['model'] or '(default)'} provider={p['provider'] or '(default)'} effort={p['effort']}")
