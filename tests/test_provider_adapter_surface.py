#!/usr/bin/env python3
"""Where an adapter says a runtime reads MCP servers -- one record, not two.

`abilities/provider-contract.yaml#adapters` says of itself that it "is the only
place a runtime-specific fact is allowed to live", and it declared one: the
claude-code adapter's `surface`, `{path_role: home, relative: ".mcp.json"}`.

`abilities/agent-runtime-contract.yaml#capabilities.mcp_surfaces` declares the
same fact, says of itself "One key, one file", and is the copy
`abilities/runtime_parity.py` actually reads and compares against this machine.
The two disagreed.

Measured 2026-09-22, because this is the part that cannot be argued from the
text: `~/.mcp.json` does not exist on this node, and this runtime's MCP servers
are in `~/.claude.json` under `mcpServers`, whose keys are `botmux` and
`playwright`. So the record one capability system had delegated as the
authority was RIGHT, and the second copy was WRONG -- and the second copy mixed
the user-scope path role with a plugin-root filename, which are two different
surfaces.

Nothing noticed, and nothing could have. No test, no checker and no module
reads the `adapters` block: `grep -rn '"adapters"' tests/` finds one unrelated
hit, and `runtime_parity.py` reads the other file. A second copy with no reader
is wrong silently and stays wrong -- the same argument this repository already
makes about human maps, applied to a declaration inside a contract.

So the shape became `surface_from`, naming the record that owns the fact, and
this file refuses a literal `surface:` coming back. The rule is a function here
rather than an assertion in one test so that the controls below can make it
fail on purpose: a gate nobody has watched fail is a gate nobody knows works.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parent.parent
PROVIDER_CONTRACT = REPO / "abilities" / "provider-contract.yaml"
RUNTIME_CONTRACT = REPO / "abilities" / "agent-runtime-contract.yaml"


def _load(path: Path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def adapter_shapes(contract: dict) -> list[dict]:
    """Every adapter shape in the contract, however the block spells them.

    `shape` is a mapping today; a second adapter shape would make it a list. The
    check reads both rather than only the spelling that happens to be in the
    file now, because a rule that silently stops applying when the block is
    reformatted is a rule that will stop applying.
    """
    block = contract.get("adapters") or {}
    out: list[dict] = []
    singular = block.get("shape")
    if isinstance(singular, dict):
        out.append(singular)
    plural = block.get("shapes")
    if isinstance(plural, list):
        out.extend(s for s in plural if isinstance(s, dict))
    return out


def restated_surfaces(contract: dict) -> list[str]:
    """Adapter shapes that declare a literal `surface` instead of referencing one.

    Returns a name for each offender; empty means the fact is single-sourced.
    """
    offenders = []
    for i, shape in enumerate(adapter_shapes(contract)):
        if "surface" in shape:
            offenders.append(str(shape.get("runtime") or f"adapters.shape[{i}]"))
    return offenders


def resolve_reference(ref: str) -> tuple[Path, object]:
    """`<path>#<dotted.key>` -> (file, the object that key names).

    Raises KeyError with the step that failed, so a dangling reference reads as
    a dangling reference rather than as an exception from somewhere else.
    """
    path_text, _, dotted = str(ref).partition("#")
    if not path_text or not dotted:
        raise KeyError(f"{ref!r} is not `<path>#<dotted.key>`")
    target = REPO / path_text
    if not target.is_file():
        raise KeyError(f"{path_text}: no such file")
    node: object = _load(target)
    for step in dotted.split("."):
        if not isinstance(node, dict) or step not in node:
            raise KeyError(f"{path_text}#{dotted}: '{step}' is absent")
        node = node[step]
    return target, node


# --------------------------------------------------------------------------- #
# the rule, on the real contract
# --------------------------------------------------------------------------- #

def test_the_shipped_contract_declares_no_literal_surface():
    """The defect itself: a second copy of a fact another file owns."""
    contract = _load(PROVIDER_CONTRACT)
    assert restated_surfaces(contract) == [], (
        "these adapter shapes restate a runtime MCP surface that "
        "`abilities/agent-runtime-contract.yaml#capabilities.mcp_surfaces` "
        "already owns: " + ", ".join(restated_surfaces(contract)))


def test_every_adapter_shape_references_a_record_that_exists():
    """A reference is only better than a copy if following it lands somewhere."""
    contract = _load(PROVIDER_CONTRACT)
    shapes = adapter_shapes(contract)
    assert shapes, "no adapter shape declared; this test would pass vacuously"
    for shape in shapes:
        ref = shape.get("surface_from")
        assert ref, f"adapter for {shape.get('runtime')!r} names no surface at all"
        _, record = resolve_reference(ref)          # KeyError is the failure
        assert isinstance(record, dict), ref
        for field in ("path_role", "relative", "key"):
            assert field in record, f"{ref} is not a surface record: no {field!r}"


def test_the_key_stays_specific_to_the_adapter():
    """The reference owns the FILE; the adapter still owns its own KEY.

    Without this the block could be made consistent by deleting what makes it an
    adapter -- the one entry it is allowed to declare.
    """
    contract = _load(PROVIDER_CONTRACT)
    for shape in adapter_shapes(contract):
        key = shape.get("key")
        if not key:
            continue
        _, record = resolve_reference(shape["surface_from"])
        # `mcpServers.playwright` is inside the surface's own key, not equal to
        # it: the surface declares where the map lives, the adapter declares one
        # entry in it.
        assert key.startswith(str(record["key"])), (
            f"{key!r} is not inside {record['key']!r}, so it is not an entry in "
            f"the surface this adapter references")


def test_every_adapter_runtime_has_a_surface_record():
    """The two contracts name the same runtimes, or the reference cannot exist."""
    contract = _load(PROVIDER_CONTRACT)
    surfaces = ((_load(RUNTIME_CONTRACT).get("capabilities") or {})
                .get("mcp_surfaces") or {})
    runtimes = (contract.get("adapters") or {}).get("runtimes") or []
    assert runtimes, "no runtimes declared"
    missing = [r for r in runtimes if r not in surfaces]
    assert not missing, (
        f"adapters declare {missing}, which `mcp_surfaces` does not cover; an "
        f"adapter for one of them has nowhere to point")


# --------------------------------------------------------------------------- #
# controls -- the rule is watched failing on purpose
# --------------------------------------------------------------------------- #

def test_the_rule_catches_a_literal_surface():
    """The positive control. Without it, a rule that matches nothing looks clean."""
    fabricated = {"adapters": {"shape": {
        "runtime": "claude-code",
        "surface": {"path_role": "home", "relative": ".mcp.json"},
        "key": "mcpServers.playwright",
    }}}
    assert restated_surfaces(fabricated) == ["claude-code"]


def test_the_rule_catches_it_in_the_plural_spelling_too():
    """The same defect under `shapes:`, which is the reformatting that would
    otherwise switch the rule off without switching the defect off."""
    fabricated = {"adapters": {"shapes": [
        {"runtime": "codex", "surface": {"path_role": "home",
                                         "relative": ".codex/config.toml"}},
    ]}}
    assert restated_surfaces(fabricated) == ["codex"]


def test_a_dangling_reference_is_reported_as_a_dangling_reference():
    """The control for the walker: absent file, absent step, and prose that is
    not a reference at all must each fail, and fail naming the step."""
    with pytest.raises(KeyError, match="no such file"):
        resolve_reference("abilities/there-is-no-such-file.yaml#a.b")
    with pytest.raises(KeyError, match="'nope' is absent"):
        resolve_reference("abilities/agent-runtime-contract.yaml#capabilities.nope")
    with pytest.raises(KeyError, match="is not"):
        resolve_reference("no-hash-here")


def test_the_controls_do_not_ride_on_the_real_contract():
    """If the shipped contract went back to a literal surface, the controls
    above would still pass and the first test would fail -- which is the right
    split, and worth stating so a red run is read as the defect it is."""
    contract = _load(PROVIDER_CONTRACT)
    shapes = adapter_shapes(contract)
    assert all("surface" not in s for s in shapes)
    # And the file it now points at is the one that was right all along.
    for shape in shapes:
        path_text, _, dotted = str(shape.get("surface_from", "")).partition("#")
        assert path_text == "abilities/agent-runtime-contract.yaml", path_text
        assert dotted == "capabilities.mcp_surfaces.claude-code", dotted


# --------------------------------------------------------------------------- #
# the machine fact, kept where it can be re-measured
# --------------------------------------------------------------------------- #

def test_the_surface_record_is_the_one_this_machine_agrees_with():
    """The measurement, re-runnable, and honest when it cannot be made.

    This is the half that decided which of the two copies was right. It reads
    `$HOME`, so it can answer "could not look" -- and it says so rather than
    passing, because a test that passes when it did not look is the same defect
    as a monitor that cannot tell broken from unread.
    """
    home = Path.home()
    claimed = resolve_reference(
        "abilities/agent-runtime-contract.yaml#capabilities.mcp_surfaces.claude-code")[1]
    target = home / claimed["relative"]
    if not target.is_file():
        pytest.skip(f"could not look: {target} is not present on this node")
    document = json.loads(target.read_text(encoding="utf-8"))
    declared_key = claimed["key"]
    assert declared_key in document, (
        f"{target} has no {declared_key!r}; the surface record is wrong for "
        f"this machine and the adapter referencing it inherits the error")
