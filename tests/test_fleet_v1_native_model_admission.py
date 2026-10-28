"""Wave 8: which model may a create name, and what is allowed to be evidence.

The question this file pins down is narrow and it is asked before any provider is
called: *given what the device published, may a caller name a model id in
`session_create`, or may it only accept whatever the provider does by default?*

The measured starting point is CloudCLI 1.41.0 (capture pinned by
`integrations/fleet-l2-control/EVIDENCE-1.41.0.json`, `tools_list_sha256
24e0bef7…`):

*   `session_create` declares `model` (type `string`, no enum) and requires only
    `project`. `session_reconfigure` declares `model` the same way.
*   There is **no** verb that lists models, and none that lists agents — the
    thirty-one verbs are `approval_answer` … `ui_visible_context`.
*   No roundtrip in the capture ever passed `model`.

So the honest reading is that the device accepts an arbitrary string and
publishes no enumeration of what that string may be. A model id can therefore
never be `PROVIDER_ADVERTISED` on this release; the most it can be is
`SCHEMA_ONLY` (a field that takes any string) or `POLICY_ALLOWED_ONLY` (the
operator's grant names it, which is a ceiling on what may be *asked*, not
evidence that a backend exists). This module refuses to blur that into a
catalogue: see `test_the_module_hardcodes_no_model_id` and the `known_models`
group, which is where a fabricated list would have to appear.

Everything here is offline and pure. No provider is called, no session is
created, no credential is read, and no device is contacted.
"""
from __future__ import annotations

import ast
import datetime as dt
import importlib.util
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = ROOT / "integrations" / "fleet-v1-native-model-admission"
EVIDENCE_1_41 = ROOT / "integrations" / "fleet-l2-control" / "EVIDENCE-1.41.0.json"


def _load(name: str, path: Path):
    """Load a module by path, consulting `sys.modules` before writing to it.

    `tests/test_loader_identity.py` forbids registering a module under a name
    without first asking what that name already means — the name is
    process-wide, so a second writer takes it from the first and each copy
    carries its own exception classes.
    """
    module = sys.modules.get(name)
    if module is None:
        spec = importlib.util.spec_from_file_location(name, path)
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        try:
            spec.loader.exec_module(module)
        except BaseException:
            sys.modules.pop(name, None)
            raise
    return module


admission = _load("native_model_admission", PACKAGE / "model_admission.py")

UTC = dt.timezone.utc

#: The capture's own clock. Every test that ages evidence measures from here so
#: that "fresh" and "expired" are decided by the test, never by the wall clock.
CAPTURED = dt.datetime(2026, 10, 27, 8, 12, 0, tzinfo=UTC)
NOW = dt.datetime(2026, 10, 27, 9, 0, 0, tzinfo=UTC)

#: Pinned in #458's `EVIDENCE-1.41.0.json`. Duplicated here on purpose: a fixture
#: that silently follows the capture would stop being a second opinion.
TOOLS_LIST_SHA256 = (
    "24e0bef7ce297e0c8a65fa6b7dc22c54c6ec12eb975339d4d31728461da21bbc"
)


def _shape_1_41() -> dict:
    """The two model-bearing tool shapes, transcribed from the 1.41 capture.

    Only the parts this module reasons about: which property names exist, what
    type each is declared as, and which are required. Deliberately *not* the
    roundtrip results — those live in the cross-check test at the bottom, which
    reads the real file when it is present.
    """
    return {
        "release": "1.41.0",
        "tools": {
            "session_create": {
                "required": ["project"],
                "properties": ["correlationId", "lifecycleMode", "message",
                               "model", "permissionMode", "project", "provider"],
                "types": {"correlationId": "string", "lifecycleMode": "string",
                          "message": "string", "model": "string",
                          "permissionMode": "string", "project": "string",
                          "provider": "string"},
            },
            "session_reconfigure": {
                "required": ["session"],
                "properties": ["effort", "model", "permissionMode", "session"],
                "types": {"effort": "string", "model": "string",
                          "permissionMode": "string", "session": "string"},
            },
        },
        #: No tool shape in the capture carries an `enum`. Recorded as the empty
        #: mapping rather than omitted, so a future release that adds one has to
        #: change this fixture and notice.
        "enums": {},
    }


ONE_FORTY_ONE = _shape_1_41()


def ev(device_ref="device:tokyo", runtime="claude", *, release=None,
       captured_at=None, schema_types=None, declared=None, sha=""):
    """One observation of one runtime on one device, defaulted to 1.41's shape."""
    if schema_types is None:
        schema_types = dict(ONE_FORTY_ONE["tools"]["session_create"]["types"])
    return admission.ModelEvidence(
        device_ref=device_ref,
        runtime=runtime,
        release=release if release is not None else ONE_FORTY_ONE["release"],
        captured_at=captured_at if captured_at is not None else CAPTURED,
        schema_types=schema_types,
        declared={} if declared is None else declared,
        capture_sha256=sha or TOOLS_LIST_SHA256,
    )


def book(*entries):
    """An evidence set keyed the way the module keys it: (device, runtime)."""
    return {(e.device_ref, e.runtime): e for e in entries}


class _NeverRead(dict):
    """A mapping that fails the test if anything touches it.

    Used to prove an ordering claim rather than assert it in prose: a refusal
    that is supposed to happen before evidence is consulted must not so much as
    look at the evidence argument.
    """

    def _boom(self, *_a, **_k):
        raise AssertionError("evidence was read before the grant check")

    get = _boom
    __getitem__ = _boom
    __contains__ = _boom
    __iter__ = _boom
    __len__ = _boom
    keys = _boom
    items = _boom
    values = _boom


# --------------------------------------------------------------------------
# A. the vocabulary, and what the real release can and cannot support
# --------------------------------------------------------------------------

def test_the_five_evidence_classes_are_declared():
    assert admission.EVIDENCE_CLASSES == (
        "PROVIDER_ADVERTISED",
        "SCHEMA_ONLY",
        "POLICY_ALLOWED_ONLY",
        "STALE",
        "UNKNOWN",
    )


def test_the_module_is_pinned_to_the_release_it_reasons_about():
    assert admission.RELEASE == "1.41.0"
    assert admission.TOOLS_LIST_SHA256 == TOOLS_LIST_SHA256


def test_the_real_release_never_advertises_a_model_id():
    evidence = ev()
    assert admission.known_models(evidence, now=NOW) == ()
    for guess in ("sonnet", "opus", "haiku", "gpt-6.1-sol", ""):
        assert admission.classify(evidence, guess, now=NOW) == "SCHEMA_ONLY"


def test_no_verb_in_the_release_enumerates_models_or_agents():
    """The capability that would make an explicit model safe is absent.

    The capture's thirty-one verbs are known by name; a future release that adds
    `models_list` would have to change this list, which is the point.
    """
    verbs = {
        "approval_answer", "approvals_list", "overview", "projects_list",
        "quay_snapshot", "run_get", "session_background",
        "session_cancel_queued", "session_close", "session_create",
        "session_get", "session_interrupt", "session_read",
        "session_reconfigure", "session_search", "session_send",
        "session_start", "sessions_list", "ui_clients_list",
        "ui_last_opened_session", "ui_open_session", "ui_visible_context",
        "project_create", "project_delete", "project_update",
        "worktree_list", "session_delete", "session_archive",
        "session_unarchive", "session_rename", "session_fork",
    }
    assert len(verbs) == 31
    assert not [v for v in verbs if "model" in v]
    assert not [v for v in verbs if "agent" in v]
    # And the module's own view of the release agrees: it declares no source.
    assert admission.MODEL_SOURCES_1_41 == ()


def test_schema_only_says_a_string_is_accepted_and_nothing_more():
    shape = ONE_FORTY_ONE["tools"]["session_create"]
    assert shape["types"]["model"] == "string"
    assert "model" in shape["properties"]
    assert ONE_FORTY_ONE["enums"] == {}
    # The same is true of reconfigure: a string, with no enumeration.
    assert ONE_FORTY_ONE["tools"]["session_reconfigure"]["types"]["model"] == "string"


# --------------------------------------------------------------------------
# B. classify: which of the five classes one (device, runtime, id) is in
# --------------------------------------------------------------------------

def test_an_enum_bearing_declaration_is_provider_advertised():
    evidence = ev(declared={"model": ("sonnet", "opus")})
    assert admission.classify(evidence, "sonnet", now=NOW) == "PROVIDER_ADVERTISED"
    assert admission.classify(evidence, "opus", now=NOW) == "PROVIDER_ADVERTISED"


def test_an_id_outside_a_real_enumeration_is_not_advertised():
    evidence = ev(declared={"model": ("sonnet",)})
    # Still SCHEMA_ONLY, not UNKNOWN: the release does accept an arbitrary string.
    assert admission.classify(evidence, "opus", now=NOW) == "SCHEMA_ONLY"


def test_a_string_field_without_an_enum_is_schema_only():
    assert admission.classify(ev(), "anything-at-all", now=NOW) == "SCHEMA_ONLY"


def test_a_release_that_declares_no_model_field_at_all_is_unknown():
    evidence = ev(schema_types={"project": "string"})
    assert admission.classify(evidence, "sonnet", now=NOW) == "UNKNOWN"


def test_no_evidence_is_unknown():
    assert admission.classify(None, "sonnet", now=NOW) == "UNKNOWN"


def test_a_release_mismatch_is_stale():
    evidence = ev(release="1.40.0", declared={"model": ("sonnet",)})
    assert admission.classify(evidence, "sonnet", now=NOW) == "STALE"


def test_an_expired_capture_is_stale():
    stale_now = CAPTURED + dt.timedelta(hours=25)
    evidence = ev(declared={"model": ("sonnet",)})
    assert admission.classify(evidence, "sonnet", now=stale_now) == "STALE"


def test_a_capture_inside_the_window_is_not_stale():
    assert admission.classify(ev(), "sonnet", now=NOW) == "SCHEMA_ONLY"
    assert admission.classify(
        ev(), "sonnet", now=CAPTURED + dt.timedelta(hours=23)
    ) == "SCHEMA_ONLY"


def test_staleness_wins_over_an_enumeration():
    """An expired enumeration is not evidence, however explicit it looks."""
    evidence = ev(declared={"model": ("sonnet",)}, captured_at=CAPTURED)
    late = CAPTURED + dt.timedelta(days=2)
    assert admission.classify(evidence, "sonnet", now=late) == "STALE"


def test_a_capture_whose_age_cannot_be_computed_is_stale():
    naive = dt.datetime(2026, 10, 27, 8, 12, 0)  # no tzinfo
    assert admission.classify(ev(captured_at=naive), "sonnet", now=NOW) == "STALE"


# --------------------------------------------------------------------------
# C. known_models: the only place a catalogue could be invented
# --------------------------------------------------------------------------

def test_known_models_is_empty_for_the_real_release():
    assert admission.known_models(ev(), now=NOW) == ()


def test_known_models_returns_only_advertised_ids():
    evidence = ev(declared={"model": ("sonnet", "opus")})
    assert admission.known_models(evidence, now=NOW) == ("sonnet", "opus")


def test_known_models_is_empty_when_stale():
    late = CAPTURED + dt.timedelta(days=3)
    evidence = ev(declared={"model": ("sonnet", "opus")})
    assert admission.known_models(evidence, now=late) == ()


def test_known_models_ignores_the_grant_and_the_schema():
    """An allowlist is a ceiling on requests; it is not a catalogue.

    The signature has no grant parameter, which is the structural half of the
    claim; the behavioural half is that a schema-only release returns nothing
    even when the caller's grant names the id.
    """
    import inspect

    assert "grant" not in inspect.signature(admission.known_models).parameters
    grant = admission.ProjectGrant(models=("sonnet",))
    assert grant.names("sonnet") is True
    assert admission.known_models(ev(), now=NOW) == ()


def test_the_module_hardcodes_no_model_id():
    """A model id written from memory is a request, not a selection.

    Scanned at the AST level so comments and this test's own text cannot make
    the module look guilty; every string literal in the module is inspected.
    """
    source = (PACKAGE / "model_admission.py").read_text(encoding="utf-8")
    literals = [
        node.value for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
    ]
    suspects = ("sonnet", "opus", "haiku", "gpt-", "claude-", "gemini", "llama",
                "o1-", "o3-", "deepseek")
    offenders = [s for s in literals if any(x in s.lower() for x in suspects)]
    assert offenders == []


# --------------------------------------------------------------------------
# D. choose_model: explicit only when a real source and a grant agree
# --------------------------------------------------------------------------

def test_a_requested_model_the_provider_advertises_is_chosen_explicitly():
    evidence = book(ev(declared={"model": ("sonnet", "opus")}))
    choice = admission.choose_model(
        evidence_by_target=evidence,
        device_ref="device:tokyo", runtime="claude",
        requested="opus", grant_models=("opus",), now=NOW,
    )
    assert choice.decision == "EXPLICIT"
    assert choice.model == "opus"
    assert choice.model_identity == "EXPLICIT"
    assert choice.evidence_class == "PROVIDER_ADVERTISED"
    assert choice.support == "PROVIDER_ADVERTISED"
    assert choice.source == "provider"
    assert choice.code == "MODEL_ADVERTISED"


def test_a_requested_model_under_schema_only_falls_back_to_the_default():
    """The caller gets a session, but not the model it named — and is told so.

    Two facts have to survive side by side: what the device published
    (`SCHEMA_ONLY` — a string field, no enumeration) and what is the only thing
    warranting this particular id (`POLICY_ALLOWED_ONLY` — the grant lists it).
    Neither is a catalogue, so the model is the provider's, and its identity is
    unknown.
    """
    evidence = book(ev())
    choice = admission.choose_model(
        evidence_by_target=evidence,
        device_ref="device:tokyo", runtime="claude",
        requested="opus", grant_models=("opus",), now=NOW,
    )
    assert choice.decision == "PROVIDER_DEFAULT"
    assert choice.model is None
    assert choice.model_identity == "UNKNOWN"
    assert choice.evidence_class == "SCHEMA_ONLY"
    assert choice.support == "POLICY_ALLOWED_ONLY"
    assert choice.code == "MODEL_NOT_VERIFIED_DEFAULT_USED"


def test_a_required_model_under_schema_only_is_refused():
    evidence = book(ev())
    choice = admission.choose_model(
        evidence_by_target=evidence,
        device_ref="device:tokyo", runtime="claude",
        requested="opus", grant_models=("opus",), now=NOW,
        require_explicit=True,
    )
    assert choice.decision == "REFUSED"
    assert choice.model is None
    assert choice.evidence_class == "SCHEMA_ONLY"
    assert choice.code == "MODEL_NOT_VERIFIED"


def test_no_requested_model_means_the_provider_default_and_an_unknown_identity():
    choice = admission.choose_model(
        evidence_by_target=book(ev()),
        device_ref="device:tokyo", runtime="claude",
        requested=None, grant_models=(), now=NOW,
    )
    assert choice.decision == "PROVIDER_DEFAULT"
    assert choice.model is None
    assert choice.model_identity == "UNKNOWN"
    assert choice.code == "PROVIDER_DEFAULT"
    assert choice.source == "none"


def test_a_required_model_that_is_not_named_is_refused():
    choice = admission.choose_model(
        evidence_by_target=book(ev()),
        device_ref="device:tokyo", runtime="claude",
        requested=None, grant_models=(), now=NOW, require_explicit=True,
    )
    assert choice.decision == "REFUSED"
    assert choice.code == "MODEL_NOT_NAMED"


def test_a_grant_that_does_not_name_the_model_refuses_before_evidence_is_read():
    choice = admission.choose_model(
        evidence_by_target=_NeverRead(),
        device_ref="device:tokyo", runtime="claude",
        requested="opus", grant_models=("sonnet",), now=NOW,
    )
    assert choice.decision == "REFUSED"
    assert choice.code == "MODEL_POLICY_DENIED"
    assert choice.source == "grant"
    assert choice.evidence_class == "UNKNOWN"


def test_an_empty_grant_allowlist_permits_naming_no_model():
    """Empty means "the caller may name neither", never "any"."""
    choice = admission.choose_model(
        evidence_by_target=_NeverRead(),
        device_ref="device:tokyo", runtime="claude",
        requested="sonnet", grant_models=(), now=NOW,
    )
    assert choice.decision == "REFUSED"
    assert choice.code == "MODEL_POLICY_DENIED"


def test_policy_allowed_only_is_its_own_class_not_an_evidence_upgrade():
    """A grant naming an id does not make the device's silence into a yes."""
    choice = admission.choose_model(
        evidence_by_target=book(ev()),
        device_ref="device:tokyo", runtime="claude",
        requested="opus", grant_models=("opus", "sonnet"), now=NOW,
    )
    assert "POLICY_ALLOWED_ONLY" in admission.EVIDENCE_CLASSES
    assert choice.support == "POLICY_ALLOWED_ONLY"
    assert choice.evidence_class == "SCHEMA_ONLY"
    assert choice.decision != "EXPLICIT"
    assert choice.model is None


def test_support_names_one_of_the_five_classes():
    """Whichever way the decision goes, the warrant is a named class."""
    cases = [
        (book(ev(declared={"model": ("opus",)})), "opus", ("opus",), False),
        (book(ev()), "opus", ("opus",), False),
        (book(ev()), "opus", ("other",), False),
        (book(ev()), None, (), False),
        (book(), "opus", ("opus",), True),
    ]
    for evidence, requested, grants, strict in cases:
        choice = admission.choose_model(
            evidence_by_target=evidence, device_ref="device:tokyo",
            runtime="claude", requested=requested, grant_models=grants,
            now=NOW, require_explicit=strict,
        )
        assert choice.support in admission.EVIDENCE_CLASSES, (requested, grants)


def test_stale_evidence_never_yields_an_explicit_model():
    late = CAPTURED + dt.timedelta(days=2)
    evidence = book(ev(declared={"model": ("opus",)}))
    relaxed = admission.choose_model(
        evidence_by_target=evidence,
        device_ref="device:tokyo", runtime="claude",
        requested="opus", grant_models=("opus",), now=late,
    )
    assert relaxed.decision == "PROVIDER_DEFAULT"
    assert relaxed.model is None
    assert relaxed.evidence_class == "STALE"
    assert relaxed.code == "MODEL_EVIDENCE_STALE_DEFAULT_USED"

    strict = admission.choose_model(
        evidence_by_target=evidence,
        device_ref="device:tokyo", runtime="claude",
        requested="opus", grant_models=("opus",), now=late,
        require_explicit=True,
    )
    assert strict.decision == "REFUSED"
    assert strict.code == "MODEL_EVIDENCE_STALE"


def test_an_unknown_target_never_yields_an_explicit_model():
    choice = admission.choose_model(
        evidence_by_target=book(),
        device_ref="device:tokyo", runtime="claude",
        requested="opus", grant_models=("opus",), now=NOW,
        require_explicit=True,
    )
    assert choice.decision == "REFUSED"
    assert choice.code == "MODEL_UNKNOWN"


def test_a_model_advertised_on_another_device_is_a_device_mismatch():
    evidence = book(ev("device:dell", "claude", declared={"model": ("opus",)}))
    choice = admission.choose_model(
        evidence_by_target=evidence,
        device_ref="device:tokyo", runtime="claude",
        requested="opus", grant_models=("opus",), now=NOW,
        require_explicit=True,
    )
    assert choice.decision == "REFUSED"
    assert choice.code == "MODEL_DEVICE_MISMATCH"


def test_a_model_advertised_for_another_runtime_is_a_runtime_mismatch():
    evidence = book(ev("device:win", "codex", declared={"model": ("opus",)}))
    choice = admission.choose_model(
        evidence_by_target=evidence,
        device_ref="device:win", runtime="claude",
        requested="opus", grant_models=("opus",), now=NOW,
        require_explicit=True,
    )
    assert choice.decision == "REFUSED"
    assert choice.code == "MODEL_RUNTIME_MISMATCH"


def test_the_same_string_for_claude_and_codex_is_two_different_facts():
    """One id, two runtimes: the Claude advertisement says nothing about Codex."""
    evidence = book(
        ev("device:win", "claude", declared={"model": ("shared-id",)}),
        ev("device:win", "codex", schema_types={"model": "string"}),
    )
    claude = admission.choose_model(
        evidence_by_target=evidence, device_ref="device:win", runtime="claude",
        requested="shared-id", grant_models=("shared-id",), now=NOW,
    )
    codex = admission.choose_model(
        evidence_by_target=evidence, device_ref="device:win", runtime="codex",
        requested="shared-id", grant_models=("shared-id",), now=NOW,
        require_explicit=True,
    )
    assert claude.decision == "EXPLICIT"
    assert codex.decision == "REFUSED"
    assert codex.code == "MODEL_RUNTIME_MISMATCH"


def test_the_same_model_name_across_devices_is_not_reusable():
    evidence = book(
        ev("device:dell", "claude", declared={"model": ("sonnet",)}),
        ev("device:tokyo", "claude", declared={"model": ("opus",)}),
    )
    assert admission.choose_model(
        evidence_by_target=evidence, device_ref="device:dell", runtime="claude",
        requested="sonnet", grant_models=("sonnet",), now=NOW,
    ).decision == "EXPLICIT"
    tokyo = admission.choose_model(
        evidence_by_target=evidence, device_ref="device:tokyo", runtime="claude",
        requested="sonnet", grant_models=("sonnet",), now=NOW,
        require_explicit=True,
    )
    assert tokyo.decision == "REFUSED"


def test_an_alias_is_not_the_base_runtime():
    """`claude-code` evidence must not admit a request for `claude`."""
    evidence = book(ev("device:tokyo", "claude-code", declared={"model": ("opus",)}))
    choice = admission.choose_model(
        evidence_by_target=evidence, device_ref="device:tokyo", runtime="claude",
        requested="opus", grant_models=("opus",), now=NOW, require_explicit=True,
    )
    assert choice.decision == "REFUSED"
    assert choice.code == "MODEL_RUNTIME_MISMATCH"


def test_every_decision_records_one_of_three_sources():
    """`source` is never blank: a reader can tell who decided."""
    cases = [
        dict(evidence_by_target=book(ev(declared={"model": ("a",)})),
             requested="a", grant_models=("a",), require_explicit=False),
        dict(evidence_by_target=book(ev()), requested="a",
             grant_models=("a",), require_explicit=False),
        dict(evidence_by_target=_NeverRead(), requested="a",
             grant_models=(), require_explicit=False),
        dict(evidence_by_target=book(ev()), requested=None,
             grant_models=(), require_explicit=False),
    ]
    for case in cases:
        choice = admission.choose_model(
            device_ref="device:tokyo", runtime="claude", now=NOW, **case
        )
        assert choice.source in {"provider", "grant", "none"}


def test_choose_model_never_calls_a_provider():
    """Pure by construction: the only callables it may reach are its own."""
    source = (PACKAGE / "model_admission.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".")[0])
    assert imported <= {"__future__", "dataclasses", "datetime", "typing",
                        "collections", "enum"}


# --------------------------------------------------------------------------
# E. preflight_create: project authorisation is the first gate
# --------------------------------------------------------------------------

def _allow(models=(), modes=()):
    return {"permission_modes": tuple(modes), "models": tuple(models)}


def _request(**over):
    base = dict(device_ref="device:tokyo", runtime="claude",
                project_id="p1", model=None, permission_mode=None)
    base.update(over)
    return admission.CreateRequest(**base)


def test_the_preflight_order_is_pinned():
    assert admission.PREFLIGHT_ORDER == (
        "PROJECT_NOT_AUTHORIZED",
        "CREATE_NOT_ALLOWED_FOR_DEVICE",
        "PERMISSION_MODE_NOT_GRANTED",
        "MODEL_POLICY_DENIED",
        "MODEL_ADMISSION",
    )


def test_a_project_outside_create_allow_is_refused_first():
    result = admission.preflight_create(
        _request(model="opus", permission_mode="bypassPermissions"),
        create_allow={"other": _allow()},
        create_allowed_for_device=True,
        evidence_by_target=_NeverRead(),
        now=NOW,
    )
    assert result.allowed is False
    assert result.code == "PROJECT_NOT_AUTHORIZED"
    assert result.provider_call_required is False
    assert result.order[0] == "PROJECT_NOT_AUTHORIZED"


def test_project_authorisation_precedes_the_mode_and_the_model():
    """Both other gates would also refuse; the project answer is the one reported."""
    result = admission.preflight_create(
        _request(model="opus", permission_mode="bypassPermissions"),
        create_allow={"p1": _allow()},
        create_allowed_for_device=False,
        evidence_by_target=_NeverRead(),
        now=NOW,
    )
    assert result.code == "CREATE_NOT_ALLOWED_FOR_DEVICE"
    assert result.order == ("PROJECT_NOT_AUTHORIZED",
                            "CREATE_NOT_ALLOWED_FOR_DEVICE")


def test_an_ungranted_permission_mode_is_refused_before_the_model():
    result = admission.preflight_create(
        _request(model="opus", permission_mode="bypassPermissions"),
        create_allow={"p1": _allow(modes=("default",))},
        create_allowed_for_device=True,
        evidence_by_target=_NeverRead(),
        now=NOW,
    )
    assert result.code == "PERMISSION_MODE_NOT_GRANTED"
    assert result.order[-1] == "PERMISSION_MODE_NOT_GRANTED"


def test_a_model_the_project_grant_does_not_name_is_refused_before_evidence():
    result = admission.preflight_create(
        _request(model="opus"),
        create_allow={"p1": _allow(models=("sonnet",))},
        create_allowed_for_device=True,
        evidence_by_target=_NeverRead(),
        now=NOW,
    )
    assert result.code == "MODEL_POLICY_DENIED"
    assert result.choice is not None
    assert result.choice.source == "grant"


def test_a_refused_preflight_never_requires_a_provider_call():
    refusals = [
        dict(request=_request(model="opus"), allow={"other": _allow()},
             device=True),
        dict(request=_request(), allow={"p1": _allow()}, device=False),
        dict(request=_request(permission_mode="plan"),
             allow={"p1": _allow(modes=("default",))}, device=True),
        dict(request=_request(model="opus"), allow={"p1": _allow()},
             device=True),
    ]
    for case in refusals:
        result = admission.preflight_create(
            case["request"], create_allow=case["allow"],
            create_allowed_for_device=case["device"],
            evidence_by_target=_NeverRead(), now=NOW,
        )
        assert result.allowed is False
        assert result.provider_call_required is False


def test_an_allowed_preflight_says_the_provider_must_be_asked():
    result = admission.preflight_create(
        _request(), create_allow={"p1": _allow()},
        create_allowed_for_device=True,
        evidence_by_target=book(ev()), now=NOW,
    )
    assert result.allowed is True
    assert result.code == "CREATE_PREFLIGHT_OK"
    assert result.provider_call_required is True
    assert result.order == admission.PREFLIGHT_ORDER


def test_an_allowed_preflight_carries_the_default_choice():
    result = admission.preflight_create(
        _request(), create_allow={"p1": _allow()},
        create_allowed_for_device=True,
        evidence_by_target=book(ev()), now=NOW,
    )
    assert result.choice is not None
    assert result.choice.decision == "PROVIDER_DEFAULT"
    assert result.choice.model is None
    assert result.choice.model_identity == "UNKNOWN"


def test_a_preflight_that_requires_a_model_refuses_the_unverified_one():
    result = admission.preflight_create(
        _request(model="opus"), create_allow={"p1": _allow(models=("opus",))},
        create_allowed_for_device=True,
        evidence_by_target=book(ev()), now=NOW, require_explicit_model=True,
    )
    assert result.allowed is False
    assert result.code == "MODEL_NOT_VERIFIED"
    assert result.provider_call_required is False


# --------------------------------------------------------------------------
# F. a rejected model is not a retry
# --------------------------------------------------------------------------

def test_a_model_rejection_is_never_retried_on_a_paid_create():
    for code in ("MODEL_NOT_FOUND", "UNKNOWN_MODEL", "INVALID_MODEL"):
        decision = admission.retry_decision(
            verb="session_create", code=code, retryable=None,
            attempt=1, paid=True,
        )
        assert decision.retry is False
        assert decision.code == "MODEL_REJECTION_NOT_RETRYABLE"


def test_a_provider_retryable_false_is_obeyed():
    decision = admission.retry_decision(
        verb="session_create", code="UNSUPPORTED_PERMISSION_MODE",
        retryable=False, attempt=1, paid=True,
    )
    assert decision.retry is False
    assert decision.code == "PROVIDER_SAYS_NOT_RETRYABLE"


def test_a_provider_retryable_true_is_obeyed_on_a_verb_that_minted_nothing():
    """`SESSION_BUSY` is `retryable: true` in the capture; a send that was
    refused never produced a delivery, so re-sending is not a second charge."""
    decision = admission.retry_decision(
        verb="session_send", code="SESSION_BUSY", retryable=True,
        attempt=1, paid=False,
    )
    assert decision.retry is True
    assert decision.code == "PROVIDER_SAYS_RETRYABLE"


def test_a_retryable_flag_cannot_excuse_a_second_paid_session():
    decision = admission.retry_decision(
        verb="session_create", code="SOMETHING_ODD", retryable=True,
        attempt=1, paid=True,
    )
    assert decision.retry is False
    assert decision.code == "PAID_SESSION_RETRY_FORBIDDEN"


def test_retrying_with_a_different_model_is_forbidden():
    decision = admission.retry_decision(
        verb="session_create", code="MODEL_NOT_FOUND", retryable=True,
        attempt=1, paid=True, first_model="opus", next_model="sonnet",
    )
    assert decision.retry is False
    assert decision.code == "MODEL_SUBSTITUTION_FORBIDDEN"


def test_an_unknown_code_on_a_paid_create_fails_closed():
    decision = admission.retry_decision(
        verb="session_create", code=None, retryable=None, attempt=1, paid=True,
    )
    assert decision.retry is False
    assert decision.code == "PAID_SESSION_RETRY_FORBIDDEN"


def test_the_attempt_ceiling_is_enforced():
    first = admission.retry_decision(
        verb="session_send", code="SESSION_BUSY", retryable=True,
        attempt=1, paid=False, max_attempts=2,
    )
    second = admission.retry_decision(
        verb="session_send", code="SESSION_BUSY", retryable=True,
        attempt=2, paid=False, max_attempts=2,
    )
    assert first.retry is True
    assert second.retry is False
    assert second.code == "ATTEMPTS_EXHAUSTED"


# --------------------------------------------------------------------------
# G. Run, Delivery and Session stay three sources
# --------------------------------------------------------------------------

def test_a_session_only_create_reports_no_delivery_and_no_run():
    """`create_no_message_*` returns `["sessionId"]` and nothing else."""
    result = admission.dispatch_sources({"sessionId": "s-1"})
    assert result["session"] == "provider"
    assert result["delivery"] == "absent"
    assert result["run"] == "absent"
    assert result["disagreement"] == ()


def test_a_per_run_create_reports_three_independent_provider_identities():
    """`create_with_message_*` returns `["deliveryId", "runId", "sessionId"]`."""
    result = admission.dispatch_sources(
        {"deliveryId": "d-1", "runId": "r-1", "sessionId": "s-1"}
    )
    assert result["delivery"] == "provider"
    assert result["run"] == "provider"
    assert result["session"] == "provider"


def test_a_queued_send_separates_the_delivery_from_the_run():
    """`send_*` returns six fields; `queued` is the only one that is a boolean."""
    result = admission.dispatch_sources({
        "blockedBy": None, "deliveryId": "d-1", "queued": True,
        "queuedMessageUuid": "q-1", "runId": "r-1", "source": "run",
    })
    assert result["delivery"] == "provider"
    assert result["run"] == "provider"
    assert result["queued"] == "provider"
    assert result["declared_source"] == "run"
    assert result["disagreement"] == ()


def test_an_absent_identity_is_absent_not_synthesised():
    result = admission.dispatch_sources({"deliveryId": "d-1", "queued": True})
    assert result["run"] == "absent"
    assert result["session"] == "absent"


def test_a_malformed_identity_is_reported_not_dropped():
    result = admission.dispatch_sources({"runId": "bad\nid", "deliveryId": "d-1"})
    assert result["run"] == "malformed"
    assert result["delivery"] == "provider"


def test_the_declared_source_label_cannot_stand_in_for_an_identity():
    """A `source` of `run` with no `runId` is a disagreement, not a run."""
    result = admission.dispatch_sources({"deliveryId": "d-1", "source": "run"})
    assert result["run"] == "absent"
    assert result["disagreement"] == ("run",)


def test_an_unrecognised_source_label_is_carried_verbatim_and_not_trusted():
    """The label's vocabulary is unverified, so unknown words change nothing."""
    result = admission.dispatch_sources({"deliveryId": "d-1", "source": "??"})
    assert result["declared_source"] == "??"
    assert result["disagreement"] == ()


def test_run_and_delivery_are_independent_dimensions():
    """Holding one identity tells you nothing about the other three.

    Each payload below carries exactly one identity kind; the table says which
    dimensions it must light up. A `queued: false` is still read (the field was
    answered, with "not queued"), which is why it counts as present.
    """
    table = [
        ({"runId": "r-1"}, {"run"}),
        ({"deliveryId": "d-1"}, {"delivery"}),
        ({"queuedMessageUuid": "q-1"}, {"delivery"}),
        ({"queued": False}, {"queued"}),
        ({"sessionId": "s-1"}, {"session"}),
    ]
    for payload, expected in table:
        result = admission.dispatch_sources(payload)
        present = {k for k in ("run", "delivery", "queued", "session")
                   if result[k] == "provider"}
        assert present == expected, payload


def test_the_identity_field_sets_are_named_and_disjoint():
    assert admission.DELIVERY_FIELDS == ("deliveryId", "queuedMessageUuid")
    assert admission.RUN_FIELDS == ("runId", "bootId")
    assert admission.SESSION_FIELDS == ("sessionId",)
    assert not (set(admission.DELIVERY_FIELDS) & set(admission.RUN_FIELDS))


# --------------------------------------------------------------------------
# H. the inlined fixture still matches the real capture
# --------------------------------------------------------------------------

@pytest.mark.skipif(not EVIDENCE_1_41.exists(),
                    reason="EVIDENCE-1.41.0.json is owned by #458 and is not on this base")
def test_the_inlined_fixture_matches_the_real_capture():
    real = json.loads(EVIDENCE_1_41.read_text(encoding="utf-8"))
    assert real["source"]["package"] == ONE_FORTY_ONE["release"]
    assert real["source"]["tools_list_sha256"] == TOOLS_LIST_SHA256
    for name, shape in ONE_FORTY_ONE["tools"].items():
        assert real["tools"][name]["required"] == shape["required"]
        assert real["tools"][name]["properties"] == shape["properties"]
        assert real["tools"][name]["types"] == shape["types"]
        # No shape carries an enumeration — that is the whole finding.
        assert "enum" not in json.dumps(real["tools"][name])
    assert sorted(real["declared_by_release"]["1.41.0"]) == sorted(
        n for n in real["tools"]
    )
    assert not [n for n in real["tools"] if "model" in n or "agent" in n]
