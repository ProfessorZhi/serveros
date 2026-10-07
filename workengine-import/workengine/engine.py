"""ServerOS v2 control-plane service."""

from __future__ import annotations

import importlib.util
import os
from pathlib import Path

from .model import Activity, ContextMode, ExecutionContext, RunState, WorkItem
from .store import ServerOSStore

class RootUnbound(RuntimeError):
    """A root with no authored answer on this device.

    Raised rather than defaulted. A default root is not "the command fails" --
    it is the command succeeding somewhere nobody bound, and the two look
    nothing alike at the call site. On 2026-09-18 this store took a Run row and
    *then* failed: `begin_run` committed the INSERT and the artifacts mkdir came
    after, so the row survived pointing at `/opt/applications/data/home-hwz/...`
    -- the *Beijing* `linux-server` profile's binding, absent on this host.
    """


# This module used to hold one machine's answer as a literal
# (`/opt/applications/data/home-hwz/...`). A path written into the binary is one
# device's binding presented as every device's, and it fails in the hardest way
# to notice: the wrong root is often still *writable*, so the work lands
# somewhere plausible and nothing raises.
#
# Roots are now asked for, in this order:
#   1. an explicit argument from the caller (the CLI's --state-dir / --artifacts-root)
#   2. the SERVEROS_* environment variable for that root
#   3. the device's own host profile -- `node.roots.<role>`
#   4. UNBOUND, which raises
#
# There is no fifth source, and specifically no fallback to the other machine
# and no `$HOME` guess. A guessed root *resolves*, which is exactly what makes it
# worse than an error.
#
# Roles are asked for by NAME -- the names `node.roots` itself uses. Nothing
# here says which device it is, so this file has no branch to get wrong when the
# fleet changes.

HOST_PROFILE_ENV = "SERVEROS_HOST_PROFILE"

# Where the device's own host profile lives. This is the one address in the
# chain that cannot be derived from a root -- everything else is reached by
# asking it -- so it is a convention (the XDG config directory), not a binding.
# SERVEROS_HOST_PROFILE overrides it, which is what the tests use.
HOST_PROFILE_DEFAULT = "~/.config/serveros/host-profile.yaml"

# Role names, as the device's host profile writes them (`node.roots.<name>`).
# Names, not paths.
#
# Note these are the host profile's `*_root` spellings. The fleet profile's
# `paths:` keys are a *different* vocabulary -- `state`, `artifacts`,
# `data_root` -- describing a different set of roles, and the two are not
# interchangeable: this host's profile binds `state_root` but has no
# `data_root` at all (its equivalent is `persistent_root`). Asking the wrong
# one is not a lookup failure, it is a lookup that succeeds on another machine.
# So this module asks the host profile, by these names, and only these.
ROLE_STATE = "state_root"
ROLE_ARTIFACTS = "artifacts_root"
ROLE_WORKSPACE = "workspace_root"
ROLE_RUNTIME_ASSETS = "runtime_assets_root"

# Environment variables. Each takes precedence over the device binding.
STATE_DIR_ENV = "SERVEROS_STATE_DIR"
ARTIFACTS_ROOT_ENV = "SERVEROS_ARTIFACTS_ROOT"
DATA_ROOT_ENV = "DATA_ROOT"


def _absolute_override(name: str, default: Path | None = None) -> Path | None:
    """The absolute path in `name`, or `default` when it is unset."""
    raw = os.environ.get(name)
    if not raw:
        return default
    path = Path(raw)
    if not path.is_absolute():
        raise ValueError(f"{name} must be an absolute path")
    return path.resolve()


def _host_profile_path() -> Path:
    raw = os.environ.get(HOST_PROFILE_ENV)
    if raw:
        candidate = Path(raw)
        if not candidate.is_absolute():
            raise ValueError(f"{HOST_PROFILE_ENV} must be an absolute path")
        return candidate
    return Path(HOST_PROFILE_DEFAULT).expanduser()


def _read_host_profile() -> dict:
    """The device host profile, or a RootUnbound saying why it could not be read."""
    path = _host_profile_path()
    if importlib.util.find_spec("yaml") is None:
        raise RootUnbound(
            f"cannot ask the device for its roots: {path} is YAML and this "
            f"interpreter has no PyYAML. Set {STATE_DIR_ENV} and "
            f"{ARTIFACTS_ROOT_ENV} to absolute paths, or run under an "
            f"interpreter that has PyYAML."
        )
    import yaml

    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise RootUnbound(f"the device host profile {path} is unreadable: {exc}") from exc
    try:
        document = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise RootUnbound(f"the device host profile {path} is not valid YAML: {exc}") from exc
    if not isinstance(document, dict):
        raise RootUnbound(f"the device host profile {path} does not hold a mapping")
    return document


def _device_roots() -> dict:
    """`node.roots` exactly as the device authored it."""
    document = _read_host_profile()
    node = document.get("node")
    roots = node.get("roots") if isinstance(node, dict) else None
    if not isinstance(roots, dict):
        raise RootUnbound(
            f"the device host profile {_host_profile_path()} declares no "
            f"`node.roots`, so this device has no roots to bind"
        )
    return {name: value for name, value in roots.items() if isinstance(value, str) and value}


def _bound_root(role: str, env_name: str | None, explicit: str | Path | None = None) -> Path:
    """Resolve one role through the authored chain, or raise RootUnbound.

    `env_name` may be None for a role with no environment variable of its own.
    """
    if explicit is not None:
        path = Path(explicit)
        if not path.is_absolute():
            raise ValueError(f"the {role} passed in must be an absolute path, got {explicit!r}")
        return path.resolve()

    if env_name is not None:
        from_env = _absolute_override(env_name)
        if from_env is not None:
            return from_env

    value = _device_roots().get(role)
    if not value:
        unset = f" and {env_name} is unset" if env_name else ""
        raise RootUnbound(
            f"the device host profile {_host_profile_path()} binds no {role!r}{unset}. "
            f"No default is used: a root nobody on this device authored is a path "
            f"that resolves, and that is what makes it worse than an error."
        )
    path = Path(value)
    if not path.is_absolute():
        raise RootUnbound(f"the device binds {role!r} to a relative path: {value!r}")
    return path.resolve()


def default_state_dir() -> Path:
    return _bound_root(ROLE_STATE, STATE_DIR_ENV)


def default_artifacts_dir() -> Path:
    return _bound_root(ROLE_ARTIFACTS, ARTIFACTS_ROOT_ENV)


def default_workspace_root() -> Path:
    """The workspace root.

    WORKSPACE_ROOT wins. DATA_ROOT is the older spelling and is still honoured
    as `<DATA_ROOT>/workspace`, which is the derivation this used to make. With
    neither set the device's host profile is asked for `workspace_root` -- a
    role it binds directly, so nothing has to be derived from a root the
    profile may not name at all.
    """
    explicit = _absolute_override("WORKSPACE_ROOT")
    if explicit is not None:
        return explicit
    data_root = _absolute_override(DATA_ROOT_ENV)
    if data_root is not None:
        return data_root / "workspace"
    return _bound_root(ROLE_WORKSPACE, None)


def default_runtime_assets_root() -> Path:
    """Where the ServerOS runtime artifacts are installed on this device."""
    return _bound_root(ROLE_RUNTIME_ASSETS, "SERVEROS_RUNTIME_ASSETS")


class ServerOS:
    def __init__(
        self,
        store: ServerOSStore | None = None,
        *,
        state_dir: str | Path | None = None,
        artifacts_root: str | Path | None = None,
    ) -> None:
        if store is None:
            store = ServerOSStore(state_dir if state_dir is not None else default_state_dir())
        self.store = store
        self.artifacts_root = (
            default_artifacts_dir() if artifacts_root is None else Path(artifacts_root)
        )
        if not self.artifacts_root.is_absolute():
            raise ValueError("artifacts root must be absolute")

    def register_project(self, project: str, prefix: str) -> None:
        self.store.register_project(project, prefix)

    def create_item(self, project: str, title: str, kind: str, card_locator: str | None = None) -> WorkItem:
        item_id = self.store.allocate_item_id(project)
        locator = card_locator or f"records/projects/{project}/items/{item_id}/card.yaml"
        return self.store.create_item(WorkItem(item_id, project, title, kind, locator))

    def add_activity(self, item_id: str, activity_id: str, kind: str, needs: list[str] | None = None) -> Activity:
        return self.store.add_activity(Activity(item_id, activity_id, kind), needs)

    def attach_context(
        self,
        item_id: str,
        activity_id: str,
        repo: str,
        mode: ContextMode | str,
        *,
        revision: str,
        worktree: str = "",
        dirty: bool = False,
    ) -> ExecutionContext:
        context = ExecutionContext(item_id, activity_id, repo, ContextMode(mode), revision, worktree, dirty)
        return self.store.attach_context(context)

    def begin_run(self, item_id: str, activity_id: str, worker: str, idempotency_key: str):
        return self.store.begin_run(item_id, activity_id, worker, idempotency_key, self.artifacts_root)

    def advance_run(self, item_id: str, run_id: str, state: RunState | str):
        return self.store.advance_run(item_id, run_id, state)

    def cancel_run(self, item_id: str, run_id: str, *, reason: str, actor: str, **options):
        return self.store.cancel_run(item_id, run_id, reason=reason, actor=actor, **options)

    def resume_run(self, item_id: str, run_id: str):
        # `run show` is this method, so its refusal is a CLI answer, and the
        # sentence that used to be written here — `unknown Run: {item}/{run}` —
        # was measured wrong twice: a Run of another Item is not unknown, and an
        # Item that does not exist is not the Run's fault. `store.require_run`
        # is the boundary that tells those three apart.
        return self.store.require_run(item_id, run_id)


WorkEngine = ServerOS
