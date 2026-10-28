"""Fleet Next monitor — read-only Legacy Fleet source adapter.

Legacy Fleet stays the control plane (Owner decision, #478). This package only
READS facts it is explicitly granted, keeps every identity field separate, and
reports missing evidence as UNKNOWN rather than success.

Nothing here is imported by Legacy, and nothing here writes anywhere.
"""
from .contract import (
    OBSERVATION_SCHEMA,
    SCHEMA,
    CallerGrant,
    Identity,
    Observation,
    SourceRefused,
)
from .adapter import (
    FixtureLegacyClient,
    LegacyReadOnlyClient,
    LegacyReadOnlySourceAdapter,
    RemoteRefused,
    RouteOffline,
    RunNotFound,
)

__all__ = [
    "SCHEMA",
    "OBSERVATION_SCHEMA",
    "CallerGrant",
    "Identity",
    "Observation",
    "SourceRefused",
    "LegacyReadOnlyClient",
    "LegacyReadOnlySourceAdapter",
    "FixtureLegacyClient",
    "RunNotFound",
    "RouteOffline",
    "RemoteRefused",
]
