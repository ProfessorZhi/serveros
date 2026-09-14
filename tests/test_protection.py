#!/usr/bin/env python3
"""§17 · Protection — declared, and never lost to a change of identity.

Two halves, and the second is the one with a live failure behind it.

The first half is a refusal: a name containing `prod` is not a permission
level, and the resolver must say so on the record rather than silently
ignoring the temptation. The tests below check both that the word decides
nothing and that the near-miss is reported.

The second half is the gate: protection on this node is declared against
NAMES, while a container also has an id, and a resolution that switches
spelling can therefore return two answers for one container without producing
an error. The tests check that the switch is detected, that the union of
identities still protects, and that a `False` is never returned on the strength
of never having been asked.

What the gate is NOT allowed to claim, and what an earlier draft of it did
claim: that the automatic path resolves by container id. It does not —
`containers.attribute` matches a registration by container name and then by
the §9 anchors a registration declares. §9's canonical identity is likewise
the strongest anchor available, not the raw id. The measurement is in
`protection.identity_switch_findings`'s docstring; the finding's `why` says
what was measured.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
RUNTIME = REPO / "runtime"
SCRIPTS = REPO / "skills/serveros/scripts"

LIVE_REGISTRY = Path("/opt/applications/data/home-hwz/state/serveros/runtime"
                     "/registry/resources.yaml")


def _load(name: str, filename: str):
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, RUNTIME / filename)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


protection = _load("serveros_runtime_protection", "protection.py")
registry = _load("serveros_runtime_registry", "registry.py")
anomaly = _load("serveros_runtime_anomaly", "anomaly.py")


def _load_guard():
    """§18/§26's scanner, under the name its own test file loads it as.

    Loaded by name through `sys.modules`, so whichever of the two test files
    runs first builds the module and the other gets the same object. Two
    copies under two names would be two scanners, and the point of the module
    is that there is one.
    """
    if "serveros_mutation_guard" in sys.modules:
        return sys.modules["serveros_mutation_guard"]
    spec = importlib.util.spec_from_file_location(
        "serveros_mutation_guard", SCRIPTS / "mutation_guard.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules["serveros_mutation_guard"] = module
    spec.loader.exec_module(module)
    return module


guard = _load_guard()


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def resource(rid="lit0104-dev-litellm", *, level="protected", probes=None):
    return {
        "id": rid,
        "kind": "test-runtime",
        "owner": {"type": "work-item", "ref": "work-item:LIT-0104"},
        "scope": "user",
        "lifecycle": {"class": "task-scoped"},
        "desired": {"state": "running"},
        "protection": {"level": level},
        "probes": probes or [],
    }


def with_container(name, *, rid="lit0104-dev-litellm", level="protected"):
    return resource(rid, level=level, probes=[
        {"id": "liveness", "kind": "container-state", "axis": "liveness",
         "container": name}])


def registry_of(*records):
    return registry.Registry(document={
        "schema": "serveros/managed-runtime-registry.v1",
        "resources": list(records),
    })


def resolve(*, name=None, container_id=None, labels=None, protected_refs=None,
            ref=None, reg=None, declared=None, declared_name=None):
    """The round trip a caller makes: spell the container's identities, then
    resolve them. `protected_refs` and `ref` are different things — the list a
    person maintains, and one ref the caller already holds — and this helper
    forwards both, so a test cannot pass by having dropped one of them."""
    identity = None
    if declared_name:
        identity = {"runtime_ref": f"runtime:container:{declared_name}",
                    "source": "container-id",
                    "declared_name": declared_name}
    return protection.resolve(
        identities=protection.identities_of(identity, name=name,
                                            container_id=container_id,
                                            labels=labels, ref=ref),
        registry_doc=reg, protected_refs=protected_refs, labels=labels,
        declared=declared)


# ---------------------------------------------------------------------------
# A name is a string, not a permission level
# ---------------------------------------------------------------------------


def test_a_name_that_looks_protected_is_not_protection():
    """The whole refusal. This container is called `prod-critical-litellm` and
    it is not protected, because nobody declared it — and the answer is `False`
    rather than `True` even though the word is sitting right there."""
    result = resolve(name="prod-critical-litellm", reg=registry_of())
    assert result["protected"] is False
    assert result["sources"] == []


def test_the_temptation_is_reported_rather_than_silently_ignored():
    """Silently ignoring it would leave the next reader unable to tell "nobody
    declared this" from "somebody declared this and the word was rejected".
    The second is a question for a person; the first is not."""
    result = resolve(name="prod-litellm", reg=registry_of())
    assert result["guessed_from"], "the near-miss must be on the record"
    guessed = result["guessed_from"][0]
    assert guessed["words"] == ["prod"]
    assert guessed["identity"]["kind"] == "container_name"
    assert "NOT treated as protection" in guessed["why"]


@pytest.mark.parametrize("word,matched", [("prod", True), ("production", True),
                                          ("critical", True), ("important", True),
                                          ("product-catalog", False),
                                          ("reproductive", False),
                                          ("importantly", False)])
def test_only_whole_words_are_even_guesses(word, matched):
    """The word list is a list of words. `product-catalog` contains `prod` and
    is not a guess, because a substring match here would flag most of a web
    estate and the list would stop being read."""
    assert bool(protection.looks_protected(word)) is matched


def test_the_guess_words_are_the_ones_the_docstring_names():
    """The refusal is legible rather than an absence of code: the list a
    reviewer has to consider is written down in one place."""
    assert set(protection.GUESS_WORDS) == {"prod", "production", "critical",
                                           "important", "live", "primary"}


def test_a_guess_is_never_a_source():
    """Not merely that the result is False — that the name contributed nothing
    to it. A `sources` entry here would be a permission derived from a string."""
    result = resolve(name="critical-prod", reg=registry_of())
    assert result["guessed_from"]
    assert all(s["source"] != "container_name" for s in result["sources"])


# ---------------------------------------------------------------------------
# The four sources that DO declare it
# ---------------------------------------------------------------------------


def test_a_container_may_declare_itself_protected():
    """A container's own label is a declaration, and it travels with the
    container — which is what makes it survive a rename, unlike a name."""
    result = resolve(name="anything-at-all", labels={protection.PROTECTED_LABEL: "true"},
                     reg=registry_of())
    assert result["protected"] is True
    assert [s["source"] for s in result["sources"]] == ["container-label"]


def test_a_container_label_survives_a_rename():
    """Same labels, different name. The two resolutions differ in nothing that
    matters, which is the property a name-keyed declaration does not have."""
    labels = {protection.PROTECTED_LABEL: "true"}
    before = resolve(name="lit-0104-dev100-litellm", labels=labels, reg=registry_of())
    after = resolve(name="renamed-by-somebody-else", labels=labels, reg=registry_of())
    assert before["protected"] is after["protected"] is True


@pytest.mark.parametrize("raw", ["true", "TRUE", "yes", "1", " true "])
def test_the_label_is_read_the_way_a_label_is_written(raw):
    result = resolve(name="x", labels={protection.PROTECTED_LABEL: raw},
                     reg=registry_of())
    assert result["protected"] is True


def test_a_label_that_says_no_is_not_a_declaration_of_protection():
    result = resolve(name="x", labels={protection.PROTECTED_LABEL: "false"},
                     reg=registry_of())
    assert result["protected"] is False
    assert result["sources"] == []


def test_a_registration_declares_protection_and_names_which_identity_matched():
    result = resolve(name="lit0104-dev-litellm", reg=registry_of(resource()))
    assert result["protected"] is True
    source = result["sources"][0]
    assert source["source"] == "registration"
    assert source["ref"] == "runtime:lit0104-dev-litellm"
    assert source["matched_identity"] == "container_name"
    assert source["matched_value"] == "lit0104-dev-litellm"


def test_a_registration_is_reached_through_a_probe_container_name():
    """A registration whose id is not the container's name still protects it,
    through the probe that names the container. #9's gap in one line: the two
    spellings are both legitimate and neither implies the other."""
    reg = registry_of(with_container("lit-0104-dev100-litellm"))
    by_id = resolve(name="lit0104-dev-litellm", reg=reg)
    by_probe = resolve(name="lit-0104-dev100-litellm", reg=reg)
    assert by_id["protected"] is True
    assert by_probe["protected"] is True
    assert by_probe["sources"][0]["ref"] == "runtime:lit0104-dev-litellm"


def test_protected_refs_match_a_free_text_name():
    """The list §3 of the brief maintains for things registered elsewhere — or
    not registered at all. LIT-0103 has no registration, and its protection
    exists only here."""
    refs = [{"ref": "work-item:LIT-0103", "because": "protected parallel lane"}]
    result = resolve(name="lit-0103-r3-litellm", protected_refs=refs, reg=registry_of())
    assert result["protected"] is True
    source = result["sources"][0]
    assert source["source"] == "protected_refs"
    assert source["ref"] == "work-item:LIT-0103"
    assert "protected parallel lane" in source["why"]


def test_a_protected_ref_does_not_match_a_name_that_merely_resembles_it():
    """The match is delimited. `lit-01030` is not `LIT-0103`, and the cost of a
    false positive here (something harmless left alone) is the safe direction —
    but a match that fired on any substring would protect half the host and the
    list would stop meaning anything."""
    refs = [{"ref": "work-item:LIT-0103", "because": "lane"}]
    assert resolve(name="lit-01030-thing", protected_refs=refs,
                   reg=registry_of())["protected"] is False


def test_a_caller_may_declare_protection_for_a_resource_with_no_registration():
    """An active worktree, a test environment, a container that predates the
    registry. The declaration is attributed to the caller rather than folded
    into one of the other sources."""
    result = resolve(name="unregistered-thing", declared=True, reg=registry_of())
    assert result["protected"] is True
    assert result["sources"][0]["source"] == "declared-by-caller"


def test_a_caller_cannot_unprotect_something_that_is_declared_protected():
    """`declared=False` is a caller saying "I found no declaration", and it
    loses to any source that actually found one. Otherwise the weakest possible
    input would be an override."""
    result = resolve(name="lit0104-dev-litellm", declared=False,
                     reg=registry_of(resource()))
    assert result["protected"] is True


# ---------------------------------------------------------------------------
# The three-valued answer
# ---------------------------------------------------------------------------


def test_nothing_askable_is_none_and_not_false():
    """The distinction the module exists to keep. With no registry, no
    protection list and no label, nothing on this node could be asked — and a
    `False` would be a protection bypass performed by a default."""
    result = resolve(name="some-container")
    assert result["protected"] is None
    assert result["sources"] == []
    assert "unknown rather than false" in result["why"]


def test_a_registry_that_names_nothing_says_false():
    """The one case in which `False` is earned: a document exists, a person
    maintains it, and it names nothing that matches."""
    result = resolve(name="some-container", reg=registry_of())
    assert result["protected"] is False
    assert "no declaration on this node protects" in result["why"]


def test_an_empty_protection_list_is_still_a_list():
    """An empty `protected_refs` is a maintained document that says no, not the
    absence of one. Conflating the two is how a default becomes a permission."""
    assert resolve(name="x", protected_refs=[], reg=registry_of())["protected"] is False
    assert resolve(name="x")["protected"] is None


def test_the_registrys_own_list_is_used_when_the_caller_passes_none():
    reg = registry.Registry(document={
        "schema": "serveros/managed-runtime-registry.v1",
        "resources": [],
        "protected_refs": [{"ref": "work-item:LIT-0103", "because": "lane"}],
    })
    result = resolve(name="lit-0103-r3-litellm", reg=reg)
    assert result["protected"] is True
    assert result["sources"][0]["source"] == "protected_refs"


# ---------------------------------------------------------------------------
# Identities
# ---------------------------------------------------------------------------


def test_every_identity_is_checked_and_the_union_decides():
    """Two identities, one of which protects. The answer is protected, and the
    row says which spelling found it — a resolver that stopped at the first
    identity would return whichever answer came first."""
    result = resolve(name="lit0104-dev-litellm", ref="runtime:lit0104-dev-litellm",
                     reg=registry_of(resource()))
    kinds = [e["kind"] for e in result["identities_checked"]]
    assert "container_name" in kinds and "runtime_ref" in kinds
    assert result["protected"] is True
    assert {s["matched_identity"] for s in result["sources"]} == {"container_name"}


def test_the_order_of_identities_changes_nothing():
    one = protection.resolve(
        identities=[{"kind": "container_name", "value": "lit0104-dev-litellm"},
                    {"kind": "container_id", "value": "abc123"}],
        registry_doc=registry_of(resource()))
    two = protection.resolve(
        identities=[{"kind": "container_id", "value": "abc123"},
                    {"kind": "container_name", "value": "lit0104-dev-litellm"}],
        registry_doc=registry_of(resource()))
    assert one["protected"] is two["protected"] is True


def test_a_rename_does_not_remove_an_identity_kind_from_the_check():
    """`identities_of` is asked for the same container under two names. The set
    of kinds it checks is the same, which is what makes the id the stable one:
    the container_name changes and the container_id does not."""
    kinds = lambda name: {e["kind"] for e in protection.identities_of(  # noqa: E731
        None, name=name, container_id="abc123")}
    assert kinds("lit-0104-dev100-litellm") == kinds("renamed-yesterday")


def test_the_canonical_identity_is_the_containers_id():
    """§9's decision, restated here because §17's second half is about the
    consequence: whichever spelling a caller holds, the id is the one the
    automatic paths use."""
    identity = {"runtime_ref": "runtime:container:abc123", "source": "container-id",
                "declared_name": "the-name-somebody-gave-it"}
    kinds = [e["kind"] for e in protection.identities_of(identity)]
    assert kinds[0] == "runtime_ref"
    assert "container_id" in kinds
    ids = [e for e in protection.identities_of(identity) if e["kind"] == "container_id"]
    assert len(ids) == 1, "the id must appear once, whichever route supplied it"
    assert "id" in ids[0]["why"]


# ---------------------------------------------------------------------------
# §17's gate: protection that a change of identity would drop
# ---------------------------------------------------------------------------


def test_the_gate_fires_when_the_id_does_not_protect():
    by_identity = {
        "container_id": {"protected": False, "sources": []},
        "container_name": {"protected": True,
                           "sources": [{"source": "protected_refs",
                                        "ref": "work-item:LIT-0104"}]},
    }
    findings = protection.identity_switch_findings(by_identity=by_identity)
    assert len(findings) == 1
    finding = findings[0]
    assert finding["code"] == protection.LOST_ON_IDENTITY_SWITCH
    assert finding["identity_that_protects"] == "container_name"
    assert finding["identity_that_does_not"] == "container_id"
    assert finding["implies_stale"] is False
    assert finding["sources"], "the finding must carry the declaration it found"


def test_the_severity_comes_from_the_catalogue_rather_than_being_typed_here():
    """One word, one severity. A second copy of `high` in this module is a
    second copy that can disagree with the contract's."""
    finding = protection.identity_switch_findings(by_identity={
        "container_id": {"protected": False},
        "container_name": {"protected": True, "sources": []},
    })[0]
    assert finding["severity"] == anomaly.SEVERITY[protection.LOST_ON_IDENTITY_SWITCH]
    assert finding["severity"] == "high"


def test_the_gate_stays_silent_when_the_id_protects_too():
    assert protection.identity_switch_findings(by_identity={
        "container_id": {"protected": True},
        "container_name": {"protected": True},
    }) == []


def test_the_gate_stays_silent_when_the_id_was_never_resolved():
    """No `container_id` entry means nobody asked under that identity, which is
    not the same as asking and being told no. Reporting a lapse here would
    manufacture a finding out of an absent input."""
    assert protection.identity_switch_findings(by_identity={
        "container_name": {"protected": True},
    }) == []


def test_the_gate_stays_silent_when_the_id_resolution_was_unknown():
    """`None` is "nothing could be asked". Only an earned `False` — a document
    that named nothing — is a lapse."""
    assert protection.identity_switch_findings(by_identity={
        "container_id": {"protected": None},
        "container_name": {"protected": True},
    }) == []


def test_resolve_by_identity_returns_one_resolution_per_kind_plus_the_findings():
    """The shape a caller consumes: the per-identity readings, so a human can
    see which spelling carries the protection, and the findings over them."""
    result = protection.resolve_by_identity(
        identities=protection.identities_of(None, name="lit-0103-r3-litellm",
                                            container_id="abc123"),
        registry_doc=registry_of(),
        protected_refs=[{"ref": "work-item:LIT-0103", "because": "lane"}])
    assert set(result["by_identity"]) == {"container_id", "container_name"}
    assert result["by_identity"]["container_name"]["protected"] is True
    assert result["by_identity"]["container_id"]["protected"] is False
    assert [f["code"] for f in result["findings"]] == [protection.LOST_ON_IDENTITY_SWITCH]


# ---------------------------------------------------------------------------
# Nothing here acts
# ---------------------------------------------------------------------------


def test_the_module_contains_no_mutating_action():
    """§18/§26, asked of the PARSED source rather than of the prose.

    This was a substring scan over the file's text, and it failed the day the
    finding's own `why` named `docker rm <id>` in a sentence about an operator
    holding a container id — a checker whose failure mode is "the docstring
    described the thing it forbids" reports the wrong thing and gets switched
    off. `mutation_guard` classifies argv from the AST, so a command *named* in
    a string is not a command *run*, and a command run is not hidden by
    formatting.

    The prohibition is unchanged and is asserted below on the classification,
    not on the spelling: no hit in this module may be a `mutation`.
    """
    source = (RUNTIME / "protection.py").read_text()
    scanned = guard.scan_file("runtime/protection.py", source)
    assert scanned["parsed"] is True, scanned["error"]
    mutations = [hit for hit in scanned["hits"] if hit["kind"] == "mutation"]
    assert mutations == [], mutations

    # ...and the scan is not silent for want of looking. The same source with a
    # real destroyer appended reports one, so "no mutations found" above is a
    # measurement rather than a property of a scanner that was pointed
    # somewhere else.
    poisoned = guard.scan_file(
        "runtime/protection.py",
        source + "\n\nimport subprocess\n"
                 "def _sweep(name):\n"
                 "    subprocess.run(['docker', 'rm', '-f', name])\n")
    assert [hit["kind"] for hit in poisoned["hits"] if hit["kind"] == "mutation"]


def test_the_anomaly_catalogue_carries_the_code_and_the_reason_beside_it():
    """A finding with no catalogue entry is a code nobody can look up. The
    contract carries the note explaining why this one is high."""
    assert protection.LOST_ON_IDENTITY_SWITCH in anomaly.SEVERITY
    contract = (RUNTIME / "attribution-contract.yaml").read_text()
    assert protection.LOST_ON_IDENTITY_SWITCH in contract
    assert "PROTECTION_LOST_ON_IDENTITY_SWITCH" in contract


# ---------------------------------------------------------------------------
# The live node
# ---------------------------------------------------------------------------


@pytest.mark.skipif(not LIVE_REGISTRY.exists(),
                    reason="this node has no registry; protection is untestable here")
def test_the_live_registry_protects_every_container_it_names():
    """§17's regression gate, against the real document and the real
    declarations rather than a fixture. Every container a live registration
    names — by its id or by a probe's container name — must resolve protected
    through EVERY identity the registry can supply, and through the union.

    This is the direction that matters for §17's second half: if protection
    were only reachable through one spelling, this fails on the other.
    """
    reg = registry.Registry(path=LIVE_REGISTRY)
    checked = 0
    for record in reg.all():
        names = {record.id} | {p.get("container") for p in record.probes if p.get("container")}
        for name in sorted(names):
            identities = protection.identities_of(None, name=name)
            union = protection.resolve(identities=identities, registry_doc=reg)
            assert union["protected"] is True, (record.id, name, union["why"])
            checked += 1
    assert checked > 0, "no live registration names a container; the gate would be vacuous"


@pytest.mark.skipif(not LIVE_REGISTRY.exists(),
                    reason="this node has no registry; protection is untestable here")
def test_the_live_container_id_identity_resolves_rather_than_erroring():
    """The id spelling is asked for on the real registry. Today it resolves to
    `False` — the id matches no registration and no protected ref, which is
    §17's recorded gap — and the point of the assertion is that it RESOLVES:
    an id-keyed lookup that raised, or that returned `None`, would be a
    different and worse failure than the one this round is reporting."""
    reg = registry.Registry(path=LIVE_REGISTRY)
    result = protection.resolve(
        identities=protection.identities_of(None, container_id="0123456789abcdef"),
        registry_doc=reg)
    assert result["protected"] in (True, False)
    assert result["identities_checked"][0]["kind"] == "container_id"
