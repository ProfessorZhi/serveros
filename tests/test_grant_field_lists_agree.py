"""One list, two modules: the fields a stored grant carries and nobody signed.

`runtime/agent_control_store.py` writes fields onto a recorded grant that the
provider never signed — the revocation triple, the generation `epoch`, and (for
durable issuance) the OS principal and the owner-policy binding. It declares
them once, as `OWNER_WRITTEN_GRANT_FIELDS`, and excludes them from its own
write-once comparison.

`context.actor.issued_delegation` compares the STORED grant against the
provider-approved scope, so it has to exclude the same set. It cannot import
the store — context/ does not reach into runtime/, and the store is not in
actor.py's closure — so it keeps its own copy. Neither module can police the
other, which is what this file is for.

The failure it was written for: `epoch` joined the store's list on 2026-10-21
and actor.py's list did not grow it, so from then on every provider-issued
grant was compared against a signed scope with one extra key in it and came
back `DELEGATION_SCOPE_CHANGED`. Five tests in `tests/test_durable_delegation.py`
were red; in production every durable delegation would have read as
unauthorized. One field, one drift, and no test looking at both sides at once.
"""
import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def load(name, path):
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, ROOT / path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


store = load("serveros_runtime_agent_control_store", "runtime/agent_control_store.py")
actor = load("serveros_context_actor", "context/actor.py")


def test_the_two_lists_are_the_same_list():
    """Same names, same order. A set comparison would let a reordering through,
    and a reordering here means somebody edited one copy without reading the
    other."""
    assert actor.STORE_WRITTEN_GRANT_FIELDS == store.OWNER_WRITTEN_GRANT_FIELDS


def test_unsigned_scope_keeps_the_signed_scope_and_drops_the_stores():
    """The whole comparison in one dict: what the provider signed survives, what
    the store wrote afterwards does not, and the envelope's own name goes with
    the store's fields because it is the key rather than a term."""
    stored = {
        "grant_ref": "grant:fixture",
        "items": ["SRV-TEST"],
        "actions": ["research"],
        "budget": {"wall_seconds": 600, "max_attempts": 2,
                   "max_output_bytes": 65536, "exhausted": False},
        "revoked": False,
        "revoked_at": None,
        "revoked_reason": None,
        "epoch": 3,
        "os_principal": "uid:1000",
        "owner_policy_ref": "owner-policy:fixture",
        "owner_policy_epoch": 2,
        "owner_policy_issued_by": "human:hwz",
        "owner_policy_issued_at": "2026-10-06T08:01:00+00:00",
        "actor_ref": "human:hwz",
        "issuance": {"schema": "serveros/delegation-issuance.v1"},
        "ref": "grant:fixture",
    }
    assert actor.unsigned_scope(stored) == {
        "grant_ref": "grant:fixture",
        "items": ["SRV-TEST"],
        "actions": ["research"],
        "budget": {"wall_seconds": 600, "max_attempts": 2,
                   "max_output_bytes": 65536, "exhausted": False},
    }


def test_a_field_nobody_declared_is_kept_so_the_comparison_can_still_refuse():
    """The direction that must not go loose. `issued_delegation` exists to
    notice a stored grant that grew a key nobody signed; a filter that dropped
    anything unfamiliar would make it notice nothing. Whatever is not on the
    declared list has to reach the comparison."""
    assert actor.unsigned_scope({"epoch": 1, "smuggled": True}) == {"smuggled": True}
