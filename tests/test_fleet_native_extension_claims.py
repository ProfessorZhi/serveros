"""A verb reached through an authored tool is not unexplained drift.

`fleet_create_project` calls the device's `project_create` directly, so that
name is deliberately absent from the mirror's recorded baseline. Before this
was stated in one place, every status call on the Tokyo device read
`TOOL LIST DRIFT ... extra=['project_create']` — a true difference reported as
an alarm, on the one device that was working exactly as designed.

Three states have to stay distinguishable:

* recorded and mirrored (`session_send`),
* not recorded, but claimed by an authored tool (`project_create`),
* not recorded and not claimed (`session_teleport`) — the real drift.

And the claim is bidirectional: a claimed verb the device STOPS declaring means
the authored tool would fail at call time, so that is drift too.
"""
from __future__ import annotations

from fleet_mcp import cloudcli_native


MIRRORED = "session_send"
CLAIMED = "project_create"
UNCLAIMED = "session_teleport"


def _observed(*names):
    return {name: {"annotations": {}, "schema": {}} for name in names}


def test_the_claimed_verb_is_not_reported_as_extra():
    report = cloudcli_native.coverage([MIRRORED, CLAIMED])
    assert report["extra"] == []
    assert report["extension_claimed"] == [CLAIMED]
    assert report["extension_claimed_missing"] == []
    # It is still not mirrored: claiming is not recording.
    assert CLAIMED not in cloudcli_native.NATIVE_BY_NAME


def test_an_unclaimed_new_verb_is_still_drift():
    report = cloudcli_native.coverage([MIRRORED, UNCLAIMED])
    assert report["extra"] == [UNCLAIMED]
    assert report["agrees"] is False


def test_a_device_predating_the_claimed_verb_agrees():
    """The baseline is 1.39.0, which never declared it — matching it is agreement."""
    report = cloudcli_native.coverage([t.name for t in cloudcli_native.NATIVE_TOOLS])
    assert report["extension_claimed"] == []
    assert report["extension_claimed_missing"] == [CLAIMED]
    assert report["agrees"] is True


def test_the_capability_snapshot_keeps_the_claim_out_of_unrecorded():
    snapshot = cloudcli_native.capability_snapshot(
        device_ref="device:tokyo-shared-dev-01",
        observed=_observed(MIRRORED, CLAIMED, UNCLAIMED),
    )
    assert snapshot["unrecorded_by_build"] == [UNCLAIMED]
    assert snapshot["extension_claimed"] == [CLAIMED]
    assert snapshot["extension_claimed_missing"] == []
    assert snapshot["declared_not_implemented"] == []


def test_the_snapshot_reports_a_missing_claim_too():
    snapshot = cloudcli_native.capability_snapshot(
        device_ref="device:tokyo-shared-dev-01", observed=_observed(MIRRORED))
    assert snapshot["extension_claimed"] == []
    assert snapshot["extension_claimed_missing"] == [CLAIMED]
