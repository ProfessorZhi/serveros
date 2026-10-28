"""Source-only preflight for side-by-side Legacy Fleet and Fleet Next plugins.

This module never calls Creator, a device, a service, OAuth or a model. It
checks the identity/resource separation an operator must establish before a
Next artifact can be installed beside the Legacy plugin:

* distinct backend plugin IDs, MCP URLs, OAuth audiences, credential anchors,
  ports, datastores and tool namespaces;
* Legacy's 8-Skill controller is unchanged;
* Next carries its own 9-Skill controller and its own version;
* the canonical 9-Skill source pin is not mixed with the standalone
  `chatgpt-fleet-next` 7-member pin;
* a same-ID update is only a future version of the **Next** plugin, with
  explicit consent, an exact `expected_release_id` and an advancing version;
* a shadow request may read, never create/send/stop/restart/wake.

The returned plan keeps `legacy_usable_on_failure=True`; a failed Next install
or rollback does not stop the Legacy controller.
"""
from __future__ import annotations

import re
from typing import Any

LEGACY_SKILLS = frozenset({
    "fleet-async-dispatch",
    "fleet-commander-orchestration",
    "fleet-create-with-grants",
    "fleet-device-and-session-discovery",
    "fleet-evidence-review",
    "fleet-plugin-update",
    "fleet-reuse-idle-session",
    "fleet-safety-boundaries",
})
NEXT_SKILLS = LEGACY_SKILLS | {"fleet-batch-commander"}

#: Exact trusted source pins. These are the two different products; their
#: digests must not be substituted for each other.
TRUSTED_SOURCE_PINS = {
    "fleet_v1_9skill": {
        "product": "fleet-v1-commander-9skill",
        "archive_sha256": "7320129eb33d57d9e988331e46c470738fc847efcc28a063f70a69bf171e12db",
        "content_sha256": "6e8c6e984d3c6414d72de47c423a162a5c1775a3c98bbd84c0f4c869b8b6e93b",
        "members": 15,
        "skills": 9,
    },
    "chatgpt_fleet_next_7member": {
        "product": "chatgpt-fleet-next-standalone",
        "archive_sha256": "14b3b4ee84a7be5428d29939e6a7297ec0aed34395f0267e74c9f18ef0bd65e2",
        "content_sha256": "df15ee3779678ac80c54ab8891b6f6597f4c902f1fe3fc9e09566b99a1b3a7e1",
        "members": 7,
        "skills": 4,
    },
}

SEMVER = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+$")
RESOURCE_FIELDS = (
    ("mcp_url", "NEXT_TAKEOVER_URL"),
    ("oauth_audience", "NEXT_SHARED_AUDIENCE"),
    ("credential_anchor", "NEXT_SHARED_CREDENTIAL"),
    ("port", "NEXT_SHARED_PORT"),
    ("datastore", "NEXT_SHARED_DATASTORE"),
    ("tool_namespace", "NEXT_TAKEOVER_NAMESPACE"),
)
SHADOW_WRITES = frozenset({"create", "send", "stop", "restart", "wake",
                           "manager_wake", "interrupt", "update"})


class Refused(Exception):
    def __init__(self, code: str, detail: str) -> None:
        super().__init__(f"{code}: {detail}")
        self.code = code
        self.detail = detail


def _blocked(code: str, detail: str) -> dict[str, Any]:
    return {"verdict": "BLOCKED", "code": code, "detail": detail,
            "legacy_usable_on_failure": True, "creator_calls": 0,
            "network": False, "production_verified": False}


def _require(record: dict[str, Any], field: str) -> Any:
    value = record.get(field)
    if value is None or (isinstance(value, str) and not value.strip()):
        raise Refused("MALFORMED_INPUT", f"missing {field}")
    return value


def _skills(record: dict[str, Any]) -> set[str]:
    value = record.get("skills")
    if not isinstance(value, (list, tuple, set)) or any(
            not isinstance(item, str) for item in value):
        raise Refused("MALFORMED_INPUT", "skills must be a string list")
    return set(value)


def _pin(record: dict[str, Any], pins: dict[str, Any]) -> dict[str, Any]:
    key = record.get("source_pin")
    if key not in pins:
        raise Refused("UNKNOWN_SOURCE_PIN", f"{key!r} is not a trusted pin")
    pin = pins[key]
    for field in ("archive_sha256", "content_sha256"):
        value = record.get(field)
        if value != pin[field]:
            raise Refused(
                "SOURCE_PIN_MISMATCH",
                f"{field} {value!r} != trusted {pin[field]!r} for {key}")
    if record.get("members") != pin["members"]:
        raise Refused("SOURCE_PIN_MISMATCH", f"member count != {pin['members']}")
    if len(_skills(record)) != pin["skills"]:
        raise Refused("SOURCE_PIN_MISMATCH", f"skill count != {pin['skills']}")
    return pin



class MockCreator:
    """Offline stand-in for Creator; it never fabricates a registration.

    `registered_plugin_id` is the exact ID an operator read back. `None` means
    "not registered" and is refused, rather than replaced with a synthetic ID.
    Every call is recorded; the source-only preflight itself never calls this.
    """

    def __init__(self, *, registered_plugin_id: str | None,
                 legacy_plugin_id: str) -> None:
        self.registered_plugin_id = registered_plugin_id
        self.legacy_plugin_id = legacy_plugin_id
        self.calls: list[dict[str, str | None]] = []

    def update_plugin(self, *, plugin_id: str | None,
                      expected_release_id: str | None,
                      archive_sha256: str | None) -> dict[str, Any]:
        self.calls.append({"plugin_id": plugin_id,
                           "expected_release_id": expected_release_id,
                           "archive_sha256": archive_sha256})
        if not self.registered_plugin_id:
            return {"status": "BLOCKED_NEXT_PLUGIN_NOT_REGISTERED",
                    "network": False, "registered": False}
        if plugin_id == self.legacy_plugin_id:
            return {"status": "LEGACY_ID_UPDATE_FORBIDDEN",
                    "network": False, "registered": False}
        if plugin_id != self.registered_plugin_id:
            return {"status": "PLUGIN_ID_MISMATCH",
                    "network": False, "registered": False}
        return {"status": "SIMULATED_NOT_SENT", "network": False,
                "registered": True, "plugin_id": plugin_id,
                "expected_release_id": expected_release_id,
                "archive_sha256": archive_sha256}

def preflight(*, legacy_baseline: dict[str, Any],
              legacy_current: dict[str, Any],
              next_plugin: dict[str, Any],
              previous_next: dict[str, Any] | None = None,
              explicit_next_update_consent: bool = False,
              expected_release_id: str | None = None,
              shadow: dict[str, Any] | None = None,
              pins: dict[str, Any] | None = None) -> dict[str, Any]:
    """Return PASS only when the two tracks are separate and Legacy is intact."""
    pins = TRUSTED_SOURCE_PINS if pins is None else pins
    try:
        legacy_id = _require(legacy_baseline, "backend_plugin_id")
        _require(legacy_baseline, "release_id")
        next_id = next_plugin.get("backend_plugin_id")
        if not isinstance(next_id, str) or not next_id.strip():
            return _blocked("BLOCKED_NEXT_PLUGIN_NOT_REGISTERED",
                            "Next has no real backend plugin ID; do not invent one")
        for field in ("release_id", "version", "mcp_url", "source_pin",
                      "archive_sha256", "content_sha256", "members"):
            _require(next_plugin, field)
        if next_plugin.get("source_pin") != "fleet_v1_9skill":
            return _blocked(
                "MIXED_SOURCE_PIN",
                "Next must use the canonical 9-Skill pin, not the standalone "
                "chatgpt-fleet-next 7-member package")

        # Legacy identity/controller is the thing Next must not touch.
        for field in ("backend_plugin_id", "name", "mcp_url", "oauth_audience",
                      "port", "datastore", "tool_namespace"):
            if legacy_current.get(field) != legacy_baseline.get(field):
                return _blocked(
                    "LEGACY_RENAMED" if field in ("backend_plugin_id", "name")
                    else "LEGACY_RESOURCE_CHANGED",
                    f"Legacy {field} changed: {legacy_current.get(field)!r} != "
                    f"{legacy_baseline.get(field)!r}")
        if _skills(legacy_current) != LEGACY_SKILLS:
            return _blocked(
                "LEGACY_CONTROLLER_MUTATED",
                "Legacy must retain exactly the 8 Operator Skills")

        # Never use the Legacy backend ID for the Next update.
        if next_id == legacy_id:
            return _blocked(
                "LEGACY_ID_UPDATE_FORBIDDEN",
                "Next may not use the Legacy backend plugin ID; that would be "
                "an update of the old controller, not a parallel release")

        for field, code in RESOURCE_FIELDS:
            if next_plugin.get(field) is not None and \
                    next_plugin.get(field) == legacy_current.get(field):
                return _blocked(
                    code,
                    f"Next must not share Legacy {field} "
                    f"({legacy_current.get(field)!r})")

        if _skills(next_plugin) != NEXT_SKILLS:
            return _blocked(
                "NEXT_CONTROLLER_INCOMPLETE",
                f"Next must carry the 8 Operator Skills + fleet-batch-commander; "
                f"got {sorted(_skills(next_plugin))}")

        _pin(next_plugin, pins)
        if legacy_current.get("archive_sha256") == next_plugin.get("archive_sha256"):
            return _blocked("MIXED_SOURCE_ARCHIVE",
                            "Legacy and Next must not share an archive digest")

        # A same-ID update is allowed only within future versions of Next.
        if previous_next is not None and next_id == previous_next.get("backend_plugin_id"):
            if not explicit_next_update_consent:
                return _blocked(
                    "NEXT_UPDATE_CONSENT_REQUIRED",
                    "same-ID Next update needs explicit owner consent")
            if expected_release_id != previous_next.get("release_id"):
                return _blocked(
                    "STALE_EXPECTED_RELEASE_ID",
                    f"{expected_release_id!r} != {previous_next.get('release_id')!r}")
            old = str(previous_next.get("version") or "")
            new = str(next_plugin.get("version") or "")
            if not SEMVER.match(old) or not SEMVER.match(new) or \
                    tuple(map(int, new.split("."))) <= tuple(map(int, old.split("."))):
                return _blocked(
                    "NEXT_VERSION_NOT_ADVANCING",
                    f"{new!r} is not above {old!r}")

        if shadow:
            writes = sorted(set(shadow.get("requested_writes") or ()) & SHADOW_WRITES)
            if writes:
                return _blocked(
                    "NEXT_SHADOW_WRITE_FORBIDDEN",
                    f"shadow is read-only; refused {writes}")

        return {
            "verdict": "PASS",
            "legacy_backend_plugin_id": legacy_id,
            "next_backend_plugin_id": next_id,
            "legacy_release_id": legacy_baseline.get("release_id"),
            "next_release_id": next_plugin.get("release_id"),
            "legacy_mcp_url": legacy_current.get("mcp_url"),
            "next_mcp_url": next_plugin.get("mcp_url"),
            "source_pin": next_plugin.get("source_pin"),
            "source_archive_sha256": next_plugin.get("archive_sha256"),
            "legacy_skills": sorted(_skills(legacy_current)),
            "next_skills": sorted(_skills(next_plugin)),
            "independent_version": next_plugin.get("version"),
            "same_id_next_update": bool(
                previous_next and next_id == previous_next.get("backend_plugin_id")),
            "legacy_usable_on_failure": True,
            "next_install_isolated": True,
            "rollback_target": legacy_baseline.get("release_id"),
            "creator_calls": 0,
            "network": False,
            "production_verified": False,
            "shadow_read_only": True,
        }
    except Refused as refusal:
        return _blocked(refusal.code, refusal.detail)
