#!/usr/bin/env python3
"""Where the Fleet controller is, and the boundary ServerOS keeps to it.

Phase 1 of this round answered a question that had been assumed rather than
asked. The npm package `@wonderwhy-er/desktop-commander` ships one remote
role — the endpoint — and the controller is a **remote MCP service on another
plane**, speaking published MCP and OAuth 2.0 rather than anything private.

Two consequences follow, and this module implements both:

  1. The controller cannot be conjured locally. It can only be **reached**
     through a standard MCP client that a human authorized. Until that
     happens, the correct answer is `ControllerNotExposed`, and a transport
     that invents an answer is worse than one that raises.

  2. Because the surface is public and standards-based, integrating with it is
     legitimate. Because the *relay* is not, integrating with that is not. The
     difference is what `fleet/controller-contract.yaml` records, and what the
     boundary tests read.

Nothing in this module reads, copies or persists a credential. A declaration
says a controller was authorized; the client that holds the authorization
holds the token, and there is no field here for it to arrive in.
"""
from __future__ import annotations

import importlib.util
import os
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent


def _load(name: str, filename: str):
    """Load a sibling module by path, reusing an existing load if there is one.

    This repo's tests load modules by path rather than by package import. The
    `sys.modules` check is not an optimisation: loading the same file twice
    under two names yields two distinct classes, and an `except` clause
    written against one of them silently misses the other.
    """
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, _HERE / filename)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


transport = _load("serveros_fleet_transport", "transport.py")

CONTROLLER_CONTRACT = _HERE / "controller-contract.yaml"

# Where a local controller surface is declared. Absent means absent: the
# contract does not let anything infer a controller from an installed binary,
# a running process, or a reachable URL.
CONTROLLER_DECLARATION = "~/.serveros/controller.yaml"


def controller_contract() -> dict:
    return transport.load_yaml(CONTROLLER_CONTRACT)


# ---------------------------------------------------------------------------
# Declarations, validated
# ---------------------------------------------------------------------------


def forbidden_controller_keys() -> tuple[str, ...]:
    """Credential-bearing keys a controller declaration may never carry.

    Reuses the transport contract's list rather than keeping a second one: two
    lists of forbidden secret names drift, and the one that drifts is the one
    nobody re-read.
    """
    extra = ("client_secret", "code_verifier", "device_code", "session")
    return tuple(transport.FORBIDDEN_BINDING_KEYS) + extra


def validate_controller_declaration(data: dict) -> dict:
    """Reject a declaration that carries credentials, or that claims too much.

    The second half matters as much as the first. A declaration asserts that a
    callable surface exists for THIS runtime; a file that also names the
    remote resource is fine, but a file that carries a bearer token has turned
    a note about configuration into a credential store.
    """
    forbidden = forbidden_controller_keys()

    def walk(node, trail=""):
        if isinstance(node, dict):
            for key, value in node.items():
                if str(key).lower() in forbidden:
                    raise ValueError(
                        f"controller declaration may not carry {trail}{key!r}. "
                        f"Authorization belongs to the MCP client that obtained "
                        f"it; ServerOS never holds the token."
                    )
                walk(value, f"{trail}{key}.")
        elif isinstance(node, list):
            for i, item in enumerate(node):
                walk(item, f"{trail}[{i}].")

    walk(data)
    return data


def declared_controller(path: Path | None = None) -> dict | None:
    """Return the locally declared controller surface, if one is declared.

    A declaration is a positive statement that something THIS runtime can
    call. Installing a package, leaving a daemon running, or knowing a URL are
    none of those things.
    """
    target = Path(os.path.expanduser(CONTROLLER_DECLARATION)) if path is None else Path(path)
    if not target.exists():
        return None
    data = validate_controller_declaration(transport.load_yaml(target) or {})
    # `kind` must be one the contract names. A declaration in a vocabulary of
    # its own invention is not a declaration this layer can honour.
    if data.get("kind") not in controller_contract()["declaration"]["kinds"]:
        return None
    if not data.get("surface"):
        return None
    return data


# ---------------------------------------------------------------------------
# The external surface, described rather than called
# ---------------------------------------------------------------------------


def external_controller() -> dict:
    """The published description of the controller plane.

    Deliberately returns a description and not a client. ServerOS may integrate
    with this surface through a standard MCP client; it may not become one, and
    it may not shortcut the authorization a client performs.
    """
    return controller_contract()["external_controller"]


def authorization_outstanding(declaration: dict | None = None) -> bool:
    """Whether reaching the controller still needs a human.

    True whenever no declaration exists. The step that clears it is a person
    completing an OAuth consent in an MCP client — not a configuration change
    ServerOS can make on its own, and not something a test can fake.
    """
    return declaration is None


def boundary_rules() -> dict[str, str]:
    """The forbidden routes, keyed by id, for callers that must cite one."""
    rows = controller_contract()["boundary"]["forbidden"]
    return {row["id"]: row["rule"] for row in rows}
