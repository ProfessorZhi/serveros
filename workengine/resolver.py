"""Ability Resolver stable interface.

This module intentionally knows **nothing** about concrete skills. The stable
interface is ``AbilityResolver.resolve_capability(capability)``. Implementations
of capabilities are registered from the outside (by a server façade or an
ability registry); no specific skill names are hardcoded here.

The two-layer separation (Ability = implementation, Capability = closed
vocabulary atom) is kept at arm's length: this exposes only the resolver seam.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class CapabilityBinding:
    """A resolved capability: an abstract capability name tied to an implementor.

    ``capability``  canonical closed-vocabulary capability name.
    ``resolver``    opaque reference to the implementing provider (NOT a hardcoded
                    skill identifier). The concrete payload is outside this scope.
    ``version``     optional binding version.
    """

    capability: str
    resolver: str
    version: str = ""


class AbilityResolver:
    """Stable seam for mapping a capability name to an implementor.

    The registry starts empty. A server façade / ability registry populates it
    via :meth:`register`. ``resolve_capability`` returns ``None`` for unknown
    capabilities so callers can fall back to a hard-decision path.
    """

    def __init__(self) -> None:
        self._bindings: dict[str, CapabilityBinding] = {}

    def register(self, capability: str, resolver: str, version: str = "") -> CapabilityBinding:
        """Register (or replace) the implementor for ``capability``."""
        binding = CapabilityBinding(capability=capability, resolver=resolver, version=version)
        self._bindings[capability] = binding
        return binding

    def resolve_capability(self, capability: str) -> CapabilityBinding | None:
        """Stable lookup interface for mapping a capability to its implementor."""
        return self._bindings.get(capability)

    def capabilities(self) -> list[str]:
        """Return the registered capability names (closed-vocabulary view)."""
        return sorted(self._bindings.keys())
