#!/usr/bin/env python3
"""§5 — identity is a lookup, not an argument.

Every test here is a way the property could be lost without anybody noticing.

The property is that an Agent runtime cannot produce a trusted actor context.
Not "should not", not "is discouraged from" — cannot, because there is no
function it could call that takes its word for who a person is. Three separate
mechanisms hold it up, and they fail independently:

    1. `build()` is called with what a PROVIDER reported, and refuses a report
       that identifies nobody.
    2. The daemon refuses a request that carries identity keys by name.
    3. `agent.context.attach` binds the report to the pid that observed it,
       from the kernel's `SO_PEERCRED` — a value no client can write.

A test that only covered (1) would pass against a daemon that accepted
`{"actor": "hwz"}` straight from a socket. So all three are here.
"""
from __future__ import annotations

import datetime
import importlib.util
import inspect
import json
import os
import socket
import sys
import threading
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
RUNTIME = REPO / "runtime"
MCP = REPO / "mcp"
CONTEXT = REPO / "context"


def _load(name: str, path: Path):
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


# Loaded by module name because `runtime/daemon.py` loads them under exactly
# these names — loading a second copy would give the daemon one class and the
# test another, and `isinstance` across the two would be silently false.
actor = _load("_serveros_context_actor", CONTEXT / "actor.py")
store_mod = _load("serveros_runtime_agent_control_store", RUNTIME / "agent_control_store.py")
approval_mod = _load("serveros_runtime_approval", RUNTIME / "approval.py")
daemon_mod = _load("serveros_runtime_daemon", RUNTIME / "daemon.py")
human_control = _load("serveros_mcp_human_control", MCP / "human_control.py")
agent_sessions = _load("serveros_runtime_agent_sessions", RUNTIME / "agent_sessions.py")

AgentControlStore = store_mod.AgentControlStore
Rejected = actor.Rejected
ForgedContext = actor.ForgedContext
BotMuxHumanControlProvider = human_control.BotMuxHumanControlProvider

VERIFIED_REPORT = {
    "human_control_provider": "provider:botmux-human-control",
    "transport": "feishu",
    "transport_identity": "botmux-verified-enterprise-user",
    "conversation_ref": "oc_conversation",
    "message_ref": "om_message",
    "observed_at": "2026-09-14T00:00:00+00:00",
    "provider_status": "VERIFIED",
    "provenance": "FACT",
    "actor_ref": "human:hwz",
    "provider_pid": 4242,
}

NOW = "2026-09-14T00:00:30+00:00"


def _epoch(stamp: str) -> float:
    """A stamp in the seconds `Daemon(now=...)` takes.

    The fixture daemons below FREEZE their clock so their tests are
    deterministic, and that freeze has to sit AT the instant the fixture reports
    observe. A frozen clock that disagrees with `observed_at` turns every
    freshness assertion in this file into a statement about the disagreement
    rather than about the record — which is what it was: the clock was frozen at
    `2026-02-25T06:13:20+00:00` while every report in the file said
    `2026-09-14`, 200 days apart, and the answer that came back from
    `agent.context.attach` was `FRESH`. That is exactly the defect
    `test_a_record_dated_ahead_of_the_clock_is_expired_and_not_fresh` names,
    reached through the fixture instead of through a caller. Derived from the
    stamp rather than written as a literal, so the two cannot drift apart again.
    """
    return datetime.datetime.fromisoformat(stamp).timestamp()


# ---------------------------------------------------------------------------
# The refs
# ---------------------------------------------------------------------------

def test_context_ref_round_trips_and_is_prefixed():
    ref = actor.context_ref("provider:botmux-human-control", "abc123")
    assert ref.startswith("human-context:")
    assert actor.parse_context_ref(ref) == ("provider:botmux-human-control", "abc123")


def test_parse_refuses_a_ref_that_is_not_a_context_ref():
    # A session ref, a device ref and a bare string all arrive here sooner or
    # later. Each is refused by name rather than looked up and missed, because
    # a miss reads as "expired" and sends the caller to re-authenticate.
    for wrong in ("agent-session:d/p/s", "device:server-beijing", "", "human:x/y/z"):
        with pytest.raises(Rejected):
            actor.parse_context_ref(wrong)


def test_actor_ref_refuses_to_wrap_an_already_typed_ref():
    with pytest.raises(Rejected):
        actor.actor_ref("human:hwz")


def test_a_ref_names_the_system_and_the_id_space_it_came_from():
    # The namespace is not decoration and the kind is not either: together they
    # are what let a reader holding only the string tell a developer-scoped id
    # from an app-scoped one, and what keep two systems' ids from reading as one
    # person.
    assert actor.actor_ref("on_alpha", namespace="feishu", kind="union") == \
        "feishu:union:on_alpha"


def test_the_original_human_ref_is_still_produced_byte_for_byte():
    # `human:` is not migrated to the new spelling. Contexts already minted carry
    # it, and `Store.for_actor_action` matches approvals on the ref verbatim, so
    # a grammar change here would make live records unreadable — a rename wearing
    # a security change's clothes.
    assert actor.actor_ref("hwz") == "human:hwz"


def test_a_separator_inside_a_value_cannot_become_a_segment_boundary():
    # Why the constructor exists instead of an f-string. An id of `a:b` under
    # kind `union`, and an id of `b` under a kind called `union:a`, would produce
    # the same string if the value were interpolated — and a reader parsing that
    # string would then be choosing an actor for a caller that wrote something
    # ambiguous. Here the separator is written by the constructor and nowhere
    # else.
    ref = actor.actor_ref("a:b", namespace="feishu", kind="union")
    assert ref == "feishu:union:a%3Ab"
    assert actor.parse_actor_ref(ref) == ("feishu", "union", "a:b")


def test_parse_actor_ref_returns_the_namespace_the_kind_and_the_id():
    assert actor.parse_actor_ref("human:hwz") == ("human", None, "hwz")
    assert actor.parse_actor_ref("feishu:union:on_alpha") == \
        ("feishu", "union", "on_alpha")


@pytest.mark.parametrize("wrong", [
    "on_alpha",                 # a bare id carrying no namespace at all
    "device:server-beijing",    # a ref namespace, but not an actor one
    "feishu:union:",            # a namespace and a kind with nobody in them
    "feishu:union:a:b",         # a hand-written separator: two readings
    "feishu:nickname:linear",   # a kind this grammar does not know
    "human:opaque:x",           # `human:` has no kinds; this is one long id
    "",
])
def test_parse_actor_ref_refuses_everything_else(wrong):
    with pytest.raises(Rejected):
        actor.parse_actor_ref(wrong)


def test_actor_ref_refuses_a_namespace_or_a_kind_it_does_not_know():
    with pytest.raises(Rejected):
        actor.actor_ref("hwz", namespace="teams")
    with pytest.raises(Rejected):
        actor.actor_ref("hwz", namespace="feishu", kind="nickname")


def test_normalize_actor_ref_takes_either_an_id_or_a_whole_ref():
    assert actor.normalize_actor_ref("hwz") == "human:hwz"
    assert actor.normalize_actor_ref("feishu:union:on_alpha") == \
        "feishu:union:on_alpha"


@pytest.mark.parametrize("wrong", ["feishu:", "feishu:union:", "human:"])
def test_normalize_actor_ref_refuses_a_typed_prefix_that_does_not_parse(wrong):
    # It must NOT wrap these as `human:<value>`. That would record an actor
    # named "feishu:" and report success, which is the one outcome a normalizer
    # standing between a person and a record must not produce.
    with pytest.raises(Rejected):
        actor.normalize_actor_ref(wrong)


def test_build_keeps_the_namespace_and_kind_of_a_namespaced_ref():
    record = actor.build({**VERIFIED_REPORT, "actor_ref": "feishu:union:on_alpha"},
                         now=NOW)
    assert record["actor_ref"] == "feishu:union:on_alpha"
    actor.validate(record)


# ---------------------------------------------------------------------------
# Forgery refusal — mechanism 2, the one the request itself trips
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("key", ["human", "actor", "actor_ref", "approved",
                                 "user", "user_id", "open_id", "email"])
def test_every_forbidden_key_is_refused_by_name(key):
    with pytest.raises(ForgedContext) as caught:
        actor.refuse_if_forged({"action": "session.stop", key: "hwz"})
    assert key in str(caught.value), (
        "the refusal must name the offending key; a caller that is told only "
        "'forbidden' learns nothing about which field to remove")


def test_forged_payload_reports_every_offending_key_at_once():
    assert actor.forged_payload({"human": "a", "approved": True, "action": "x"}) == \
        ["approved", "human"]


def test_the_canonical_agent_self_approval_is_refused():
    # The exact payload §5 forbids: an agent asserting it is a person and that
    # the person approved. Both halves are refused, and the refusal happens
    # before any lookup, so there is no path where a partially-trusted record
    # is written first and validated later.
    with pytest.raises(ForgedContext):
        actor.refuse_if_forged({"action": "session.interrupt",
                                "context_ref": "human-context:x/y",
                                "human": "hwz", "approved": True})


def test_a_clean_payload_passes():
    assert actor.forged_payload({"action": "session.list", "context": {}}) == []
    actor.refuse_if_forged({"action": "session.list", "context": {}})


# ---------------------------------------------------------------------------
# Building — mechanism 1
# ---------------------------------------------------------------------------

def test_a_verified_report_builds_a_context():
    record = actor.build(VERIFIED_REPORT, now=NOW)
    assert record["actor_ref"] == "human:hwz"
    assert record["provider_status"] == "VERIFIED"
    assert record["provenance"] == "FACT"
    actor.validate(record)


def test_the_same_event_from_the_same_provider_is_one_context():
    # Idempotency is not a de-duplication pass; it falls out of the ref being a
    # hash of the provider's own fields. A second report of one event must not
    # mint a second context, or replaying a report would multiply evidence.
    first = actor.build(VERIFIED_REPORT, now=NOW)
    second = actor.build({**VERIFIED_REPORT, "provider_pid": 9999}, now="2026-09-14T00:00:45+00:00")
    assert first["context_ref"] == second["context_ref"], (
        "provider_pid is deliberately outside the digest: it binds the report "
        "to a process, and a restart must not make the same event a new context")


def test_a_blocked_provider_produces_no_context_at_all():
    # The live answer from this device. It must not produce a context with an
    # empty actor, a context attributed to "unknown", or an exception the
    # caller can catch and continue past.
    report = {**VERIFIED_REPORT, "provider_status": "BLOCKED", "provenance": "UNKNOWN",
              "actor_ref": None}
    with pytest.raises(Rejected) as caught:
        actor.build(report, now=NOW)
    assert "identifies no human" in str(caught.value)


def test_a_verified_report_cannot_carry_unknown_provenance():
    with pytest.raises(Rejected):
        actor.build({**VERIFIED_REPORT, "provenance": "UNKNOWN"}, now=NOW)


def test_an_unverified_report_cannot_carry_fact():
    with pytest.raises(Rejected):
        actor.build({**VERIFIED_REPORT, "provider_status": "BLOCKED",
                     "provenance": "FACT"}, now=NOW)


def test_a_report_missing_evidence_fields_is_refused():
    for missing in ("transport", "conversation_ref", "observed_at"):
        report = {k: v for k, v in VERIFIED_REPORT.items() if k != missing}
        with pytest.raises(Rejected):
            actor.build(report, now=NOW)


def test_a_report_observed_after_the_reading_clock_is_refused():
    # `expires_at` is DERIVED from `observed_at`, so a future stamp mints a
    # window that has not opened yet and stays open long past the event — and
    # `freshness` would call it FRESH, because a negative age is below every
    # threshold. Clamped to `now` would be worse than refused: it would produce
    # a context about an observation nobody made. This is the only place a
    # context is minted, so refusing here covers every writer.
    with pytest.raises(Rejected) as caught:
        actor.build({**VERIFIED_REPORT, "observed_at": "2026-09-14T00:05:00+00:00"},
                    now=NOW)
    assert "2026-09-14T00:05:00+00:00" in str(caught.value), (
        "the refusal must name the stamp it refused")
    assert NOW in str(caught.value), (
        "...and the clock it was measured against, since the two together are "
        "the whole finding")


def test_a_report_observed_exactly_at_the_reading_clock_is_accepted():
    # The boundary, and the reason the comparison is `>` rather than `>=`: a
    # provider that probes and reports inside one call can land on the same
    # second as the read, and refusing that would refuse the honest case while
    # the dishonest one is minutes away. Age zero is FRESH.
    record = actor.build({**VERIFIED_REPORT, "observed_at": NOW}, now=NOW)
    assert record["expires_at"] > NOW
    assert actor.freshness(record, NOW)["state"] == "FRESH"


def test_a_context_with_no_window_is_refused():
    with pytest.raises(Rejected):
        actor.validate({"context_ref": "human-context:p/d", "actor_ref": "human:hwz",
                        "observed_at": "2026-09-14T00:00:00+00:00",
                        "expires_at": "2026-09-14T00:00:00+00:00"})


def test_an_unparseable_timestamp_is_refused():
    # A record that cannot be aged can never be found stale, which is the
    # failure mode of every "expires: yes, but nothing computes it" field.
    record = actor.build(VERIFIED_REPORT, now=NOW)
    record["expires_at"] = "soon"
    with pytest.raises(Rejected):
        actor.validate(record)


# ---------------------------------------------------------------------------
# Freshness
# ---------------------------------------------------------------------------

def test_freshness_is_computed_from_the_clock_not_read_from_the_record():
    record = actor.build(VERIFIED_REPORT, now=NOW)
    assert actor.freshness(record, "2026-09-14T00:00:30+00:00")["state"] == "FRESH"
    assert actor.freshness(record, "2026-09-14T00:10:00+00:00")["state"] == "STALE"
    assert actor.freshness(record, "2026-09-14T00:20:00+00:00")["state"] == "EXPIRED"


def test_a_record_dated_ahead_of_the_clock_is_expired_and_not_fresh():
    # `build` refuses to mint one, so this record is written the way a second
    # writer or an edited store would write it — which is why the reader is
    # hardened separately. It must not read as FRESH: a negative age means the
    # two clocks disagree about which instant the record belongs to, and the
    # bucket that authorizes nothing is the one that can be wrong safely.
    record = actor.build(VERIFIED_REPORT, now=NOW)
    record["observed_at"] = "2026-09-14T00:05:00+00:00"      # 270s ahead of NOW
    fresh = actor.freshness(record, NOW)
    assert fresh["state"] == "EXPIRED"
    assert fresh["age_seconds"] == -270.0
    assert "FUTURE" in fresh["because"], (
        "the reason must name the real one; 'observed Ns ago, past the 900s "
        "window' about a record dated in the future is precisely the "
        "confident-but-wrong sentence this module refuses to emit")
    assert "ago" not in fresh["because"]


def test_a_context_dated_ahead_of_the_clock_authorizes_nothing():
    # The exact shape `context/delegation.check` and `abilities/target.py` read:
    # `.get("authorizes")` and `.get("because")`, with `state` carried through.
    # A refusal that dropped `state` would break the display path, and a
    # `because` naming the wrong cause sends the operator to fix the wrong thing.
    record = actor.build(VERIFIED_REPORT, now=NOW)
    record["observed_at"] = "2026-09-14T00:05:00+00:00"
    verdict = actor.authorizes(record, NOW)
    assert verdict["authorizes"] is False
    assert verdict["state"] == "EXPIRED"
    assert "FUTURE" in verdict["because"]
    assert verdict["provenance"] == "FACT", (
        "the refusal is about the clock, not about the evidence: a verdict "
        "that also degraded the provenance would send the reader to re-verify "
        "an actor that was verified correctly")


def test_stale_does_not_authorize():
    record = actor.build(VERIFIED_REPORT, now=NOW)
    verdict = actor.authorizes(record, "2026-09-14T00:10:00+00:00")
    assert verdict["authorizes"] is False
    assert verdict["state"] == "STALE"


def test_fresh_and_verified_authorizes():
    record = actor.build(VERIFIED_REPORT, now=NOW)
    assert actor.authorizes(record, "2026-09-14T00:00:30+00:00")["authorizes"] is True


def test_authorizes_says_which_of_the_three_conditions_failed():
    # "no" is not an answer a requester can act on. The reason names whether it
    # was identity, provenance or time — three different repairs.
    blocked = {**VERIFIED_REPORT, "provider_status": "BLOCKED", "provenance": "UNKNOWN",
               "actor_ref": "human:hwz"}
    record = actor.build(blocked, now=NOW)
    verdict = actor.authorizes(record, "2026-09-14T00:00:30+00:00")
    assert verdict["authorizes"] is False
    assert "did not verify this actor" in verdict["because"]


# ---------------------------------------------------------------------------
# Resolution — mechanism 3's read half
# ---------------------------------------------------------------------------

def test_resolving_a_ref_serveros_never_minted_is_a_miss_not_a_default():
    result = actor.resolve("human-context:nobody/neverminted", {})
    assert result == {"found": False, "context_ref": "human-context:nobody/neverminted",
                      "because": "no context with this ref was reported by any "
                                 "HumanControlProvider"}


def test_resolving_a_malformed_ref_is_a_named_miss_not_a_raise():
    # Returned, not raised. A caller that invented a handle must not be able to
    # catch an exception and continue with a substitute context.
    result = actor.resolve("device:server-beijing", {"device:server-beijing": {"x": 1}})
    assert result["found"] is False
    assert "not a trusted actor context ref" in result["because"]


def test_resolving_a_stored_ref_returns_the_record():
    record = actor.build(VERIFIED_REPORT, now=NOW)
    found = actor.resolve(record["context_ref"], {record["context_ref"]: record})
    assert found["found"] is True and found["context"] is record


# ---------------------------------------------------------------------------
# The provider adapter
# ---------------------------------------------------------------------------

def test_a_blocked_actor_command_is_reported_as_blocked_not_unsupported():
    # The measured answer on this device, replayed through the real code path.
    provider = BotMuxHumanControlProvider(
        runner=lambda: {"schema": "botmux.current-actor.v2", "status": "blocked",
                        "error": "current_actor_unverified"},
        clock=lambda: NOW)
    report = provider.identify(conversation_ref="oc_x")
    assert report.status == "BLOCKED"
    assert report.actor_id is None
    assert "current_actor_unverified" in report.detail


def test_a_changed_actor_schema_is_refused_rather_than_read():
    # The `declared-but-absent` pathology, defended at the one place where it
    # would be a security failure: reading `user` out of a document that no
    # longer has one yields None, and None at the call site looks exactly like
    # "no user" rather than "this is not the document I think it is".
    #
    # The schema here is one no daemon has ever answered with. v3 is accepted
    # now, so it can no longer carry this test — a fixture that used it would
    # have been testing the version check with a version that passes it.
    provider = BotMuxHumanControlProvider(
        runner=lambda: {"schema": "botmux.current-actor.v9", "user": "hwz"},
        clock=lambda: NOW)
    report = provider.identify()
    assert report.status == "BLOCKED"
    assert "schema" in report.detail
    assert report.actor_id is None
    # The accepted set is named in the refusal, so an operator reading it knows
    # whether the daemon is newer than this reader or older.
    assert "botmux.current-actor.v3" in report.detail
    assert "botmux.current-actor.v2" in report.detail


def test_verified_without_an_actor_is_blocked_not_a_private_turn():
    # A verification with no identity is a contradiction in the provider's own
    # document. Resolving it as "no actor" would turn a broken provider into
    # something that looks like an ordinary unauthenticated turn.
    provider = BotMuxHumanControlProvider(
        runner=lambda: {"schema": "botmux.current-actor.v2",
                        "status": "verified"},
        clock=lambda: NOW)
    report = provider.identify()
    assert report.status == "BLOCKED"
    assert "no actor.email" in report.detail


def test_the_status_this_cli_never_emits_is_refused_by_name():
    # The `declared-but-absent` pathology again, at the field that decides
    # everything: `"ok"` is what the binary's OTHER subsystems answer with, and
    # it is not a word the actor document uses. A reader that accepted it would
    # be reading a different document than the one being served, and would do
    # so silently — the failure mode is a green light, not an exception.
    provider = BotMuxHumanControlProvider(
        runner=lambda: {"schema": "botmux.current-actor.v2", "status": "ok",
                        "user": "hwz"},
        clock=lambda: NOW)
    report = provider.identify()
    assert report.status == "BLOCKED"
    assert "'ok'" in report.detail
    assert report.actor_id is None


def test_a_verified_answer_produces_the_full_report_and_a_context():
    # The measured success document, verbatim in shape: the literal is
    # `verified`, and the identity is an email under `actor`.
    provider = BotMuxHumanControlProvider(
        runner=lambda: {"schema": "botmux.current-actor.v2",
                        "status": "verified",
                        "actor": {"email": "hwz"}},
        clock=lambda: NOW)
    report = provider.identify(conversation_ref="oc_x", message_ref="om_y")
    assert report.status == "VERIFIED" and report.actor_id == "hwz"
    record = actor.build(report.as_report(), now=NOW)
    assert record["actor_ref"] == "human:hwz"
    assert record["conversation_ref"] == "oc_x"


# ---------------------------------------------------------------------------
# The v3 document — the identity moved off the email
#
# The v2 tests above are not superseded by these. ServerOS deploys BEFORE the
# BotMux change that publishes v3, so for the length of that window the only
# document a live daemon answers with is the v2 one, and a reader that had
# dropped it would refuse every turn in the window it was deployed to protect.
# ---------------------------------------------------------------------------

def v3_document(*, actor_payload: dict | None = None, **top) -> dict:
    """The shape the patched daemon answers with, any part overridable."""
    document = {
        "schema": "botmux.current-actor.v3",
        "status": "verified",
        "actor": {
            "canonical": {"kind": "union_id", "value": "on_alpha"},
            "appLocal": {"larkAppId": "cli_alpha", "openId": "open-alpha"},
        },
        "chatId": "chat-alpha",
        "turnId": "turn-alpha",
    }
    if actor_payload is not None:
        document["actor"] = actor_payload
    document.update(top)
    return document


def test_a_v3_document_identifies_the_actor_and_both_locators():
    provider = BotMuxHumanControlProvider(runner=v3_document, clock=lambda: NOW)
    report = provider.identify()
    assert report.status == "VERIFIED"
    assert report.actor_id == "on_alpha"
    record = actor.build(report.as_report(), now=NOW)
    # The ref names the system and the id space, and the two locators the
    # trusted-context contract has always required now have a producer.
    assert record["actor_ref"] == "feishu:union:on_alpha"
    assert record["conversation_ref"] == "chat-alpha"
    assert record["message_ref"] == "turn-alpha"
    actor.validate(record)


def test_the_app_local_pair_is_evidence_and_never_the_identity():
    # The canonical id is what the ref is built from. A reader that used the
    # open id instead would mint a ref that names a different person to every
    # app that read it — which is the property the canonical id was introduced
    # to remove, and the reason the pair travels beside it rather than in it.
    provider = BotMuxHumanControlProvider(
        runner=lambda: v3_document(actor_payload={
            "canonical": {"kind": "union_id", "value": "on_alpha"},
            "appLocal": {"larkAppId": "cli_alpha", "openId": "open-beta"},
        }), clock=lambda: NOW)
    record = actor.build(provider.identify().as_report(), now=NOW)
    assert record["actor_ref"] == "feishu:union:on_alpha"
    assert "open-beta" not in json.dumps(record), (
        "the app-local id must not reach the record at all: it is evidence "
        "about this app's view, and it is not what the context is about")


def test_a_v3_document_with_no_canonical_id_does_not_fall_back_to_the_email():
    # The one fallback this reader must not have. The document below carries a
    # perfectly well-formed email; reading it would mint a context naming
    # whoever that email belonged to, out of a document that said it was
    # identifying somebody by a different id — and the whole point of the
    # change is that the email is not evidence.
    provider = BotMuxHumanControlProvider(
        runner=lambda: v3_document(actor_payload={
            "appLocal": {"larkAppId": "cli_alpha", "openId": "open-alpha"},
            "email": "alpha@example.com",
        }), clock=lambda: NOW)
    report = provider.identify()
    assert report.status == "BLOCKED"
    assert report.actor_id is None
    assert "actor.canonical" in report.detail


@pytest.mark.parametrize("canonical, because", [
    ({"kind": "open_id", "value": "open-alpha"}, "kind"),
    ({"kind": "union_id", "value": "open-alpha"}, "union_id"),
    ({"kind": "union_id", "value": "on_"}, "union_id"),
    ({"kind": "union_id", "value": "on_ alpha"}, "union_id"),
    ({"kind": "union_id"}, "union_id"),
    ({"value": "on_alpha"}, "kind"),
    ("on_alpha", "actor.canonical"),
])
def test_a_v3_canonical_id_that_is_not_one_is_refused(canonical, because):
    # The daemon refuses the same values before it publishes them. The check is
    # made again here because the two sides do not share a trust domain: a
    # document that reached this process by some other road gets no credit for a
    # check it did not run.
    provider = BotMuxHumanControlProvider(
        runner=lambda: v3_document(actor_payload={
            "canonical": canonical,
            "appLocal": {"larkAppId": "cli_alpha", "openId": "open-alpha"},
        }), clock=lambda: NOW)
    report = provider.identify()
    assert report.status == "BLOCKED"
    assert report.actor_id is None
    assert because in report.detail


def test_a_v3_document_without_the_app_local_evidence_is_refused():
    provider = BotMuxHumanControlProvider(
        runner=lambda: v3_document(actor_payload={
            "canonical": {"kind": "union_id", "value": "on_alpha"},
        }), clock=lambda: NOW)
    report = provider.identify()
    assert report.status == "BLOCKED"
    assert "appLocal" in report.detail


def test_a_v2_document_is_still_read_and_still_typed_as_a_human_ref():
    provider = BotMuxHumanControlProvider(
        runner=lambda: {"schema": "botmux.current-actor.v2", "status": "verified",
                        "actor": {"email": "hwz"}},
        clock=lambda: NOW)
    report = provider.identify()
    assert report.status == "VERIFIED"
    assert report.actor_namespace == "human" and report.actor_kind is None
    assert report.as_report()["actor_ref"] == "human:hwz"
    # And it still CANNOT become a context, which is not a regression this
    # change introduced — it is the whole reason the change exists. A v2
    # document publishes no locator, `build` requires both, and the refusal
    # names them. The v2 window is therefore a NO-CONTEXT window rather than a
    # fail-open one, which is the distinction that makes deploying ServerOS
    # first survivable.
    with pytest.raises(Rejected) as caught:
        actor.build(report.as_report(), now=NOW)
    assert "conversation_ref" in str(caught.value)
    assert "message_ref" in str(caught.value)


def test_a_v2_document_mints_once_a_locator_is_supplied_from_outside():
    # The shape that worked before v3 and still works: a caller asking about a
    # conversation it already holds passes one. This is what the provider's own
    # `--identify` mode is for, and it is why the v2 reader is a real reader
    # rather than a tombstone.
    provider = BotMuxHumanControlProvider(
        runner=lambda: {"schema": "botmux.current-actor.v2", "status": "verified",
                        "actor": {"email": "hwz"}},
        clock=lambda: NOW)
    report = provider.identify(conversation_ref="chat-alpha",
                               message_ref="turn-alpha")
    record = actor.build(report.as_report(), now=NOW)
    assert record["actor_ref"] == "human:hwz"
    assert record["conversation_ref"] == "chat-alpha"


def test_the_message_locator_is_read_under_both_names_it_has_had():
    # `turnId` is what v3 publishes; `messageId` is the name this reader asked
    # BotMux for before the daemon had either. One value under two names, and
    # the order is fixed rather than a preference, so a document that somehow
    # carried both is not a choice this reader gets to make.
    for field in ("turnId", "messageId"):
        document = v3_document()
        document.pop("turnId")
        document[field] = "turn-alpha"
        provider = BotMuxHumanControlProvider(runner=lambda d=document: d,
                                              clock=lambda: NOW)
        assert provider.identify().message_ref == "turn-alpha"


def test_the_v2_and_v3_readers_produce_different_refs_for_the_same_person():
    # Not a curiosity: the ref is what an approval is matched on, so the two
    # documents are two actors as far as the store is concerned, and the change
    # from one to the other is a change of actor rather than a rename. Stated as
    # a test because somebody will otherwise read the pair as a bug.
    v2 = BotMuxHumanControlProvider(
        runner=lambda: {"schema": "botmux.current-actor.v2", "status": "verified",
                        "actor": {"email": "alpha@example.com"}},
        clock=lambda: NOW).identify()
    v3 = BotMuxHumanControlProvider(runner=v3_document, clock=lambda: NOW).identify()
    assert v2.as_report()["actor_ref"] == "human:alpha@example.com"
    assert v3.as_report()["actor_ref"] == "feishu:union:on_alpha"
    assert v2.as_report()["actor_ref"] != v3.as_report()["actor_ref"]


def test_a_refusal_reason_is_carried_through_into_the_detail():
    # BotMux's own word for why it refused is the only thing that can send an
    # operator to the right place. Dropping it leaves "BLOCKED" pointing at
    # nothing, which is what made the live gate unattributable from outside.
    provider = BotMuxHumanControlProvider(
        runner=lambda: {"schema": "botmux.current-actor.v2",
                        "status": "blocked", "reason": "peer_not_in_turn"},
        clock=lambda: NOW)
    report = provider.identify()
    assert report.status == "BLOCKED"
    assert "peer_not_in_turn" in report.detail
    assert report.actor_id is None


def test_a_missing_actor_surface_is_unsupported_not_blocked():
    # "the interface is not there" and "the interface said no" send an operator
    # to different places, so they are different words.
    provider = BotMuxHumanControlProvider(
        runner=lambda: {"schema": None, "status": "unsupported", "error": "FileNotFoundError"},
        clock=lambda: NOW)
    assert provider.identify().status == "UNSUPPORTED"


def test_request_approval_refuses_and_names_the_binding_it_cannot_do():
    """The refusal survived the re-measurement, and its reason changed.

    Until 2026-09-15 the exception said BotMux had *no* surface for a human
    decision, resting on three `/api/*` 404s. BotMux v3.18 does have one:
    `botmux ask buttons --options "a,b"` delivers the question and returns the
    chosen key. So "there is no surface" became false, and a refusal resting on
    a false premise is a refusal somebody will delete once they notice.

    What is still true, and is why this still raises: a returned key is not
    BOUND to a request fingerprint, so it cannot be shown to be about the
    request the approval is for. §AA forbids treating a delivered message as an
    approval. The test asserts the surviving reason, not the retired one.
    """
    provider = BotMuxHumanControlProvider(clock=lambda: NOW)
    with pytest.raises(human_control.HumanControlUnsupported) as caught:
        provider.request_approval(conversation_ref="oc_x", question={}, idempotency_key="k")
    message = str(caught.value)
    assert "BOUND" in message and "fingerprint" in message, (
        "the refusal must name what is actually missing — an answer bound to "
        "the request — rather than an absence of a surface that now exists")
    assert "404" not in message, (
        "the retired claim. Every /api/* path answers 401 today, including a "
        "bogus one, so the 404s are not reproducible and may not be restated")


def test_capabilities_do_not_claim_a_capability_that_was_never_observed():
    caps = BotMuxHumanControlProvider(clock=lambda: NOW).capabilities()["capabilities"]
    assert caps["human.identity.verify"]["state"] == "UNKNOWN", (
        "the surface exists and answers, and has never been observed to verify "
        "anybody from here. Calling that SUPPORTED would be the declaration "
        "running ahead of the data, which is the pathology this round exists "
        "to stop repeating.")
    notification = caps["human.notification.transport"]
    assert notification["state"] == "UNKNOWN", (
        "corrected DOWNWARD from SUPPORTED on 2026-09-25, and it is the first "
        "correction in this table that goes that way. An unaddressed "
        "POST /api/trigger — which is what this provider sends — is a synthetic "
        "session: the service hands the text to a model as a task whose answer "
        "comes back over HTTP, under a header that says it is 'not shown in a "
        "chat' and forbids posting to Feishu. Nothing reaches a person. The "
        "surface exists (an addressed trigger routes into a real chat) and has "
        "never been observed to deliver from here, which is UNKNOWN and not "
        "UNSUPPORTED.")
    assert "http_async" in notification["reason"] or "synthetic" in notification["reason"], (
        "the reason has to name the measurement, not just the verdict: the next "
        "reader has to be able to tell 'we have never seen it work' from 'we "
        "watched it not work', and only one of those can be argued with")
    # Four UNKNOWNs as of 2026-09-15, two of them corrected downward-from-
    # UPWARD: approval and rendering were UNSUPPORTED, which claims the surface
    # does not exist. The CLI measurement says the surfaces do exist (`ask
    # buttons` returns a key; `send --card-file` is documented and reachable),
    # and that nothing has shown them carrying what ServerOS needs. UNSUPPORTED
    # would have meant "design around it"; UNKNOWN means "go and find out", and
    # the finding-out is named in each reason.
    assert caps["human.approval.transport"]["state"] == "UNKNOWN"
    assert caps["human.interactive.rendering"]["state"] == "UNKNOWN"
    assert caps["human.transport.ingress"]["state"] == "UNKNOWN"
    for name, entry in caps.items():
        assert entry.get("reason"), f"{name} is declared with no reason"
    # An UNSUPPORTED anywhere in this table is now a claim that a documented
    # surface is absent, and none survives measurement.
    assert not [n for n, e in caps.items() if e["state"] == "UNSUPPORTED"], (
        "UNSUPPORTED says the surface does not exist. Nothing in this table "
        "can support that today; each entry names a documented CLI path")


# ---------------------------------------------------------------------------
# The daemon doorway — all three mechanisms, through the served interface
# ---------------------------------------------------------------------------

class FakeRegistry:
    def all(self):
        return []

    def get(self, key):
        raise KeyError(key)

    def __len__(self):
        return 0


@pytest.fixture
def daemon(tmp_path):
    return daemon_mod.Daemon(FakeRegistry(), state_dir=tmp_path / "state",
                             socket_path=tmp_path / "run/serveros.sock",
                             now=lambda: _epoch(NOW),
                             log=lambda message: None)


def _call(daemon_obj, method, params, *, peer=None):
    return daemon_obj.handle({"method": method, "params": params}, peer=peer)


def test_attach_refuses_a_payload_carrying_identity(daemon):
    response = _call(daemon, "agent.context.attach",
                     {"report": VERIFIED_REPORT, "human": "hwz"}, peer={"pid": 4242})
    assert response["ok"] is False
    assert response["error"]["code"] == "ForgedContext"
    assert "human" in response["error"]["message"]


def test_attach_refuses_a_report_observed_by_a_different_process(daemon):
    # The mechanism that makes this more than a schema check: a report is
    # produced by running the identity probe inside the turn being identified,
    # so the report carries the pid that observed it, and that pid must be the
    # caller. An agent cannot change which process it is.
    response = _call(daemon, "agent.context.attach", {"report": VERIFIED_REPORT},
                     peer={"pid": 9999})
    assert response["ok"] is False
    assert "observed by pid 4242" in response["error"]["message"]
    assert "arrived from pid 9999" in response["error"]["message"]


def test_attach_refuses_a_report_with_no_observer_pid(daemon):
    report = {k: v for k, v in VERIFIED_REPORT.items() if k != "provider_pid"}
    response = _call(daemon, "agent.context.attach", {"report": report},
                     peer={"pid": 4242})
    assert response["ok"] is False
    assert "which process observed" in response["error"]["message"]


def test_attach_refuses_when_there_is_no_caller_process(daemon):
    # `handle` called directly, with no socket behind it. Refused rather than
    # treated as trusted-by-default, which is how an in-process shortcut
    # becomes the way around the check.
    response = _call(daemon, "agent.context.attach", {"report": VERIFIED_REPORT})
    assert response["ok"] is False
    assert "no caller process" in response["error"]["message"]


def test_attach_mints_a_context_and_stores_it(daemon):
    response = _call(daemon, "agent.context.attach", {"report": VERIFIED_REPORT},
                     peer={"pid": 4242})
    assert response["ok"] is True
    result = response["result"]
    assert result["context_ref"].startswith("human-context:")
    assert result["actor_ref"] == "human:hwz"
    assert result["observed_by_pid"] == 4242
    assert result["freshness"]["state"] == "FRESH"
    assert daemon.control_store().get_context(result["context_ref"]) is not None


def test_attach_refuses_a_blocked_report(daemon):
    report = {**VERIFIED_REPORT, "provider_status": "BLOCKED",
              "provenance": "UNKNOWN", "actor_ref": None}
    response = _call(daemon, "agent.context.attach", {"report": report},
                     peer={"pid": 4242})
    assert response["ok"] is False
    assert "identifies no human" in response["error"]["message"]


def test_inspect_of_an_unminted_ref_reports_a_miss(daemon):
    response = _call(daemon, "agent.context.inspect",
                     {"context_ref": "human-context:nobody/neverminted"})
    assert response["ok"] is True
    assert response["result"]["found"] is False


def test_control_status_reports_only_what_the_daemon_owns(daemon):
    """§18 holds here too, and this is the test that says so.

    The first version of `agent.control.status` imported the provider module to
    ask it for its capability list. That is the daemon becoming a client of a
    transport, which §18 forbids — and the failure mode is specific: a daemon
    that depends on a provider cannot answer when the provider is broken, which
    is exactly when somebody asks.
    """
    result = _call(daemon, "agent.control.status", {})["result"]
    assert result["store"]["contexts"] == 0
    assert "session.interrupt" in result["human_gated_actions"]
    assert result["authority"] == "serverosd"
    assert "mcp/human_control.py" in result["provider_surface"]
    # No transport capability is restated here, in any spelling. The provider
    # answers that question, on a surface allowed to know about transports.
    blob = json.dumps(result).lower()
    for leaked in ("feishu", "lark", "botmux", "http://", "approval.transport\":"):
        assert leaked not in blob, (
            f"{leaked!r} leaked into the node's control status; the daemon must "
            "not carry a transport's declaration")


def test_the_daemon_does_not_import_the_provider_module():
    """The invariant is about the import, not the substring.

    `human_control_provider` is a FIELD NAME in the actor context contract —
    the record the daemon stores must say which provider attested it, and a
    grep for the words would forbid the honest field along with the dependency.
    What is forbidden is loading the adapter: the daemon owns the trusted store
    and the decision, and owning the transport would make it unable to answer
    when the transport is broken.
    """
    source = (RUNTIME / "daemon.py").read_text()
    assert "serveros_mcp_human_control" not in source, (
        "the daemon loads the HumanControlProvider adapter")
    assert "human_control" not in {
        name for name in dir(daemon_mod) if not name.startswith("__")
    }, ("the daemon module carries a provider helper; the transport's "
        "declaration belongs on the surface that is allowed to know about it")
    assert not hasattr(daemon_mod, "_human_control_caps")


# ---------------------------------------------------------------------------
# The transport boundary, asserted rather than asserted-about
# ---------------------------------------------------------------------------

def test_peer_credentials_come_from_the_kernel_not_the_client(tmp_path):
    """The one test that proves the pid cannot be forged.

    A real AF_UNIX socket pair, a real `SO_PEERCRED` read, and a client that
    has done nothing but connect. If this returned anything the client could
    influence, every claim above would be decoration.
    """
    left, right = socket.socketpair(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        peer = daemon_mod._peer_credentials(left)
    finally:
        left.close()
        right.close()
    assert peer is not None, "SO_PEERCRED is required on the deployment platform"
    import os
    assert peer["pid"] == os.getpid(), (
        "the peer of a local socketpair is this process, and the kernel is the "
        "one that says so")
    assert peer["uid"] == os.getuid()


def test_agent_control_store_is_keyed_by_ref_and_never_by_actor(tmp_path):
    path = tmp_path / "store.json"
    store = AgentControlStore(path)
    record = actor.build(VERIFIED_REPORT, now=NOW)
    store.put_context(record)
    on_disk = json.loads(path.read_text())
    assert record["context_ref"] in on_disk["contexts"]
    # The negative that matters: there is no method that returns "everything
    # about this person". Adding one is the change that turns an evidence log
    # into a directory of people, so its absence is a test.
    for forbidden in ("get_actor", "contexts_for_actor", "approvals_for_actor",
                      "actor", "whois"):
        assert not hasattr(store, forbidden), (
            f"AgentControlStore.{forbidden} would make this a HumanIdentity DB")


def test_a_corrupt_store_refuses_to_load_rather_than_reading_as_empty(tmp_path):
    path = tmp_path / "store.json"
    path.write_text("{ this is not json")
    with pytest.raises(store_mod.StoreError) as caught:
        AgentControlStore(path)
    assert "Refusing to start with an empty control store" in str(caught.value)


def test_the_gated_actions_are_read_from_the_session_contract_not_restated():
    from pathlib import Path as _Path
    import yaml
    contract = yaml.safe_load(
        (_Path(REPO) / "runtime" / "agent-session-contract.yaml").read_text())
    declared = set(contract["admission"]["human_gated"])
    assert set(approval_mod.human_gated_actions()) == declared, (
        "two lists of human-gated verbs agree until the day one changes, and "
        "the disagreement is an action that reads gated in the contract and "
        "ungated in the code that enforces it")


def test_the_session_contract_vocabulary_has_no_unused_states():
    # §5's admission classes must all be reachable. A declared class no action
    # lands in is the same pathology one layer down.
    contract = approval_mod.contract()  # the approval contract, for its own vocabulary
    assert contract["consumption"]["model"] == "single-use"


# ---------------------------------------------------------------------------
# The mint path — §5's doorway, reached by a real caller over a real socket
#
# Until this existed, `agent.context.attach` had no producer anywhere in the
# repository. Every test above reaches it the way a test can and a caller
# cannot: `handle(..., peer={"pid": 4242})`, with the report and the peer pid
# both written by the test. So the mechanism was proven and the door was never
# used, and since every mutating Agent Session action requires a `context_ref`,
# nothing that changes a session had ever passed admission from a live caller.
# These tests open the door the way a caller does — a connection — and the
# interesting assertions are about what the kernel says on the other end.
# ---------------------------------------------------------------------------

node_mcp = _load("serveros_mcp_node_shared", MCP / "node_mcp.py")


class _ServingDaemon:
    """A real daemon on a real socket, accepting on a thread.

    Not `handle()` in-process, and the difference is the entire subject of
    these tests: `SO_PEERCRED` is filled in per connection by the kernel. A
    test that passed `peer={"pid": ...}` itself would be measuring its own
    fixture, which is how this door came to look proven while being unused.

    `serve(n)` accepts exactly the `n` connections the test expects, so a test
    that expects no call gets no thread rather than a fixture that quietly
    absorbs one.
    """

    def __init__(self, daemon_obj):
        self.daemon = daemon_obj
        self.server = daemon_obj._open_socket()
        self._threads: list[threading.Thread] = []

    def serve(self, count: int = 1) -> None:
        def loop():
            for _ in range(count):
                connection, _ = self.server.accept()
                try:
                    self.daemon._serve_connection(connection)
                finally:
                    connection.close()

        thread = threading.Thread(target=loop, daemon=True)
        thread.start()
        self._threads.append(thread)

    def client(self, timeout: float = 20.0):
        return node_mcp.DaemonClient(self.daemon.socket_path, timeout=timeout)

    def close(self) -> None:
        for thread in self._threads:
            thread.join(timeout=10)
        self.server.close()


@pytest.fixture
def serving_daemon(daemon):
    served = _ServingDaemon(daemon)
    try:
        yield served
    finally:
        served.close()


def _verified_provider():
    """A provider whose probe answers the way the live surface really answers.

    The document is the measured shape: `status: "verified"` and the identity at
    `actor.email`. It carries no conversation or message locator, because the
    live one carries none — that absence is the subject of several tests below,
    so this fixture must not paper over it.
    """
    return BotMuxHumanControlProvider(
        runner=lambda: {"schema": "botmux.current-actor.v2",
                        "status": "verified", "actor": {"email": "hwz"}},
        clock=lambda: NOW)


def _verified_provider_with_locators():
    """The same verification, plus the locators a future BotMux should publish.

    Stands in for item 3 of the upstream proposal so that what is BELOW the
    provider — the attach path, the store, the exit code — stays tested. It is
    deliberately a different fixture from `_verified_provider`, so that a change
    making the live one mint successfully cannot happen by editing a fixture.
    """
    return BotMuxHumanControlProvider(
        runner=lambda: {"schema": "botmux.current-actor.v2",
                        "status": "verified", "actor": {"email": "hwz"},
                        "chatId": "oc_conversation", "messageId": "om_message"},
        clock=lambda: NOW)


def _blocked_provider():
    return BotMuxHumanControlProvider(
        runner=lambda: {"schema": "botmux.current-actor.v2", "status": "blocked",
                        "error": "current_actor_unverified"},
        clock=lambda: NOW)


def test_a_verified_turn_mints_a_context_over_a_real_socket(serving_daemon):
    """The producer §5 was missing, exercised the way a caller uses it.

    Everything below the `attach` call is the real path: the provider's own
    probe result, this process's pid, a real connection, the kernel's
    `SO_PEERCRED`, and the daemon's store. Nothing is passed in that a caller
    could not supply.

    The probe document here carries the two locators BotMux does not publish
    yet, because that is the only way this far end is reachable at all — see
    `test_a_verified_turn_without_locators_mints_nothing` for what today's
    surface produces, which is the honest state and not a test artefact.
    """
    serving_daemon.serve(1)
    result = human_control.attach_context(_verified_provider_with_locators(),
                                          daemon=serving_daemon.client())

    assert result["attached"] is True, result
    assert result["context_ref"].startswith("human-context:")
    assert result["actor_ref"] == "human:hwz"
    # The daemon echoes what it bound to, and it is this process — because this
    # process ran the probe.
    assert result["observed_by_pid"] == os.getpid()

    # The ref it handed back is one the control plane will actually accept,
    # which is the only thing the caller wanted a ref for.
    record = serving_daemon.daemon.control_store().get_context(result["context_ref"])
    assert record is not None
    assert actor.authorizes(record, NOW)["authorizes"] is True


def test_a_report_observed_by_another_process_is_refused_through_this_path(
        serving_daemon):
    """The pid is not decoration: it is the one thing the caller cannot fake.

    Driven through `attach_context` rather than through `handle`, because the
    refusal only matters if a caller meets it — a mechanism nobody can reach is
    a mechanism nobody can be stopped by.
    """
    serving_daemon.serve(1)
    result = human_control.attach_context(_verified_provider(), pid=99999,
                                          daemon=serving_daemon.client())

    assert result["attached"] is False
    assert result["state"] == "REFUSED"
    assert result["actor_ref"] is None
    assert "observed by pid 99999" in result["detail"]
    assert f"arrived from pid {os.getpid()}" in result["detail"]


def test_a_blocked_provider_mints_nothing_and_never_knocks(serving_daemon):
    """"No context" and "a context about nobody" must not be the same outcome.

    The load-bearing half is the store: the daemon here is reachable and has
    one, so "attached: false" is only credible if it is still empty. A path
    that tried and was refused would leave the same store empty — which is why
    the refusal is returned before the call rather than after it, and why this
    test asserts the emptiness instead of trusting the flag.
    """
    result = human_control.attach_context(_blocked_provider(),
                                          daemon=serving_daemon.client(timeout=1.0))

    assert result["attached"] is False
    assert result["state"] == "BLOCKED"
    assert result["actor_ref"] is None
    assert "current_actor_unverified" in result["detail"]
    minted = _call(serving_daemon.daemon, "agent.control.status", {})["result"]
    assert minted["store"]["contexts"] == 0


def test_a_verified_turn_without_locators_mints_nothing(serving_daemon):
    """Today's live surface, end to end: the human is verified and nothing mints.

    This is the state a BotMux deployment is actually in. The gate can verify
    the sender, and the document it publishes names neither the conversation nor
    the event, so there is nothing to bind the context to. The refusal names
    both fields rather than failing later with a sentence about something else,
    and — the load-bearing half — the store is still empty, so "attached: false"
    is not a flag over a context that got written anyway.

    The cheap repair would be to default the locations to something plausible,
    or to read them from the process environment; the first puts a fabrication
    into an audit record and the second lets the caller choose which
    conversation it is recorded against.
    """
    serving_daemon.serve(1)
    result = human_control.attach_context(_verified_provider(),
                                          daemon=serving_daemon.client())

    assert result["attached"] is False and result["state"] == "REFUSED"
    assert "conversation_ref" in result["detail"]
    assert "message_ref" in result["detail"]
    assert result["observed_by_pid"] == os.getpid()
    assert _call(serving_daemon.daemon,
                 "agent.control.status", {})["result"]["store"]["contexts"] == 0


def test_the_mint_path_has_no_parameter_for_evidence():
    """A structural refusal, kept structural.

    The moment this function takes a report — or an actor, or a status — the
    door that only the process which ran the probe may walk through becomes a
    door anyone can hand evidence to, and no amount of checking downstream
    would restore it. Asserted against the signature rather than against a
    behaviour, because the behaviour is the thing that would be redefined.
    """
    parameters = set(inspect.signature(human_control.attach_context).parameters)
    assert parameters == {"provider", "daemon", "pid"}, (
        "attach_context grew a parameter; if it can be handed evidence, an "
        "actor, or the conversation the observation is recorded against, it is "
        "no longer an observation being presented")


def test_the_attach_verb_exits_nonzero_when_no_context_was_minted(serving_daemon,
                                                                monkeypatch,
                                                                capsys):
    """The command's verb is `attach`, so its exit code reports whether it did.

    `--identify` and the other modes here always exit 0 because they are
    questions and a question that was answered succeeded, even when the answer
    was "nobody was identified". An act that did not happen returning 0 is the
    declaration-read-as-fact failure this repository keeps finding.
    """
    monkeypatch.setattr(human_control, "BotMuxHumanControlProvider",
                        _blocked_provider)
    assert human_control.main(["--attach"]) == 1
    blocked = json.loads(capsys.readouterr().out)
    assert blocked["attached"] is False and blocked["state"] == "BLOCKED"

    serving_daemon.serve(1)
    monkeypatch.setattr(human_control, "BotMuxHumanControlProvider",
                        _verified_provider_with_locators)
    assert human_control.main(["--attach",
                               "--socket", str(serving_daemon.daemon.socket_path)]) == 0
    attached = json.loads(capsys.readouterr().out)
    assert attached["attached"] is True
    assert attached["observed_by_pid"] == os.getpid()


# ---------------------------------------------------------------------------
# What the ingress produced, said out loud
#
# Until 2026-09-28 the mount that answers a Feishu turn ran `--attach` with its
# stdout and stderr sent to /dev/null and `|| true` after it. Four different
# outcomes — a provider that identified nobody, a payload the daemon refused, a
# daemon that was not listening, and a minted context — produced the same
# observable from outside: nothing. A real human message arrived that day, the
# store still read `contexts: {}`, and this node could not say which of the
# four had happened. These tests are about that silence, and about the one
# thing the record must never become.
# ---------------------------------------------------------------------------


def _attempt(**overrides):
    result = {
        "provider": "botmux",
        "transport": "feishu",
        "attached": False,
        "state": "BLOCKED",
        "detail": "status=blocked error=current_actor_unverified",
    }
    result.update(overrides)
    return result


def _lines(path: Path) -> list[dict]:
    return [json.loads(line) for line in
            path.read_text(encoding="utf-8").splitlines() if line.strip()]


def test_the_ingress_record_names_no_actor(tmp_path):
    """The record says whether somebody was identified. It never says who.

    A file that carried an actor id would be a second place identity can be
    read from, and the first place is the one §5 built a daemon, a socket and a
    kernel-attested pid around. The record is append-only and writable by a
    hook, so an actor id here would be a name an attachment could write into a
    file that looks like evidence.
    """
    written = human_control.record_attempt(
        _attempt(attached=True, state="ATTACHED", actor_ref="actor:hwz-2026",
                 actor_id="hwz-2026", context_ref="ctx:botmux/abc123"),
        path=tmp_path / "ingress.jsonl")

    assert written["recorded"] is True
    (line,) = _lines(tmp_path / "ingress.jsonl")
    assert line["actor_identified"] is True, (
        "the boolean is the part a diagnosis needs and the part that is safe "
        "to keep: it separates 'nobody was identified' from 'identified and "
        "refused' without putting a name on disk")
    assert "hwz-2026" not in json.dumps(line), (
        "an actor id reached the record; attach_context takes no report for the "
        "same reason, and a file is a report anybody can append to")


def test_an_attempt_that_minted_nothing_is_still_written_down(tmp_path):
    """The failing case is the whole point of the file existing.

    A record that only held successes would be empty on exactly the turns
    somebody is asking about, and empty would then be read as "no attempt was
    made" — which is the reading §5 forbids and the one this node was actually
    stuck on for a day.
    """
    written = human_control.record_attempt(
        _attempt(), path=tmp_path / "human-control" / "ingress.jsonl")

    assert written == {"recorded": True,
                       "path": str(tmp_path / "human-control" / "ingress.jsonl"),
                       "state": "BLOCKED"}
    (line,) = _lines(tmp_path / "human-control" / "ingress.jsonl")
    assert line["attached"] is False and line["state"] == "BLOCKED"
    assert line["actor_identified"] is False
    assert line["detail"], (
        "a state with no detail is the same silence in a different file: "
        "`BLOCKED` has to carry what the provider answered")


def test_a_record_that_cannot_be_written_is_returned_not_raised(tmp_path):
    """A logging problem must not become a failed turn.

    The mount runs inside UserPromptSubmit, so raising here would turn "the
    record is unwritable" into "this turn did not happen" — trading one silence
    for a worse one. The caller gets `recorded: false` and says so.
    """
    blocked = tmp_path / "a-directory"
    blocked.mkdir()

    written = human_control.record_attempt(_attempt(), path=blocked)

    assert written["recorded"] is False
    assert written["detail"], "a non-write has to say why it did not write"


def test_the_record_is_on_by_default_and_no_record_turns_it_off(serving_daemon,
                                                                monkeypatch,
                                                                capsys,
                                                                tmp_path):
    """`--attach` alone writes, and `--no-record` is what writes nothing.

    The default is ON because the primary caller is a turn hook whose command
    string was read off a settings file that may predate the flag: measured
    2026-09-28, the session holding the Feishu conversation ran `--attach` with
    no `--record`, so an off-by-default record would have been silent on
    exactly the turn being asked about, and "the hook never ran" and "the hook
    ran and minted nothing" would have been the same empty file.

    The off switch is asserted rather than assumed for the opposite reason: the
    suite calls `main(["--attach"])` as a plain function, in-process, on this
    node, with this node's roots bound, so a caller with no off switch would
    litter a real log root.
    """
    asked: list[str] = []

    def counted(ref=human_control.INGRESS_RECORD_REF):
        asked.append(ref)
        raise AssertionError("the record was resolved without being asked for")

    monkeypatch.setattr(human_control, "ingress_record_path", counted)
    monkeypatch.setattr(human_control, "BotMuxHumanControlProvider",
                        _blocked_provider)

    assert human_control.main(["--attach", "--no-record"]) == 1
    assert asked == []
    assert "record" not in json.loads(capsys.readouterr().out)

    written = tmp_path / "ingress.jsonl"
    monkeypatch.setattr(human_control, "ingress_record_path",
                        lambda ref=human_control.INGRESS_RECORD_REF: written)

    assert human_control.main(["--attach"]) == 1
    reported = json.loads(capsys.readouterr().out)
    assert reported["record"]["recorded"] is True, (
        "the default is the whole point: a hook command string that says only "
        "`--attach` still has to leave a trace")
    assert len(_lines(written)) == 1


def test_the_attach_verb_records_a_blocked_attempt(serving_daemon,
                                                   monkeypatch,
                                                   capsys,
                                                   tmp_path):
    """The exit code and the record have to agree with each other.

    Exit 1 already says no context was minted. The record exists so that the
    same turn also says *why* — which of the four outcomes it was — without
    anyone having to reproduce it. The explicit `--record` spelling is kept
    working as well as the default, because the installed hook string carries
    it and a flag that stopped being accepted would turn a mount into a
    traceback.
    """
    record = tmp_path / "ingress.jsonl"
    monkeypatch.setattr(human_control, "ingress_record_path",
                        lambda ref=human_control.INGRESS_RECORD_REF: record)
    monkeypatch.setattr(human_control, "BotMuxHumanControlProvider",
                        _blocked_provider)

    assert human_control.main(["--attach", "--record"]) == 1
    reported = json.loads(capsys.readouterr().out)
    assert reported["attached"] is False and reported["state"] == "BLOCKED"
    assert reported["record"]["recorded"] is True

    (line,) = _lines(record)
    assert line["state"] == "BLOCKED"
    assert line["actor_identified"] is False
    assert line["schema"] == "serveros/human-control-ingress.v1", (
        "an append-only file many processes write to needs its own schema, so a "
        "reader can tell a line this product wrote from a line it did not")


def test_the_record_is_not_in_the_authority_namespace():
    """`logs/`, and never `state/serveros/runtime/agent-control/`.

    The whole reason the control store can be trusted as the answer to "what
    does this node believe about actors" is that nothing else claims to be. An
    ingress record inside that namespace would be a second thing to read before
    answering that, and a reader would have to know which of the two wins.
    """
    ref = human_control.INGRESS_RECORD_REF
    assert ref.startswith("logs/"), (
        "the ingress record moved out from under `logs/`; if it is meant to be "
        "authority it needs a producer and a place in the root contract, and if "
        "it is meant to be a log it stays here")
    assert "agent-control" not in ref and "state/serveros" not in ref


@pytest.mark.reads_the_ingress_resolver
def test_the_record_path_comes_from_the_root_contract(monkeypatch, tmp_path):
    """Resolved, not composed. An unbound node is told so, not guessed at.

    `{DATA_ROOT}/logs/...` is a plausible-looking path on a machine nobody
    identified, and a record written there would be found later by a reader who
    has no way to tell it was never this node's.

    The marker exempts this test from `tests/conftest.py`'s redirect, because
    the redirect replaces `ingress_record_path` and this test's whole subject IS
    that function. It resolves and writes nothing, so the session-level check
    that the node's real record did not move still holds.
    """
    paths = human_control._load("serveros_runtime_paths",
                                human_control._REPO / "runtime" / "paths.py")

    class Unbound:
        bound = False
        unconfigured = ("DATA_ROOT",)

        def resolve(self, ref):
            raise AssertionError("an unbound node resolved a path anyway")

    monkeypatch.setattr(paths, "Paths", Unbound)
    with pytest.raises(human_control.HumanControlError) as caught:
        human_control.ingress_record_path()
    assert "UNBOUND" in str(caught.value)

    class Bound:
        bound = True
        unconfigured = ()

        def resolve(self, ref):
            assert ref == human_control.INGRESS_RECORD_REF
            return {"ref": ref, "path": str(tmp_path / "ingress.jsonl")}

    monkeypatch.setattr(paths, "Paths", Bound)
    assert human_control.ingress_record_path() == tmp_path / "ingress.jsonl"
