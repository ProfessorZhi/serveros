"""Owner-issued policy binding: kernel uid -> audited actor -> exact scope.

WHY THIS EXISTS
---------------
`agent_control_store.verify_live_grant` (#272) proves a grant is live, carries a
store-minted epoch, is bound to the kernel peer uid of its issuing call, and is
scoped. It does **not** prove the OWNER ever authorized that uid to act as that
principal for that project/Item/actions. The provider-signed delegation names a
human; the issuing process names a uid. The remaining #237 B3 unknown is that
bridge.

This module defines the contract, pure checks and explicit bootstrap issuer.
Configured daemons persist owner-issued policies in the existing Agent Control
ledger through `owner_operations`; there is no second policy database. With
no configured issuer or resolver the source remains blocked. Nothing infers a
policy from a uid, context, request label or clock; no unknown uid is backfilled.

An `OwnerPolicy` binds:

* `os_principal` — the kernel uid the owner authorized (`uid:<n>`);
* `actor_ref` — the audited principal that uid may act as;
* `projects` / `items` / `actions` — the exact scope;
* `epoch` — the owner-policy generation (a stale grant binding refuses);
* `valid_from` / `expires_at` — zoned instants;
* `revoked` — the owner's end of the delegation;
* `issued_by` / `issued_at` — the owner audit trail.

The resolver is a read: `resolve(...)` returns the live `OwnerPolicy` for the
grant's `owner_policy_ref`, or `None`. An exception or `None` is a refusal.
"""
from __future__ import annotations

import datetime
import json
import os
import re
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

#: The source owner -> uid -> actor -> scope binding is not established.
BLOCKED_OWNER_POLICY = "BLOCKED_OWNER_POLICY"

_REF = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:@/|%+\-]{0,199}\Z")
_UID = re.compile(r"uid:(0|[1-9][0-9]*)\Z")


def _reference(value, field):
    if not isinstance(value, str) or _REF.fullmatch(value) is None:
        raise ValueError(f"INVALID_OWNER_POLICY: {field} must be an exact bounded reference")
    return value


def _principal(value):
    if not isinstance(value, str) or _UID.fullmatch(value) is None:
        raise ValueError("INVALID_OWNER_POLICY: os_principal must be a canonical uid")
    if int(value[4:]) > 2**32 - 2:
        raise ValueError("INVALID_OWNER_POLICY: uid is outside the kernel uid range")
    return value


@dataclass(frozen=True)
class OwnerPolicyIssuer:
    """Explicit operator binding; never inferred from the daemon's own uid.

    This is the identity permitted to author policy, not a policy or grant.
    The daemon additionally requires its current trusted context and the
    original kernel socket peer for every operation.
    """

    os_principal: str
    actor_ref: str
    human_control_provider: str

    def __post_init__(self):
        _principal(self.os_principal)
        _reference(self.actor_ref, "actor_ref")
        _reference(self.human_control_provider, "human_control_provider")

    @classmethod
    def from_document(cls, document):
        if (not isinstance(document, dict)
                or set(document) != {"schema", "os_principal", "actor_ref", "human_control_provider"}
                or document["schema"] != "serveros/owner-policy-issuer.v1"):
            raise ValueError("INVALID_OWNER_ISSUER: exact issuer schema required")
        return cls(**{key: value for key, value in document.items() if key != "schema"})


def validate_policy_request(document, *, now):
    """Validate only owner-authored policy fields; epochs/audit are minted later."""
    fields = {"policy_ref", "os_principal", "actor_ref", "projects", "items", "actions",
              "valid_from", "expires_at"}
    if not isinstance(document, dict) or set(document) != fields:
        raise ValueError("INVALID_OWNER_POLICY: exact policy fields required")
    result = dict(document)
    _reference(result["policy_ref"], "policy_ref")
    _reference(result["actor_ref"], "actor_ref")
    _principal(result["os_principal"])
    for key in ("projects", "items", "actions"):
        values = result[key]
        if not isinstance(values, list) or not 1 <= len(values) <= 256:
            raise ValueError(f"INVALID_OWNER_POLICY: {key} requires 1..256 exact references")
        for value in values:
            _reference(value, key)
        if len(set(values)) != len(values):
            raise ValueError(f"INVALID_OWNER_POLICY: {key} has duplicate references")
        result[key] = sorted(values)
    start, end, clock = (instant(result["valid_from"]), instant(result["expires_at"]), instant(now))
    if start is None or end is None or clock is None or not start < end or end <= clock:
        raise ValueError("INVALID_OWNER_POLICY: live bounded zoned window required")
    return result


def load_issuer(path):
    """Load an explicitly named private POSIX bootstrap identity file.

    Native Windows needs a separately reviewed ACL/named-pipe identity path;
    it must never substitute the daemon's uid or a request field here.
    """
    target = Path(path)
    if not target.is_absolute():
        raise ValueError("owner issuer path must be absolute")
    if os.name != "posix":
        raise ValueError("OWNER_ISSUER_PLATFORM_UNSUPPORTED: POSIX ownership and kernel peer required")
    for directory in reversed(target.parents):
        info = directory.lstat()
        if not stat.S_ISDIR(info.st_mode) or info.st_uid not in (0, os.geteuid()):
            raise ValueError("OWNER_ISSUER_UNSAFE_PATH: unowned or symbolic ancestor")
        if info.st_mode & 0o022 and not (info.st_uid == 0 and info.st_mode & stat.S_ISVTX):
            raise ValueError("OWNER_ISSUER_UNSAFE_PATH: writable ancestor")
    info = target.lstat()
    if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid()
            or info.st_mode & 0o077 or info.st_nlink != 1):
        raise ValueError("OWNER_ISSUER_UNSAFE_FILE: a private owner regular file is required")
    handle = os.open(target, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(handle, encoding="utf-8") as stream:
        opened = os.fstat(stream.fileno())
        if ((opened.st_dev, opened.st_ino) != (info.st_dev, info.st_ino)
                or opened.st_mode & 0o077 or opened.st_uid != os.geteuid() or opened.st_nlink != 1):
            raise ValueError("OWNER_ISSUER_UNSAFE_FILE: issuer file changed while opening")
        raw = stream.read(8193)
    if len(raw) > 8192:
        raise ValueError("OWNER_ISSUER_UNSAFE_FILE: issuer document exceeds bound")

    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("INVALID_OWNER_ISSUER: duplicate key")
            result[key] = value
        return result

    return OwnerPolicyIssuer.from_document(json.loads(raw, object_pairs_hook=unique))


@dataclass(frozen=True)
class OwnerPolicy:
    policy_ref: str
    os_principal: str
    actor_ref: str
    projects: tuple[str, ...]
    items: tuple[str, ...]
    actions: tuple[str, ...]
    valid_from: str
    expires_at: str
    revoked: bool
    epoch: int
    issued_by: str
    issued_at: str


class OwnerPolicyResolver(Protocol):
    """The trusted owner source's one read. It must not mint a grant."""

    def resolve(self, *, policy_ref, grant, context, peer, project, item_ref,
                action, now) -> OwnerPolicy | None: ...


def instant(value):
    """A zoned instant, or None. Unreadable or naive is not an instant."""
    try:
        parsed = datetime.datetime.fromisoformat(
            str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    return parsed if parsed.tzinfo is not None else None


def os_principal_of(peer) -> str | None:
    """`uid:<n>` from a kernel peer record, or None."""
    if not isinstance(peer, dict):
        return None
    uid = peer.get("uid")
    if isinstance(uid, bool) or not isinstance(uid, int) or uid < 0:
        return None
    return f"uid:{uid}"


def _refusal(reason, because):
    return {"verified": False, "reason": reason, "because": because}


def _scope_covers(policy, grant, *, project=None, item_ref=None, action=None):
    """The grant's whole scope must be inside the owner policy."""
    for field, requested in (("projects", project), ("items", item_ref),
                             ("actions", action)):
        allowed = set(getattr(policy, field) or ())
        stored = (grant or {}).get(field)
        if isinstance(stored, str):
            return False
        stored = set(stored or ())
        if not stored or not stored <= allowed:
            return False
        if requested is not None and str(requested) not in allowed:
            return False
    return True


def _common_refusal(*, policy, grant, context, peer, now, require_binding):
    """Checks shared by issuance and verify. Returns a refusal or None."""
    if policy is None:
        return _refusal(
            BLOCKED_OWNER_POLICY,
            "no owner-issued policy resolves for this grant; the owner-to-uid "
            "binding is UNKNOWN and authorizes nothing")
    if not isinstance(policy, OwnerPolicy):
        return _refusal(
            BLOCKED_OWNER_POLICY,
            "the owner-policy resolver returned no OwnerPolicy")
    if not str(policy.policy_ref or "").strip():
        return _refusal(BLOCKED_OWNER_POLICY, "the owner policy has no ref")
    if not isinstance(policy.revoked, bool):
        # `revoked=1` / `"yes"` / None is a data error, not an owner decision.
        # It cannot be read as unrevoked, and silently normalizing it would
        # turn a broken policy source into an authorization.
        return _refusal(
            BLOCKED_OWNER_POLICY,
            f"the owner policy revoked field {policy.revoked!r} is not a bool; "
            f"a non-boolean revocation state cannot be read as unrevoked")
    if policy.revoked:
        return _refusal("POLICY_DENIED",
                        f"{policy.policy_ref} was revoked by the owner")
    if isinstance(policy.epoch, bool) or not isinstance(policy.epoch, int) \
            or policy.epoch < 1:
        return _refusal(BLOCKED_OWNER_POLICY,
                        f"the owner policy epoch {policy.epoch!r} is not a "
                        f"positive integer")
    if not str(policy.issued_by or "").strip():
        return _refusal(BLOCKED_OWNER_POLICY,
                        "the owner policy has no audited issuer")
    issued_at_instant = instant(policy.issued_at)
    if issued_at_instant is None:
        return _refusal(
            BLOCKED_OWNER_POLICY,
            f"the owner policy issued_at {policy.issued_at!r} is not a "
            f"readable zoned instant")

    now_instant = instant(now)
    valid_from = instant(policy.valid_from)
    expires_at = instant(policy.expires_at)
    if now_instant is None or valid_from is None or expires_at is None:
        return _refusal(
            "INVALID_CONTEXT",
            "the owner policy window or the verification instant is not a "
            "readable zoned timestamp; it cannot be checked")
    if now_instant < valid_from:
        return _refusal("POLICY_DENIED",
                        f"{policy.policy_ref} is not valid until "
                        f"{policy.valid_from}")
    if now_instant >= expires_at:
        return _refusal("POLICY_DENIED",
                        f"{policy.policy_ref} expired at {policy.expires_at}")
    if issued_at_instant > now_instant:
        return _refusal(
            BLOCKED_OWNER_POLICY,
            f"{policy.policy_ref} was issued at {policy.issued_at}, after the "
            f"verification instant; a policy that has not been issued yet "
            f"authorizes nothing")
    if issued_at_instant > expires_at:
        return _refusal(
            BLOCKED_OWNER_POLICY,
            f"{policy.policy_ref} was issued at {policy.issued_at}, after its "
            f"own expiry {policy.expires_at}; the audit order is impossible")

    peer_principal = os_principal_of(peer)
    if peer_principal is None:
        return _refusal(
            "UNTRUSTED_ACTOR",
            "this call has no kernel peer credential; a uid in the payload is "
            "a claim, not evidence")
    if str(policy.os_principal) != peer_principal:
        return _refusal(
            "UNTRUSTED_ACTOR",
            f"the owner policy binds {policy.os_principal!r} and this call "
            f"arrived as {peer_principal!r}; the uid mapping is not this one")

    actor = str((context or {}).get("actor_ref") or "").strip()
    if not actor:
        return _refusal("UNTRUSTED_ACTOR",
                        "the trusted context records no actor")
    if str(policy.actor_ref) != actor:
        return _refusal(
            "UNTRUSTED_ACTOR",
            f"the owner policy binds actor {policy.actor_ref!r} and the "
            f"trusted context is {actor!r}; the uid may not act as this actor")

    if require_binding:
        ref = str((grant or {}).get("owner_policy_ref") or "").strip()
        if not ref:
            return _refusal(
                BLOCKED_OWNER_POLICY,
                f"grant {(grant or {}).get('grant_ref')!r} carries no "
                f"owner_policy_ref; a grant issued before the owner binding "
                f"is UNKNOWN and authorizes nothing")
        if str(policy.policy_ref) != ref:
            return _refusal(
                BLOCKED_OWNER_POLICY,
                f"the live policy is {policy.policy_ref!r} and the grant was "
                f"issued under {ref!r}")
        stored_epoch = (grant or {}).get("owner_policy_epoch")
        if isinstance(stored_epoch, bool) or not isinstance(stored_epoch, int):
            return _refusal(BLOCKED_OWNER_POLICY,
                            "the grant records no owner-policy epoch")
        if policy.epoch != stored_epoch:
            return _refusal(
                BLOCKED_OWNER_POLICY,
                f"the owner policy moved from epoch {stored_epoch} to "
                f"{policy.epoch}; the grant's binding is stale")
        bound = str((grant or {}).get("os_principal") or "").strip()
        if not bound:
            return _refusal("UNTRUSTED_ACTOR",
                            "the grant records no os_principal")
        if not (str(policy.os_principal) == bound == peer_principal):
            return _refusal(
                "UNTRUSTED_ACTOR",
                f"the owner policy binds {policy.os_principal!r}, the grant "
                f"{bound!r}, and this call arrived as {peer_principal!r}")
        grant_actor = str((grant or {}).get("actor_ref") or "").strip()
        if not grant_actor:
            return _refusal(BLOCKED_OWNER_POLICY,
                            "the grant records no audited actor")
        if not (str(policy.actor_ref) == grant_actor == actor):
            return _refusal(
                "UNTRUSTED_ACTOR",
                f"the owner policy binds actor {policy.actor_ref!r}, the grant "
                f"{grant_actor!r}, and this context is {actor!r}")
    return None


def check_issue_policy(*, grant, context, peer, now, policy):
    """The owner-policy check at `agent.grant.issue`. Refusal or None.

    The grant is the provider-verified scope that is about to be stored; it has
    no store-issued epoch/os_principal/owner binding yet. The policy must cover
    the issuing kernel uid, the trusted context's actor, the whole grant scope,
    and be live and audited.
    """
    refusal = _common_refusal(policy=policy, grant=grant, context=context,
                              peer=peer, now=now, require_binding=False)
    if refusal is not None:
        return refusal
    if not _scope_covers(policy, grant):
        return _refusal(
            "POLICY_DENIED",
            f"the grant scope is not a subset of the owner policy "
            f"{policy.policy_ref}: projects={grant.get('projects')!r}, "
            f"items={grant.get('items')!r}, actions={grant.get('actions')!r}")
    return None


def check_policy_binding(*, grant, context, peer, project, item_ref, action,
                         now, policy):
    """The owner-policy half of `agent.lease.authority.verify`. Refusal or None.

    `verify_live_grant` has already checked the grant's own epoch, window,
    os_principal, revocation and the requested scope's membership. This checks
    that the OWNER binding stored on the grant still matches the live owner
    policy: same policy ref, same policy epoch (stale refuses), same uid, same
    audited actor, and a policy scope covering the grant and the requested
    scope.
    """
    refusal = _common_refusal(policy=policy, grant=grant, context=context,
                              peer=peer, now=now, require_binding=True)
    if refusal is not None:
        return refusal
    if not _scope_covers(policy, grant, project=project, item_ref=item_ref,
                         action=action):
        return _refusal(
            "POLICY_DENIED",
            f"the grant/request scope is not inside the owner policy "
            f"{policy.policy_ref}: projects={grant.get('projects')!r}, "
            f"items={grant.get('items')!r}, actions={grant.get('actions')!r}")
    return None


def audit(policy) -> dict:
    """The owner binding, for an auditable verification answer."""
    return {
        "policy_ref": policy.policy_ref,
        "policy_epoch": policy.epoch,
        "issued_by": policy.issued_by,
        "issued_at": policy.issued_at,
        "actor_ref": policy.actor_ref,
        "os_principal": policy.os_principal,
    }
