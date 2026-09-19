#!/usr/bin/env python3
"""§12 — the runtime/resource graph.

Hermetic. Every record here is a dict produced by `containers.attribute()`,
which is itself a pure function over an inspect record and a registry index.
The graph is built from those records and never from its own fixtures, so a
test cannot pass because the fixture and the code agree about a shape neither
of them sees on the real host.

That rule bit back while writing this file, which is why the refs below come
from `record["ref"]` instead of being spelled out. `evidence()` reads
`(record["Id"])[:12]`, so a fixture handing in a 64-character id gets a
12-character ref back, and every expectation written against the long form was
wrong in the same direction. The first version of this file encoded the
mistake fourteen times; the fix is to ask the producer rather than to assume.

The three things this file exists to prevent, each with the test that pins it:

  * An owner edge drawn from a NAME. Two containers called `litellm-1` and
    `litellm-2` are two containers. See `test_a_similar_name_never_produces_an_owner`.

  * A disagreement resolved silently. When two sources name different owners,
    both edges survive and the pair is reported. See
    `test_conflicting_owner_evidence_keeps_both_edges`.

  * A stale edge that reads as a current one. See
    `test_an_edge_older_than_its_window_is_stale_not_absent`.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

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


graph = _load("serveros_runtime_graph", "graph.py")
cont = _load("serveros_runtime_containers", "containers.py")

T0 = "2026-09-14T03:00:00+00:00"
DEVICE = "device:server-beijing"

REGISTRY = {
    "registry_present": True,
    "device_ref": DEVICE,
    **cont.build_registry_index({
        "device_ref": DEVICE,
        "resources": [
            {
                "id": "lit0104-dev-litellm",
                "kind": "container",
                "owner": {"type": "work-item", "ref": "work-item:LIT-0104"},
                "protection": {"level": "protected"},
                "lifecycle": {"class": "task-scoped"},
                "probes": [{"container": "lit-0104-dev100-litellm"}],
            },
        ],
        "protected_refs": [],
        "defer_refs": [],
    }),
    "owner_label_namespaces": {},
    "recognised_namespaces": {},
}


def inspect_record(name, *, cid, labels=None, mounts=None, ports=None,
                   networks=None, state="running"):
    """A `docker inspect` record, trimmed to the keys this code reads."""
    return {
        "Id": cid,
        "Name": f"/{name}",
        "Created": "2026-09-11T03:26:28.916759338Z",
        "State": {"Status": state, "StartedAt": "2026-09-11T03:26:29Z",
                  "ExitCode": 0},
        "Image": "sha256:" + "a" * 64,
        "Config": {"Image": "litellm:test", "Labels": dict(labels or {})},
        "Mounts": list(mounts or []),
        "NetworkSettings": {
            "Ports": dict(ports or {}),
            "Networks": {n: {} for n in (networks or [])},
        },
    }


def one_record(**kwargs) -> dict:
    """One attributed container, from the real producer."""
    return cont.attribute(
        cont.evidence(inspect_record(**kwargs)),
        registry=REGISTRY, observed_at=T0,
    )


def evidence_for(**kwargs) -> dict:
    return cont.evidence(inspect_record(**kwargs))


def graph_of(*records, evidences=None, **kwargs) -> dict:
    return graph.resource_graph(
        {"containers": list(records), "device_ref": DEVICE},
        evidences=evidences, observed_at=T0, now=T0, **kwargs,
    )


def refs_by_relationship(document, relationship):
    return sorted(
        (e["source"], e["value"]) for e in document["edges"]
        if e["relationship"] == relationship
    )


def node(document, ref):
    hits = [n for n in document["nodes"] if n["ref"] == ref]
    assert len(hits) == 1, f"expected exactly one node for {ref}, got {len(hits)}"
    return hits[0]


# -- the shape ---------------------------------------------------------------

def test_every_edge_carries_the_five_fields_12_asks_for():
    """§12's edge contract, asserted over every edge rather than a sample.

    A sample would let a new edge producer ship missing `freshness`, and the
    failure would be a region of the graph where a stale edge looks current.
    """
    doc = graph_of(one_record(name="lit-0104-dev100-litellm", cid="b" * 64))
    assert doc["edges"], "the fixture produced no edges at all"
    for e in doc["edges"]:
        for field in ("relationship", "confidence", "observed_at", "freshness",
                      "source", "value", "status"):
            assert field in e, f"{e['relationship']} edge has no {field}"
        assert e["confidence"] in graph.CONFIDENCE_LEVELS
        assert e["freshness"]["state"] in ("FRESH", "STALE", "UNKNOWN")
        assert e["relationship"] in graph.RELATIONSHIPS


def test_an_unknown_relationship_is_refused_rather_than_drawn():
    """An edge nothing can interpret is a string, not a finding."""
    try:
        graph.edge("kind-of-owns", "a", "b", provenance=graph.FACT,
                   confidence=graph.AUTHORITATIVE)
    except ValueError as exc:
        assert "not a relationship" in str(exc)
    else:
        raise AssertionError("an unknown relationship was accepted")


def test_an_unknown_confidence_is_refused():
    try:
        graph.edge("owned-by", "a", "b", provenance=graph.FACT,
                   confidence="probably")
    except ValueError as exc:
        assert "not a confidence level" in str(exc)
    else:
        raise AssertionError("an unknown confidence was accepted")


def test_provenance_and_confidence_are_not_the_same_field():
    """The distinction the module exists to keep: a FACT can carry an
    INFERRED edge, and collapsing the two would force one to be wrong."""
    mounts = [{"Type": "bind", "Name": None, "Source": "/home/hwz",
               "Destination": "/home/hwz", "Mode": "rw"}]
    record = one_record(name="xpra", cid="d" * 64, mounts=mounts)
    doc = graph_of(record, evidences=[evidence_for(name="xpra", cid="d" * 64,
                                                   mounts=mounts)])
    edge = [e for e in doc["edges"] if e["relationship"] == "binds-to"][0]
    assert edge["status"] == graph.FACT
    assert edge["confidence"] == graph.INFERRED


# -- device -> runtime -------------------------------------------------------

def test_the_device_hosts_the_runtime_it_observed():
    record = one_record(name="lit-0104-dev100-litellm", cid="b" * 64)
    doc = graph_of(record)
    assert refs_by_relationship(doc, "hosts") == [(DEVICE, record["ref"])]
    edge = [e for e in doc["edges"] if e["relationship"] == "hosts"][0]
    assert edge["status"] == graph.FACT
    assert edge["confidence"] == graph.AUTHORITATIVE


def test_a_container_declaring_another_device_keeps_both_edges():
    """§12: an identity conflict is preserved, not resolved."""
    record = one_record(
        name="elsewhere", cid="c" * 64,
        labels={"serveros.device_ref": "device:some-other-host"},
    )
    doc = graph_of(record)
    assert refs_by_relationship(doc, "hosts") == sorted([
        (DEVICE, record["ref"]),
        ("device:some-other-host", record["ref"]),
    ])
    conflict = [c for c in doc["conflicts"] if c["relationship"] == "hosts"]
    assert len(conflict) == 1
    assert conflict[0]["candidates"] == [DEVICE, "device:some-other-host"]
    assert "does not choose" in conflict[0]["why"]
    node(doc, "device:some-other-host")


def test_a_declared_device_that_agrees_is_not_a_conflict():
    record = one_record(
        name="agreeing", cid="c" * 64,
        labels={"serveros.device_ref": DEVICE},
    )
    doc = graph_of(record)
    assert refs_by_relationship(doc, "hosts") == [(DEVICE, record["ref"])]
    assert doc["conflicts"] == []


# -- runtime -> provider resource --------------------------------------------

def test_a_named_volume_is_an_authoritative_edge():
    """The provider's own structured API named the volume. That is the bar."""
    record = one_record(
        name="lit-0104-dev100-litellm", cid="b" * 64,
        mounts=[{"Type": "volume", "Name": "lit-0104-dev100-observerdata",
                 "Source": "/var/lib/docker/volumes/x/_data",
                 "Destination": "/var/lib/lit0104-observer", "Mode": "rw"}],
    )
    doc = graph_of(record, evidences=[evidence_for(
        name="lit-0104-dev100-litellm", cid="b" * 64,
        mounts=[{"Type": "volume", "Name": "lit-0104-dev100-observerdata",
                 "Source": "/var/lib/docker/volumes/x/_data",
                 "Destination": "/var/lib/lit0104-observer", "Mode": "rw"}],
    )])
    mounts = [e for e in doc["edges"] if e["relationship"] == "mounts"]
    assert [e["value"] for e in mounts] == ["volume:lit-0104-dev100-observerdata"]
    assert mounts[0]["confidence"] == graph.AUTHORITATIVE
    assert mounts[0]["status"] == graph.FACT


def test_a_bind_mount_is_inferred_because_a_path_is_not_an_identity():
    record = one_record(
        name="xpra", cid="d" * 64,
        mounts=[{"Type": "bind", "Name": None, "Source": "/home/hwz",
                 "Destination": "/home/hwz", "Mode": "rw"}],
    )
    doc = graph_of(record, evidences=[evidence_for(
        name="xpra", cid="d" * 64,
        mounts=[{"Type": "bind", "Name": None, "Source": "/home/hwz",
                 "Destination": "/home/hwz", "Mode": "rw"}],
    )])
    binds = [e for e in doc["edges"] if e["relationship"] == "binds-to"]
    assert [e["value"] for e in binds] == ["path:/home/hwz"]
    assert binds[0]["confidence"] == graph.INFERRED
    assert binds[0]["status"] == graph.FACT, "the fact is the provider's"
    assert "not a resource identity" in binds[0]["why"]
    assert node(doc, "path:/home/hwz")["kind"] == "host-path"


def test_a_volume_is_drawn_once_even_when_two_inputs_name_it():
    """The structured mount and the record's `resource_refs` overlap.

    Counting it twice would make the edge count a measure of how many lists a
    volume appeared in rather than how many resources the runtime holds.
    """
    mounts = [{"Type": "volume", "Name": "v1", "Source": "/x",
               "Destination": "/y", "Mode": "rw"}]
    record = one_record(name="lit-0104-dev100-litellm", cid="b" * 64,
                        mounts=mounts)
    assert len(record["runtime"]["resource_refs"]) == 1, "fixture assumption"
    doc = graph_of(record, evidences=[evidence_for(
        name="lit-0104-dev100-litellm", cid="b" * 64, mounts=mounts)])
    assert refs_by_relationship(doc, "mounts") == [(record["ref"], "volume:v1")]


def test_the_fallback_path_still_draws_a_volume_without_raw_evidence():
    """The record's `resource_refs` is the only source when the caller has no
    evidence, and then a volume name IS authoritative — it came from the
    provider, just through the attribution rather than the mount list."""
    record = one_record(
        name="lit-0104-dev100-litellm", cid="b" * 64,
        mounts=[{"Type": "volume", "Name": "v1", "Source": "/x",
                 "Destination": "/y", "Mode": "rw"}],
    )
    doc = graph_of(record)  # no evidences
    mounts = [e for e in doc["edges"] if e["relationship"] == "mounts"]
    assert [e["value"] for e in mounts] == ["volume:v1"]
    assert mounts[0]["because" if "because" in mounts[0] else "why"] == \
        "mounted at /y"
    assert mounts[0]["confidence"] == graph.AUTHORITATIVE


def test_a_network_is_an_authoritative_edge():
    record = one_record(
        name="lit-0104-dev100-litellm", cid="b" * 64,
        networks=["lit-0104-dev100-internal"],
    )
    doc = graph_of(record)
    assert refs_by_relationship(doc, "attached-to") == [
        (record["ref"], "network:lit-0104-dev100-internal"),
    ]


def test_a_container_with_no_mount_evidence_says_so():
    """`no mounts` and `nobody passed the mounts` are different sentences."""
    record = one_record(name="bare", cid="e" * 64)
    doc = graph_of(record)  # no `evidences=` at all
    coverage = doc["coverage"]
    assert coverage["mount_evidence_supplied"] == 0
    assert coverage["mount_evidence_absent_for"] == [record["ref"]]
    with_evidence = graph_of(
        record, evidences=[evidence_for(name="bare", cid="e" * 64)],
    )
    assert with_evidence["coverage"]["mount_evidence_absent_for"] == []
    assert with_evidence["coverage"]["mount_evidence_supplied"] == 1


# -- runtime -> owner --------------------------------------------------------

def test_a_registered_container_has_an_owner_edge():
    record = one_record(name="lit-0104-dev100-litellm", cid="b" * 64)
    doc = graph_of(record)
    assert refs_by_relationship(doc, "owned-by") == [
        (record["ref"], "work-item:LIT-0104"),
    ]
    assert node(doc, "work-item:LIT-0104")["kind"] == "owner"
    assert node(doc, "work-item:LIT-0104")["owner_type"] == "work-item"


def test_a_missing_owner_produces_no_edge_at_all():
    """§12's `missing owner`. An edge to an unknown owner is a present-looking
    answer where there is only an absence."""
    record = one_record(name="mystery", cid="f" * 64)
    assert record["owner"]["ref"] is None, "fixture assumption"
    doc = graph_of(record)
    assert refs_by_relationship(doc, "owned-by") == []
    assert node(doc, record["ref"])["owner"]["value"] is None
    assert node(doc, record["ref"])["owner"]["status"] == graph.UNKNOWN
    assert "absence" in node(doc, record["ref"])["owner"]["why"]
    assert doc["coverage"]["runtimes_without_owner_edge"] == [record["ref"]]


def test_a_similar_name_never_produces_an_owner():
    """§12's forbidden rule, as a property of the real producer.

    `lit-0104-dev100-litellm` is registered. `litellm-1` is not, and shares a
    word with it. Nothing about that resemblance may reach the owner field.
    """
    registered = one_record(name="lit-0104-dev100-litellm", cid="b" * 64)
    stranger = one_record(name="litellm-1", cid="2" * 64)
    assert stranger["owner"]["ref"] is None, "fixture assumption"
    doc = graph_of(registered, stranger)
    owned = {e["source"]: e["value"] for e in doc["edges"]
             if e["relationship"] == "owned-by"}
    assert owned == {registered["ref"]: "work-item:LIT-0104"}
    assert stranger["ref"] not in owned
    for e in doc["edges"]:
        assert "similar" not in (e.get("rule") or ""), (
            "an edge cites name similarity as its rule"
        )


def test_conflicting_owner_evidence_keeps_both_edges():
    """§12: `保留 conflict。不要 silently choose one。`

    The registry attributes a runtime by name to one work item and the
    tunnel's own declaration names another. Both are written down; neither
    wins, and both edges are INFERRED because a conflicted edge is not
    authoritative however sure either source is.
    """
    record = one_record(name="lit-0104-dev100-litellm", cid="b" * 64)
    doc = graph_of(record, tunnels=[{
        "tunnel_ref": "tunnel:shared",
        "runtime_ref": record["ref"],
        "owner_ref": "work-item:LIT-0104",
        "ownership_confidence": "AUTHORITATIVE",
        "provider": "sshd",
        "owner_conflicts": [{"owner_ref": "work-item:LIT-0999",
                             "source": "the container's own label"}],
    }])
    conflict = [c for c in doc["conflicts"] if c["relationship"] == "owned-by"]
    assert len(conflict) == 1
    assert conflict[0]["candidates"] == ["work-item:LIT-0104", "work-item:LIT-0999"]
    assert "does not choose" in conflict[0]["why"]
    assert sorted(e["value"] for e in doc["edges"]
                  if e["source"] == "tunnel:shared"
                  and e["relationship"] == "owned-by") == [
        "work-item:LIT-0104", "work-item:LIT-0999"]
    for e in doc["edges"]:
        if e["source"] == "tunnel:shared" and e["relationship"] == "owned-by":
            assert e["confidence"] == graph.INFERRED


def test_a_disagreement_is_never_counted_as_agreement():
    """Two sources naming the SAME owner is not a conflict."""
    record = one_record(name="lit-0104-dev100-litellm", cid="b" * 64)
    doc = graph_of(record, tunnels=[{
        "tunnel_ref": "tunnel:agreed",
        "runtime_ref": record["ref"],
        "owner_ref": "work-item:LIT-0104",
        "ownership_confidence": "AUTHORITATIVE",
        "owner_conflicts": [{"owner_ref": "work-item:LIT-0104"}],
    }])
    assert [c for c in doc["conflicts"] if c["relationship"] == "owned-by"] == []
    assert refs_by_relationship(doc, "owned-by") == [
        (record["ref"], "work-item:LIT-0104"),
        ("tunnel:agreed", "work-item:LIT-0104"),
    ]


def test_a_tunnel_with_no_owner_gets_a_missing_owner_link_not_an_edge():
    doc = graph.resource_graph(
        {"containers": [], "device_ref": DEVICE}, observed_at=T0, now=T0,
        tunnels=[{"tunnel_ref": "tunnel:orphan-lead", "runtime_ref": None,
                  "provider": "sshd", "owner_ref": None,
                  "ownership_confidence": "UNKNOWN"}],
    )
    assert refs_by_relationship(doc, "owned-by") == []
    assert node(doc, "tunnel:orphan-lead")["owner"]["status"] == graph.UNKNOWN


# -- freshness ---------------------------------------------------------------

def test_an_edge_older_than_its_window_is_stale_not_absent():
    """§12's `stale edge`. The edge survives; its freshness does the talking."""
    record = one_record(name="lit-0104-dev100-litellm", cid="b" * 64)
    doc = graph.resource_graph(
        {"containers": [record], "device_ref": DEVICE},
        observed_at="2026-09-14T02:00:00+00:00",
        now="2026-09-14T03:00:00+00:00",
    )
    assert doc["edges"], "a stale edge was dropped instead of aged"
    for e in doc["edges"]:
        assert e["freshness"]["state"] == "STALE"
        assert e["freshness"]["age_seconds"] == 3600.0
    assert refs_by_relationship(doc, "owned-by") != []


def test_a_reader_with_no_clock_gets_unknown_freshness_not_fresh():
    doc = graph.resource_graph(
        {"containers": [one_record(name="x", cid="9" * 64)], "device_ref": DEVICE},
        observed_at=T0, now=None,
    )
    assert doc["edges"]
    for e in doc["edges"]:
        assert e["freshness"]["state"] == "UNKNOWN"
        assert "no clock" in e["freshness"]["why"]


def test_a_source_with_no_declared_window_is_unknown_not_defaulted():
    assert graph.freshness_of(T0, now=T0, source="a-source-nobody-declared") == {
        "state": "UNKNOWN", "age_seconds": None, "within_seconds": None,
        "observed_at": T0,
        "why": ("no freshness window is declared for evidence from "
                "a-source-nobody-declared, so its age is not measurable "
                "against a policy"),
    }


def test_an_unparseable_clock_blames_the_clock_and_not_the_observation():
    """Found by using it. `_seconds_between` returns None when EITHER side
    fails, and the message named `observed_at` for both — so a caller passing
    a display string as `now` was told its observations were malformed and
    would go and fix the end that was already correct.

    In production this read as four registrations reporting "nobody looked" on
    a node where everything was running, because the argument was
    `2026-09-20 01:25:00 CST` and the parser takes ISO-8601.
    """
    result = graph.freshness_of(T0, now="2026-09-20 01:25:00 CST",
                                source="registry")
    assert result["state"] == "UNKNOWN"
    assert "the clock supplied" in result["why"]
    assert repr(T0) not in result["why"]
    # ...and the other direction still names the observation, which is what
    # makes the two distinguishable rather than a swap.
    other = graph.freshness_of("not a time", now=T0, source="registry")
    assert other["state"] == "UNKNOWN"
    assert "the observation's timestamp" in other["why"]


def test_an_unparseable_timestamp_is_unknown_and_never_zero():
    """An age of zero reads as FRESH, which is the most misleading answer
    this function could give."""
    result = graph.freshness_of("last Tuesday", now=T0, source="docker-inspect")
    assert result["state"] == "UNKNOWN"
    assert result["age_seconds"] is None
    assert "does not parse" in result["why"]


def test_a_naive_and_an_aware_time_are_not_subtracted():
    """Python raises. Guessing a zone would shift an age by hours."""
    result = graph.freshness_of("2026-09-14T03:00:00", now=T0,
                                source="docker-inspect")
    assert result["state"] == "UNKNOWN"
    assert result["age_seconds"] is None


def test_the_boundary_is_inclusive():
    assert graph.freshness_of(
        "2026-09-14T03:00:00+00:00", now="2026-09-14T03:05:00+00:00",
        source="docker-inspect",
    )["state"] == "FRESH"
    assert graph.freshness_of(
        "2026-09-14T03:00:00+00:00", now="2026-09-14T03:05:01+00:00",
        source="docker-inspect",
    )["state"] == "STALE"


# -- identity across a rename ------------------------------------------------

def test_a_rename_does_not_change_a_single_node_or_edge():
    """§12's `container rename 后 graph identity 不变`.

    Deliberately an UNREGISTERED container: a registered one loses its owner
    edge on a rename, because the registry knows it by name, and that is a
    separate finding (`test_a_rename_does_not_lose_the_registration` below).
    Mixing the two would make this test pass or fail for the registry's
    reason rather than for identity's.
    """
    mounts = [{"Type": "volume", "Name": "v1", "Source": "/x",
               "Destination": "/y", "Mode": "rw"}]
    before = graph_of(one_record(name="an-unregistered-thing", cid="b" * 64,
                                 mounts=mounts, networks=["n1"]))
    after = graph_of(one_record(name="renamed-by-hand", cid="b" * 64,
                                mounts=mounts, networks=["n1"]))
    assert [n["ref"] for n in before["nodes"]] == \
           [n["ref"] for n in after["nodes"]]
    assert [(e["source"], e["value"], e["relationship"]) for e in before["edges"]] == \
           [(e["source"], e["value"], e["relationship"]) for e in after["edges"]]
    # Only the human-readable label moves, and it is still reported — a
    # rename that changed nothing a reader could see would be undetectable
    # rather than survivable.
    assert [n.get("display_name") for n in before["nodes"]] != \
           [n.get("display_name") for n in after["nodes"]]
    assert "renamed-by-hand" in [n.get("display_name") for n in after["nodes"]]
    assert "an-unregistered-thing" in \
           [n.get("display_name") for n in before["nodes"]]


def test_a_rename_costs_a_registered_container_its_owner_edge():
    """The finding, not the requirement.

    The registry pins this container by NAME, so a rename defeats rung ③ and
    the owner edge goes. It is reported rather than hidden: `matched: false`
    with the anchors that were tried, and the missing owner on the node.
    §10's `by_anchor` rung exists to close exactly this, and it is empty until
    a registration declares one.
    """
    renamed = one_record(name="renamed-by-hand", cid="b" * 64)
    assert renamed["registry_anchors_tried"]["matched"] is False
    assert renamed["registry_anchors_tried"]["tried"], "no anchors were tried"
    doc = graph_of(renamed)
    assert refs_by_relationship(doc, "owned-by") == []
    assert doc["coverage"]["runtimes_without_owner_edge"] == [renamed["ref"]]
    assert node(doc, renamed["ref"])["owner"]["status"] == graph.UNKNOWN


# -- registry aliases --------------------------------------------------------

def test_a_registry_name_becomes_an_alias_node_not_a_rewrite():
    """§12's `protected alias 仍映射到 canonical runtime`.

    A registry that pins its container by NAME must still reach the node keyed
    by id. The name is kept as its own node so a reader searching for the
    registry's spelling finds it.
    """
    record = one_record(name="lit-0104-dev100-litellm", cid="b" * 64)
    alias = "runtime:container:lit-0104-dev100-litellm"
    assert alias != record["ref"], "fixture assumption"
    doc = graph_of(record, aliases={alias: record["ref"]})
    assert node(doc, alias)["alias_of"] == record["ref"]
    assert node(doc, alias)["kind"] == "managed-runtime"
    assert refs_by_relationship(doc, "alias-of") == [(alias, record["ref"])]
    edge = [e for e in doc["edges"] if e["relationship"] == "alias-of"][0]
    assert edge["confidence"] == graph.AUTHORITATIVE
    assert "must still find it" in edge["why"]
    assert edge["freshness"]["within_seconds"] == 86400


def test_a_runtime_ref_is_not_collapsed_onto_itself():
    """An alias mapping a ref to itself is not an alias."""
    record = one_record(name="lit-0104-dev100-litellm", cid="b" * 64)
    doc = graph_of(record, aliases={record["ref"]: record["ref"]})
    assert refs_by_relationship(doc, "alias-of") == []


def test_a_tunnel_naming_an_aliased_runtime_reaches_the_canonical_node():
    record = one_record(name="lit-0104-dev100-litellm", cid="b" * 64)
    alias = "runtime:container:lit-0104-dev100-litellm"
    doc = graph_of(record, aliases={alias: record["ref"]}, tunnels=[{
        "tunnel_ref": "tunnel:t1", "runtime_ref": alias,
        "owner_ref": None, "ownership_confidence": "UNKNOWN",
        "provider": "sshd",
    }])
    assert refs_by_relationship(doc, "forwards") == [(record["ref"], "tunnel:t1")]
    edge = [e for e in doc["edges"] if e["relationship"] == "forwards"][0]
    assert edge["confidence"] == graph.INFERRED
    assert "canonical node is" in edge["why"]


# -- the two container ref spaces -------------------------------------------

def test_the_cgroup_ref_and_the_inspect_ref_are_not_the_same_string():
    """The finding, stated as a test so nobody re-derives it as an assumption.

    `docker inspect` is read for the first 12 hex characters and the cgroup
    scope carries all 64, so `runtime:container:<12>` !=
    `runtime:container:<64>` and a join by equality silently fails.
    """
    record = one_record(name="x", cid="b" * 64)
    short = "runtime:container:" + "b" * 12
    full = "runtime:container:" + "b" * 64
    assert record["ref"] == short, "the truncation this test is about"
    assert short != full, "the two spellings differ; a join must not assume"
    assert graph.reconcile_container_refs([short], [full])["matched"] == {
        full: short,
    }


def test_an_ambiguous_prefix_joins_nothing_and_is_reported():
    """Two containers can share twelve hex characters. A coin-toss join is
    the failure this refuses."""
    shared = "abcdef123456"
    doc = graph.reconcile_container_refs(
        [f"runtime:container:{shared}" + "0" * 52,
         f"runtime:container:{shared}" + "1" * 52],
        [f"runtime:container:{shared}"],
    )
    assert doc["matched"] == {}
    assert len(doc["ambiguous"]) == 1
    assert doc["ambiguous"][0]["candidates"] == [
        f"runtime:container:{shared}" + "0" * 52,
        f"runtime:container:{shared}" + "1" * 52,
    ]
    assert "coin toss" in doc["ambiguous"][0]["why"]


def test_an_unmatched_ref_is_reported_rather_than_dropped():
    doc = graph.reconcile_container_refs(
        ["runtime:container:aaaaaaaaaaaa"], ["runtime:container:" + "f" * 64],
    )
    assert doc["matched"] == {}
    assert doc["unmatched"] == ["runtime:container:" + "f" * 64]
    assert doc["ambiguous"] == []


def test_one_node_per_record_even_when_the_id_is_long():
    record = one_record(name="x", cid="b" * 64)
    doc = graph_of(record)
    assert [n["ref"] for n in doc["nodes"]
            if n["kind"] == "managed-runtime"] == [record["ref"]]


# -- ports -------------------------------------------------------------------

def test_a_published_port_is_an_edge_from_the_runtime():
    record = one_record(name="lit-0104-dev100-litellm", cid="b" * 64)
    doc = graph_of(record, ports=[{
        "port_ref": "port:tcp:0.0.0.0:4000", "protocol": "tcp",
        "local_address": "0.0.0.0", "state": "LISTENING",
        "runtime_ref": record["ref"], "provider": "docker",
        "confidence": "AUTHORITATIVE", "provenance": "FACT",
    }])
    assert refs_by_relationship(doc, "publishes") == [
        (record["ref"], "port:tcp:0.0.0.0:4000"),
    ]


def test_an_unowned_port_is_a_node_with_no_edge():
    """§14: an external listener is a fact about the host, not a runtime's
    port. Drawing the edge would attribute it to the nearest container."""
    doc = graph.resource_graph(
        {"containers": [], "device_ref": DEVICE}, observed_at=T0, now=T0,
        ports=[{"port_ref": "port:tcp:127.0.0.1:17890", "protocol": "tcp",
                "local_address": "127.0.0.1", "state": "LISTENING",
                "runtime_ref": None, "provider": None, "confidence": "UNKNOWN"}],
    )
    assert refs_by_relationship(doc, "publishes") == []
    node(doc, "port:tcp:127.0.0.1:17890")


def test_a_port_whose_runtime_reference_was_inferred_keeps_that_word():
    record = one_record(name="x", cid="b" * 64)
    doc = graph_of(record, ports=[{
        "port_ref": "port:tcp:0.0.0.0:8080", "protocol": "tcp",
        "local_address": "0.0.0.0", "state": "LISTENING",
        "runtime_ref": record["ref"], "provider": "docker-proxy",
        "confidence": "INFERRED", "provenance": "INFERRED",
    }])
    edge = [e for e in doc["edges"] if e["relationship"] == "publishes"][0]
    assert edge["confidence"] == graph.INFERRED
    assert edge["status"] == graph.INFERRED


# -- coverage ----------------------------------------------------------------

def test_coverage_counts_what_the_graph_has_rather_than_only_what_it_lacks():
    record = one_record(
        name="lit-0104-dev100-litellm", cid="b" * 64, networks=["n1"])
    doc = graph_of(record)
    coverage = doc["coverage"]
    assert coverage["runtime_nodes"] == 1
    assert coverage["owner_nodes"] == 1
    assert coverage["conflicts"] == 0
    assert coverage["edges"] == len(doc["edges"])
    assert coverage["nodes"] == len(doc["nodes"])


def test_an_empty_inventory_produces_an_empty_graph_and_no_invention():
    doc = graph.resource_graph(
        {"containers": [], "device_ref": DEVICE}, observed_at=T0, now=T0,
    )
    assert doc["nodes"] == [{"ref": DEVICE, "kind": "device",
                            "display_name": DEVICE}]
    assert doc["edges"] == []
    assert doc["conflicts"] == []
    assert doc["coverage"]["runtimes_without_owner_edge"] == []


def test_the_schema_is_named_so_a_reader_can_tell_a_graph_from_a_list():
    doc = graph_of(one_record(name="x", cid="b" * 64))
    assert doc["schema"] == "serveros/resource-graph.v1"
