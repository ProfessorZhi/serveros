"""Session and run identity for the Fleet surface.

The failure this module exists to prevent
-----------------------------------------
Two CloudCLI installations on two machines each hand out their own session ids.
Today both happen to be UUIDv4 and collisions are unlikely — which is exactly
the kind of fact that holds until it does not, and then presents as one
machine's conversation appearing under another machine's name.

So a session is never addressed by its provider-local id. It is addressed by a
triple, and the triple is the key:

    device_ref  ·  provider  ·  native_session_id

`device_ref` is ServerOS's typed identity (`device:<id>`), authored in
`fleet/registry.yaml` and never derived from a hostname. `provider` is the
runtime family the session belongs to (`claude`, `codex`, …). The native id is
whatever the provider calls the session, carried unchanged and never
interpreted here.

The textual form is `device:work-fj|claude|b2cb3f10-…`. It round-trips, it
sorts, and — the property that matters — a caller cannot construct one that
addresses a session on a device it did not name.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from .errors import BAD_SESSION_REF, refuse

# `|` is the separator because it cannot appear in a device id
# (`transport-contract.yaml#identity.id_pattern`), cannot appear in a provider
# name, and does not appear in the UUIDs CloudCLI issues. A separator that can
# occur inside a part is a parser that guesses.
SEPARATOR = "|"

# Native ids are carried, not parsed. The only constraint this surface places
# on them is that they are non-empty and free of the separator — anything more
# would be this surface claiming to know a provider's id rules, and the id that
# fails the stricter rule would be the one a caller needed.
NATIVE_ID_MAX = 200

_PROVIDER_RE = re.compile(r"^[a-z][a-z0-9._-]{0,31}$")


@dataclass(frozen=True, order=True)
class SessionKey:
    """`device_ref + provider + native_session_id`, and nothing else."""

    device_ref: str
    provider: str
    native_session_id: str

    def __post_init__(self) -> None:
        if not self.device_ref.startswith("device:"):
            raise ValueError(
                f"{self.device_ref!r} is not a typed device ref; a bare id or a "
                f"transport name is not an identity"
            )
        if not _PROVIDER_RE.match(self.provider):
            raise ValueError(f"invalid provider name {self.provider!r}")
        if not self.native_session_id:
            raise ValueError("native_session_id must not be empty")
        if SEPARATOR in self.native_session_id:
            raise ValueError(
                f"native_session_id contains the reference separator {SEPARATOR!r}"
            )
        if len(self.native_session_id) > NATIVE_ID_MAX:
            raise ValueError("native_session_id is implausibly long")

    @property
    def ref(self) -> str:
        return SEPARATOR.join((self.device_ref, self.provider, self.native_session_id))

    def as_dict(self) -> dict[str, str]:
        return {
            "device_ref": self.device_ref,
            "provider": self.provider,
            "native_session_id": self.native_session_id,
            "session_ref": self.ref,
        }

    @classmethod
    def parse(cls, value: str) -> "SessionKey":
        """Parse `device:<id>|<provider>|<native id>`.

        Raises a typed refusal rather than ValueError, because this is reached
        from a tool call where the caller's mistake has to come back as a
        code it can act on.
        """
        if not isinstance(value, str) or not value:
            raise refuse(BAD_SESSION_REF, "session_ref must be a non-empty string")
        parts = value.split(SEPARATOR)
        if len(parts) != 3:
            raise refuse(
                BAD_SESSION_REF,
                "session_ref must be exactly "
                "`device:<id>|<provider>|<native session id>`; got "
                f"{len(parts)} part(s). A bare session id is not enough: the "
                "same id can exist on two devices.",
            )
        device_ref, provider, native = parts
        if not device_ref.startswith("device:"):
            raise refuse(
                BAD_SESSION_REF,
                f"{device_ref!r} is not a typed device ref. Use the "
                f"`device_ref` value `fleet_list_devices` returned.",
            )
        if not provider or not native:
            raise refuse(BAD_SESSION_REF, "provider and native session id must be non-empty")
        try:
            return cls(device_ref=device_ref, provider=provider, native_session_id=native)
        except ValueError as exc:
            raise refuse(BAD_SESSION_REF, str(exc)) from exc

    @classmethod
    def compose(cls, device: str, provider: str, native: str) -> "SessionKey":
        """Build from three named arguments, normalizing the device ref.

        Accepting the bare id here (and only here) is deliberate: the tool
        schema takes `device` and `session` as separate, easy-to-supply fields,
        and this is the one place that turns them into the identity everything
        downstream uses.
        """
        device_ref = device if device.startswith("device:") else f"device:{device}"
        try:
            return cls(device_ref=device_ref, provider=provider, native_session_id=native)
        except ValueError as exc:
            raise refuse(BAD_SESSION_REF, str(exc)) from exc

    @property
    def device_id(self) -> str:
        return self.device_ref[len("device:"):]


@dataclass(frozen=True, order=True)
class RunKey:
    """A run, identified within the session that owns it.

    A run id alone is not enough for the same reason a session id alone is
    not: `run_get` on the wrong device would either miss or — worse — hit a
    different run that happens to share an id.
    """

    session: SessionKey
    run_id: str

    @property
    def ref(self) -> str:
        return f"{self.session.ref}{SEPARATOR}run:{self.run_id}"

    def as_dict(self) -> dict[str, str]:
        return {**self.session.as_dict(), "run_id": self.run_id}
