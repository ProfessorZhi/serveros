"""Autonomous Delivery Workflow v0 - C1 Work Engine.

Pure-Python, cross-platform, no third-party dependencies.

Public surface (stable interface names):
  - ``WorkItem``        Work Item data model (persisted state).
  - ``State``           Work Item state machine enum.
  - ``WorkItemStore``   Local JSON persistence (absolute-path located).
  - ``WorkEngine``      Orchestration: create / advance / resume / list.
  - ``WorkRequest``     Server-faceda facade: requirement -> Work Item.
  - ``AbilityResolver`` Stable resolver interface (``resolve_capability``).
"""

from .model import State, WorkItem, now_iso  # noqa: F401
from .state_machine import (  # noqa: F401
    IllegalTransition,
    allowed_transitions,
    can_transition,
    transition,
)
from .store import WorkItemStore  # noqa: F401
from .engine import WorkEngine, default_state_dir  # noqa: F401
from .request import WorkRequest  # noqa: F401
from .resolver import AbilityResolver, CapabilityBinding  # noqa: F401

__version__ = "0.1.0"
__all__ = [
    "State",
    "WorkItem",
    "now_iso",
    "IllegalTransition",
    "allowed_transitions",
    "can_transition",
    "transition",
    "WorkItemStore",
    "WorkEngine",
    "default_state_dir",
    "WorkRequest",
    "AbilityResolver",
    "CapabilityBinding",
    "__version__",
]
