"""The serverosd #272 trusted-parent read, mapped onto Work Engine's seam.

WHY THIS MODULE EXISTS
----------------------
Work Engine #14's `OwnerVerifiedLease` refuses to write an owner unless a
`LeaseAuthority` verified one. serveros #272 added exactly that read:
`agent.lease.authority.verify` resolves the context in-process, reads the
kernel `SO_PEERCRED` peer on the socket, and re-checks the live grant's
`delegated_by`, project, Item, actions, revocation, window and epoch. It mints
no grant, acquires no lease and mutates nothing.

This module is the CLIENT ADAPTER between that read and `LeaseGrant`. It is
deliberately narrow:

  * `grant_ref` is bound at construction. The adapter cannot be asked to verify
    some other grant, and it never accepts an owner, `os_user`, action list,
    epoch or token from its caller.
  * The only caller inputs are `context_ref`, and the exact action/project/Item
    being asked about. Those are not authority: the serverosd read returns
    `verified: True` only after checking them against the live grant.
  * The `items`/`actions` fields on the returned `LeaseGrant` are the exact
    values serverosd just verified, not a list the adapter invents. A grant
    that lacks `lease.acquire` (or `lease.renew`/`lease.release`/`work.commit`)
    produces `POLICY_DENIED`, not a silent grant.
  * An old grant with no epoch produces `INVALID_CONTEXT`; the adapter does
    not backfill one.

The process-boundary rule is load-bearing: `UnixSocketVerifyClient` must
connect from the SAME process that will call `OwnerVerifiedLease`. The
serverosd peer is then that process's kernel uid, and `OwnerVerifiedLease`'s
`os_principal == uid:<os.getuid()>` check binds the returned grant back to it.
A proxy process would change the peer and must be refused; the adapter does not
paper over that.

No CLI verb and no MCP tool is registered here. Until an owner-approved parent
binds this adapter, the production status remains `BLOCKED_TRUST_SOURCE`.
"""
from __future__ import annotations

import datetime
import json
import socket
from pathlib import Path
from typing import Protocol

from .authority import AuthorityError, LeaseGrant


class VerifyClient(Protocol):
    """The one read the adapter needs. The production client is below."""

    def verify(self, context_ref: str, *, grant_ref: str, action: str,
               project: str, item_ref: str) -> dict: ...


class UnixSocketVerifyClient:
    """A newline-JSON client for serverosd's `agent.lease.authority.verify`.

    The socket path is REQUIRED and must be absolute. There is no default and
    no environment fallback: a default would let a test or a caller silently
    reach a production daemon it did not name, which is the opposite of the
    explicit parent this seam requires.
    """

    METHOD = "agent.lease.authority.verify"
    MAX_RESPONSE = 1 << 20

    def __init__(self, socket_path: str | Path, *, timeout: float = 5.0) -> None:
        path = Path(socket_path)
        if not path.is_absolute():
            raise ValueError("the serverosd verify socket path must be absolute")
        self.socket_path = path
        self.timeout = float(timeout)

    def verify(self, context_ref: str, *, grant_ref: str, action: str,
               project: str, item_ref: str) -> dict:
        if not str(context_ref or "").strip():
            raise AuthorityError(
                "UNTRUSTED_ACTOR",
                "no context_ref was presented; the serverosd read resolves the "
                "context out of band and the caller's word is not one")
        if not str(grant_ref or "").strip():
            raise AuthorityError(
                "INVALID_CONTEXT", "no grant_ref was bound to this verify read")
        request = {
            "method": self.METHOD,
            "params": {
                "context_ref": str(context_ref),
                "grant_ref": str(grant_ref),
                "action": str(action),
                "project": str(project),
                "item_ref": str(item_ref),
            },
        }
        connection = None
        try:
            connection = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            connection.settimeout(self.timeout)
            connection.connect(str(self.socket_path))
            connection.sendall((json.dumps(request) + "\n").encode())
            buffer = b""
            while b"\n" not in buffer:
                chunk = connection.recv(65536)
                if not chunk:
                    break
                buffer += chunk
                if len(buffer) > self.MAX_RESPONSE:
                    raise AuthorityError(
                        "UNTRUSTED_ACTOR",
                        "the serverosd verify answer exceeded the 1 MiB bound")
        except AuthorityError:
            raise
        except OSError as exc:
            # Missing socket, refused connection, timeout: the parent is not
            # reachable. That is BLOCKED_TRUST_SOURCE, not a caller refusal and
            # not a reason to fall back to an unverified owner.
            raise AuthorityError(
                "BLOCKED_TRUST_SOURCE",
                f"serverosd verify socket {self.socket_path} did not answer: "
                f"{type(exc).__name__}") from exc
        finally:
            if connection is not None:
                try:
                    connection.close()
                except OSError:
                    pass
        line = buffer.split(b"\n", 1)[0]
        try:
            answer = json.loads(line.decode())
        except (ValueError, UnicodeDecodeError) as exc:
            raise AuthorityError(
                "UNTRUSTED_ACTOR",
                "the serverosd verify socket returned no JSON object") from exc
        if not isinstance(answer, dict):
            raise AuthorityError(
                "UNTRUSTED_ACTOR",
                "the serverosd verify answer was not an object")
        if answer.get("ok") is not True:
            error = answer.get("error")
            error = error if isinstance(error, dict) else {}
            raise AuthorityError(
                str(error.get("code") or "UNTRUSTED_ACTOR"),
                str(error.get("message") or "serverosd refused the verify read"))
        result = answer.get("result")
        if not isinstance(result, dict):
            raise AuthorityError(
                "UNTRUSTED_ACTOR",
                "the serverosd verify answer carried no result object")
        return result


class ServerOSLeaseAuthority:
    """`LeaseAuthority` backed by the serverosd #272 verified read.

    `grant_ref` is fixed here, not per call. A caller that could choose the
    grant could choose one it was not delegated; a parent binds the one grant
    it provisioned for this lane.
    """

    def __init__(self, client: VerifyClient, *, grant_ref: str) -> None:
        if not str(grant_ref or "").strip():
            raise ValueError("a grant_ref must be bound to the authority")
        self._client = client
        self._grant_ref = str(grant_ref).strip()

    def verify(self, context_ref: str, *, action: str, project: str,
               item_ref: str, now: float) -> LeaseGrant:
        answer = self._client.verify(
            context_ref, grant_ref=self._grant_ref, action=action,
            project=project, item_ref=item_ref)
        if not isinstance(answer, dict):
            raise AuthorityError(
                "UNTRUSTED_ACTOR", "the verify client returned no object")
        if answer.get("verified") is not True:
            raise AuthorityError(
                str(answer.get("reason") or "UNTRUSTED_ACTOR"),
                str(answer.get("because") or "the trusted parent refused the "
                    "verify read"))
        grant = answer.get("grant")
        if not isinstance(grant, dict):
            raise AuthorityError(
                "UNTRUSTED_ACTOR",
                "the verified answer carried no grant; a positive flag with no "
                "record is not an identity")
        if str(grant.get("grant_ref") or "") != self._grant_ref:
            raise AuthorityError(
                "INVALID_CONTEXT",
                "the verified grant is not the grant this authority bound")
        try:
            principal = str(grant["principal"]).strip()
            os_principal = str(grant["os_principal"]).strip()
            epoch = grant["epoch"]
            if type(epoch) is not int or epoch < 1:
                raise ValueError("epoch is not a positive integer")
            valid_from = _instant(grant["valid_from"])
            expires_at = _instant(grant["expires_at"])
        except (KeyError, TypeError, ValueError) as exc:
            raise AuthorityError(
                "UNTRUSTED_ACTOR",
                "the verified grant is not usable: principal, os_principal, "
                "epoch and a readable window are all required") from exc
        if not principal or not os_principal:
            raise AuthorityError(
                "UNTRUSTED_ACTOR",
                "the verified grant carries no principal or OS principal")
        # serverosd returned verified=True only after checking THIS action,
        # project and Item against the live grant. Projecting those exact
        # values is the verified subset, not a new grant: a missing action
        # would have been POLICY_DENIED above and never reached this line.
        return LeaseGrant(
            principal=principal,
            grant_ref=self._grant_ref,
            project=str(project),
            items=(str(item_ref),),
            actions=(str(action),),
            valid_from=valid_from,
            expires_at=expires_at,
            revoked=bool(grant.get("revoked", False)),
            epoch=epoch,
            os_principal=os_principal,
        )


def _instant(value) -> float:
    """Parse the ISO instant serverosd records into an epoch float."""
    if isinstance(value, bool):
        raise ValueError("a boolean is not an instant")
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    return datetime.datetime.fromisoformat(text).timestamp()
