"""Where this surface keeps its own files on a host — and where it must not.

Why this module exists rather than two hard-coded paths
-------------------------------------------------------
The obvious home for a local binding is `~/.serveros/`, next to `node.yaml` and
`targets.yaml`. It is the wrong home, and the repository already says so:

*   `fleet/node-root-contract.yaml` closes that root's name list to five files
    (`files:` and `declarations:`), and `runtime/paths.py` transcribes them into
    `HOME_DOT_SERVEROS_ALLOWED` — which `path.admit()` enforces, refusing a
    write into `~/.serveros` that names anything else. Its own stated remedy is
    "move it rather than widen this list".
*   The contract records why that rule exists: an operator was once printed a
    path the product then refused, and "the operator's next move is to write it
    anyway, and then the root holds a file the contract says cannot exist".

Shipping a default binding path that `admit()` refuses would be that failure
again, one level up. So this surface owns its own roots and writes into no
ServerOS root it does not own.

Two roots, because a binding is not state
-----------------------------------------
`.serveros/README.md` is explicit that the binding layer and the mutable runtime
state layer do not mix, and the reason is durability: a binding is meant to be
semi-static and re-derivable, and a file that accumulates runtime state stops
being either. They are therefore separate directories here as well.

    binding   which devices this runtime reaches, and how
    state     what it has done — the audit trail, the OAuth issuer's keys

Nothing in either root is a credential. Credentials are named by anchors that
point wherever the credential already lives, and are read at the point of use.

Platform defaults
-----------------
    Windows  binding  %LOCALAPPDATA%\\ServerOS\\bindings
             state    %LOCALAPPDATA%\\ServerOS\\state\\fleet-mcp
    POSIX    binding  $XDG_CONFIG_HOME/serveros-fleet-mcp   (~/.config/…)
             state    $XDG_STATE_HOME/serveros-fleet-mcp    (~/.local/state/…)

Local rather than roaming on Windows, deliberately: a route binding copied to
another machine is a claim about the wrong machine, which is the whole reason
`~/.serveros` is gitignored and never travels.

`FLEET_MCP_HOME` overrides both roots, for a test or a second deployment on one
host. It is a single directory holding `bindings/` and `state/`.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

BINDING_DIRNAME = "bindings"
STATE_DIRNAME = "state"

# The root this surface must never write into, and the reason it is refused
# rather than merely discouraged. Transcribed here as a name so that the check
# below is about a fact in the repository and not about a path that looked
# wrong to whoever wrote this file.
CLOSED_ROOT = "~/.serveros"
CLOSED_ROOT_ALLOWED = (
    "node.yaml",
    "targets.yaml",
    "controller.yaml",
    "business-calendar.yaml",
    "service-bindings.yaml",
)


def home_override() -> Path | None:
    value = os.environ.get("FLEET_MCP_HOME")
    return Path(os.path.expanduser(value)) if value else None


def binding_root() -> Path:
    override = home_override()
    if override is not None:
        return override / BINDING_DIRNAME
    if sys.platform == "win32":
        local = os.environ.get("LOCALAPPDATA")
        base = Path(local) if local else Path.home() / "AppData" / "Local"
        return base / "ServerOS" / BINDING_DIRNAME
    xdg = os.environ.get("XDG_CONFIG_HOME")
    base = Path(xdg) if xdg else Path.home() / ".config"
    return base / "serveros-fleet-mcp"


def state_root() -> Path:
    override = home_override()
    if override is not None:
        return override / STATE_DIRNAME
    if sys.platform == "win32":
        local = os.environ.get("LOCALAPPDATA")
        base = Path(local) if local else Path.home() / "AppData" / "Local"
        return base / "ServerOS" / "state" / "fleet-mcp"
    xdg = os.environ.get("XDG_STATE_HOME")
    base = Path(xdg) if xdg else Path.home() / ".local" / "state"
    return base / "serveros-fleet-mcp"


def binding_candidates(name: str) -> tuple[Path, ...]:
    """Where to look for a binding file, in order — which is one place.

    The current directory is deliberately not a candidate. A binding in a
    checkout is a binding that gets committed, and this repository's rule is
    that a binding never travels; auto-loading one from the working directory
    would make "which devices does this runtime reach" depend on where a
    process was started from. A file that has to be named with `--config` is a
    file somebody chose.
    """
    return (binding_root() / name,)


def refusal_if_closed_root(path: str | Path) -> str | None:
    """Return a refusal message if this path would land in `~/.serveros`.

    Called at load time, before the file is opened, so the failure is a
    statement about where the file is rather than a parse error inside a file
    that should not have been there.
    """
    candidate = Path(str(path)).expanduser()
    closed = Path(CLOSED_ROOT).expanduser()
    try:
        relative = candidate.resolve().relative_to(closed.resolve())
    except (ValueError, OSError):
        return None
    if relative.parts and relative.parts[0] in CLOSED_ROOT_ALLOWED:
        return None
    return (
        f"{candidate} is inside {CLOSED_ROOT}, which admits only "
        f"{list(CLOSED_ROOT_ALLOWED)} — see fleet/node-root-contract.yaml, whose "
        f"own remedy is `move it rather than widen this list`. Put this binding "
        f"in {binding_root()} instead, or name it explicitly with --config."
    )
