"""Trusted-authority seams for the exclusive Work lease and run finalization.

WHY THIS MODULE EXISTS
----------------------
`ServerOSStore.acquire_lease(resource, owner=...)` answers "is this writer the
current holder". It cannot answer "who is this writer", because that is not a
store fact: a caller's word is not an identity. serveros #261/#262/#264 now
check the Work Engine lease A/1 at admission, and independent QA #256 Snapshot H
found the missing half: no trusted authenticated `acquire_lease` producer.

This module is that half as a CONTRACT, not a privilege. A trusted parent must
implement `LeaseAuthority.verify` (and, for T10, `RunnerReceiptAuthority.verify`)
and derive the principal from the kernel/daemon — `SO_PEERCRED`, a ServerOS
`context_ref` resolved by the daemon against its grant store, with `revoked`,
`expires_at` and a monotonic epoch. Work Engine cannot mint that authority, and
this module does not invent a token or a role to stand in for it.

Two consequences are deliberate:

  * `OwnerVerifiedLease` takes no `owner` argument. The owner written into the
    lease is the verified principal, never the caller's string.
  * With no authority bound every operation refuses `BLOCKED_TRUST_SOURCE`.
    There is no permissive default, no environment fallback and no argv path.
    No CLI verb and no MCP tool is registered for it: exposing an unguarded
    lease create action would be the P0 this milestone exists to close.

The adapter is still a seam, not a security boundary against arbitrary
in-process code: a Python caller can instantiate its own verifier. That is
exactly why the production boundary must be an out-of-process parent with
kernel-attested identity and a grant store. `docs/lease-authority-contract.md`
states the exact interface the original owner must provide.
"""
from __future__ import annotations

import os
import re
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Protocol

from .store import LeaseError

_HEX64 = re.compile(r"^[0-9a-f]{64}$")
_HEX40 = re.compile(r"^[0-9a-f]{40}$")


class AuthorityError(ValueError):
    """A lease operation the trusted authority would not authorise.

    `reason_code` is part of the answer, not decoration: a revoked grant, a
    superseded epoch and an unbound verifier are three different repairs.
    """

    def __init__(self, reason_code: str, detail: str) -> None:
        self.reason_code = reason_code
        self.detail = detail
        super().__init__(f"{reason_code}: {detail}")


class ReceiptError(ValueError):
    """A run finalization that has no trusted runner receipt behind it."""

    def __init__(self, reason_code: str, detail: str) -> None:
        self.reason_code = reason_code
        self.detail = detail
        super().__init__(f"{reason_code}: {detail}")


@dataclass(frozen=True)
class LeaseGrant:
    """The grant a trusted parent verified for one context_ref.

    `items` and `actions` are exact names, never wildcards. `epoch` is the
    grant's current generation; a caller may pin the epoch it observed and the
    adapter refuses if the authority has moved on. `os_principal` is the
    kernel-attested identity the parent bound to the context, in
    `uid:<n>` form; the adapter checks it against this process before it writes.
    """

    principal: str
    grant_ref: str
    project: str
    items: tuple[str, ...]
    actions: tuple[str, ...]
    valid_from: float
    expires_at: float
    revoked: bool
    epoch: int
    os_principal: str


class LeaseAuthority(Protocol):
    """The trusted parent's one read operation.

    It must resolve `context_ref` out of band (never from a caller argument),
    re-read the grant's `revoked`/`expires_at`/`epoch` at `now`, and bind the
    actor to an OS principal. Raising is a refusal; returning a `LeaseGrant`
    is the only positive answer.
    """

    def verify(self, context_ref: str, *, action: str, project: str,
               item_ref: str, now: float) -> LeaseGrant: ...


@dataclass(frozen=True)
class VerifiedRunReceipt:
    """A bounded-runner receipt the trusted parent verified.

    The six fields after the Item/Run identity are the ones serveros #263
    anchors: `action_key`, `input_digest`, `receipt_sha256`,
    `process_exit_code`, plus the runner identity and exact commit SHA. The
    store re-checks their shape and their agreement with the requested verdict;
    it never parses child stdout/stderr and never treats a caller hash or a
    bare exit code as this object.
    """

    item_ref: str
    run_ref: str
    action_key: str
    input_digest: str
    receipt_sha256: str
    process_exit_code: int
    verdict: str
    runner_identity: str
    commit_sha: str
    actor_ref: str


@dataclass(frozen=True)
class T10Binding:
    """The exact Work facts a T10-required finalize must name.

    The trusted parent derives these while it still holds the Work store and
    the grant: the Run is the unique non-terminal Run for the Item, the commit
    is the worktree's exact full SHA, and the actor is the grant principal —
    never a value supplied by the child or by the caller's payload. Comparing
    them to the verified receipt is what makes a receipt for another Run,
    another commit or another actor unusable here.
    """

    item_ref: str
    run_ref: str
    commit_sha: str
    actor_ref: str


class RunnerReceiptAuthority(Protocol):
    """The trusted parent's receipt verifier.

    `receipt_ref` is opaque to Work Engine. The parent must resolve it to the
    real `invoke_once` record for `item_ref`/`run_ref`, verify the
    `receipt_sha256` anchor over the receipt and its streams, and return the
    verified fields. Any uncertainty is a refusal, never a guess.
    """

    def verify(self, receipt_ref: str, *, item_ref: str, run_ref: str,
               now: float) -> VerifiedRunReceipt: ...


class OwnerVerifiedLease:
    """The only lease producer that does not trust a caller's owner string."""

    #: The exact action names a grant must carry for each verb.
    ACTIONS = {
        "acquire": "lease.acquire",
        "renew": "lease.renew",
        "release": "lease.release",
        "commit": "work.commit",
    }

    def __init__(self, store, authority: LeaseAuthority | None = None) -> None:
        self._store = store
        self._authority = authority

    # -- the trust boundary ------------------------------------------------

    def _verified(self, context_ref: str, *, verb: str, project: str,
                  item_ref: str, expected_epoch: int | None,
                  now: float) -> LeaseGrant:
        if self._authority is None:
            raise AuthorityError(
                "BLOCKED_TRUST_SOURCE",
                "no trusted lease authority is bound to this Work Engine, so no "
                "caller can be identified and no owner may be written. The "
                "original owner must bind a parent that resolves context_ref "
                "with an OS principal and a live grant.")
        if not str(context_ref or "").strip():
            raise AuthorityError(
                "UNTRUSTED_ACTOR",
                "no context_ref was presented; the caller's word is not an "
                "identity and cannot become a lease owner")
        action = self.ACTIONS[verb]
        try:
            grant = self._authority.verify(
                context_ref, action=action, project=project,
                item_ref=item_ref, now=now)
        except AuthorityError:
            raise
        except Exception as exc:  # a resolver that cannot answer is a refusal
            raise AuthorityError(
                "UNTRUSTED_ACTOR",
                f"the trusted authority did not answer: {type(exc).__name__}") from exc
        if not isinstance(grant, LeaseGrant):
            raise AuthorityError(
                "UNTRUSTED_ACTOR",
                "the authority returned no LeaseGrant; a resolution that found "
                "nothing is not an identity")
        if not str(grant.principal or "").strip():
            raise AuthorityError(
                "UNTRUSTED_ACTOR", "the authority returned no principal")
        if not str(grant.grant_ref or "").strip():
            raise AuthorityError(
                "UNTRUSTED_ACTOR", "the authority returned no grant_ref")
        if str(grant.project) != str(project):
            raise AuthorityError(
                "PROJECT_MISMATCH",
                f"the grant is for project {grant.project!r}, not {project!r}")
        if item_ref not in tuple(grant.items):
            raise AuthorityError(
                "ITEM_MISMATCH",
                f"the grant does not name Item {item_ref!r}; it names "
                f"{', '.join(grant.items) or 'nothing'}")
        if action not in tuple(grant.actions):
            raise AuthorityError(
                "ACTION_NOT_GRANTED",
                f"the grant does not carry {action!r}; it carries "
                f"{', '.join(grant.actions) or 'nothing'}")
        if grant.revoked:
            raise AuthorityError(
                "GRANT_REVOKED",
                f"{grant.grant_ref} was revoked; a revoked writer may not "
                "acquire, renew, release or commit")
        if not (float(grant.valid_from) <= now < float(grant.expires_at)):
            if now < float(grant.valid_from):
                raise AuthorityError(
                    "GRANT_NOT_YET_VALID",
                    f"{grant.grant_ref} starts at {grant.valid_from}, after "
                    f"the requested instant {now}")
            raise AuthorityError(
                "GRANT_EXPIRED",
                f"{grant.grant_ref} closed at {grant.expires_at}, before the "
                f"requested instant {now}")
        if type(grant.epoch) is not int or grant.epoch < 1:
            raise AuthorityError(
                "EPOCH_MISSING",
                "the authority returned no usable grant epoch; an epoch-less "
                "grant cannot be fenced")
        if expected_epoch is not None and grant.epoch != expected_epoch:
            raise AuthorityError(
                "EPOCH_STALE",
                f"the grant is at epoch {grant.epoch}, but this caller observed "
                f"epoch {expected_epoch}; authority moved and the old turn lost")
        expected_os = f"uid:{os.getuid()}"
        if str(grant.os_principal) != expected_os:
            raise AuthorityError(
                "OS_PRINCIPAL_MISMATCH",
                f"the grant is bound to {grant.os_principal!r}, but this "
                f"process is {expected_os!r}")
        return grant

    @staticmethod
    def _resource(item_ref: str) -> str:
        item = str(item_ref or "").strip()
        if not item:
            raise AuthorityError("ITEM_MISMATCH", "a lease names an Item; '' names none")
        return f"work-write:{item}"

    def _now(self, now) -> float:
        """`now` is an explicit test seam; absent it, the store's clock.

        The adapter and the store must age a grant and a lease on the SAME
        clock, or a caller can move one without moving the other.
        """
        return self._store.clock() if now is None else float(now)

    # -- the three lease verbs --------------------------------------------

    def acquire(self, *, context_ref: str, project: str, item_ref: str,
                ttl_s: float, expected_epoch: int | None = None,
                now=None) -> dict:
        now_e = self._now(now)
        grant = self._verified(context_ref, verb="acquire", project=project,
                               item_ref=item_ref, expected_epoch=expected_epoch,
                               now=now_e)
        try:
            return self._store.acquire_lease(
                self._resource(item_ref), owner=grant.principal,
                ttl_s=ttl_s, now=now_e)
        except LeaseError as exc:
            raise AuthorityError(exc.reason_code, exc.detail) from exc

    def renew(self, *, context_ref: str, project: str, item_ref: str,
              token: int, ttl_s: float, expected_epoch: int | None = None,
              now=None) -> dict:
        now_e = self._now(now)
        grant = self._verified(context_ref, verb="renew", project=project,
                               item_ref=item_ref, expected_epoch=expected_epoch,
                               now=now_e)
        try:
            return self._store.renew_lease(
                self._resource(item_ref), owner=grant.principal,
                token=token, ttl_s=ttl_s, now=now_e)
        except LeaseError as exc:
            raise AuthorityError(exc.reason_code, exc.detail) from exc

    def release(self, *, context_ref: str, project: str, item_ref: str,
                token: int, expected_epoch: int | None = None,
                now=None) -> dict:
        now_e = self._now(now)
        grant = self._verified(context_ref, verb="release", project=project,
                               item_ref=item_ref, expected_epoch=expected_epoch,
                               now=now_e)
        try:
            return self._store.release_lease(
                self._resource(item_ref), owner=grant.principal,
                token=token, now=now_e)
        except LeaseError as exc:
            raise AuthorityError(exc.reason_code, exc.detail) from exc

    def check_writer(self, *, context_ref: str, project: str, item_ref: str,
                     token: int, expected_epoch: int | None = None,
                     now=None) -> dict:
        """The commit boundary: re-verify the grant, then the fence."""
        now_e = self._now(now)
        grant = self._verified(context_ref, verb="commit", project=project,
                               item_ref=item_ref, expected_epoch=expected_epoch,
                               now=now_e)
        try:
            return self._store.check_writer(
                self._resource(item_ref), owner=grant.principal,
                token=token, now=now_e)
        except LeaseError as exc:
            raise AuthorityError(exc.reason_code, exc.detail) from exc

    @contextmanager
    def commit_guard(self, *, context_ref: str, project: str, item_ref: str,
                     token: int, expected_epoch: int | None = None):
        """Re-verify the grant, then hold the exclusive fence across the effect.

        `check_writer` answers at one instant; the caller's effect lands in a
        different transaction. This guard is the atomic form: it re-verifies the
        grant at the boundary, opens the store's `lease_guard` (BEGIN IMMEDIATE),
        yields the connection the effect must use, and re-verifies the grant
        again after the effect and before the commit. The store guard then
        re-reads the lease expiry at the trusted store clock. Any refusal raises
        inside the store transaction, so the effect is rolled back rather than
        landing on an authorization that moved.

        The clock is the store's, never a caller argument: a commit boundary
        cannot accept a caller-chosen instant.
        """
        grant = self._verified(
            context_ref, verb="commit", project=project, item_ref=item_ref,
            expected_epoch=expected_epoch, now=self._store.clock())
        try:
            with self._store.lease_guard(
                    self._resource(item_ref), owner=grant.principal,
                    token=token) as conn:
                yield conn
                # The grant and the lease are two clocks. The lease is re-read
                # by the store guard on exit; the grant is re-read here, still
                # inside the same transaction.
                self._verified(
                    context_ref, verb="commit", project=project,
                    item_ref=item_ref, expected_epoch=expected_epoch,
                    now=self._store.clock())
        except LeaseError as exc:
            raise AuthorityError(exc.reason_code, exc.detail) from exc


def validate_verified_receipt(receipt: VerifiedRunReceipt, *, item_id: str,
                              run_id: str, verdict: str, exit_code: int
                              ) -> VerifiedRunReceipt:
    """Re-check a verified receipt; the store's own half of the gate.

    The authority is responsible for provenance. This function is responsible
    for the receipt being about THIS Item/Run and for agreeing with the request.
    A mismatch is a refusal, never a correction.
    """
    verdict = str(verdict).strip().upper()
    if receipt.item_ref != item_id:
        raise ReceiptError(
            "RECEIPT_ITEM_MISMATCH",
            f"the receipt is for Item {receipt.item_ref!r}, not {item_id!r}")
    if receipt.run_ref != run_id:
        raise ReceiptError(
            "RECEIPT_RUN_MISMATCH",
            f"the receipt is for Run {receipt.run_ref!r}, not {run_id!r}")
    if not str(receipt.action_key or "").strip():
        raise ReceiptError("INVALID_RECEIPT", "action_key is empty")
    if not _HEX64.fullmatch(str(receipt.input_digest or "")):
        raise ReceiptError(
            "INVALID_RECEIPT", "input_digest is not a 64-hex digest")
    if not _HEX64.fullmatch(str(receipt.receipt_sha256 or "")):
        raise ReceiptError(
            "INVALID_RECEIPT", "receipt_sha256 is not a 64-hex anchor")
    if not _HEX40.fullmatch(str(receipt.commit_sha or "")):
        raise ReceiptError(
            "INVALID_RECEIPT", "commit_sha is not a full 40-hex SHA")
    if not str(receipt.runner_identity or "").strip():
        raise ReceiptError("INVALID_RECEIPT", "runner_identity is empty")
    if not str(receipt.actor_ref or "").strip():
        raise ReceiptError("INVALID_RECEIPT", "actor_ref is empty")
    if type(receipt.process_exit_code) is not int:
        raise ReceiptError("INVALID_RECEIPT", "process_exit_code is not an int")
    if receipt.process_exit_code != exit_code:
        raise ReceiptError(
            "EXIT_CODE_DISAGREEMENT",
            f"the receipt says exit {receipt.process_exit_code}, the caller "
            f"says {exit_code}; the receipt is the authority")
    if str(receipt.verdict).strip().upper() != verdict:
        raise ReceiptError(
            "RECEIPT_VERDICT_DISAGREEMENT",
            f"the receipt says {receipt.verdict!r}, the caller says {verdict!r}")
    if verdict == "PASS" and receipt.process_exit_code != 0:
        raise ReceiptError(
            "RECEIPT_INCONSISTENT",
            f"a receipt cannot say PASS with process_exit_code "
            f"{receipt.process_exit_code}")
    return receipt


def validate_t10_binding(binding: T10Binding, *, item_id: str, run_id: str,
                         receipt: VerifiedRunReceipt) -> T10Binding:
    """Require the exact Item/Run/commit/actor the trusted parent derived.

    The receipt already agrees with the Item and Run it was verified for. This
    comparison adds the three facts a receipt alone does not name to the caller:
    the exact commit, the exact actor, and the binding the parent asked to
    finalize. A receipt for another Run, commit or actor is refused here rather
    than merely recorded beside a mismatched request.
    """
    if not _HEX40.fullmatch(str(binding.commit_sha or "")):
        raise ReceiptError(
            "INVALID_T10_BINDING",
            "commit_sha is not a full 40-hex SHA")
    if not str(binding.actor_ref or "").strip():
        raise ReceiptError("INVALID_T10_BINDING", "actor_ref is empty")
    if binding.item_ref != item_id:
        raise ReceiptError(
            "T10_ITEM_MISMATCH",
            f"the binding names Item {binding.item_ref!r}, not {item_id!r}")
    if binding.run_ref != run_id:
        raise ReceiptError(
            "T10_RUN_MISMATCH",
            f"the binding names Run {binding.run_ref!r}, not {run_id!r}")
    if binding.commit_sha != receipt.commit_sha:
        raise ReceiptError(
            "T10_COMMIT_MISMATCH",
            f"the binding names commit {binding.commit_sha}, the receipt "
            f"names {receipt.commit_sha}; a receipt for another commit is not "
            "evidence for this Run")
    if binding.actor_ref != receipt.actor_ref:
        raise ReceiptError(
            "T10_ACTOR_MISMATCH",
            f"the binding names actor {binding.actor_ref!r}, the receipt was "
            f"verified for {receipt.actor_ref!r}")
    return binding
