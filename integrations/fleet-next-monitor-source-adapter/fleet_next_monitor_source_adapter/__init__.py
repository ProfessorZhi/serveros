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
from .compat import COMPAT_SCHEMA, CompatMonitorSource
# The one standard of evidence, published so the monitor can import it instead
# of re-deriving a second one (#508 P1, #515).
from .evidence import MONITOR_RUN_KEYS, native_evidence, normalise_run_fact

__all__ = [
    "SCHEMA",
    "OBSERVATION_SCHEMA",
    "COMPAT_SCHEMA",
    "MONITOR_RUN_KEYS",
    "CallerGrant",
    "Identity",
    "Observation",
    "SourceRefused",
    "LegacyReadOnlyClient",
    "LegacyReadOnlySourceAdapter",
    "CompatMonitorSource",
    "FixtureLegacyClient",
    "RunNotFound",
    "RouteOffline",
    "RemoteRefused",
    "native_evidence",
    "normalise_run_fact",
]
