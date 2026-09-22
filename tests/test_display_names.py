#!/usr/bin/env python3
"""Container display names: the four cases AC18 names, and the one it forbids.

The requirement is that a card names a container the way a person knows it,
and the forbidden implementation is the obvious one: shell out to
`docker inspect` while rendering. That would make the card depend on the host
being reachable at the moment somebody reads it, make the renderer a second
uncounted reader of the same truth, and — the reason it is a *test* and not a
convention — put a subprocess in a module whose whole job is to describe.

So the names come from evidence ServerOS already collected: the `container
attribution` document and the managed-runtime registry. Four cases, in the
priority order the ruling gives:

  1. a logical service name, when a registration declares one
  2. the container's own name, when only Docker knows it
  3. the shortened ref, when the only thing available is an opaque id
  4. nothing at all, when a name is missing — and then it must not be made up

The last class here is the one that matters most, because it is the one a
reviewer cannot check by reading the successful cases: the way a display-name
feature fails is not by printing the wrong name, it is by printing a
confident-looking one that no evidence supports.
"""
from __future__ import annotations

import ast
import importlib.util
import re
import sys
import unittest
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


presentation = _load("serveros_runtime_presentation", "presentation.py")
cards = _load("serveros_runtime_cards", "cards.py")

#: A full container id, 64 hex. Written out rather than truncated because the
#: 64-to-12 reconciliation below is one of the things under test, and a
#: conveniently-round fake would not exercise it.
FULL_HEX = "08e734361c06aa99b1c2d3e4f5a6b7c8d9e0f1a2b3c4d5e6f7a8b9c0d1e2f3a4"
SHORT_HEX = FULL_HEX[:12]

#: The two spellings the process layer and the attribution document use for one
#: container. `graph._describe` writes `docker:<hex>` into a subject ref;
#: `attribution` records the first twelve characters under `evidence.id`.
DOCKER_REF = f"runtime-group:process-tree:system.slice:docker:{FULL_HEX}"

ATTRIBUTION = {
    "schema": "serveros/attribution.v1",
    "containers": [
        {"container": "container0-postgres",
         "ref": f"runtime:container:{SHORT_HEX}",
         "evidence": {"id": FULL_HEX, "name": "container0-postgres",
                      "image": "pgvector/pgvector:pg16"},
         "identity": {"display_name": "container0-postgres",
                      "anchors": [{"anchor": "container-id",
                                   "runtime_ref": f"runtime:container:{SHORT_HEX}"}]}},
        {"container": "unregistered-thing",
         "ref": "runtime:container:aaaabbbbcccc",
         "evidence": {"id": "aaaabbbbccccdddd", "name": "unregistered-thing"}},
    ],
}

REGISTRY = {
    "schema": "serveros/managed-runtime-registry.v1",
    "resources": [
        {"id": "postgres-primary", "kind": "container", "criticality": "important",
         "probes": [{"id": "p", "kind": "container-healthcheck", "axis": "functional",
                     "container": "container0-postgres"}]},
        {"id": "serveros-control-plane", "kind": "systemd-user-service",
         "criticality": "critical",
         "probes": [{"id": "s", "kind": "systemd-unit-state", "axis": "liveness",
                     "unit": "serverosd.service"}]},
    ],
}


def _names(**kw) -> dict:
    return presentation.name_index(**kw)


def _face(model: dict) -> str:
    """The visible markdown of a card, panels excluded.

    Excluding them is deliberate: everything inside a collapsed panel is the
    detail a person opens on purpose, refs and raw codes included. The
    assertions below are about the first screen."""
    card = cards.alert_card(model)
    return "\n".join(str(e.get("content") or "")
                     for e in (card.get("body") or {}).get("elements") or []
                     if e.get("tag") == "markdown")


def _one_row(subject_ref: str, *, code: str = "DESIRED_ACTUAL_MISMATCH") -> dict:
    return {"key": "alert:x", "kind": "NEW", "code": code, "severity": "medium",
            "severity_word": "WARNING", "state": "OPEN", "subject_ref": subject_ref,
            "first_seen": "2026-09-20T05:00:00+00:00"}


def _model_for(subject_ref: str, *, names: dict | None = None,
               code: str = "DESIRED_ACTUAL_MISMATCH") -> dict:
    return presentation.alert_model(
        {"at": "2026-09-20T14:20:00+08:00", "device_ref": "device:stub",
         "rows": [_one_row(subject_ref, code=code)]},
        at="2026-09-20T14:20:00+08:00", names=names)


class TheFourCasesTests(unittest.TestCase):
    """AC18, in the priority order the ruling gives."""

    def test_a_registered_container_takes_the_registration_id(self):
        """Case 1. The registration is the logical service; the container is
        only where it happens to be running, so `postgres-primary` wins over
        `container0-postgres`."""
        found = presentation._lookup_name(f"runtime:container:{SHORT_HEX}",
                                          _names(attribution=ATTRIBUTION,
                                                 registry=REGISTRY))
        self.assertEqual(found["text"], "postgres-primary")
        self.assertEqual(found["rule"], "declared-service")
        self.assertEqual(found["resource_id"], "postgres-primary")
        self.assertEqual(found["criticality"], "important")

    def test_an_unregistered_container_keeps_its_docker_name(self):
        """Case 2. Still a string a person typed, still better than hex."""
        found = presentation._lookup_name("runtime:container:aaaabbbbcccc",
                                          _names(attribution=ATTRIBUTION,
                                                 registry=REGISTRY))
        self.assertEqual(found["text"], "unregistered-thing")
        self.assertEqual(found["rule"], "container-name")
        self.assertIsNone(found["criticality"])

    def test_a_container_with_only_an_id_gets_the_short_ref_and_not_a_name(self):
        """Case 3. `rule` is the rung, and the rung says the ref was printed
        because nothing knew its name — so a reader can tell an understood
        subject from an unresolved one without diffing strings."""
        shown = presentation.subject_display(f"runtime:container:{FULL_HEX}",
                                            names=_names(attribution=ATTRIBUTION,
                                                         registry=REGISTRY))
        self.assertEqual(shown["rule"], "runtime:container:")
        self.assertEqual(shown["text"], f"容器 {SHORT_HEX}…")
        self.assertEqual(shown["ref"], f"runtime:container:{FULL_HEX}")

    def test_a_missing_name_is_never_fabricated(self):
        """Case 4, which is the actual test of the other three. A row with no
        name is left out of the index rather than given a placeholder: a
        renderer that invented `container-1` would be inventing an identity,
        and `<unnamed>` on a card is a claim."""
        index = _names(attribution={"containers": [
            {"ref": "runtime:container:111111111111",
             "evidence": {"id": "1111111111112222"}}]}, registry=REGISTRY)
        self.assertEqual([k for k in index if "111111" in k], [])
        shown = presentation.subject_display("runtime:container:111111111111",
                                            names=index)
        for invented in ("container-1", "unnamed", "未知", "container:111111111111"):
            with self.subTest(invented=invented):
                self.assertNotIn(invented, shown["text"])

    def test_a_named_subject_says_which_rung_named_it(self):
        """`because` is the evidence sentence, and it is present exactly when
        something in the index answered — so its absence is what marks a
        subject nothing knew."""
        named = presentation.subject_display(f"runtime:container:{SHORT_HEX}",
                                             names=_names(attribution=ATTRIBUTION,
                                                          registry=REGISTRY))
        self.assertTrue(named["because"])
        unnamed = presentation.subject_display("runtime:container:ffffffffffff")
        self.assertIsNone(unnamed["because"])


class TheLiveSpellingTests(unittest.TestCase):
    """The spellings that actually reach a card on this node.

    Every one of these was read off a live document or off `serverosctl alert
    list`'s own output. The failure this class catches is not a wrong rule — it
    is a correct rule keyed on a spelling no producer emits, which passes every
    test written against the fixture its author had in mind.
    """

    def test_the_bare_resource_id_resolves(self):
        """MEASURED: this node's live alert row for `container0-litellm` carries
        `subject_ref: "container0-litellm"` with `resource_ref: null` — a BARE
        resource id, no `runtime:` and no `container:` prefix. Before that
        spelling was indexed, `_criticality_of` returned None for a registered
        resource and the escalation rule could not fire on the live data it was
        written for."""
        names = _names(registry={"resources": [
            {"id": "container0-litellm", "kind": "container",
             "criticality": "important",
             "probes": [{"container": "container0-litellm"}]}]})
        found = presentation._lookup_name("container0-litellm", names)
        self.assertIsNotNone(found)
        self.assertEqual(found["rule"], "declared-service")
        self.assertEqual(found["criticality"], "important")
        self.assertEqual(presentation._criticality_of({"subject_ref": "container0-litellm"},
                                                     names), "important")

    def test_the_spellings_the_two_producers_emit_agree_on_one_name(self):
        """`resource_anomaly` emits `container:<name>`; the process layer emits
        `runtime:container:<name>`; the attribution document records the
        12-character id under its own key. All three are the same container and
        must render the same, or one card would name it two ways."""
        names = _names(attribution=ATTRIBUTION, registry=REGISTRY)
        for spelling in (f"runtime:container:{SHORT_HEX}",
                         "runtime:container:container0-postgres",
                         "container:container0-postgres"):
            with self.subTest(spelling=spelling):
                self.assertEqual(presentation.subject_display(spelling,
                                                              names=names)["text"],
                                 "postgres-primary")

    def test_the_producers_own_docker_colon_spelling_is_the_one_matched(self):
        """`graph._describe` writes `docker:<hex>`. `_CONTAINER_ID` also accepts
        the two cgroup forms `docker/<hex>` and `docker-<hex>.scope`, and a
        colon in the character class is easy to lose — losing it means every
        process-tree alert silently falls back to the ref, which looks exactly
        like "there was no name to find"."""
        self.assertEqual(presentation._container_hex(DOCKER_REF), FULL_HEX)
        self.assertEqual(
            presentation._container_hex("runtime-group:process-tree:system.slice:"
                                        f"docker-{SHORT_HEX}.scope"), SHORT_HEX)
        self.assertEqual(
            presentation._container_hex(f"docker/{SHORT_HEX}"), SHORT_HEX)

    def test_a_64_hex_ref_reconciles_to_the_12_hex_record(self):
        """The two documents disagree about how much of the id to keep, for the
        same container. Both spellings have to reach one entry, or one alert
        would name the container and the next would show hex."""
        names = _names(attribution=ATTRIBUTION, registry=REGISTRY)
        shown = presentation.subject_display(DOCKER_REF, names=names)
        self.assertEqual(shown["rule"], "declared-service")
        self.assertEqual(shown["text"], "postgres-primary")

    def test_an_unknown_64_hex_ref_resolves_to_nothing_rather_than_to_the_nearest(self):
        """A prefix match that accepted any hex would put the postgres name on
        whatever container happened to be running. Only a stored spelling that
        is a prefix OF the id counts."""
        names = _names(attribution=ATTRIBUTION, registry=REGISTRY)
        other = f"runtime-group:process-tree:system.slice:docker:{'f' * 64}"
        self.assertIsNone(presentation._lookup_name(other, names))

    def test_a_non_unique_prefix_is_refused_rather_than_guessed(self):
        """The guard in `_lookup_name`. Naming the wrong container is worse
        than naming none, because an id at least looks unresolved.

        The index shape here is hand-built: `name_index` writes every key at
        exactly `_SHORT_HEX` characters, and two keys of equal length cannot
        both be prefixes of one id, so this shape is not reachable through
        `name_index` today. The test pins the guard rather than a live path —
        which is why it is written against the lookup and not through the
        producer.
        """
        names = {"runtime:container:aaaa": {"text": "one", "rule": "container-name"},
                 "runtime:container:aaaaaaaabbbb": {"text": "two",
                                                    "rule": "container-name"}}
        self.assertIsNone(presentation._lookup_name(
            "runtime-group:process-tree:system.slice:docker:aaaaaaaabbbb1111",
            names))


class TheRendererRunsNothingTests(unittest.TestCase):
    """The forbidden implementation, asserted against the source."""

    def test_the_name_path_shells_out_to_nothing(self):
        """Structural, not textual. `presentation.py`'s own docstring names
        `docker inspect` to explain why it is forbidden, so a substring search
        would fail on the sentence prohibiting the thing — a check that
        punishes the comment and would pass on a module that had moved the call
        behind an f-string. This walks the parse tree instead: the modules
        imported, the names called, and the non-docstring string literals.
        """
        tree = ast.parse((RUNTIME / "presentation.py").read_text(encoding="utf-8"))
        imported: set[str] = set()
        called: set[str] = set()
        literals: list[str] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported |= {a.name.split(".")[0] for a in node.names}
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module.split(".")[0])
            elif isinstance(node, ast.Call):
                target = node.func
                if isinstance(target, ast.Attribute):
                    called.add(target.attr)
                elif isinstance(target, ast.Name):
                    called.add(target.id)
            elif isinstance(node, ast.Constant) and isinstance(node.value, str):
                literals.append(node.value)
        docstrings = {(n.body[0].value) for n in ast.walk(tree) if isinstance(
            n, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))
            and n.body and isinstance(n.body[0], ast.Expr)
            and isinstance(n.body[0].value, ast.Constant)}
        self.assertEqual(imported & {"subprocess", "shlex", "pty"}, set())
        self.assertEqual(called & {"system", "popen", "Popen", "run", "call",
                                   "check_call", "check_output"}, set())
        # One assertion and not one per literal: a subTest per string in a
        # module this size reports thousands of passes and buries the failure.
        # The docstrings are exempt because they are where the prohibition is
        # written down, and a check that forbids writing down the rule is a
        # check that deletes its own rationale.
        commands = ("docker inspect", "docker ps", "docker exec", "systemctl")
        offenders = sorted({f"{forbidden!r} in {text[:60]!r}"
                            for text in literals
                            if not any(text is d.value for d in docstrings)
                            for forbidden in commands
                            if forbidden in text})
        self.assertEqual(offenders, [])

    def test_the_evidence_comes_from_two_collected_documents_and_no_more(self):
        """`name_index` takes exactly the attribution document and the registry,
        both optional. A required argument would mean the renderer had to go
        and collect something, and a third source would be a third reader of
        this node's runtime truth — the readers of which are counted."""
        import inspect
        signature = inspect.signature(presentation.name_index)
        self.assertEqual(sorted(signature.parameters), ["attribution", "registry"])
        for name, parameter in signature.parameters.items():
            with self.subTest(parameter=name):
                self.assertIsNone(parameter.default)

    def test_the_card_shows_the_name_it_was_given(self):
        """End to end through the renderer, because a resolver that works and a
        card that does not call it is the same defect as no resolver."""
        names = _names(attribution=ATTRIBUTION, registry=REGISTRY)
        face = _face(_model_for(DOCKER_REF, names=names))
        self.assertIn("postgres-primary", face)
        self.assertNotIn(SHORT_HEX, face)

    def test_the_first_screen_carries_no_raw_opaque_id(self):
        """A 64-character cgroup id belongs in the detail, beside the ref,
        which is where something a person matches against a log goes. On the
        face it is twelve characters and an ellipsis — the id is still there to
        be matched, and it is no longer the sentence."""
        face = _face(_model_for(DOCKER_REF))
        self.assertIsNone(re.search(re.escape(FULL_HEX), face))
        self.assertIn(f"{SHORT_HEX}…", face)

    def test_the_card_reads_the_criticality_the_registration_declared(self):
        """The one thing a display name is used for besides being read: the
        escalation rule asks the registration, through this index, and not the
        ref's shape."""
        names = _names(registry=REGISTRY)
        model = _model_for("serveros-control-plane",
                           names=names, code="FUNCTIONAL_PROBE_FAILED")
        self.assertEqual([e["attention"]["level"] for e in model["events"]],
                         ["ACTION_REQUIRED"])
        self.assertTrue(model["events"][0]["attention"]["escalated"])


class TheEntryKeepsTheValueAndTheSentenceApartTests(unittest.TestCase):
    """One entry, two halves: `text` is what a person reads, `criticality` is
    what the machine decided about. Collapsing them would translate
    `critical` into a word at the point where the escalation rule needs the
    declared value."""

    def test_the_entry_is_the_declaration_plus_the_display_string(self):
        found = presentation._lookup_name(f"runtime:container:{SHORT_HEX}",
                                          _names(attribution=ATTRIBUTION,
                                                 registry=REGISTRY))
        self.assertEqual(sorted(found),
                         ["because", "criticality", "resource_id", "rule", "text"])
        self.assertEqual(found["criticality"], "important")
        self.assertEqual(found["resource_id"], "postgres-primary")

    def test_the_display_string_is_a_sentence_and_the_rest_is_not(self):
        """A renderer that put a translated word in `criticality` could not
        widen it to a security code; a renderer that formatted the count itself
        could still round it. The separation is cheap to keep and cheap to
        check."""
        for spelling in (f"runtime:container:{SHORT_HEX}",
                         "runtime:container:aaaabbbbcccc"):
            with self.subTest(spelling=spelling):
                found = presentation._lookup_name(spelling,
                                                  _names(attribution=ATTRIBUTION,
                                                         registry=REGISTRY))
                self.assertIsInstance(found["text"], str)
                self.assertIn(found["criticality"], (None, "important", "critical"))
                self.assertIn(found["rule"], presentation.NAME_RULES)


if __name__ == "__main__":
    unittest.main()
