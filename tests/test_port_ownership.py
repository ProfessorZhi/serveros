#!/usr/bin/env python3
"""§14 · Port ownership.

The question is narrow on purpose: WHO holds this address:port on this node.
The tests are organised around the four ways that question gets answered
wrongly, all of which are attractive:

  * A PROCESS NAME BECOMES THE OWNER. `ss -lptn` prints `sshd` next to a port,
    and every reasonable person reads that as "sshd owns it". §14 forbids it,
    so a visible pid goes into `holders` and never into `owner_ref`, and
    `test_a_process_name_is_never_an_ownership_authority` asserts the module
    does not read a command line at all.

  * A PROVIDER THAT DID NOT ANSWER BECOMES ZERO PORTS. Docker failing is not
    Docker answering "nothing is published". Each producer reports its own
    state, and the tests assert an unavailable provider is distinguishable from
    an idle one.

  * EVERY CONNECTION BECOMES A MANAGED RESOURCE. So ESTABLISHED is scoped to
    local endpoints this view already holds, and the excluded ones are COUNTED
    rather than dropped — an exclusion a reader cannot see is indistinguishable
    from a producer that failed.

  * A CONFLICT GETS RESOLVED. Two producers naming different owners is kept as
    a finding, and the row's own owner is left empty. A claim of NOTHING is not
    a claim of somebody else, which is the distinction that stops every
    published port on this host being reported as disputed.
"""
from __future__ import annotations

import importlib.util
import re
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
RUNTIME = REPO / "runtime"


def _load(name: str, filename: str):
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, RUNTIME / filename)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


ports = _load("serveros_runtime_ports", "ports.py")
infrastructure = _load("serveros_runtime_infrastructure", "infrastructure.py")
registry = _load("serveros_runtime_registry", "registry.py")

FACT = ports.FACT
INFERRED = ports.INFERRED
UNKNOWN = ports.UNKNOWN
AUTHORITATIVE = ports.AUTHORITATIVE

BRIEF_FIELDS = (
    "protocol", "local_address", "local_port", "remote_address", "state",
    "runtime_ref", "container_ref", "tunnel_ref", "owner_ref", "confidence",
    "observed_at", "provenance",
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


class FakeHost:
    """A synthetic /proc. Files by absolute path; sockets by pid."""

    name = "fake-host"

    def __init__(self, files=None, fds=None, uptime=1000.0):
        self.files = files or {}
        self.fds = fds or {}
        self._uptime = uptime

    def read_text(self, path):
        return self.files.get(path)

    def read_bytes(self, path):
        value = self.files.get(path)
        return value.encode() if isinstance(value, str) else None

    def listdir(self, path):
        if path in self.fds:
            return list(self.fds[path])
        if path == "/proc":
            return list(self._proc_entries())
        return []

    def _proc_entries(self):
        for path in list(self.files) + list(self.fds):
            parts = path.split("/")
            if len(parts) > 2 and parts[1] == "proc" and parts[2].isdigit():
                yield parts[2]

    def readlink(self, path):
        return self.fds.get(path)

    def run(self, args, timeout=8.0):
        return 127, "", "not found"

    def uptime(self):
        return self._uptime

    def page_size(self):
        return 4096

    def uid_name(self, uid):
        return f"uid{uid}"


def stat_line(*, pid, comm, state="S", ppid=1, starttime=0):
    values = {n: 0 for n in range(3, 25)}
    values.update({3: state, 4: ppid, 18: 20, 20: 1, 22: starttime})
    return f"{pid} ({comm}) " + " ".join(str(values[n]) for n in range(3, 25)) + "\n"


def status_line(uid=1006, name="svc"):
    return f"Name:\t{name}\nUid:\t{uid}\t{uid}\t{uid}\t{uid}\n"


def net_tcp(entries):
    """(inode, local_hex, remote_hex, state) quads -> a /proc/net/tcp body."""
    lines = ["  sl  local_address rem_address   st tx_queue rx_queue tr tm->when "
             "retrnsmt   uid  timeout inode"]
    for inode, local_hex, remote_hex, state in entries:
        lines.append(f"  0: {local_hex} {remote_hex} {state} 00000000:00000000 "
                     f"00:00000000 00000000     0        0 {inode} 1 0000000000000000 100 0 0 10 0")
    return "\n".join(lines) + "\n"


LISTEN = "0A"
ESTABLISHED = "01"


def no_sockets():
    return FakeHost()


def a_listener(local_hex, *, inode="111", holder=None, pid=500):
    """A host with exactly one LISTEN socket, optionally held by a visible pid.

    The holder's command line says `litellm` on purpose: if any code path ever
    starts reading argv to guess an owner, this fixture is what catches it.
    """
    files = {"/proc/net/tcp": net_tcp([(inode, local_hex, "00000000:0000", LISTEN)])}
    fds = {}
    if holder:
        files[f"/proc/{pid}/stat"] = stat_line(pid=pid, comm="svc")
        files[f"/proc/{pid}/cmdline"] = "litellm\x00--port\x008080\x00"
        files[f"/proc/{pid}/status"] = status_line()
        fds[f"/proc/{pid}/fd"] = ["3"]
        fds[f"/proc/{pid}/fd/3"] = f"socket:[{inode}]"
    return FakeHost(files=files, fds=fds)


def container_row(ref="runtime:container:abc123abc123", *, owner_ref=None,
                  owner_type=None, confidence="unknown", container="svc",
                  ports=None):
    return {
        "ref": ref,
        "container": container,
        "owner": {"ref": owner_ref, "type": owner_type, "status": INFERRED},
        "attribution": {"confidence": confidence, "provenance": "registry-match"},
        "evidence": {"ports": ports or []},
    }


def containers_document(*rows, registry_present=True):
    return {
        "registry_present": registry_present,
        "containers": list(rows),
        "count": len(rows),
    }


def binding(host_port, *, host_ip="127.0.0.1", container="8080/tcp"):
    return {"container": container, "host_ip": host_ip, "host_port": str(host_port)}


def tunnel_record(rid="tun-1", *, local="127.0.0.1:9000", **kw):
    record = {
        "id": rid,
        "kind": "tunnel",
        "owner": {"type": kw.pop("owner_type", "work-item"),
                  "ref": kw.pop("owner_ref", "work-item:LIT-0104")},
        "scope": "user",
        "lifecycle": {"class": kw.pop("lifecycle", "task-scoped")},
        "desired": {"state": "running"},
        "protection": {"level": "normal"},
        "transport": {"class": "ssh", "local": local, "remote": "far.example:80"},
        "probes": [
            {"id": "t-chan", "kind": "socket-connect", "axis": "transport",
             "path": "/run/t.sock"},
            {"id": "t-func", "kind": "http-health", "axis": "functional",
             "url": "https://far.example/"},
        ],
    }
    record.update(kw)
    return record


def registry_of(*records):
    return registry.Registry(document={
        "schema": "serveros/managed-runtime-registry.v1",
        "resources": list(records),
    })


def view(**kw):
    kw.setdefault("host", no_sockets())
    kw.setdefault("observed_at", "2026-09-14T05:00:00+00:00")
    return ports.port_inventory(**kw)


def row_for(document, port_ref):
    return next(r for r in document["ports"] if r["port_ref"] == port_ref)


# ---------------------------------------------------------------------------
# The field set
# ---------------------------------------------------------------------------


def test_the_field_list_contains_every_field_the_brief_names():
    missing = [f for f in BRIEF_FIELDS if f not in ports.PORT_FIELDS]
    assert missing == [], missing


def test_a_row_carries_every_field_even_the_empty_ones():
    document = view(containers_document=containers_document())
    assert document["ports"] == []
    document = view(host=a_listener("0100007F:2328"),
                    containers_document=containers_document())
    row = document["ports"][0]
    missing = [f for f in ports.PORT_FIELDS if f not in row]
    assert missing == [], missing


def test_the_state_vocabulary_is_the_briefs_list():
    assert set(ports.PORT_STATES) == {"LISTENING", "ESTABLISHED", "FORWARDED", UNKNOWN}


# ---------------------------------------------------------------------------
# Docker published ports
# ---------------------------------------------------------------------------


def test_a_published_port_reaches_the_containers_owner():
    document = view(containers_document=containers_document(container_row(
        owner_ref="work-item:LIT-0104", owner_type="work-item", confidence="high",
        ports=[binding(26400)],
    )))
    row = row_for(document, "port:tcp:127.0.0.1:26400")
    assert row["state"] == ports.FORWARDED
    assert row["owner_ref"] == "work-item:LIT-0104"
    assert row["owner_type"] == "work-item"
    assert row["confidence"] == AUTHORITATIVE
    assert row["runtime_ref"] == "runtime:container:abc123abc123"
    assert row["container_ref"] == "runtime:container:abc123abc123"
    assert row["provenance"] == FACT


def test_a_published_binding_is_a_fact_and_its_owner_is_a_separate_answer():
    """The pair that a single confidence field cannot hold.

    Docker resolved this binding without difficulty; nobody has registered the
    container that published it. Reporting the row as one confidence would
    either doubt the binding or promote the owner.
    """
    document = view(containers_document=containers_document(container_row(
        ports=[binding(3000, host_ip="0.0.0.0")])))
    row = row_for(document, "port:tcp:0.0.0.0:3000")
    assert row["provenance"] == FACT
    assert row["confidence"] == UNKNOWN
    assert row["owner_ref"] is None
    assert row["container_ref"] == "runtime:container:abc123abc123"
    assert "no owner" in row["claimed_by"][0]["why"]


def test_a_binding_declared_in_the_image_but_not_published_is_not_a_host_port():
    """`expose` is not `publish`. Reporting it would put a port in the
    inventory that nothing on this node can connect to."""
    document = view(containers_document=containers_document(container_row(
        owner_ref="work-item:LIT-0104", owner_type="work-item", confidence="high",
        ports=[{"container": "9000/tcp", "host_ip": None, "host_port": None}],
    )))
    assert document["ports"] == []


def test_a_container_that_publishes_nothing_contributes_nothing():
    document = view(containers_document=containers_document(
        container_row(owner_ref="work-item:LIT-0104", owner_type="work-item")))
    assert document["ports"] == []
    assert document["providers"]["docker"]["state"] == "AVAILABLE"


# ---------------------------------------------------------------------------
# The two confidence vocabularies
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("source,expected", [
    ("high", AUTHORITATIVE),
    ("medium", INFERRED),
    ("low", INFERRED),
    ("unknown", UNKNOWN),
])
def test_the_contract_translates_between_the_two_confidence_vocabularies(source, expected):
    alignment = ports._confidence_alignment()
    assert alignment[source] == expected


def test_a_confidence_word_the_contract_does_not_name_is_not_passed_through():
    """The strict direction. `very-high` is a word somebody invented, and
    returning it unchanged would put it in a field whose vocabulary is closed."""
    word, source, why = ports._confidence_of(
        {"confidence": "very-high"},
        {"ref": "work-item:LIT-0104", "type": "work-item"},
        alignment={},
    )
    assert word == UNKNOWN
    assert source == "very-high", "the source word is kept, not discarded"
    assert "confidence_alignment" in why


def test_an_unreadable_mapping_does_not_promote_high_to_authoritative(monkeypatch):
    monkeypatch.setattr(ports, "_confidence_alignment", lambda: {})
    document = view(containers_document=containers_document(container_row(
        owner_ref="work-item:LIT-0104", owner_type="work-item", confidence="high",
        ports=[binding(26400)],
    )))
    row = row_for(document, "port:tcp:127.0.0.1:26400")
    assert row["owner_ref"] == "work-item:LIT-0104", "the owner is still reported"
    assert row["confidence"] == UNKNOWN, "but the strength of the claim is not assumed"


def test_the_source_word_survives_the_translation():
    """A reader who knows what a bind-mount rule is must still be able to see
    which class decided the owner."""
    document = view(containers_document=containers_document(container_row(
        owner_ref="project:litellm", owner_type="project", confidence="medium",
        ports=[binding(8080)])))
    claim = row_for(document, "port:tcp:127.0.0.1:8080")["claimed_by"][0]
    assert claim["confidence"] == INFERRED
    assert claim["confidence_source"] == "medium"
    assert "medium" in claim["confidence_why"]


# ---------------------------------------------------------------------------
# Host listeners · a pid is evidence, never an owner
# ---------------------------------------------------------------------------


def test_an_external_listening_port_is_reported_with_no_owner():
    document = view(host=a_listener("0100007F:2328"), containers_document=containers_document())
    row = document["ports"][0]
    assert row["port_ref"] == "port:tcp:127.0.0.1:9000"
    assert row["state"] == ports.LISTENING
    assert row["owner_ref"] is None
    assert row["confidence"] == UNKNOWN
    assert row["runtime_ref"] is None
    assert row["container_ref"] is None


def test_a_listener_held_by_no_visible_process_is_still_a_row():
    """`attributed: false` is a fact about the measurement, not a reason to
    drop the endpoint. Most listeners on this host are root-owned."""
    document = view(host=a_listener("0100007F:2328"), containers_document=containers_document())
    row = document["ports"][0]
    assert row["attributed"] is False
    assert row["holders"] == []
    assert "hidepid" in row["claimed_by"][0]["confidence_why"]


def test_a_pid_holding_a_socket_is_evidence_and_never_an_owner():
    document = view(host=a_listener("0100007F:2328", holder=True),
                    containers_document=containers_document())
    row = document["ports"][0]
    assert row["holders"] == [500]
    assert row["attributed"] is True
    assert row["owner_ref"] is None
    assert row["confidence"] == UNKNOWN


def test_a_process_name_is_never_an_ownership_authority():
    """The host in this fixture runs a process whose command line says
    `litellm`, on a port no registration claims. Reading the argv would make it
    ours.

    Asserted mechanically as well as behaviourally: the module is not allowed
    to open /proc/<pid>/cmdline at all, because a rule that is only a habit is
    one edit away from being gone.
    """
    document = view(host=a_listener("0100007F:2328", holder=True),
                    containers_document=containers_document())
    row = document["ports"][0]
    assert row["owner_ref"] is None

    source = (RUNTIME / "ports.py").read_text()
    for forbidden in ("cmdline", "argv", "process_name", "docker ps", "read_text("):
        assert forbidden not in source, (
            f"ports.py mentions {forbidden!r}. Every port fact must come from a "
            f"producer that already owns it — a command line is the source of "
            f"the process-name-as-owner mistake §14 names, and reading /proc "
            f"here would be the second socket walk `host_sockets` exists to "
            f"prevent."
        )


def test_the_port_view_spawns_nothing_and_connects_to_nothing():
    source = (RUNTIME / "ports.py").read_text()
    for forbidden in ("subprocess", "socket.socket", ".connect(", "os.kill", "popen"):
        assert forbidden not in source, forbidden


# ---------------------------------------------------------------------------
# Declared tunnels
# ---------------------------------------------------------------------------


def test_a_tunnels_local_endpoint_is_attributed_to_its_registration():
    document = view(containers_document=containers_document(),
                    registry_doc=registry_of(tunnel_record()))
    row = row_for(document, "port:tcp:127.0.0.1:9000")
    assert row["state"] == ports.FORWARDED
    assert row["tunnel_ref"] == "tunnel:tun-1"
    assert row["owner_ref"] == "work-item:LIT-0104"
    assert row["confidence"] == INFERRED, (
        "a declaration names the endpoint; whether anything is bound to it is "
        "a measurement this view did not take")


def test_a_tunnel_that_names_no_endpoint_is_reported_rather_than_dropped():
    document = view(containers_document=containers_document(),
                    registry_doc=registry_of(tunnel_record(local="not-an-endpoint")))
    assert document["ports"] == []
    unusable = document["declarations_without_an_endpoint"]
    assert [u["tunnel_ref"] for u in unusable] == ["tunnel:tun-1"]
    assert "not-an-endpoint" in unusable[0]["why"]


def test_a_tunnel_with_no_registry_is_reported_as_an_unavailable_producer():
    document = view(containers_document=containers_document())
    assert document["providers"]["registry"]["state"] == "UNAVAILABLE"
    assert document["providers"]["registry"]["why"]


# ---------------------------------------------------------------------------
# Conflicts · kept, never resolved
# ---------------------------------------------------------------------------


def test_two_producers_naming_different_owners_is_a_conflict_that_is_not_resolved():
    document = view(
        containers_document=containers_document(container_row(
            owner_ref="project:litellm", owner_type="project", confidence="high",
            ports=[binding(9000, host_ip="127.0.0.1")])),
        registry_doc=registry_of(tunnel_record(local="127.0.0.1:9000")),
    )
    row = row_for(document, "port:tcp:127.0.0.1:9000")
    assert row["owner_ref"] is None, "the view does not choose"
    assert len(row["claimed_by"]) == 2
    assert len(document["conflicts"]) == 1
    conflict = document["conflicts"][0]
    assert conflict["port_ref"] == "port:tcp:127.0.0.1:9000"
    assert conflict["resolved"] is False
    assert {c["owner_ref"] for c in conflict["claims"]} == {
        "project:litellm", "work-item:LIT-0104"}


def test_a_claim_of_nothing_is_not_a_claim_of_somebody_else():
    """The ordinary shape of a published port on this host: Docker names the
    container's owner, and the docker-proxy socket holding the same endpoint
    names nobody. Counting that as a conflict would report every published
    port on the machine as disputed."""
    document = view(
        host=a_listener("0100007F:6720"),  # 127.0.0.1:26400
        containers_document=containers_document(container_row(
            owner_ref="work-item:LIT-0104", owner_type="work-item", confidence="high",
            ports=[binding(26400)])),
    )
    row = row_for(document, "port:tcp:127.0.0.1:26400")
    assert document["conflicts"] == []
    assert row["owner_ref"] == "work-item:LIT-0104"
    assert row["confidence"] == AUTHORITATIVE, (
        "a listener that makes no ownership claim must not drag the container's "
        "confidence down; that would be reading 'no claim' as 'no owner'")
    assert len(row["claimed_by"]) == 2


def test_the_row_adopts_the_strongest_state_any_producer_claims():
    """FORWARDED over LISTENING: something is not only bound here, something
    republishes it."""
    document = view(
        host=a_listener("0100007F:6720"),
        containers_document=containers_document(container_row(
            ports=[binding(26400)])),
    )
    row = row_for(document, "port:tcp:127.0.0.1:26400")
    assert row["state"] == ports.FORWARDED
    assert {c["state"] for c in row["claimed_by"]} == {ports.FORWARDED, ports.LISTENING}


# ---------------------------------------------------------------------------
# Provider availability is not zero
# ---------------------------------------------------------------------------


def test_a_provider_that_did_not_answer_is_not_a_provider_that_answered_zero():
    document = view(containers_provider={"state": "UNAVAILABLE",
                                        "why": "docker: permission denied"},
                    containers_document=None)
    assert document["providers"]["docker"]["state"] == "UNAVAILABLE"
    assert "permission denied" in document["providers"]["docker"]["why"]
    assert document["coverage"]["claimed_by_a_container"] == 0
    assert document["coverage"]["endpoints"] == 0


def test_no_container_attribution_at_all_says_so_rather_than_reporting_none_published():
    document = view(containers_document=None)
    assert document["providers"]["docker"]["state"] == "UNAVAILABLE"
    assert "Docker" in document["providers"]["docker"]["why"]


def test_an_unavailable_docker_still_produces_the_host_half():
    document = view(host=a_listener("0100007F:2328"), containers_document=None)
    assert document["port_count"] == 1
    assert document["providers"]["proc"]["state"] == "AVAILABLE"
    assert document["providers"]["docker"]["state"] == "UNAVAILABLE"


def test_the_registry_halves_are_checked_for_agreement():
    """The container attribution and this view are handed registries
    separately. Nothing forces them to be the same one, and a disagreement
    would show up as an owner on one half and not the other with no field
    saying so."""
    agreed = view(containers_document=containers_document(registry_present=True),
                  registry_doc=registry_of(tunnel_record()))
    assert agreed["providers"]["registry"]["consistent_with_the_container_half"] is True
    assert agreed["providers"]["registry"]["inconsistency_because"] is None

    split = view(containers_document=containers_document(registry_present=False),
                 registry_doc=registry_of(tunnel_record()))
    registry_state = split["providers"]["registry"]
    assert registry_state["consistent_with_the_container_half"] is False
    assert "different registries" in registry_state["inconsistency_because"]


def test_the_udp_gap_is_declared_rather_than_left_to_be_discovered():
    document = view(containers_document=containers_document())
    assert "NOT OBSERVABLE" in document["out_of_scope"]["udp_listeners"]


# ---------------------------------------------------------------------------
# Endpoints are literal, and the ref round-trips
# ---------------------------------------------------------------------------


def test_a_wildcard_bind_and_a_loopback_bind_are_different_endpoints():
    """One accepts a connection from anywhere and the other does not.
    Normalizing them would report a service as reachable on an address it
    never bound."""
    document = view(containers_document=containers_document(container_row(
        ports=[{"container": "3000/tcp", "host_ip": "0.0.0.0", "host_port": "3000"},
               {"container": "3000/tcp", "host_ip": "127.0.0.1", "host_port": "3000"}])))
    refs = {r["port_ref"] for r in document["ports"]}
    assert refs == {"port:tcp:0.0.0.0:3000", "port:tcp:127.0.0.1:3000"}
    for row in document["ports"]:
        assert row["local_port"] == 3000


def test_a_wildcard_row_says_so():
    document = view(containers_document=containers_document(container_row(
        ports=[binding(3000, host_ip="0.0.0.0")])))
    assert row_for(document, "port:tcp:0.0.0.0:3000")["bind_address_is_wildcard"] is True
    document = view(containers_document=containers_document(container_row(
        ports=[binding(3000)])))
    assert row_for(document, "port:tcp:127.0.0.1:3000")["bind_address_is_wildcard"] is False


@pytest.mark.parametrize("address,key,ref", [
    ("127.0.0.1", "tcp:127.0.0.1:8080", "port:tcp:127.0.0.1:8080"),
    ("0.0.0.0", "tcp:0.0.0.0:8080", "port:tcp:0.0.0.0:8080"),
    ("::", "tcp:[::]:8080", "port:tcp:[::]:8080"),
    ("fe80::1", "tcp:[fe80::1]:8080", "port:tcp:[fe80::1]:8080"),
])
def test_an_ipv6_address_round_trips_through_its_ref(address, key, ref):
    """The ref is the key an anomaly or a note will carry. `port:tcp::::8080`
    cannot be read back into an address and a port by anybody, and a ref a
    reader cannot take apart is one they cannot go and act on."""
    assert ports._endpoint(address, 8080) == key
    assert f"port:{ports._endpoint(address, 8080)}" == ref
    # The row's own ref round-trips back into the address and port it names.
    assert ports._split(ref[len("port:tcp:"):]) == (address, 8080)


def test_an_ipv6_wildcard_is_its_own_endpoint():
    document = view(containers_document=containers_document(container_row(
        ports=[{"container": "3000/tcp", "host_ip": "::", "host_port": "3000"}])))
    assert row_for(document, "port:tcp:[::]:3000")["local_address"] == "::"


# ---------------------------------------------------------------------------
# ESTABLISHED · scoped, and the exclusion is counted
# ---------------------------------------------------------------------------


def test_a_connection_through_a_reported_endpoint_is_a_peer_of_that_row():
    host = FakeHost(files={
        "/proc/net/tcp": net_tcp([
            ("111", "0100007F:6720", "00000000:0000", LISTEN),
            ("222", "0100007F:6720", "0100007F:4000", ESTABLISHED),
        ]),
    })
    document = view(host=host, containers_document=containers_document(container_row(
        owner_ref="work-item:LIT-0104", owner_type="work-item", confidence="high",
        ports=[binding(26400)])))
    row = row_for(document, "port:tcp:127.0.0.1:26400")
    assert row["peers"] == ["127.0.0.1:16384"]
    assert row["carrying_traffic"] is True
    assert row["state"] == ports.FORWARDED


def test_a_connection_elsewhere_is_counted_as_out_of_scope_not_reported():
    """A connection is not a binding. Enumerating them would turn a port
    inventory into a connection log, and an ordinary outbound request into a
    resource with an owner."""
    host = FakeHost(files={
        "/proc/net/tcp": net_tcp([
            ("111", "0100007F:9C40", "5DB8D822:01BB", ESTABLISHED),
            ("222", "0A000005:9C41", "5DB8D822:01BB", ESTABLISHED),
        ]),
    })
    document = view(host=host, containers_document=containers_document())
    assert document["ports"] == []
    assert document["out_of_scope"][
        "established_sockets_not_touching_a_reported_endpoint"] == 2
    assert document["out_of_scope"]["why"]


def test_the_out_of_scope_count_is_zero_when_nothing_was_excluded():
    """A count that only ever rises is one nobody reads."""
    document = view(host=no_sockets(), containers_document=containers_document())
    assert document["out_of_scope"][
        "established_sockets_not_touching_a_reported_endpoint"] == 0


def test_a_connection_never_becomes_a_row_of_its_own():
    host = FakeHost(files={
        "/proc/net/tcp": net_tcp([
            ("111", "0100007F:6720", "00000000:0000", LISTEN),
            ("222", "0100007F:6720", "0100007F:4000", ESTABLISHED),
        ]),
    })
    document = view(host=host, containers_document=containers_document())
    assert [r["port_ref"] for r in document["ports"]] == ["port:tcp:127.0.0.1:26400"]
    assert all(r["state"] != ports.ESTABLISHED for r in document["ports"])


# ---------------------------------------------------------------------------
# The shape of the answer
# ---------------------------------------------------------------------------


def test_the_coverage_block_counts_what_the_answer_could_not_settle():
    document = view(host=a_listener("0100007F:2328"),
                    containers_document=containers_document(container_row(
                        owner_ref="work-item:LIT-0104", owner_type="work-item",
                        confidence="high", ports=[binding(26400)])))
    coverage = document["coverage"]
    assert coverage["endpoints"] == 2
    assert coverage["with_an_owner"] == 1
    assert coverage["with_an_authoritative_owner"] == 1
    assert coverage["unattributed"] == 1
    assert coverage["listening_with_no_visible_holder"] == 1
    assert coverage["conflicts"] == 0


def test_an_empty_host_reports_zero_and_says_this_was_an_observation():
    document = view(host=no_sockets(), containers_document=containers_document())
    assert document["schema"] == "serveros/port-observation.v1"
    assert document["port_count"] == 0
    assert document["ports"] == []
    assert "read-only" in document["note"]


def test_the_observed_at_stamp_is_the_callers_and_is_on_every_row():
    document = view(
        host=a_listener("0100007F:2328"),
        containers_document=containers_document(container_row(
            owner_ref="work-item:LIT-0104", owner_type="work-item", confidence="high",
            ports=[binding(26400)])),
        observed_at="2026-09-14T05:00:00+00:00")
    assert {r["observed_at"] for r in document["ports"]} == {"2026-09-14T05:00:00+00:00"}


def test_the_observation_logs_a_gap_it_cannot_close():
    document = view(containers_document=containers_document(), registry_doc=None)
    assert document["providers"]["registry"]["state"] == "UNAVAILABLE"
    assert document["providers"]["proc"]["state"] == "AVAILABLE"


def test_the_module_has_no_writing_entry_point():
    """§26's gate for this surface: there is nothing here to call."""
    exported = {name for name in dir(ports) if not name.startswith("_")}
    for forbidden in ("bind", "unbind", "forward", "stop", "kill", "remove",
                      "prune", "close_socket", "connect"):
        assert forbidden not in exported, forbidden
