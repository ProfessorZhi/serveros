"""Authenticated identity labels and bounded, non-authorizing correlation refs.

Credential name remains the source for grants, replay keys and client cursors.
Only an exact mapping authored in the local binding supplies canonical kind and
subject. Neither token subject claims nor tool arguments select these labels.
"""
from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any

from .errors import BAD_REQUEST, SCOPE_DENIED, refuse

KINDS = frozenset({"owner", "client", "agent"})
CONTEXT_REFS = ("work_ref", "parent_ref", "agent_ref")
_REF = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:|\-]*\Z")


def valid_ref(value) -> bool:
    return isinstance(value, str) and len(value) <= 200 and _REF.fullmatch(value) is not None


def parse_principals(raw) -> dict[str, dict[str, str]]:
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise ValueError("auth.principals must map exact credential names to subject/kind")
    result, subjects = {}, {}
    for name, entry in raw.items():
        if (not valid_ref(name) or not isinstance(entry, dict)
                or set(entry) != {"subject", "kind"}
                or not valid_ref(entry.get("subject"))
                or not isinstance(entry.get("kind"), str) or entry["kind"] not in KINDS):
            raise ValueError("auth.principals requires exact names, bounded subjects and owner/client/agent kind")
        subject, kind = entry["subject"], entry["kind"]
        if subject in subjects and subjects[subject] != kind:
            raise ValueError("one canonical subject cannot have multiple principal kinds")
        subjects[subject] = kind
        result[name] = {"subject": subject, "kind": kind}
    return result


@dataclass(frozen=True)
class Principal:
    name: str
    scopes: frozenset[str]
    credential_kind: str
    kind: str = "client"
    subject: str | None = None

    def __post_init__(self):
        if not isinstance(self.kind, str) or self.kind not in KINDS:
            raise ValueError("unknown principal kind")
        if self.subject is None:
            object.__setattr__(self, "subject", self.name)
        elif not valid_ref(self.subject):
            raise ValueError("invalid canonical principal subject")

    def require(self, scope: str) -> None:
        if scope not in self.scopes:
            raise refuse(SCOPE_DENIED,
                         f"credential {self.name!r} ({self.credential_kind}) does not "
                         f"carry {scope!r}; it carries {sorted(self.scopes)}")

    def as_dict(self) -> dict[str, Any]:
        return {"name": self.name, "credential_kind": self.credential_kind,
                "scopes": sorted(self.scopes), "kind": self.kind, "subject": self.subject}


def authenticated_principal(config, *, name, scopes, credential_kind) -> Principal:
    """Apply only local authored mapping after the existing credential proof."""
    mapping = config.auth.principals.get(name, {}) if config is not None else {}
    return Principal(name, scopes, credential_kind, **mapping)


def dispatch_context(*, work_ref=None, parent_ref=None, agent_ref=None) -> dict[str, str]:
    """Correlation only. Does not resolve Work ownership or grant a parent lease."""
    result = {}
    for name, value in (("work_ref", work_ref), ("parent_ref", parent_ref), ("agent_ref", agent_ref)):
        if value is not None:
            if not valid_ref(value):
                raise refuse(BAD_REQUEST, f"{name} must be an identifier of at most 200 characters")
            result[name] = value
    return result
