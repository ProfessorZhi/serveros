"""Validate session identity before metadata becomes a subsequent address."""
from __future__ import annotations

from .errors import BAD_SESSION_REF, FleetRefusal, refuse
from .identity import SessionKey


def validate_binding_target(key, binding, *, observation=False):
    if binding is None:
        return
    if key.device_ref != binding.device_ref:
        raise refuse(BAD_SESSION_REF, "session does not belong to this device binding")
    # Legacy lists can observe an unresolved native family. Addressed calls
    # require the explicit route; an observation is not that declaration.
    multiplex = binding.multiplex_native_families or observation
    allowed = ("claude", "codex") if binding.runtime == "claude" and multiplex else (binding.runtime,)
    if key.provider not in allowed:
        raise refuse(BAD_SESSION_REF, "session does not belong to this runtime binding")


def normalize_detail(key, payload, binding=None, *, repair_legacy_claude=False):
    validate_binding_target(key, binding)
    if not isinstance(payload, dict):
        raise refuse(BAD_SESSION_REF, "session detail is not an object")
    nested = payload.get("session")
    if "session" in payload and not isinstance(nested, dict):
        raise refuse(BAD_SESSION_REF, "session detail has no native session object")
    nested = nested if isinstance(nested, dict) else None
    native = nested if nested is not None else payload
    expected_family = (
        binding.family if binding is not None and key.provider == binding.runtime
        and key.provider not in ("claude", "codex") else key.provider)
    native_id_seen = False
    for field in ("id", "sessionId", "session_id", "native_session_id"):
        if field in native:
            if not isinstance(native[field], str) or native[field] != key.native_session_id:
                raise refuse(BAD_SESSION_REF, "detail names another native session")
            native_id_seen = True
    # Native provider means family; typed wire provider means address namespace.
    native_family = native.get("provider") if "session_ref" not in native else None
    if native_family is not None and native_family != expected_family:
        raise refuse(BAD_SESSION_REF, "native family contradicts the requested runtime")
    repair_needed = False
    for is_outer, obj in ((True, payload), (False, native)):
        if obj.get("device_ref") is not None and obj["device_ref"] != key.device_ref:
            raise refuse(BAD_SESSION_REF, "detail names another device")
        if (obj.get("native_session_id") is not None
                and obj["native_session_id"] != key.native_session_id):
            raise refuse(BAD_SESSION_REF, "detail names another native session")
        providers = []
        if obj.get("session_ref") is not None:
            try:
                address = SessionKey.parse(obj["session_ref"])
            except FleetRefusal:
                raise refuse(BAD_SESSION_REF, "session metadata contains an invalid address") from None
            if (address.device_ref != key.device_ref
                    or address.native_session_id != key.native_session_id):
                raise refuse(BAD_SESSION_REF, "detail reference names another target")
            providers.append(address.provider)
        if obj.get("provider") is not None and (
                "session_ref" in obj or is_outer and nested is not None):
            providers.append(obj["provider"])
        for provider in providers:
            if provider == key.provider:
                continue
            if (is_outer and nested is not None and repair_legacy_claude
                    and provider == "claude"):
                repair_needed = True
            else:
                raise refuse(BAD_SESSION_REF, "detail reference names another runtime")
    if repair_needed:
        typed_native = native.get("session_ref") == key.ref
        if not native_id_seen or not (typed_native or native_family == expected_family):
            raise refuse(BAD_SESSION_REF, "legacy wrapper lacks matching native identity evidence")
    return {**payload, **key.as_dict()}
