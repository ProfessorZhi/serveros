"""The connector a Windows workstation runs. It dials out; it accepts nothing in.

Why this side exists at all
---------------------------
The Windows CloudCLI personal access token is a credential for everything
CloudCLI can do on that machine, and the machine is a corporate workstation. The
token never leaves it. What crosses the tunnel instead is a signed, closed set
of session operations, served by this process on the far end of a reverse SSH
forward that this machine owns and can drop.

Two independent gates, on purpose
---------------------------------
The Fleet surface decides whether a *caller* may drive this device. This
connector decides whether *anything* may drive the sessions on it. They are
different questions asked by different parties on different machines, and a
connector that trusted the far end's decision would be a connector whose
security is one configuration file away from being someone else's configuration
file.

So `allow_send_to` here is empty by default. Empty means every send is refused,
including from the Fleet owner. Turning it on is a deliberate edit to a file on
this machine, naming the sessions it applies to — and it can be withdrawn here,
by the person at this keyboard, without touching the other end.

Creation is a third grant, not a consequence of the second. `allow_create` is
`false` by default and independent of `allow_send_to`, because "you may talk to
a session I already have" and "you may make me a new one" are answers an
operator gives separately — and an operator who has not been asked the second
question has not answered it. There is no per-session form of this grant to
write down, because the session it would name does not exist yet.

What is not here
----------------
No `exec`. No shell. No path parameter. No generic "call any CloudCLI tool"
route. The route table in `fleet_mcp.node_protocol.ROUTES` is the whole surface,
and an unlisted route is refused *by name* before any dispatch happens, so
adding one is an edit to that tuple rather than a default a framework chose.

The grant that names no session
-------------------------------
`allow_send_to` is a list of sessions. It has one shape it cannot express, and
it is the shape the Fleet owner actually asked for: *any* session on this
machine, including one that does not exist yet. A named list cannot say that,
because it cannot name a session that has not been created — so keeping it
current means editing this file on every creation, and a list that has to be
edited to stay true stops being a record of what was authorized. The list that
was audited and the list in the file drift, silently, in the direction of
granting more than was read.

So there is a second grant here, `allow_all_sessions_for_owner`: a list of
*verbs*, empty by default, that the person at this keyboard may open for every
session on this device at once. It is separate from `allow_send_to` rather than
a wildcard entry inside it, because the two are read differently — the named
list is read against the sessions it covers, and a floor is read against the
machine. Which of the two admitted a given message is reported (`send_grant`)
and, for the floor, journalled, since a floor send is the only one that leaves
no trace in this binding.

It is not a claim about **who** the caller is. Nothing on this side can see a
remote principal: the signature proves a holder of the far end's secret, not a
person, and the Fleet host's scope-and-device decision was made on another
machine by a process this one cannot interrogate. A floor is therefore the
owner of *this* machine stating what may be driven *here* — the same kind of
statement `allow_send_to` is, made at a wider width. It is revocable by the
person who granted it, in this file, without the other end's cooperation; and
the vocabulary it can be written in is closed (`OWNER_FLOOR_VERBS`), so that
widening it is an edit to a tuple in this file rather than a word a caller or
an operator can invent. `bypassPermissions` is a CloudCLI session's own mode
and is not, and does not become, this.
"""
from __future__ import annotations

import errno
import json
import os
import stat
import sys
import time
import traceback
import urllib.parse
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import yaml

from ..errors import BAD_REQUEST, FleetRefusal
from ..identity import SessionKey
from ..local_roots import binding_candidates, refusal_if_closed_root
from ..runtime_identity import BASE_RUNTIME
from ..node_protocol import (
    PROTOCOL,
    CORE_SIGNATURE_HEADER,
    NONCE_HEADER,
    ROUTES,
    sign_core_response,
    ReplayCache,
    SignatureError,
    error_payload,
    split_path_and_query,
    verify,
)
from ..providers.cloudcli import CloudCliProvider
from ..config import ProviderBinding

SCHEMA = "serveros/fleet-node.local/v1"

# Signature failures that mean "no authenticated peer here", as opposed to a
# peer whose request was malformed (wrong device, stale, replayed).
_PEER_REJECTIONS = frozenset(
    {"PROTOCOL_MISMATCH", "MISSING_DEVICE", "MISSING_SIGNATURE", "BAD_SIGNATURE"}
)

DEFAULT_CONFIG_PATHS = binding_candidates("fleet-node.local.yaml")

# What an owner floor may be a floor *for*. Closed on purpose, because of the
# shape of the failure it prevents. `allow_send_to` names sessions, so a typo in
# it refuses a send loudly at the session it names. A floor names none, so a
# floor that silently meant nothing would refuse every send with nothing to
# point at but a binding that reads as correct — and, in the other direction, an
# operator who wrote `"*"` or `"all"` or `"root"` and got the widest grant this
# file can express would have skipped every question this gate exists to ask.
# So a word outside this tuple is refused when the binding is read, not ignored
# when a caller arrives.
#
# `"control"` is here because the order this comment demanded has been paid:
# the route (`POST /v1/sessions/{id}/interrupt` in `ROUTES`), the provider
# method (`CloudCliProvider.interrupt`) and the scope (`fleet:control`) all
# exist in this revision, so a binding that claims a control floor is claiming
# something the connector can actually honour. Adding the word before that
# would have let a binding read as if it granted a verb that had nowhere to go.
OWNER_FLOOR_VERBS: tuple[str, ...] = ("send", "control")


#: The largest Core snapshot this connector will read, mirroring the caller's
#: own cap. Enforced on BOTH ends on purpose: the connector refuses to spend the
#: memory, and the caller refuses to accept a body it did not budget for.
CORE_SNAPSHOT_MAX_BYTES = 512 * 1024


def _read_bounded_no_follow(path: Path, cap: int) -> bytes:
    """At most `cap + 1` bytes from a file that is WHAT IT SAYS IT IS.

    Every check is taken on the OPEN DESCRIPTOR rather than on the path, because
    a path is a question that can be answered differently a microsecond later.
    The descriptor cannot be swapped underneath the answer.

    The ancestor check is a comparison rather than a walk: `O_NOFOLLOW` protects
    the last component only, so a symlinked parent directory would still be
    followed. If the configured path is not its own resolved form, some
    component is a link, and this refuses instead of resolving it — the whole
    point of an owner-fixed path is that the operator chose it.
    """
    o_nofollow = getattr(os, "O_NOFOLLOW", None)
    if o_nofollow is None:
        # Windows has no O_NOFOLLOW, and this route is reached from Windows
        # connectors. On such a platform this cannot be done safely, so it is
        # refused BY NAME rather than done without the protection: a silent
        # weakening is exactly the failure this function exists to prevent.
        raise FleetRefusal(
            "UNSUPPORTED",
            f"this platform has no O_NOFOLLOW, so the Core snapshot cannot be "
            f"opened without the risk of a substituted file; the route refuses "
            f"rather than reading a path it cannot verify")
    try:
        if path.resolve() != path:
            raise FleetRefusal(
                "BAD_REQUEST",
                "the Core snapshot path does not resolve to itself, so one of "
                "its components is a symlink; it is an owner-fixed path and must "
                "be the file the operator chose")
        fd = os.open(path, os.O_RDONLY | o_nofollow | getattr(os, "O_CLOEXEC", 0)
                     | getattr(os, "O_NONBLOCK", 0))
    except FileNotFoundError:
        raise FleetRefusal("BAD_REQUEST", "no Core snapshot at the configured path") from None
    except FleetRefusal:
        raise
    except OSError as exc:
        # `ELOOP` is the kernel's own answer to a symlink under O_NOFOLLOW, and
        # it arrives here rather than as a symlink check because the check and
        # the open are now the same syscall.
        if exc.errno == getattr(errno, "ELOOP", None):
            raise FleetRefusal(
                "BAD_REQUEST",
                "the Core snapshot path is a symlink; it is an owner-fixed path "
                "and must be the file itself") from None
        raise FleetRefusal(
            "BAD_REQUEST",
            f"the Core snapshot could not be opened ({type(exc).__name__})") from None
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode):
            raise FleetRefusal("BAD_REQUEST",
                               "the Core snapshot is not a regular file")
        if info.st_uid != os.geteuid():
            # Another account's file is another account's Core. This is the
            # check that keeps a readable-by-accident path from being published
            # as this user's own reading.
            raise FleetRefusal(
                "BAD_REQUEST",
                "the Core snapshot is owned by a different OS account than the "
                "one this connector runs as")
        if info.st_mode & 0o022:
            raise FleetRefusal(
                "BAD_REQUEST",
                "the Core snapshot is readable by group or other; a Core "
                "snapshot is this account's own and must not be shared")
        chunks: list[bytes] = []
        remaining = cap + 1
        while remaining > 0:
            chunk = os.read(fd, remaining)
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
    finally:
        os.close(fd)
    raw = b"".join(chunks)
    if len(raw) > cap:
        # Refused on the BYTES READ, not on a size reported before reading: a
        # sparse file can report a length it will never deliver, and a length
        # that shrinks or grows between two calls is not a bound.
        raise FleetRefusal(
            "BAD_REQUEST",
            f"the Core snapshot is past the {cap} bytes this route carries; a "
            f"Core-Lite document is a bounded shape")
    return raw


@dataclass
class NodeConfig:
    device_ref: str
    secret_anchor: Path
    cloudcli_endpoint: str
    cloudcli_credential_anchor: Path
    bind: str = "127.0.0.1"
    port: int = 3402
    # Where THIS machine's Core writes its status snapshot. `None` is the
    # default and means "this connector has no Core source", which is reported
    # as exactly that rather than as a failed read.
    #
    # It is OWNER-FIXED, read from this file and nowhere else: no request can
    # name a path, a command or an option, so a caller cannot make a monitoring
    # surface read the machine for it. That is the whole reason the route is a
    # named constant with no parameters rather than a "run this and return the
    # output" forward.
    core_snapshot_path: Path | None = None
    connect_timeout: float = 5.0
    read_timeout: float = 45.0
    # Native session ids or full `device:<id>|<provider>|<id>` refs. Empty is
    # the default and means "no session on this machine may be sent to".
    allow_send_to: tuple[str, ...] = ()
    # A second, separate grant on this machine: may a caller *mint* a session
    # here? Off by default, and off independently of `allow_send_to` — an
    # operator who authorized messaging has not thereby authorized creation.
    # There is no per-session form of this grant to write down, because the
    # session it would name does not exist yet.
    allow_create: bool = False
    # Which projects a create may name *on this machine*. Empty is the default
    # and means none.
    #
    # `allow_create` alone answers "may a session be minted here"; it cannot
    # answer "a session on what". A project id is what decides the working tree,
    # the credentials and the reachable services the minted session inherits, so
    # the second question is the one that matters and the boolean was never an
    # answer to it. Until this field existed the floor was device-wide, which
    # `fleet/fleet-mcp-contract.yaml` recorded as a gap rather than as a
    # decision.
    #
    # Empty meaning *none* rather than *all* is the same polarity as elsewhere
    # in this system, and it is enforced rather than merely documented: a
    # binding that sets `allow_create: true` and names no project is refused at
    # load. That is a contradiction an operator can only have reached by
    # accident — "creation is on, for nothing" — and it would otherwise present
    # as a connector that accepts the request and refuses every project.
    #
    # Exact match, no wildcard, and no `"*"` spelling: an entry that means
    # "every project" would re-open the question this field closes.
    allow_create_projects: tuple[str, ...] = ()
    # A fourth grant, and the first one this machine uses to END something
    # rather than to add to it. Off by default and independent of the other
    # three: an operator who has authorized messaging and creation has still
    # not said "you may stop my work", and that is the question this flag
    # answers. It is the local floor for control — the Fleet surface has its
    # own, and a caller reaches the provider only if BOTH said yes.
    allow_control: bool = False
    # Sessions this machine permits an interrupt on, by native id or full ref.
    # Empty means "every interrupt is refused here *by name*", including from
    # the Fleet owner — the workstation's answer, given at this keyboard, and
    # withdrawable here without touching the other end. It is not the whole
    # answer any more: `allow_all_sessions_for_owner` may carry `"control"`,
    # which admits a session this list does not name. Read `control_grant`
    # rather than this list to find out which of the two admitted a given call.
    allow_control_to: tuple[str, ...] = ()
    # The mirrored `approval_answer`: may a caller answer an approval request ON
    # THIS MACHINE? Off by default and its own flag, because answering an
    # approval is a person's decision about one request, not a consequence of
    # being allowed to stop a run or send a message. A device that never wrote
    # this word refuses every approval answer, including from the Fleet owner.
    #: WHICH runtime this connector serves on this machine. A machine can host
    #: several — different OS accounts, each with its own CloudCLI and its own
    #: credential — and each runs its OWN connector, bound to its own runtime.
    #: This is not read off the wire and never taken from a request: it is what
    #: this process was started as, and it is what a signed request must name.
    runtime: str = BASE_RUNTIME
    allow_approve: bool = False
    # The mirrored `ui_open_session`: may a caller change what this machine's
    # screen shows? Off by default and separate from control, because navigation
    # is a visible side effect on somebody's desktop, not a session operation.
    allow_navigate: bool = False
    max_body_bytes: int = 1_000_000
    notes: str = ""
    # Owner-granted floors: verbs the person at this keyboard has granted for
    # EVERY session on this machine, the ones that exist and the ones that do
    # not yet. Deliberately a separate field from `allow_send_to` rather than a
    # wildcard entry inside it — see the module docstring, "The grant that names
    # no session". Empty is the default, and empty means exactly what this file
    # meant before the field existed.
    #
    # Withdrawing a floor is removing its verb here and restarting, the same
    # latency and the same place as every other binding in this system. Nothing
    # else reads this file, so nothing else can re-grant it.
    allow_all_sessions_for_owner: tuple[str, ...] = ()
    owner_access: Any = None

    def __post_init__(self):
        if self.owner_access is not None:
            from ..owner_access import OwnerAccessProfile
            if not isinstance(self.owner_access, OwnerAccessProfile):
                raise ValueError("owner_access must be a validated owner profile")
            for name in ("allow_create", "allow_control", "allow_approve", "allow_navigate"):
                object.__setattr__(self, name, True)
            object.__setattr__(self, "allow_all_sessions_for_owner", OWNER_FLOOR_VERBS)

    def secret(self) -> str | None:
        try:
            return self.secret_anchor.read_text(encoding="utf-8").strip() or None
        except OSError:
            return None

    def send_grant(self, key: SessionKey) -> str | None:
        """Which gate admits a send to `key`: `"named"`, `"owner-floor"`, `None`.

        The named list is asked first, so a session an operator wrote down stays
        attributed to that list even while a floor is open. Withdrawing a floor
        must not silently move a send that was named into the wider bucket, or
        the wider bucket's audit line would grow entries that were never the
        floor's to admit.

        `key` is always composed from *this* config's `device_ref` (see
        `Connector._key`), so neither gate can be satisfied on behalf of another
        device: a caller cannot name a foreign device into existence here.
        """
        if key.ref in self.allow_send_to or key.native_session_id in self.allow_send_to:
            return "named"
        if "send" in self.allow_all_sessions_for_owner:
            return "owner-floor"
        return None

    def send_default(self) -> str:
        """One sentence, for an operator, on a send to a session this file does
        not name. Reported rather than inferred: three distinct states, because
        "nothing is named" and "nothing may be sent" are not the same answer."""
        floor = "send" in self.allow_all_sessions_for_owner
        if floor and self.allow_send_to:
            return (
                "the sessions named in allow_send_to, and every other session on "
                "this device (owner floor: send)"
            )
        if floor:
            return "every session on this device (owner floor: send); none is named here"
        if self.allow_send_to:
            return "only the sessions named in allow_send_to"
        return "refused"

    def permits_send(self, key: SessionKey) -> bool:
        return self.send_grant(key) is not None

    def control_grant(self, key: SessionKey) -> str | None:
        """Which gate admits an interrupt of `key`: `"named"`, `"owner-floor"`, `None`.

        The exact mirror of `send_grant`, and separated from `permits_control`
        for the same reason: the two lists are read differently by an operator
        auditing them, so a call admitted by the device-wide floor must not
        appear to have been admitted by the named list.

        The named list is asked first. Withdrawing a floor must not silently
        re-bucket a session that was named, or the floor's audit line would grow
        entries that were never the floor's to admit.
        """
        if key.ref in self.allow_control_to or key.native_session_id in self.allow_control_to:
            return "named"
        if "control" in self.allow_all_sessions_for_owner:
            return "owner-floor"
        return None

    def control_default(self) -> str:
        """One sentence, for an operator, on an interrupt of a session this file
        does not name. Reported rather than inferred, and three-valued for the
        same reason `send_default` is.

        Send has no machine-wide switch — a send happens here exactly when some
        list names a session — but control does, so this method asks
        `allow_control` before the lists and answers "refused" when it is off.
        The lists are still reported beside this sentence (`control_allowed`,
        `control_allow_list`, `control_owner_floor` in `_self`), which is where
        an operator sees the case this method flattens: a device carrying a
        generous `allow_control_to` under `allow_control: false`, whose list is
        inert. That case gets the same sentence as a device that names nothing,
        because at the door it is the same answer, and a fourth branch here
        would be a second spelling of a state the fields already show.
        """
        if not self.allow_control:
            return "refused"
        floor = "control" in self.allow_all_sessions_for_owner
        if floor and self.allow_control_to:
            return (
                "the sessions named in allow_control_to, and every other session "
                "on this device (owner floor: control)"
            )
        if floor:
            return "every session on this device (owner floor: control); none is named here"
        if self.allow_control_to:
            return "only the sessions named in allow_control_to"
        return "refused"

    def permits_control(self, key: SessionKey) -> bool:
        """The one place this machine decides whether a run on it may be stopped.

        Both arms live in `control_grant` rather than inline in the handler so
        that the device-wide grant — "this operator may stop any run on this
        machine" — has exactly one seam to widen, and it is the same seam
        `send_grant` has.

        `allow_control` is still asked first and still means what it always
        meant: an operator who has not said "control happens on this machine at
        all" is not overridden by a floor. The floor names no session; the flag
        decides whether this machine ends anything. Both are needed.
        """
        if not self.allow_control:
            return False
        return self.control_grant(key) is not None

    def permits_any_control(self) -> bool:
        """Whether any interrupt at all is permitted here.

        Separate from `permits_control` because a caller asking "can this
        machine be asked to stop anything" has no session to ask about, and
        `permits_control` answers a different question about one session. Kept
        narrow on purpose: it is the flag AND (a named session OR the floor),
        so a machine with `allow_control: true` and neither list answers False
        rather than reading as an open door.
        """
        if not self.allow_control:
            return False
        return bool(self.allow_control_to) or "control" in self.allow_all_sessions_for_owner


def _owner_floors(value: Any) -> tuple[str, ...]:
    """Read the owner floor, refusing anything this file cannot mean.

    Refused at load rather than dropped at use, for the reason in
    `OWNER_FLOOR_VERBS`: the failure a floor has to be protected from is not
    "an attacker widened it" but "nobody noticed it was never on". A binding
    that says something this connector cannot honour must fail where an operator
    is watching, which is startup, not at the first caller.
    """
    if value is None:
        return ()
    if not isinstance(value, (list, tuple)):
        raise FleetRefusal(
            BAD_REQUEST,
            f"allow_all_sessions_for_owner must be a list of verbs, not a "
            f"{type(value).__name__}. It names no session, so there is no "
            f"per-session form of it to accept instead.",
        )
    unknown = [v for v in value if v not in OWNER_FLOOR_VERBS]
    if unknown:
        raise FleetRefusal(
            BAD_REQUEST,
            f"allow_all_sessions_for_owner names {unknown!r}, which is not a "
            f"floor this connector has. It knows exactly "
            f"{list(OWNER_FLOOR_VERBS)}. Refusing rather than ignoring: a floor "
            f"that silently meant nothing would look exactly like a floor that "
            f"was never needed, and this binding would still read as correct.",
        )
    # One grant repeated is still one grant; the list is read by a person, so
    # its shape is kept as written and only made hashable/stable.
    return tuple(dict.fromkeys(str(v) for v in value))


def _create_projects(value: Any) -> tuple[str, ...]:
    """Read the project floor, refusing a wildcard and refusing a bare string.

    A bare string is refused rather than wrapped, for the same reason it is in
    `_owner_floors`: `allow_create_projects: notes` is a one-character-away
    typo, and the reading that makes it a one-element list is also the reading
    that makes a mistake into a grant. Refusing means the operator writes the
    brackets.
    """
    if value is None:
        return ()
    if not isinstance(value, (list, tuple)):
        raise FleetRefusal(
            BAD_REQUEST,
            f"allow_create_projects must be a list of project ids, not a "
            f"{type(value).__name__}. A bare string is refused rather than "
            f"wrapped: this field decides which working trees a minted session "
            f"inherits, and a typo that silently became a grant is the failure "
            f"it exists to prevent.",
        )
    out: list[str] = []
    for item in value:
        if not isinstance(item, str) or not item.strip():
            raise FleetRefusal(
                BAD_REQUEST, "allow_create_projects must contain non-empty strings")
        name = item.strip()
        if name == "*":
            raise FleetRefusal(
                BAD_REQUEST,
                "allow_create_projects contains '*'. A wildcard would make the "
                "project floor meaningless — it would be `allow_create` again, "
                "under a longer name. Name the projects.",
            )
        if name not in out:
            out.append(name)
    return tuple(out)


def _flag(value: Any, *, name: str) -> bool:
    """Read a boolean binding field, refusing anything that is not a bool.

    `bool(raw.get(...))` answers True for the string `"false"`, which a binding
    rendered from a template produces without anyone intending it. On
    `allow_control` that is the wrong way to be wrong: the field opens a verb
    that ends a run, and the mistake is silent on both sides — the file reads
    as "off" to the person who wrote it and as "on" to the connector.

    YAML 1.1 already reads the unquoted spellings (`true`/`false`, and
    `yes`/`no`/`on`/`off`) as booleans, so asking for the type costs an operator
    nothing they were doing on purpose.
    """
    if value is None:
        return False
    if not isinstance(value, bool):
        raise FleetRefusal(
            BAD_REQUEST,
            f"`{name}` must be true or false, not {type(value).__name__} "
            f"({value!r}). A quoted `\"false\"` is a string, and a non-empty "
            f"string is true.",
        )
    return value


def _control_sessions(value: Any) -> tuple[str, ...]:
    """Read `allow_control_to`, refusing a wildcard and refusing a bare string.

    `allow_send_to` is read with a bare `tuple(...)` because it predates this
    file's rule about floors, and a session named there is refused loudly at the
    session it names, so a typo costs one failed send and nothing else. Control
    is not the same shape of grant: it *ends* work rather than adding to it, and
    the failure a wildcard would cause is not a refused call but a silent one.
    So this field is read the strict way from the start — the same way
    `allow_create_projects` is — rather than inheriting the older leniency by
    accident of being next to `allow_send_to` in the binding.

    A wildcard is refused here specifically because the device-wide grant
    already exists and is written elsewhere (`allow_all_sessions_for_owner:
    [control]`). Accepting `"*"` in this list would give an operator two
    spellings for one grant, and the one that reads like a session list would
    be the one that is not.
    """
    if value is None:
        return ()
    if not isinstance(value, (list, tuple)):
        raise FleetRefusal(
            BAD_REQUEST,
            f"allow_control_to must be a list of sessions, not a "
            f"{type(value).__name__}. A bare string is refused rather than "
            f"wrapped: this field authorizes ending a run, and a typo that "
            f"silently became a grant is the failure it exists to prevent.",
        )
    out: list[str] = []
    for item in value:
        if not isinstance(item, str) or not item.strip():
            raise FleetRefusal(
                BAD_REQUEST, "allow_control_to must contain non-empty strings")
        name = item.strip()
        if name == "*":
            raise FleetRefusal(
                BAD_REQUEST,
                "allow_control_to contains '*'. The device-wide control grant is "
                "written as `allow_all_sessions_for_owner: [control]`, not as a "
                "wildcard in a list of sessions. Name the sessions, or move the "
                "grant to the field that means it.",
            )
        if name not in out:
            out.append(name)
    return tuple(out)

def _core_snapshot_path(value: Any) -> Path | None:
    """The owner's Core snapshot path, or `None`, or a refusal.

    Absent means this connector has no Core source — a real state, reported as
    `NOT_CONFIGURED` rather than as a failed read. Present means it must be an
    absolute path: `~` is expanded because an operator writing their own home is
    not a mistake, but a relative path is refused because it would resolve
    against a working directory the operator did not choose.
    """
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    expanded = Path(text).expanduser()
    if not expanded.is_absolute():
        raise FleetRefusal(
            "BAD_REQUEST",
            "core_snapshot_path must be absolute; a relative path resolves "
            "against the connector's working directory, which the operator did "
            "not choose.")
    return expanded


def _runtime_tag(value: Any) -> str:
    """The runtime this connector serves, validated by the addressing authority.

    Defaults to the device's own runtime, so a binding written before a machine
    could host two means exactly what it meant then. A tag this surface could
    not address in a session reference is refused here rather than at the first
    request, because a connector that starts and can never be reached is a
    failure that looks like silence.
    """
    if value is None:
        return BASE_RUNTIME
    if not isinstance(value, str) or not value.strip():
        raise FleetRefusal(
            BAD_REQUEST,
            f"`runtime` must be a provider tag, not {type(value).__name__}",
        )
    tag = value.strip()
    try:
        SessionKey("device:validation", tag, "validation")
    except ValueError as exc:
        raise FleetRefusal(BAD_REQUEST, f"`runtime` is not a tag this surface "
                                        f"can address: {exc}") from None
    return tag


def load_node_config(path: str | Path | None = None) -> NodeConfig:
    if path is None:
        for candidate in DEFAULT_CONFIG_PATHS:
            expanded = Path(candidate).expanduser()
            if expanded.exists():
                path = expanded
                break
    if path is None:
        raise FleetRefusal(
            "BAD_REQUEST",
            "no connector binding found; looked for "
            + ", ".join(str(p) for p in DEFAULT_CONFIG_PATHS)
            + ". The connector refuses to start rather than running with no "
            "device identity.",
        )
    source = Path(str(path)).expanduser()
    closed = refusal_if_closed_root(source)
    if closed:
        raise FleetRefusal(BAD_REQUEST, closed)
    try:
        raw = yaml.safe_load(source.read_text(encoding="utf-8")) or {}
    except FileNotFoundError:
        raise FleetRefusal(BAD_REQUEST, f"connector binding not found at {source}") from None
    except yaml.YAMLError as exc:
        raise FleetRefusal(
            BAD_REQUEST, f"connector binding at {source} is not valid YAML: {exc}"
        ) from None
    if not isinstance(raw, dict):
        raise FleetRefusal("BAD_REQUEST", "connector binding must be a mapping")
    if raw.get("schema") != SCHEMA:
        raise FleetRefusal(
            "BAD_REQUEST",
            f"connector binding declares schema {raw.get('schema')!r}; this reads {SCHEMA!r}",
        )
    device_ref = str(raw.get("device_ref") or "")
    if not device_ref.startswith("device:"):
        raise FleetRefusal(
            "BAD_REQUEST",
            "`device_ref` must be this machine's typed fleet ref (`device:<id>`). "
            "A hostname would give two machines the same identity the moment one "
            "was renamed.",
        )
    from ..owner_access import parse_owner_access, OwnerAccessError
    try:
        owner_access = parse_owner_access(raw.get("owner_access"))
    except OwnerAccessError as exc:
        raise FleetRefusal(BAD_REQUEST, str(exc)) from None
    allow_create = _flag(raw.get("allow_create"), name="allow_create")
    allow_create_projects = _create_projects(raw.get("allow_create_projects"))
    if allow_create and not allow_create_projects and owner_access is None:
        # "Creation is on, for nothing" is only reachable by accident, and the
        # accident is invisible at the moment it happens: the connector starts,
        # serves, and refuses every project for a reason no operator wrote
        # down. Failing here puts it where the operator who made the edit is
        # still looking at the file.
        raise FleetRefusal(
            BAD_REQUEST,
            "`allow_create` is true and `allow_create_projects` names no project. "
            "Creation on this machine is per project as well as per device: the "
            "project id decides which working tree and which credentials a "
            "minted session inherits, so `allow_create: true` alone grants "
            "creation for nothing. Name the projects (or set `allow_create: "
            "false`).",
        )
    return NodeConfig(
        owner_access=owner_access,
        device_ref=device_ref,
        secret_anchor=Path(str(raw["secret_anchor"])).expanduser(),
        cloudcli_endpoint=str(raw["cloudcli_endpoint"]),
        cloudcli_credential_anchor=Path(str(raw["cloudcli_credential_anchor"])).expanduser(),
        bind=str(raw.get("bind", "127.0.0.1")),
        port=int(raw.get("port", 3402)),
        connect_timeout=float(raw.get("connect_timeout", 5.0)),
        read_timeout=float(raw.get("read_timeout", 45.0)),
        allow_send_to=tuple(raw.get("allow_send_to") or ()),
        allow_create=allow_create,
        allow_create_projects=allow_create_projects,
        runtime=_runtime_tag(raw.get("runtime")),
        allow_control=_flag(raw.get("allow_control"), name="allow_control"),
        allow_control_to=_control_sessions(raw.get("allow_control_to")),
        # The two flags that open `approval_answer` and `ui_open_session` on THIS
        # machine. They were declared on `NodeConfig` and read by `_native_floor`,
        # but never passed here — so both stayed at their `False` default however
        # the binding was written, and a device owner who set them got a machine
        # that silently refused every approval and every navigation while their
        # own file said the opposite. A field the loader forgets is a field no
        # operator can turn on, so this is the whole of the switch.
        allow_approve=_flag(raw.get("allow_approve"), name="allow_approve"),
        allow_navigate=_flag(raw.get("allow_navigate"), name="allow_navigate"),
        max_body_bytes=int(raw.get("max_body_bytes", 1_000_000)),
        # The Core source, if this machine has one. Read from the OWNER'S file
        # and never from a request. Refused outright unless it is an absolute
        # path: a relative one would resolve against whatever working directory
        # the connector happened to be started in, which is a path the operator
        # did not choose and cannot read off their own file.
        core_snapshot_path=_core_snapshot_path(raw.get("core_snapshot_path")),
        notes=str(raw.get("notes") or ""),
        allow_all_sessions_for_owner=_owner_floors(raw.get("allow_all_sessions_for_owner")),
    )


class Connector:
    """The closed API, over an upstream CloudCLI gateway on this machine."""

    def __init__(self, config: NodeConfig) -> None:
        self.config = config
        self.replay = ReplayCache()
        self.provider = CloudCliProvider(
            ProviderBinding(
                device_ref=config.device_ref,
                kind="cloudcli-mcp-http",
                endpoint=config.cloudcli_endpoint,
                credential_anchor=config.cloudcli_credential_anchor,
                connect_timeout=config.connect_timeout,
                read_timeout=config.read_timeout,
                # Inert here — nothing above this line reads it — but carried
                # honestly: a binding that claimed `allow_send: True` on a
                # connector that would refuse every send would be a document
                # that disagrees with the machine it describes. Either gate
                # opening makes a send possible, so either one makes this true.
                allow_send=bool(config.allow_send_to or config.allow_all_sessions_for_owner),
                allow_create=bool(config.allow_create),
                allow_control=bool(config.allow_control),
            )
        )

    # -- helpers -----------------------------------------------------------

    def _secret(self) -> str:
        secret = self.config.secret()
        if not secret:
            raise FleetRefusal(
                "BAD_REQUEST",
                f"no node secret at {self.config.secret_anchor}; this connector "
                f"cannot verify anyone and will not answer",
            )
        return secret

    def _key(self, native: str) -> SessionKey:
        return SessionKey.compose(self.config.device_ref, "claude", native)

    @staticmethod
    def _upstream_code(exc: FleetRefusal) -> tuple[int, str]:
        """Map a CloudCLI refusal onto the node protocol's closed code set."""
        mapping = {
            "PROVIDER_UNAUTHORIZED": (502, "UPSTREAM_UNAUTHORIZED"),
            "PROVIDER_UNREACHABLE": (504, "UPSTREAM_UNREACHABLE"),
            "PROVIDER_TIMEOUT": (504, "UPSTREAM_UNREACHABLE"),
            "SESSION_NOT_FOUND": (404, "SESSION_NOT_FOUND"),
            "RUN_NOT_FOUND": (404, "RUN_NOT_FOUND"),
            # This machine's own refusal, lifted out of the generic 400 on
            # purpose: it is the one failure here whose fix is an edit to a file
            # on *this* device, and a caller that saw BAD_REQUEST would go
            # looking at its own arguments.
            "WRITE_NOT_GRANTED": (403, "WRITE_NOT_GRANTED"),
        }
        status, code = mapping.get(exc.code, (400, "BAD_REQUEST"))
        return status, code

    # -- routes ------------------------------------------------------------

    def route(self, method: str, target: str, body: bytes, headers: dict[str, str]):
        """Return (status, payload). Raises nothing a caller should see raw."""
        route = f"{method} {split_path_and_query(target)[0]}"
        if not self._is_declared_route(route):
            return 404, error_payload(
                "UNKNOWN_ROUTE",
                f"{route!r} is not one of the {len(ROUTES)} routes this connector "
                f"serves. It serves exactly: {list(ROUTES)}",
            )
        try:
            device_ref = verify(
                self._secret(),
                method=method,
                path=split_path_and_query(target)[0],
                body=body,
                headers=headers,
                expected_device_ref=self.config.device_ref,
                replay=self.replay,
                expected_runtime=self.config.runtime,
            )
        except SignatureError as exc:
            # 401 is "this connector does not accept you as a peer at all"; 400
            # is "you are a peer and this particular message is not usable".
            # A caller that acted on one of these by retrying would be wrong to
            # retry the first and wrong to change its secret for the second.
            status = 401 if exc.code in _PEER_REJECTIONS else 400
            return status, error_payload(exc.code, exc.detail)

        try:
            return self._dispatch(method, target, body, device_ref)
        except FleetRefusal as exc:
            status, code = self._upstream_code(exc)
            return status, error_payload(code, exc.detail)
        except Exception as exc:  # noqa: BLE001
            traceback.print_exc(file=sys.stderr)
            return 500, error_payload("INTERNAL", f"{type(exc).__name__}: {exc}")

    def _is_declared_route(self, route: str) -> bool:
        method, _, path = route.partition(" ")
        segments = [s for s in path.split("/") if s]
        for declared in ROUTES:
            declared_method, _, declared_path = declared.partition(" ")
            if declared_method != method:
                continue
            declared_segments = [s for s in declared_path.split("/") if s]
            if len(declared_segments) != len(segments):
                continue
            # A `{...}` segment is a placeholder for exactly one path part — the
            # session id, and nothing that could carry a path of its own.
            if all(seg.startswith("{") or seg == actual
                   for seg, actual in zip(declared_segments, segments)):
                return True
        return False

    def _dispatch(self, method: str, target: str, body: bytes, device_ref: str):
        path, query = split_path_and_query(target)
        # Percent-decoded *after* verification, never before: the signature
        # covers the target as it came off the wire, so decoding first would
        # make two different byte strings with one signature.
        parts = [urllib.parse.unquote(p) for p in path.split("/") if p]
        args = urllib.parse.parse_qs(query)
        payload: dict[str, Any] = {}
        if body.strip():
            try:
                payload = json.loads(body.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise FleetRefusal("BAD_REQUEST", f"body is not JSON: {exc}") from None
            if not isinstance(payload, dict):
                raise FleetRefusal("BAD_REQUEST", "body must be a JSON object")

        if parts == ["v1", "health"]:
            return 200, self._health()
        if parts == ["v1", "self"]:
            return 200, self._self(device_ref)
        if parts == ["v1", "sessions"]:
            if method.upper() == "POST":
                return 200, self._create(payload)
            return 200, self._sessions(args)
        if len(parts) == 3 and parts[:2] == ["v1", "sessions"]:
            return 200, self.provider.get_session(self._key(parts[2]))
        if len(parts) == 4 and parts[:2] == ["v1", "sessions"] and parts[3] == "read":
            return 200, self._read(parts[2], payload)
        if len(parts) == 4 and parts[:2] == ["v1", "sessions"] and parts[3] == "send":
            return 200, self._send(parts[2], payload)
        if len(parts) == 4 and parts[:2] == ["v1", "sessions"] and parts[3] == "interrupt":
            return 200, self._interrupt(parts[2])
        if len(parts) == 3 and parts[:2] == ["v1", "runs"]:
            return 200, self._run(parts[2], args)
        if parts == ["v1", "core", "status"]:
            return 200, self._core_status()
        if parts == ["v1", "native", "call"]:
            return 200, self._native_call(payload)
        return 404, error_payload("UNKNOWN_ROUTE", f"{method} {path} is not routed")

    def _core_status(self) -> dict[str, Any]:
        """This machine's Core-Lite status, read from the owner's own file.

        The read is BOUNDED and the file is VERIFIED, and both had to be fixed:
        the first version called `read_bytes()` and then compared the length, so
        a large file was loaded in full before being refused; and it checked
        `is_symlink()`/`is_file()` in one syscall and read in another, so what it
        finally read was not necessarily what it had checked.

        So: ONE open, with `O_NOFOLLOW` and non-blocking, and every subsequent
        decision taken on THAT file descriptor. `fstat` on it — never `stat` on
        the path — decides whether it is a regular file and whose it is. The read
        stops one byte past the cap, so a file that is too large is refused
        without being read. And the configured path is compared with its own
        resolved form, which is what catches a symlinked ANCESTOR directory:
        `O_NOFOLLOW` covers the last component only.

        Refusals, each its own reason: unconfigured; a symlink or a symlinked
        ancestor; not a regular file; owned by another account; readable by
        group or other; past the cap; not JSON; not an object. The messages name
        the KIND of failure and never the path or anything read from it.
        """
        path = self.config.core_snapshot_path
        if path is None:
            return {"state": "NOT_CONFIGURED",
                    "reason": "this connector has no Core snapshot path configured"}

        raw = _read_bounded_no_follow(path, CORE_SNAPSHOT_MAX_BYTES)
        try:
            document = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise FleetRefusal("BAD_REQUEST",
                               f"the Core snapshot is not JSON: {type(exc).__name__}") from None
        if not isinstance(document, dict):
            raise FleetRefusal("BAD_REQUEST", "the Core snapshot is not a JSON object")
        return document

    def _native_call(self, payload: dict[str, Any]):
        """One mirrored CloudCLI verb, over the closed route.

        The tool is named in the SIGNED body and refused BY NAME if it is not in
        the recorded 22: there is no path, no URL, no shell and no redirect here,
        and the connector forwards a verb it did not record only by refusing it.
        `arguments` go to the local CloudCLI gateway unchanged. This device-side
        half is why a `fleet-node` machine answers a native call at all — without
        it, only a `cloudcli-mcp-http` device could.
        """
        from .. import cloudcli_native

        tool = payload.get("tool")
        arguments = payload.get("arguments") or {}
        if cloudcli_native.NATIVE_BY_NAME.get(tool) is None:
            raise FleetRefusal(
                "BAD_REQUEST",
                f"{tool!r} is not a CloudCLI tool this connector mirrors; it "
                f"serves the {cloudcli_native.NATIVE_TOOL_COUNT} recorded verbs. "
                f"A tool name that arrived off the wire is not a capability this "
                f"connector offers.",
            )
        if not isinstance(arguments, dict):
            raise FleetRefusal("BAD_REQUEST", "arguments must be a JSON object")
        # BLOCKER-2: the DEVICE's own floor, applied here on the machine, using
        # the device-LOCAL session id inside `arguments` (not the Fleet-side
        # address, which this machine never sees). Without this the route would
        # be a way around every `allow_send_to` / `allow_create` /
        # `allow_control_to` this workstation's owner wrote at this keyboard.
        self._native_floor(cloudcli_native, cloudcli_native.NATIVE_BY_NAME[tool],
                           arguments)
        # DEVICE-side approval ownership: the requestId must be one THIS machine
        # enumerates for the named session, checked here from the machine's own
        # register. The host checks the same thing on its side; both must hold,
        # and neither trusts the other's word for what the request belongs to.
        if tool == "approval_answer":
            self._verify_approval_ownership(payload, arguments)
        caller = getattr(self.provider, "native_call", None)
        if caller is None:
            raise FleetRefusal(
                "MCP_TOOL_NOT_IMPLEMENTED",
                f"this connector's provider ({getattr(self.provider, 'kind', '?')}) "
                f"does not inherit the CloudCLI native surface.",
            )
        return caller(tool, arguments)

    def _verify_approval_ownership(self, payload: dict[str, Any],
                                   arguments: dict[str, Any]) -> None:
        """The requestId must be one THIS machine enumerates for a session here.

        The proof is the SIGNED `session_ref` in the payload — the whole body is
        HMAC-signed, so it is authenticated, and it says which Fleet session the
        host checked the floor over. The MACHINE re-checks, against its OWN
        register: it enumerates `approvals_list(session=<native id>)` on the
        local CloudCLI and requires the `requestId` to be in it. So the host's
        word that a request belongs somewhere is never enough, and an answer to
        a request that belongs to another device, another session, or that has
        expired (and so left the register) is refused here.

        It is fail-closed at every step: no proof, a proof naming another
        device, a device that will not enumerate, or an id nobody enumerates all
        refuse the answer, and the answer never reaches the provider.
        """
        request_id = arguments.get("requestId")
        proof = payload.get("session_ref")
        native_id = None
        if isinstance(proof, str) and proof:
            parts = proof.split("|")
            if len(parts) == 3 and parts[0] == self.config.device_ref:
                native_id = parts[2]
        if not native_id:
            raise FleetRefusal(
                "APPROVAL_REQUIRED",
                "the approval answer carried no session proof bound to this "
                "machine, so which request it belongs to cannot be established "
                "here. An answer is not executed on a caller's word alone.",
            )
        caller = getattr(self.provider, "native_call", None)
        if caller is None:
            raise FleetRefusal(
                "APPROVAL_REQUIRED",
                "this connector's provider cannot enumerate approvals, so the "
                "request's ownership cannot be confirmed and the answer is "
                "refused.",
            )
        try:
            listing = caller("approvals_list", {"session": native_id})
        except FleetRefusal as exc:
            raise FleetRefusal(
                "APPROVAL_REQUIRED",
                f"this machine could not enumerate its approvals for the named "
                f"session ({exc.code}); a request that cannot be located is not "
                f"answered.",
            ) from None
        from .. import cloudcli_native
        if request_id not in cloudcli_native.approval_ids(listing):
            raise FleetRefusal(
                "APPROVAL_REQUIRED",
                "the request is not one this machine enumerates for the named "
                "session: it belongs to another session, another device, or has "
                "expired. An answer is executed only for a request this machine "
                "can locate.",
            )

    def _native_floor(self, native_mod, native, arguments: dict[str, Any]) -> None:
        """The DEVICE's own floor for one mirrored verb. Both ends must say yes.

        The host checks the Fleet floor; this checks the machine's, and the
        two are independent — a host credential that reaches this route has not
        thereby opened anything on this workstation. Which machine-floor applies
        is the verb's class:

          * send -> `send_grant(key)`, the same list/floor `_send` uses;
          * control / reconfigure -> `permits_control(key)`, the same one
            `_interrupt` uses — `session_start`/`session_close` land here too;
          * create -> `allow_create` AND `allow_create_projects`;
          * approve / navigate -> new default-deny flags (`allow_approve`,
            `allow_navigate`), because neither is a session operation and
            neither may ride on `allow_control`.

        The session is taken from `arguments.session` — the DEVICE-LOCAL id the
        native payload carries, which is what `_key` composes against. The host
        side is the mirror image (it uses the Fleet address), and neither may be
        skipped.
        """
        try:
            governance = native_mod.governance_for(native, arguments)
        except native_mod.NativeArgumentError as exc:
            # The ONE decision, reused on this side too: a present-but-empty
            # `stopTaskId` is refused here exactly as it is on the host.
            raise FleetRefusal("BAD_REQUEST", str(exc)) from None
        if governance == native_mod.GOV_READ:
            return
        if governance == native_mod.GOV_APPROVE:
            if not self.config.allow_approve:
                raise FleetRefusal(
                    "WRITE_NOT_GRANTED",
                    "this machine does not authorize an approval to be answered "
                    "on it. `allow_approve` is off in its own binding, and that "
                    "file lives on this machine, not on the caller's.",
                )
            return
        if governance == native_mod.GOV_NAVIGATE:
            if not self.config.allow_navigate:
                raise FleetRefusal(
                    "WRITE_NOT_GRANTED",
                    "this machine does not authorize its screen to be navigated. "
                    "`allow_navigate` is off in its own binding.",
                )
            return
        if governance == native_mod.GOV_CREATE:
            if not self.config.allow_create:
                raise FleetRefusal(
                    "WRITE_NOT_GRANTED",
                    "this machine does not authorize session creation. "
                    "`allow_create` is off in its own binding.",
                )
            project = arguments.get("project")
            if self.config.owner_access is not None:
                from ..fleet import FleetService
                FleetService._verify_owner_project(self.provider, project)
                arguments.setdefault("permissionMode", self.config.owner_access.permission_mode)
                return
            if (not isinstance(project, str) or not project.strip()
                    or project not in self.config.allow_create_projects):
                raise FleetRefusal(
                    "WRITE_NOT_GRANTED",
                    f"project {project!r} is not one this machine authorizes a "
                    f"session on. `allow_create_projects` names them, and the "
                    f"default is none.",
                )
            return
        # send / control / reconfigure: the session-bound machine floors.
        native_id = arguments.get("session")
        if not isinstance(native_id, str) or not native_id.strip():
            raise FleetRefusal(
                "BAD_REQUEST",
                "this verb addresses one session, and the call named none. The "
                "machine floor is stated over a session id and cannot be skipped "
                "by omitting it.",
            )
        key = self._key(native_id.strip())
        if governance == native_mod.GOV_SEND:
            if self.config.send_grant(key) is None:
                raise FleetRefusal(
                    "WRITE_NOT_GRANTED",
                    f"this machine is not authorized to send to {key.ref}. Its "
                    f"allow_send_to list is {list(self.config.allow_send_to)} and "
                    f"that decision lives on this machine, not on the caller's.",
                )
            return
        if not self.config.permits_control(key):
            raise FleetRefusal(
                "WRITE_NOT_GRANTED",
                f"this machine is not authorized to control {key.ref}. Its "
                f"allow_control_to list is {list(self.config.allow_control_to)} "
                f"and that decision lives on this machine, not on the caller's.",
            )

    # -- handlers ----------------------------------------------------------

    def _health(self):
        started = time.monotonic()
        health = self.provider.health()
        measured = round((time.monotonic() - started) * 1000, 1)
        return {
            "device_ref": self.config.device_ref,
            "detail": health.detail,
            "reachable": health.reachable,
            "authorized": health.authorized,
            # The provider's own measurement when it took one; otherwise the
            # wall clock around it, which includes the connector's overhead.
            "latency_ms": health.latency_ms if health.latency_ms is not None else measured,
            "measured_ms": measured,
            "upstream_error_code": health.error_code,
            "upstream": {
                "server_name": health.server_name,
                "server_version": health.server_version,
                "tool_count": health.tool_count,
            },
        }

    def _self(self, device_ref: str):
        """What this connector is, and what it will refuse.

        Reported rather than assumed: an operator asking "why can't ChatGPT
        message my laptop" needs to see that the answer is a file on the laptop.
        """
        return {
            "device_ref": self.config.device_ref,
            "authenticated_as": device_ref,
            "protocol": PROTOCOL,
            "routes": list(ROUTES),
            "control": {
                "send_allow_list": list(self.config.allow_send_to),
                # Reported beside the named list rather than folded into it, and
                # as its own key rather than as a `"*"` entry inside it: the two
                # are read differently. "May this session be sent to" is answered
                # by the list; "may anything on this machine be" is answered
                # here, and an operator auditing the first needs to be able to
                # see which of the two actually admitted a message.
                "send_owner_floor": list(self.config.allow_all_sessions_for_owner),
                "send_default": self.config.send_default(),
                # Separate from the sends above, and reported separately: the
                # two grants answer different questions, and an operator
                # reading one must not have to infer the other.
                "create_allowed": bool(self.config.allow_create),
                "create_default": "allowed" if self.config.allow_create else "refused",
                # The project half, reported beside the boolean rather than
                # folded into it. `create_allowed: true` with an empty list is
                # unreachable — the loader refuses it — but a caller asking "may
                # I create *here*" needs the projects to answer, and an operator
                # auditing a create needs to see which of the two floors
                # admitted it.
                "create_projects": list(self.config.allow_create_projects),
                # Reported beside the sends and the create above, and not folded
                # into either. "Why can't ChatGPT stop my run" has to be
                # answerable from this document alone, and an operator reading
                # `send_default` must not have to infer this one from it — the
                # two answer different questions about two different verbs.
                #
                # `control_default` is the three-valued sentence from the
                # config, not a re-derivation here: the owner floor can admit
                # an interrupt that no list names, so a two-branch expression
                # in this method would report "refused" for a device that in
                # fact interrupts everything.
                "control_allowed": bool(self.config.allow_control),
                "control_allow_list": list(self.config.allow_control_to),
                # The raw floor, exactly as `send_owner_floor` reports it and
                # for the same reason: this document says what the file says,
                # and `control_default` — the sentence below — is where the
                # reader learns whether the flag in front of it is open. Hiding
                # an inert floor here would make the two floors audit
                # differently for no gain.
                "control_owner_floor": list(self.config.allow_all_sessions_for_owner),
                "control_default": self.config.control_default(),
            },
            "upstream_endpoint": self.config.cloudcli_endpoint,
            "credential_stays_here": True,
            "notes": self.config.notes,
        }

    def _sessions(self, args: dict[str, list[str]]):
        limit = int((args.get("limit") or ["20"])[0])
        project = (args.get("project") or [None])[0]
        state = (args.get("state") or [None])[0]
        summaries = self.provider.list_sessions(limit=limit, project=project, state=state)
        return {"sessions": [s.as_dict() for s in summaries], "total": len(summaries)}

    def _read(self, native: str, payload: dict[str, Any]):
        return self.provider.read_session(
            self._key(native),
            mode=str(payload.get("mode") or "latest"),
            limit=int(payload.get("limit") or 20),
            around_id=payload.get("aroundId"),
            before=payload.get("before"),
            after=payload.get("after"),
            cursor=payload.get("cursor"),
        )

    def _create(self, payload: dict[str, Any]):
        """Mint a session here, if this machine has said that may happen.

        Two floors, and both are needed. The device floor (`allow_create`) is a
        different flag from `allow_send_to`: the two grants answer different
        questions, and an operator who has answered one has not answered the
        other. The project floor (`allow_create_projects`) is the second half,
        and it is the one that answers the question the boolean cannot: *which*
        project. A project id is what decides the working tree, the credentials
        and the reachable services a minted session inherits, so "authorized to
        create here" was never an answer to "authorized to create this".

        A missing project is refused before either floor is consulted, so the
        three refusals stay distinguishable — one is about the request, one
        about this machine, and one about the project named.
        """
        project = payload.get("project")
        if not isinstance(project, str) or not project.strip():
            raise FleetRefusal("BAD_REQUEST", "`project` is required and must be a non-empty string")
        if not self.config.allow_create:
            raise FleetRefusal(
                "WRITE_NOT_GRANTED",
                f"this connector is not authorized to create sessions. Session "
                f"creation is granted by `allow_create` in this machine's own "
                f"binding, and that file lives on this machine, not on the "
                f"caller's. It is {self.config.allow_create!r} here.",
            )
        if self.config.owner_access is not None:
            from ..fleet import FleetService
            FleetService._verify_owner_project(self.provider, project)
        elif project.strip() not in self.config.allow_create_projects:
            raise FleetRefusal(
                "WRITE_NOT_GRANTED",
                f"this connector is not authorized to create a session on "
                f"project {project.strip()!r}. Creation is granted per project "
                f"as well as per device by `allow_create_projects` in this "
                f"machine's own binding, and this project is not in it. The "
                f"list is {list(self.config.allow_create_projects)}.",
                project=project.strip(),
                allowed=list(self.config.allow_create_projects),
            )
        return self.provider.create(
            project=project,
            message=payload.get("message"),
            provider=str(payload.get("provider") or "claude"),
            model=payload.get("model"),
            lifecycle_mode=payload.get("lifecycleMode"),
            permission_mode=(payload.get("permissionMode") or self.config.owner_access.permission_mode
                             if self.config.owner_access is not None else payload.get("permissionMode")),
        )

    def _send(self, native: str, payload: dict[str, Any]):
        key = self._key(native)
        message = payload.get("message")
        if not isinstance(message, str) or not message.strip():
            raise FleetRefusal("BAD_REQUEST", "message must be a non-empty string")
        grant = self.config.send_grant(key)
        if grant is None:
            raise FleetRefusal(
                "WRITE_NOT_GRANTED",
                f"this connector is not authorized to send to {key.ref}. Its "
                f"allow_send_to list is {list(self.config.allow_send_to)} and its "
                f"owner floor is "
                f"{list(self.config.allow_all_sessions_for_owner) or 'closed'}; "
                f"both live on this machine, not on the caller's.",
            )
        if grant == "owner-floor":
            # The only send this machine admits without naming the session, and
            # therefore the only one whose admission is explained nowhere in the
            # binding. The Fleet host records the send itself, but nothing over
            # there can record that this machine had not named the session, so
            # this line is what an operator reads to answer "what went out under
            # the floor, and when it started".
            #
            # The ref is the one the request line above already carries (see
            # `_Handler.log_message`); what is added here is the *gate*, not the
            # identity. Repeated rather than left implicit so that the pair of
            # lines cannot be mismatched by two concurrent handlers: the request
            # line is interleavable, this one is self-contained. One `write` call
            # for the same reason. No message body, no header, no signature.
            sys.stderr.write(
                f"connector: send admitted by the owner floor, session not named "
                f"in this binding: {key.ref}\n"
            )
        # The caller's idempotency key is not forwarded: CloudCLI has no such
        # concept, and passing one along would imply a guarantee this hop cannot
        # make. The replay guard is the Fleet surface's, above this line.
        return self.provider.send(key, message)

    def _interrupt(self, native: str):
        """Stop the run this machine's session has in flight, if this machine said so.

        The floor is checked before the provider is touched, and it is a
        different decision from `permits_send`: an operator who authorized
        messaging this session has not thereby authorized ending its run, and a
        connector that read the send list here would be answering a question
        nobody was asked.

        No payload parameter, and that is deliberate rather than lazy. The only
        thing an interrupt may name is the session — CloudCLI's own
        `session_interrupt` takes one argument and validates it — so a handler
        that accepted a body would be opening a door with nothing behind it
        today and something behind it the first time someone added a field.
        """
        key = self._key(native)
        if not self.config.permits_control(key):
            raise FleetRefusal(
                "WRITE_NOT_GRANTED",
                f"this connector is not authorized to interrupt runs of "
                f"{key.ref}. Control is granted by `allow_control` and the "
                f"`allow_control_to` list in this machine's own binding, and "
                f"that file lives on this machine, not on the caller's. They "
                f"are {self.config.allow_control!r} and "
                f"{list(self.config.allow_control_to)} here.",
            )
        return self.provider.interrupt(key)

    def _run(self, run_id: str, args: dict[str, list[str]]):
        session = (args.get("session") or [None])[0]
        if not session:
            raise FleetRefusal("BAD_REQUEST", "`session` is required: a run id alone "
                                              "does not identify a run")
        wait = (args.get("wait") or ["0"])[0] in ("1", "true", "True")
        wait_seconds = float((args.get("waitSeconds") or ["0"])[0] or 0)
        return self.provider.get_run(self._key(session), run_id, wait=wait,
                                     wait_seconds=wait_seconds)


class _Handler(BaseHTTPRequestHandler):
    server_version = "serveros-fleet-node-connector/0.1.0"
    protocol_version = "HTTP/1.1"
    connector: Connector

    def _respond(self, status: int, payload: dict[str, Any], *,
                 sign_core: bool = False) -> None:
        raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        if sign_core:
            # The Core response is signed over the CLIENT's own request nonce, so
            # this answer is valid for that one question and no other. Signing
            # the request proved who the caller is; this proves the answer came
            # from a connector that holds the same secret AND was answering THIS
            # call — which is what stops a different service on that URL, or a
            # replayed earlier answer, from being read as this machine's Core.
            self.send_header(
                CORE_SIGNATURE_HEADER,
                sign_core_response(
                    self.connector._secret(),
                    device_ref=self.connector.config.device_ref,
                    runtime=self.connector.config.runtime,
                    request_nonce=self.headers.get(NONCE_HEADER) or "",
                    body=raw))
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(raw)

    def _handle(self) -> None:
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            length = 0
        if length > self.connector.config.max_body_bytes:
            self.close_connection = True
            self._respond(413, error_payload("BAD_REQUEST", "body too large"))
            return
        body = self.rfile.read(length) if length else b""
        headers = {k.lower(): v for k, v in self.headers.items()}
        try:
            status, payload = self.connector.route(self.command, self.path, body, headers)
        except FleetRefusal as exc:
            status, payload = 400, error_payload("BAD_REQUEST", exc.detail)
        except Exception as exc:  # noqa: BLE001
            traceback.print_exc(file=sys.stderr)
            status, payload = 500, error_payload("INTERNAL", f"{type(exc).__name__}: {exc}")
        is_core = split_path_and_query(self.path or "")[0] == "/v1/core/status"
        self._respond(status, payload, sign_core=is_core)

    do_GET = do_POST = _handle

    def log_message(self, fmt: str, *args: Any) -> None:
        # `log_request`/`log_error` hand us the whole request line, query string
        # and all. The query carries filter values the caller chose, and a log
        # line is a copy that outlives the request — so this writes the path and
        # stops. Nothing here logs a body, a header or a session id.
        path, _ = split_path_and_query(self.path or "")
        sys.stderr.write("connector: %s %s\n" % (self.command or "-", path))


def serve(config: NodeConfig, *, connector: Connector | None = None) -> ThreadingHTTPServer:
    connector = connector or Connector(config)
    handler = type("NodeHandler", (_Handler,), {"connector": connector})
    server = ThreadingHTTPServer((config.bind, config.port), handler)
    server.daemon_threads = True
    server.allow_reuse_address = True
    server.timeout = 30
    return server


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    config_path = None
    if "--config" in argv or "-c" in argv:
        flag = "--config" if "--config" in argv else "-c"
        index = argv.index(flag)
        if index + 1 >= len(argv):
            sys.stderr.write("connector: --config needs a path\n")
            return 2
        config_path = argv[index + 1]
    try:
        config = load_node_config(config_path)
    except FleetRefusal as exc:
        sys.stderr.write(f"connector: {exc.code}: {exc.detail}\n")
        return 3
    if not config.secret():
        sys.stderr.write(f"connector: no secret at {config.secret_anchor}\n")
        return 3
    if not config.cloudcli_credential_anchor.exists():
        sys.stderr.write(
            f"connector: no CloudCLI credential at {config.cloudcli_credential_anchor}\n"
        )
        return 3
    server = serve(config)
    sys.stderr.write(
        f"connector: {config.device_ref} serving {len(ROUTES)} routes on "
        f"http://{config.bind}:{config.port}\n"
        f"connector: upstream CloudCLI {config.cloudcli_endpoint}\n"
        f"connector: send allow list "
        f"{list(config.allow_send_to) or 'empty'}"
        f"; owner floor {list(config.allow_all_sessions_for_owner) or 'closed'}"
        f" — a session not named here: {config.send_default()}\n"
        f"connector: create "
        f"{'allowed for ' + str(list(config.allow_create_projects)) if config.allow_create else 'refused — allow_create is false'}\n"
        f"connector: control "
        f"{config.control_default()}\n"
    )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        sys.stderr.write("connector: interrupted\n")
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
