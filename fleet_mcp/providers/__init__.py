"""Device providers — one adapter per way this runtime can reach a device."""
from __future__ import annotations

from ..config import FleetConfig, ProviderBinding
from ..errors import BAD_REQUEST, refuse
from .base import (
    ARCHIVED,
    MANAGED_IDLE,
    RESIDENT_HOST,
    RUNNING_AGENT,
    DeviceProvider,
    ProviderHealth,
    SessionSummary,
    classify,
    summary_from_dict,
)
from .cloudcli import CloudCliProvider
from .fleet_node import FleetNodeProvider

__all__ = [
    "ARCHIVED",
    "MANAGED_IDLE",
    "RESIDENT_HOST",
    "RUNNING_AGENT",
    "CloudCliProvider",
    "DeviceProvider",
    "FleetNodeProvider",
    "ProviderHealth",
    "SessionSummary",
    "classify",
    "build_providers",
    "summary_from_dict",
]


def build_provider(binding: ProviderBinding) -> DeviceProvider:
    if not binding.enabled:
        raise refuse(BAD_REQUEST, f"provider {binding.device_ref} is disabled")
    if binding.kind == "cloudcli-mcp-http":
        return CloudCliProvider(binding)
    if binding.kind == "fleet-node":
        return FleetNodeProvider(binding)
    # `config.PROVIDER_KINDS` already rejects unknown kinds at load; reaching
    # here means the two lists disagree, which is a defect in this module.
    raise refuse(BAD_REQUEST, f"no adapter for provider kind {binding.kind!r}")


def build_providers(config: FleetConfig) -> dict[str, DeviceProvider]:
    return {
        ref: build_provider(binding)
        for ref, binding in config.providers.items()
        if binding.enabled
    }
