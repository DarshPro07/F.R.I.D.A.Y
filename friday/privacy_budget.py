"""
PRIV-001 (PRD v7 section 95): an objective's privacy budget.

A budget is what one objective may let leave the machine: the most sensitive
data class, the providers and the external services. It rides in the
objective's execution envelope (SEC-005), so a child can only narrow it, and
the model gateway intersects it with the global data-class policy
(`data_class.decision`) at the one seam a request leaves for a provider. It
only ever narrows: a SECRET refused off a local backend stays refused whatever
an objective declared.

`None` in a field is "no limit of its own" and an empty tuple is "none
permitted", the same way an envelope's `0` ceilings mean no ceiling of their
own. A local backend keeps the payload on this machine, so the data-class
ceiling does not apply to it; the provider list names providers and does.
"""
from __future__ import annotations

from dataclasses import dataclass

from friday import data_class, privacy
from friday.contracts import ContractError


def _names(raw, what: str) -> tuple[str, ...] | None:
    if raw is None:
        return None
    if isinstance(raw, str) or not isinstance(raw, (list, tuple)):
        raise ContractError(f"a privacy budget's {what} is a list of names")
    if not all(isinstance(name, str) and name.strip() for name in raw):
        raise ContractError(f"a privacy budget's {what} holds only names")
    return tuple(name.strip().lower() for name in raw)


@dataclass(frozen=True)
class PrivacyBudget:
    """What one objective may let leave the machine (PRIV-001)."""

    #: The most sensitive `data_class` a payload may be and still go to a
    #: non-local provider; "" is no ceiling of its own.
    max_data_class: str = ""
    #: Providers the objective may use; None is any the router would pick.
    providers: tuple[str, ...] | None = None
    #: External services the objective may reach; None is any its capabilities reach.
    external_services: tuple[str, ...] | None = None

    def __post_init__(self) -> None:
        if self.max_data_class and self.max_data_class not in data_class.CLASSES:
            raise ContractError(
                f"{self.max_data_class!r} is not a data class "
                f"({', '.join(data_class.CLASSES)})")
        object.__setattr__(self, "providers", _names(self.providers, "providers"))
        object.__setattr__(self, "external_services",
                           _names(self.external_services, "external services"))

    def refusal(self, content_class: str, provider: str) -> str:
        """Empty when a payload of CONTENT_CLASS may go to PROVIDER, otherwise why not."""
        name = (provider or "").strip().lower()
        if self.providers is not None and name not in self.providers:
            return (f"{provider or 'this provider'} is outside this objective's privacy "
                    f"budget (providers: {', '.join(self.providers) or 'none'})")
        local = name in privacy.LOCAL_BACKENDS
        if (not local and self.max_data_class
                and data_class.CLASSES.index(content_class)
                > data_class.CLASSES.index(self.max_data_class)):
            return (f"{content_class} content is above this objective's privacy budget "
                    f"({self.max_data_class}) and cannot be sent to {provider or 'a cloud provider'}")
        return ""

    def permits_service(self, service: str) -> bool:
        """Whether the budget lets the objective reach the external SERVICE."""
        return (self.external_services is None
                or (service or "").strip().lower() in self.external_services)

    def wider_than(self, parent: "PrivacyBudget | None") -> list[str]:
        """What this budget allows that PARENT (None: unbudgeted) does not."""
        if parent is None:
            return []
        problems = []
        if parent.max_data_class and (
                not self.max_data_class
                or data_class.CLASSES.index(self.max_data_class)
                > data_class.CLASSES.index(parent.max_data_class)):
            problems.append("privacy budget data class")
        for what, mine, theirs in (("providers", self.providers, parent.providers),
                                   ("external services", self.external_services,
                                    parent.external_services)):
            if theirs is not None and (mine is None or not set(mine) <= set(theirs)):
                problems.append(f"privacy budget {what}")
        return problems

    def to_dict(self) -> dict:
        return {"max_data_class": self.max_data_class,
                "providers": None if self.providers is None else list(self.providers),
                "external_services": (None if self.external_services is None
                                      else list(self.external_services))}

    @classmethod
    def from_dict(cls, raw) -> "PrivacyBudget":
        """The stored form, read strictly: anything but a mapping of known keys is no budget."""
        if not isinstance(raw, dict):
            raise ContractError("a stored privacy budget is a mapping")
        unknown = sorted(set(raw) - {"max_data_class", "providers", "external_services"})
        if unknown:
            raise ContractError(f"a privacy budget has no field {unknown}")
        ceiling = raw.get("max_data_class", "")
        if not isinstance(ceiling, str):
            raise ContractError("a privacy budget's data class is a name")
        return cls(max_data_class=ceiling, providers=raw.get("providers"),
                   external_services=raw.get("external_services"))
