"""First-run operating contract (Phase 9; R14 / build-pack 10).

Nine adaptive questions, persisted as versioned policy. Rules:

- NOT a questionnaire prison: any question whose answer is already
  derivable from context is skipped, and everything can be changed
  later in plain conversation ("I trust you to publish Instagram
  without asking" -> versioned permission update via user_policy).
- Answers land in the SAME stores production reads: permission domains
  in UserPolicy, preferences in the contract file. No parallel truth.
- The contract survives restart; a deleted contract simply re-asks.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

from friday.user_policy import UserPolicy

#: The nine contract questions (build-pack 10). `field` is where the
#: answer lives; `domain` links to UserPolicy where applicable.
QUESTIONS = (
    {"id": "name", "field": "call_me",
     "ask": "What should I call you?"},
    {"id": "autonomy", "field": "autonomy",
     "ask": "How independently should I act for ordinary reversible "
            "work? (act / ask-first)"},
    {"id": "confirmation", "field": "confirmation_boundary",
     "ask": "For consequential changes, when do you want confirmation? "
            "(spend/deploy/delete are always confirmed)"},
    {"id": "cost", "field": "token_mode",
     "ask": "Economy / Balanced / Deep as default, or automatic?"},
    {"id": "quality", "field": "default_build_quality",
     "ask": "Unspecified software work: prototype or production?"},
    {"id": "computer", "field": "computer_authority", "domain":
     "workspace_access",
     "ask": "May I use your browser/computer for authorized ordinary "
            "tasks?"},
    {"id": "comms", "field": "auto_comms",
     "ask": "Which communication actions may I perform automatically? "
            "(customer email / social publishing / none)"},
    {"id": "spend", "field": "spend_envelopes",
     "ask": "Any pre-authorized spending envelopes? (platform + cap, "
            "or none)"},
    {"id": "learning", "field": "global_learning_consent",
     "ask": "May de-identified successful procedures improve shared "
            "skills? (yes / no)"},
)


class FirstRunContract:
    def __init__(self, home: str | Path | None = None,
                 policy: UserPolicy | None = None) -> None:
        if home is None:
            from friday.config import DATA_DIR
            home = Path(DATA_DIR)
        self.path = Path(home) / "operating_contract.json"
        self.policy = policy or UserPolicy()

    # -- state -------------------------------------------------------------

    def exists(self) -> bool:
        return self.path.exists()

    def read(self) -> dict:
        if not self.path.exists():
            return {}
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except ValueError:
            return {}

    # -- the wizard --------------------------------------------------------

    def pending_questions(self, known: dict | None = None) -> list[dict]:
        """Questions still worth asking: not already answered in the
        contract, not derivable from `known` context. This is the
        anti-questionnaire-prison rule in code."""
        answered = self.read()
        known = known or {}
        out = []
        for q in QUESTIONS:
            if q["field"] in answered or q["field"] in known:
                continue
            out.append({"id": q["id"], "ask": q["ask"]})
        return out

    def record(self, answers: dict) -> dict:
        """
        Persist contract answers. Permission-shaped answers also update
        the versioned UserPolicy so runtime checks and the contract can
        never disagree.
        """
        contract = self.read()
        contract.update({k: v for k, v in answers.items() if v is not None})
        contract["updated_at"] = time.time()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(contract, indent=1),
                             encoding="utf-8")

        applied = []
        if "computer_authority" in answers:
            allow = str(answers["computer_authority"]).lower() in (
                "yes", "true", "act", "allow", "auto")
            self.policy.grant("workspace_access",
                              "AUTO" if allow else "CONFIRM",
                              reason="first-run contract: computer "
                                     f"authority = {answers['computer_authority']}")
            applied.append("workspace_access")
        if "auto_comms" in answers:
            text = str(answers["auto_comms"]).lower()
            email = "email" in text or "all" in text
            social = "social" in text or "publish" in text or "all" in text
            self.policy.grant("customer_email",
                              "AUTO" if email else "CONFIRM",
                              reason="first-run contract: auto comms = "
                                     f"{answers['auto_comms']}")
            self.policy.grant("social_publish",
                              "AUTO" if social else "CONFIRM",
                              reason="first-run contract: auto comms = "
                                     f"{answers['auto_comms']}")
            applied.extend(["customer_email", "social_publish"])
        return {"status": "recorded", "applied_domains": applied,
                "contract_path": str(self.path)}

    # -- natural adaptation ------------------------------------------------

    def adapt(self, field: str, value, *, reason: str) -> dict:
        """Explicit later statements update the contract (versioned via
        file rewrite + policy events for permission fields)."""
        return self.record({field: value}) | {"reason": reason}
