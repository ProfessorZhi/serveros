"""ServerOS v3 federated control-plane public API."""

from .activity import ActivityGraph, GraphError
from .artifacts import ArtifactBoundaryError, artifact_dir
from .engine import ServerOS, WorkEngine, default_state_dir
from .migration import MigrationError, MigrationManager
from .model import (
    Activity,
    ActivityState,
    ContextMode,
    CandidateState,
    ExecutionContext,
    ItemState,
    Outcome,
    Run,
    RunState,
    State,
    WorkItem,
    now_iso,
)
from .resolver import AbilityResolver, CapabilityBinding
from .request import WorkRequest
from .state_machine import IllegalTransition, allowed_transitions, can_transition, reopen, transition
from .store import ServerOSStore, WorkItemStore

__version__ = "0.3.0"

__all__ = [
    "Activity",
    "ActivityGraph",
    "ActivityState",
    "ArtifactBoundaryError",
    "ContextMode",
    "ExecutionContext",
    "GraphError",
    "IllegalTransition",
    "ItemState",
    "MigrationError",
    "MigrationManager",
    "Outcome",
    "Run",
    "RunState",
    "ServerOS",
    "ServerOSStore",
    "State",
    "WorkEngine",
    "WorkItem",
    "WorkItemStore",
    "WorkRequest",
    "allowed_transitions",
    "artifact_dir",
    "can_transition",
    "default_state_dir",
    "now_iso",
    "reopen",
    "transition",
    "AbilityResolver",
    "CapabilityBinding",
]
