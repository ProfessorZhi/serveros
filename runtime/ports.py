#!/usr/bin/env python3
"""§14 · Port ownership — who owns this address:port on this node.

A read-only view. It answers one question — *who holds this endpoint* — and it
answers it by joining producers that already exist rather than by measuring
anything itself:

    containers.attribute_all()      Docker's published bindings, and who owns
                                    the container on the far side of each one
    infrastructure.host_sockets()   the one walk of /proc/net/tcp and
                                    /proc/<pid>/fd in this codebase
    infrastructure.declared_tunnels() the local endpoints a registration claims

Nothing here shells out, calls a provider API, or reads a config file. §18
gives every fact exactly one producer, and all three of these are mine to
consume, not to re-implement.

Three rules the brief states, and what each one compels:

  * NOT every network connection is a managed runtime. A listening port that no
    container and no declaration claims is reported with `runtime_ref: None` and
    `confidence: UNKNOWN` and stays that way. Absorbing it into "the managed
    runtime that probably serves it" is the name-similarity mistake §12 forbids,
    arriving through a port table.

  * A PROCESS NAME IS NEVER AN OWNERSHIP AUTHORITY. So a visible pid holding a
    socket goes into `holders` — evidence, with its provenance — and never into
    `owner_ref`. On this host that distinction is doing real work: `sshd` holds
    forwards, and `sshd` is also the name every root-owned socket on the machine
    would be attributed to if the rule were relaxed.

  * PROVIDER UNAVAILABLE IS NOT ZERO PORTS. Docker answering `permission denied`
    means the published-binding producer contributed nothing, and a view that
    returned an empty list would read as "nothing is published here". Each
    producer reports its own state in `providers`, and `coverage` counts what
    answered.

The scope of ESTABLISHED is deliberately narrow, and the exclusions are counted
rather than dropped. A connection is in scope when its LOCAL endpoint is one
this view already reports — a listening socket, a published binding, or a
declared tunnel's local end. Every other established socket on the host is
someone's traffic: it is not a binding, it says nothing about who owns a port,
and enumerating it would turn a port inventory into a connection log. The
count of what was left out is reported under `out_of_scope`, because an
exclusion a reader cannot see is indistinguishable from a producer that failed.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent


def _load(name: str, filename: str):
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, _HERE / filename)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


infrastructure = _load("serveros_runtime_infrastructure", "infrastructure.py")
containers = _load("serveros_runtime_containers", "containers.py")
graph = _load("serveros_runtime_graph", "graph.py")

FACT = infrastructure.FACT
INFERRED = infrastructure.INFERRED
UNKNOWN = infrastructure.UNKNOWN
AUTHORITATIVE = infrastructure.AUTHORITATIVE

# §14's state vocabulary. `UNKNOWN` is here as a first-class member rather than
# as an error path: an endpoint whose holder this uid cannot read is a row with
# a state, not a row that failed to be produced.
LISTENING = "LISTENING"
ESTABLISHED = "ESTABLISHED"
FORWARDED = "FORWARDED"

PORT_STATES = (LISTENING, ESTABLISHED, FORWARDED, UNKNOWN)

# The fields §14 asks for, and the order it asks for them in. Asserted on every
# row by the tests, including the rows where the value is None: an absent field
# and a null field read differently, and only one of them is a statement.
PORT_FIELDS = (
    "protocol", "local_address", "local_port", "remote_address",
    "state", "runtime_ref", "container_ref", "tunnel_ref", "owner_ref",
    "owner_type", "confidence", "observed_at", "provenance",
    # Where the answer came from and what it refused to conclude. The claims
    # are kept as a list because there may be more than one and §14 says a
    # conflict is a finding, not something to resolve.
    "claimed_by", "holders", "why",
)


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


def _endpoint(address, port, *, protocol="tcp") -> str:
    """The ref an endpoint is keyed by. Literal, never normalized.

    `0.0.0.0:8080` and `127.0.0.1:8080` are different endpoints and stay
    different rows — one accepts a connection from anywhere and the other does
    not. Normalizing them into one row would report a service as reachable on
    an address it never bound, which is the reading that stops somebody
    looking.

    An IPv6 address is bracketed, because the ref is the key an anomaly or a
    note will carry and `port:tcp::::22` cannot be read back into an address
    and a port by anybody. `_split` undoes it, so the ref round-trips.
    """
    host = f"[{address}]" if ":" in str(address) else address
    return f"{protocol}:{host}:{port}"


def _split(local: str) -> tuple[str, int] | tuple[None, None]:
    """`127.0.0.1:8080` -> `("127.0.0.1", 8080)`, else `(None, None)`.

    Strict, and the strictness is the point: a lenient parse is how a
    registration named `127.0.0.1` comes to cover a requirement named
    `127.0.0.10`.
    """
    if not isinstance(local, str) or ":" not in local:
        return None, None
    address, _, port = local.rpartition(":")
    return (address.strip("[]"), int(port)) if port.isdigit() else (None, None)


def _overlaps(left: str, right: str) -> bool:
    """Whether two endpoints could be the same socket.

    Directional in principle and symmetric here: a socket bound to
    `0.0.0.0:8080` accepts connections addressed to `127.0.0.1:8080` and not
    the reverse, so both directions are tried and either match is a match. The
    asymmetry is preserved where it matters — deciding whether a LISTENER
    covers a declared endpoint is `_serves`, one direction only.
    """
    return (infrastructure._serves(left, right)
            or infrastructure._serves(right, left))


def _wildcard_holds(address: str) -> bool:
    return infrastructure._is_wildcard(address) or not address


# ---------------------------------------------------------------------------
# Claims · one per producer, each carrying where it came from
# ---------------------------------------------------------------------------


def _docker_claims(containers_document: dict | None) -> tuple[list[dict], dict]:
    """Published bindings from Docker, one claim per host endpoint.

    A container that publishes a port has TOLD the host to hold that endpoint
    on its behalf, so the binding itself is a fact about the container and the
    owner is the container's owner. The claim records both separately, because
    the second can be UNKNOWN while the first is certain — that is the ordinary
    case for an unregistered container, and reporting the pair as one value
    would make it look like the binding was doubtful too.
    """
    if containers_document is None:
        return [], {
            "state": "UNAVAILABLE",
            "why": ("no container attribution was supplied, so nothing on this "
                    "node asked Docker what it publishes"),
        }

    alignment = _confidence_alignment()
    claims = []
    for row in containers_document.get("containers") or []:
        ports = (row.get("evidence") or {}).get("ports") or []
        if not ports:
            continue
        attribution = row.get("attribution") or {}
        owner = row.get("owner") or {}
        confidence, source_word, confidence_why = _confidence_of(
            attribution, owner, alignment=alignment)
        for binding in ports:
            host_port = binding.get("host_port")
            if not host_port:
                # Declared in the image, not published to the host. It holds no
                # host endpoint, so there is nothing here to attribute — and
                # reporting it as an unpublished row would put a port in the
                # inventory that nothing on this node can connect to.
                continue
            protocol = "udp" if str(binding.get("container", "")).endswith("/udp") else "tcp"
            address = binding.get("host_ip") or "0.0.0.0"
            claims.append({
                "source": "docker-published",
                "protocol": protocol,
                "local_address": address,
                "local_port": int(host_port),
                "state": FORWARDED,
                "runtime_ref": row.get("ref"),
                "container_ref": row.get("ref"),
                "container": row.get("container"),
                "tunnel_ref": None,
                "owner_ref": owner.get("ref"),
                "owner_type": owner.get("type"),
                # The container's attribution decides this, not this module.
                # Copying the verdict rather than forming one is the §18 rule
                # applied to an answer instead of to a measurement.
                "confidence": confidence,
                "confidence_source": source_word,
                "confidence_why": confidence_why,
                "provenance": FACT,
                "why": (
                    f"the container declares this host binding, and Docker "
                    f"resolved the container to {owner.get('ref') or 'no owner'}"
                ),
                "because": (
                    "docker inspect NetworkSettings.Ports — the container's own "
                    "declaration of what it published"
                ),
            })
    return claims, {
        "state": "AVAILABLE",
        "why": None,
        "containers": len(containers_document.get("containers") or []),
    }


def _confidence_alignment() -> dict:
    """The declared join between the two confidence vocabularies, or `{}`.

    `attribution.py` answers `high/medium/low/unknown` about its evidence
    class; §12 and §14 ask `AUTHORITATIVE/INFERRED/UNKNOWN` about whether an
    edge binds. The mapping is in `attribution-contract.yaml` so that the two
    lists meet in one declared place.

    An unreadable mapping returns `{}`, which maps EVERYTHING to UNKNOWN. That
    is the stricter direction and it is not incidental: a missing mapping must
    not let `high` become AUTHORITATIVE by being passed through unchanged.
    """
    try:
        return infrastructure.attribution.load_contract().get("confidence_alignment") or {}
    except Exception:  # noqa: BLE001 - an unreadable contract is "no mapping"
        return {}


def _confidence_of(attribution: dict, owner: dict, *, alignment: dict) -> tuple:
    """The ownership half of a claim, in §14's vocabulary, and its source word.

    A binding this host can see is a fact. Who owns the thing holding it is a
    separate question with its own answer, and §14 asks for both — collapsing
    them would promote "we know this port is taken" into "we know by whom".

    Returns `(binding_word, source_word, why)`. The source word is kept
    verbatim so that nothing is lost in translation — a reader who knows
    `medium` means a bind-mount rule can still see that, and the mapping above
    is the only thing that had to be agreed.
    """
    if not owner.get("ref"):
        return UNKNOWN, None, (
            "the container reached no owner, so neither did this binding. That "
            "is a fact about the container's attribution and not about the "
            "binding, which Docker resolved without difficulty"
        )
    source = attribution.get("confidence") or "unknown"
    mapped = alignment.get(source)
    if mapped is None:
        return UNKNOWN, source, (
            f"the container's own confidence is {source!r} and the contract's "
            f"`confidence_alignment` does not name it, so it was not translated. "
            f"That is a gap in the declaration, not a weak owner"
        )
    return mapped, source, (
        f"the container's own attribution reports {source!r}, which the "
        f"contract maps to {mapped}"
    )


def _tunnel_claims(registry_doc) -> tuple[list[dict], list[dict]]:
    """Declared tunnel local endpoints, as claims on those endpoints.

    Returns the claims and the declarations that could not become claims. A
    tunnel with no local endpoint is not dropped: it is a registration that
    says a tunnel exists and does not say where this end of it is, and a
    reader needs to be told that rather than to find a shorter list.
    """
    claims, unusable = [], []
    if registry_doc is None:
        return claims, unusable
    for declared in infrastructure.declared_tunnels(registry_doc):
        local = declared.get("local")
        address, port = _split(local)
        if address is None:
            unusable.append({
                "tunnel_ref": f"tunnel:{declared['id']}",
                "state": UNKNOWN,
                "why": (
                    f"the local endpoint {local!r} is not host:port, so this "
                    f"registration does not name a local socket"
                ),
            })
            continue
        claims.append({
            "source": "declared-tunnel",
            "protocol": "tcp",
            "local_address": address,
            "local_port": port,
            "state": FORWARDED,
            "runtime_ref": declared.get("runtime_ref"),
            "container_ref": None,
            "tunnel_ref": f"tunnel:{declared['id']}",
            "owner_ref": declared.get("owner_ref"),
            "owner_type": declared.get("owner_type"),
            # A declaration of an endpoint is not a measurement of the socket,
            # so the binding is INFERRED however authoritative its owner is.
            "confidence": INFERRED if declared.get("owner_ref") else UNKNOWN,
            "confidence_why": (
                "the registration names this endpoint and its owner; whether "
                "anything is bound to it is a separate measurement"
                if declared.get("owner_ref") else
                "the registration names this endpoint and no owner"
            ),
            "provenance": FACT,
            "why": (
                f"the tunnel registration declares this local endpoint, and "
                f"names {declared.get('owner_ref') or 'no owner'}"
            ),
            "because": "the managed-runtime registry, read through declared_tunnels",
        })
    return claims, unusable


def _socket_claims(host, *, table=None) -> tuple[list[dict], dict, int]:
    """Host sockets, as claims, plus what was excluded and why.

    LISTEN rows all become claims — a socket in LISTEN is holding an endpoint
    whether or not anybody claims it, and that is exactly the population the
    brief wants visible ("external listening port").

    ESTABLISHED rows become claims only for local endpoints already in the
    view. Everything else is counted: see the module docstring.
    """
    sockets = infrastructure.host_sockets(host, table=table)
    listening = [s for s in sockets if s["state"] == "LISTEN"]
    established = [s for s in sockets if s["state"] == "ESTABLISHED"]

    claims = []
    for socket in listening:
        address, port = _split(socket["local"])
        if address is None:
            continue
        attributed = socket["attributed"]
        claims.append({
            "source": "host-listener",
            "protocol": "tcp",
            "local_address": address,
            "local_port": port,
            "state": LISTENING,
            "runtime_ref": None,
            "container_ref": None,
            "tunnel_ref": None,
            # Deliberately None even when a pid is visible. See the module
            # docstring: a process name is not an ownership authority, and the
            # tempting shortcut here is `ps` output read as a claim.
            "owner_ref": None,
            "owner_type": None,
            # A listener makes NO ownership claim, so it carries no confidence
            # about one. `attributed` below says whether the holder was
            # readable, which is a fact about this measurement — it is not
            # promoted into an ownership confidence, because a readable pid
            # whose name looks like a project is the inference §14 forbids.
            "confidence": UNKNOWN,
            "confidence_why": (
                "a LISTEN socket is a fact about this host, but no registration "
                "and no container claims this endpoint, so who owns it is not "
                "established by its being listened on"
                if attributed else
                "the socket is held by no process this uid can read. On a host "
                "with hidepid=invisible that is what a root-owned listener "
                "looks like — Docker's port mappings, sshd, or something else "
                "entirely"
            ),
            "provenance": FACT,
            "holders": socket["holders"],
            "attributed": attributed,
            "why": (
                f"a LISTEN socket at {socket['local']} in the socket table this "
                f"uid can read"
            ),
            "because": "/proc/net/tcp, joined to /proc/<pid>/fd by inode",
        })

    return claims, {"established": established, "listening": len(listening)}, len(sockets)


# ---------------------------------------------------------------------------
# The view
# ---------------------------------------------------------------------------


def port_inventory(*, host=None, registry_doc=None, containers_document=None,
                   device_ref=None, observed_at=None, now=None,
                   containers_provider: dict | None = None) -> dict:
    """§14. Every endpoint this node holds, and what is known about who it is.

    Rows are keyed by `protocol:address:port` and never merged across endpoints.
    Claims on one endpoint from different producers are kept TOGETHER in
    `claimed_by`, and a disagreement between them is reported in `conflicts`
    with both sides intact: §14 forbids silently choosing, for the same reason
    §12 does — the disagreement is the finding.
    """
    host = infrastructure.readable_host(host)
    docker_claims, docker_state = _docker_claims(containers_document)
    if containers_provider:
        docker_state = containers_provider
    tunnel_claims, unusable_tunnels = _tunnel_claims(registry_doc)

    # The two halves of this view read the registry SEPARATELY: the container
    # attribution was handed a registry when it ran, and the tunnel
    # declarations were handed one here. Nothing forces those to be the same
    # registry, and a disagreement would show up as a port that is owned by a
    # work item through one half and unowned through the other — with no field
    # saying so. Cheap to check, and free of charge when they agree.
    registry_consistent = (
        (registry_doc is not None)
        == bool((containers_document or {}).get("registry_present", registry_doc is not None))
    )

    socket_claims, socket_notes, socket_total = _socket_claims(host)

    claims = [*docker_claims, *tunnel_claims, *socket_claims]

    by_endpoint: dict[str, list[dict]] = {}
    for claim in claims:
        key = _endpoint(claim["local_address"], claim["local_port"],
                        protocol=claim["protocol"])
        by_endpoint.setdefault(key, []).append(claim)

    # Endpoints an ESTABLISHED socket touches. Computed before the rows are
    # built, because the established pass is a second look at the same table
    # rather than a second producer.
    reported = list(by_endpoint)

    rows, conflicts = [], []
    for key, endpoint_claims in sorted(by_endpoint.items()):
        row = _row_of(key, endpoint_claims, observed_at=observed_at)
        rows.append(row)
        disagreement = _conflict_of(row, endpoint_claims)
        if disagreement:
            conflicts.append(disagreement)

    peers, excluded = _established_peers(socket_notes["established"], reported)
    for row in rows:
        key = row["port_ref"][len("port:"):]
        if key in peers:
            # The endpoint is already a row: a live connection through it is an
            # ATTRIBUTE of that row, not a second row about the same port. Two
            # rows for one endpoint would make "the port" ambiguous, which is
            # the opposite of what a port view is for.
            row["peers"] = peers[key]
            row["carrying_traffic"] = True

    rows.sort(key=lambda r: (r["protocol"], r["local_port"], r["local_address"]))

    return {
        "schema": "serveros/port-observation.v1",
        "device_ref": device_ref,
        "observed_at": observed_at,
        "ports": rows,
        "port_count": len(rows),
        "conflicts": conflicts,
        "declarations_without_an_endpoint": unusable_tunnels,
        "out_of_scope": {
            "established_sockets_not_touching_a_reported_endpoint": excluded,
            "why": (
                "a connection is not a binding. These are somebody's traffic on "
                "an endpoint nothing here is asked about; including them would "
                "make this a connection log rather than a port inventory, and "
                "would report an ordinary outbound request as a resource with "
                "an owner"
            ),
            "udp_listeners": (
                "NOT OBSERVABLE from this producer: /proc/net/tcp carries TCP "
                "only, and /proc/net/udp is not read here. A container that "
                "publishes UDP is reported from Docker's own declaration and "
                "nothing on this host corroborates it"
            ),
        },
        "providers": {
            # §14: a provider that did not answer is not a provider that
            # answered zero. Each one says which it is.
            "docker": docker_state,
            "proc": {
                "state": "AVAILABLE",
                "why": None,
                "socket_rows_read": socket_total,
                "listen_rows": socket_notes["listening"],
            },
            "registry": {
                "state": "AVAILABLE" if registry_doc is not None else "UNAVAILABLE",
                "why": None if registry_doc is not None else (
                    "no registry document was supplied, so no declaration could "
                    "claim an endpoint"
                ),
                "declared_tunnels": len(tunnel_claims) + len(unusable_tunnels),
                "consistent_with_the_container_half": registry_consistent,
                "inconsistency_because": None if registry_consistent else (
                    "the container attribution and this view were given "
                    "different registries — one of them had a registry and the "
                    "other did not. Both halves are reported as measured, and "
                    "an owner that appears on one side and not the other is an "
                    "artefact of that, not a finding"
                ),
            },
        },
        "coverage": {
            "endpoints": len(rows),
            "with_an_owner": sum(1 for r in rows if r["owner_ref"]),
            "with_an_authoritative_owner": sum(
                1 for r in rows if r["confidence"] == AUTHORITATIVE),
            "with_no_owner": sum(1 for r in rows if not r["owner_ref"]),
            "claimed_by_a_container": sum(1 for r in rows if r["container_ref"]),
            "claimed_by_a_tunnel": sum(1 for r in rows if r["tunnel_ref"]),
            "claimed_by_nothing": sum(1 for r in rows if not r["claimed_by"]),
            "conflicts": len(conflicts),
            # §14's own warning, counted: an endpoint nothing claims is the
            # population that must not be swept into "the runtime that
            # probably serves it".
            "unattributed": sum(1 for r in rows if r["confidence"] == UNKNOWN),
            "listening_with_no_visible_holder": sum(
                1 for r in rows if r.get("attributed") is False),
        },
        "note": (
            "§14: read-only. Nothing here binds, unbinds, forwards, or stops "
            "anything, and no connection was opened to measure a port. "
            "`UNKNOWN` is a state a row can have, not a row that failed."
        ),
    }


def _row_of(key: str, claims: list[dict], *, observed_at) -> dict:
    """One endpoint, with every claim on it kept.

    The row's own `owner_ref` is filled only when the claims that NAME an owner
    all name the same one. When two of them disagree it stays None and
    `conflicts` names both — a row that picked one would be a silent choice,
    which is the specific thing §14 and §12 both forbid. The claims themselves
    are never merged or dropped.

    A claim that names no owner is not a disagreement. This is the ordinary
    shape of a published port: Docker's binding names the container's owner,
    and the docker-proxy socket holding the same endpoint names nobody because
    nothing this uid can read owns it. Counting that as a conflict would report
    every published port on the machine as disputed.
    """
    first = claims[0]
    addresses = {c["local_address"] for c in claims}
    states = {c["state"] for c in claims}

    ownership_claims = [c for c in claims if c.get("owner_ref")]
    owners = {(c["owner_ref"], c.get("owner_type")) for c in ownership_claims}
    agreed_owner = owners.pop() if len(owners) == 1 else (None, None)

    # The strongest state any producer claims. FORWARDED wins over LISTENING
    # because it carries strictly more information: something is not only bound
    # here, something republishes it.
    order = (FORWARDED, ESTABLISHED, LISTENING, UNKNOWN)
    state = next((s for s in order if s in states), UNKNOWN)

    # The confidence of the ownership answer, over the claims that MADE one.
    # A listener that claims nothing must not drag a container's `high` down to
    # UNKNOWN any more than it must count as a conflict — both would be reading
    # "no claim" as "a claim of no owner".
    confidence = _weakest([c["confidence"] for c in ownership_claims])

    return {
        "port_ref": f"port:{key}",
        "protocol": first["protocol"],
        "local_address": first["local_address"],
        "local_port": first["local_port"],
        "remote_address": None,
        "state": state,
        "runtime_ref": _one_or_none({c.get("runtime_ref") for c in claims}),
        "container_ref": _one_or_none({c.get("container_ref") for c in claims}),
        "tunnel_ref": _one_or_none({c.get("tunnel_ref") for c in claims}),
        "owner_ref": agreed_owner[0],
        "owner_type": agreed_owner[1],
        "confidence": confidence,
        "observed_at": observed_at,
        # Provenance is how the endpoint was established; confidence is how far
        # the ownership answer binds. A published binding this host can see and
        # a container nobody has registered are the same FACT and different
        # confidences, and a single field cannot hold both.
        "provenance": FACT if any(c["provenance"] == FACT for c in claims) else UNKNOWN,
        "bind_address_is_wildcard": _wildcard_holds(first["local_address"]),
        "claims_disagree_on_address": len(addresses) > 1,
        "claimed_by": [
            {
                "source": c["source"],
                "state": c["state"],
                "runtime_ref": c.get("runtime_ref"),
                "container_ref": c.get("container_ref"),
                "container": c.get("container"),
                "tunnel_ref": c.get("tunnel_ref"),
                "owner_ref": c.get("owner_ref"),
                "owner_type": c.get("owner_type"),
                "confidence": c["confidence"],
                # The source vocabulary's own word, verbatim. `high` is not
                # lost by being translated: a reader who knows what a bind-mount
                # rule is can still see the class that decided it.
                "confidence_source": c.get("confidence_source"),
                "confidence_why": c.get("confidence_why"),
                "provenance": c["provenance"],
                "attributed": c.get("attributed"),
                "because": c.get("because"),
                "why": c.get("why"),
            }
            for c in sorted(claims, key=lambda c: c["source"])
        ],
        "holders": sorted({pid for c in claims for pid in (c.get("holders") or [])}),
        "attributed": all(c.get("attributed") for c in claims if c["source"] == "host-listener")
                      if any(c["source"] == "host-listener" for c in claims) else None,
        "why": (
            "no producer claims this endpoint"
            if not claims else
            "; ".join(sorted({c["why"] for c in claims if c.get("why")}))
        ),
    }


def _one_or_none(values: set):
    """The single non-null value, or None when there is not exactly one.

    An endpoint claimed by two containers is not one container's, and reporting
    either would be choosing. That the two exist is reported as a conflict.
    """
    present = {v for v in values if v}
    return present.pop() if len(present) == 1 else None


def _weakest(confidences: list[str]) -> str:
    """The confidence of a row with several claims is the weakest one.

    A row is only as attributable as its least attributable claim: if a
    declaration names an owner and a host listener on the same endpoint does
    not, the endpoint has an owner-shaped claim and no established owner.
    """
    for level in (UNKNOWN, INFERRED, AUTHORITATIVE):
        if level in confidences:
            return level
    return UNKNOWN


def _conflict_of(row: dict, claims: list[dict]) -> dict | None:
    """Two producers claiming one endpoint for DIFFERENT owners.

    Kept as a finding. The conflicting answers are named individually so a
    reader can go and look at both, and the row's own `owner_ref` is None —
    the view does not choose, and §14 says so.

    Only claims that name an owner can conflict. See `_row_of`: a claim of
    nothing is not a claim of somebody else.
    """
    owners = sorted({
        (c["owner_ref"], c.get("source"))
        for c in claims if c.get("owner_ref")
    })
    distinct = {owner for owner, _ in owners}
    if len(distinct) < 2:
        return None
    return {
        "port_ref": row["port_ref"],
        "state": "CONFLICT",
        "claims": [
            {"source": source, "owner_ref": owner} for owner, source in owners
        ],
        "resolved": False,
        "because": (
            "two producers claim this endpoint for different owners. This view "
            "does not choose between them: either answer on its own would read "
            "as an attributed port, and the disagreement is the thing a reader "
            "needs to know about"
        ),
    }


def _established_peers(established: list[dict], reported: list[str]) -> tuple[dict, int]:
    """Live peers per reported endpoint, and how many sockets were left out.

    In scope when the local endpoint is one already reported. Everything else
    is counted, never silently dropped — see the module docstring. The remote
    address is what §14 means by "remote_address if meaningful": for a binding
    it is not (the bind address is the whole answer), and for a connection
    through a binding it is the only new fact the row gains.
    """
    index: dict[str, list[str]] = {}
    for key in reported:
        protocol, _, endpoint = key.partition(":")
        index.setdefault(protocol, []).append(endpoint)

    peers: dict[str, set[str]] = {}
    excluded = 0
    for socket in established:
        address, port = _split(socket["local"])
        remote = socket["remote"]
        if address is None:
            excluded += 1
            continue
        endpoint = f"{address}:{port}"
        if not any(_overlaps(endpoint, known) for known in index.get("tcp", ())):
            excluded += 1
            continue
        if not remote or remote.startswith("0.0.0.0:0") or remote.endswith(":0"):
            continue
        key = _endpoint(address, port)
        peers.setdefault(key, set()).add(remote)
    return {key: sorted(value) for key, value in peers.items()}, excluded
