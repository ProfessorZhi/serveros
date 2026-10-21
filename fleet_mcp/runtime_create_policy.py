"""Owner-approved per-runtime, per-project CloudCLI session-create defaults.

The upstream model is a provider-native ID, not proof of entitlement. A runtime
means one credential instance, NOT one OS user. No wildcard runtime or project,
no implicit cross-account/model fallback, and no credential content in policy.

The global project grant and the bound runtime independently cap authority.
An explicit caller override must be permitted by BOTH caps. Defaults are
applied only inside a named, owner-approved runtime/project entry.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Mapping

PROFILE_KINDS = frozenset({"cloudcli-deepseek", "official-claude", "marketops-claude", "codex", "other"})
FAMILIES = frozenset({"claude", "codex"})
FIND = re.compile(r"^[a-zA-Z0-9_.:/-]{1,128}$")
PROJECT = re.compile(r"^[a-zA-Z0-9_.-]{1,128}$")


class PolicyError(ValueError):
    pass


@dataclass(frozen=True)
class RuntimeProjectDefaults:
    kind: str
    family: str
    default_model: str | None
    default_permission_mode: str | None
    allowed_models: tuple[str, ...]
    allowed_permission_modes: tuple[str, ...]

    def resolve(self, *, family: str, model: str | None,
                permission_mode: str | None) -> tuple[str | None, str | None]:
        if family != self.family:
            raise PolicyError("agent family not authorized on this runtime/project")
        chosen_model = self.default_model if model is None else model
        chosen_mode = self.default_permission_mode if permission_mode is None else permission_mode
        if chosen_model is not None and chosen_model not in self.allowed_models:
            raise PolicyError("model not approved for this runtime/project")
        if chosen_mode is not None and chosen_mode not in self.allowed_permission_modes:
            raise PolicyError("permission mode not approved for this runtime/project")
        return chosen_model, chosen_mode


def _names(value: Any, label: str) -> tuple[str, ...]:
    if not isinstance(value, list) or len(value) == 0:
        raise PolicyError(label + " must be a nonempty list")
    if any(not isinstance(x, str) or not FIND.fullmatch(x) for x in value):
        raise PolicyError(label + " must contain only opaque nonempty identifiers")
    if len(set(value)) != len(value):
        raise PolicyError(label + " contains duplicates")
    return tuple(value)


def parse_runtime_create_defaults(
    raw: Any, *, known_runtimes: set[str],
    project_ceilings: Mapping[str, Any],
) -> dict[str, dict[str, RuntimeProjectDefaults]]:
    """Compile strict config, fail closed before serving tools.

    'known_runtimes' are resolved device|runtime provider binding keys, not
    caller-supplied account names. Each project ceiling has 'models' and
    'permission_modes' as existing CreateGrant fields.
    """
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise PolicyError("runtime_create_defaults must be a mapping")
    result: dict[str, dict[str, RuntimeProjectDefaults]] = {}
    for key, projects in raw.items():
        if not isinstance(key, str) or key not in known_runtimes:
            raise PolicyError("runtime_create_defaults references unbound runtime")
        if not isinstance(projects, dict) or not projects:
            raise PolicyError("runtime must name nonempty projects mapping")
        compiled = {}
        for project, entry in projects.items():
            if not isinstance(project, str) or not PROJECT.fullmatch(project) or project not in project_ceilings:
                raise PolicyError("runtime_create_defaults names unauthorized project")
            if not isinstance(entry, dict):
                raise PolicyError("project defaults must be a mapping")
            supported = {"kind", "family", "owner_approved", "default_model", "default_permission_mode",
                         "allowed_models", "allowed_permission_modes"}
            if set(entry) != supported:
                raise PolicyError("runtime project defaults have missing or unknown fields")
            if entry["owner_approved"] is not True:
                raise PolicyError("owner approval must be explicit")
            kind, family = entry["kind"], entry["family"]
            if kind not in PROFILE_KINDS or family not in FAMILIES:
                raise PolicyError("unsupported profile kind or provider family")
            if kind in {"official-claude", "marketops-claude", "cloudcli-deepseek"} and family != "claude":
                raise PolicyError("Claude profile cannot declare Codex agent family")
            if kind == "codex" and family != "codex":
                raise PolicyError("Codex profile cannot declare Claude agent family")
            model_names = _names(entry["allowed_models"], "allowed_models")
            mode_names = _names(entry["allowed_permission_modes"], "allowed_permission_modes")
            grant = project_ceilings[project]
            if not set(model_names).issubset(set(grant.models)):
                raise PolicyError("runtime models exceed project create grant")
            if not set(mode_names).issubset(set(grant.permission_modes)):
                raise PolicyError("runtime permission modes exceed project create grant")
            default_model = entry["default_model"]
            default_mode = entry["default_permission_mode"]
            if default_model is not None and default_model not in model_names:
                raise PolicyError("default model missing from runtime allowlist")
            if default_mode is not None and default_mode not in mode_names:
                raise PolicyError("default permission mode missing from runtime allowlist")
            if kind in {"official-claude", "marketops-claude"} and any("deepseek" in m.lower() or "v4.1flash" in m.lower() for m in model_names):
                raise PolicyError("dedicated Claude account must not inherit DeepSeek route")
            if kind == "cloudcli-deepseek" and (default_model is None or default_mode is None):
                raise PolicyError("DeepSeek runtime must declare explicit model and permission defaults")
            compiled[project] = RuntimeProjectDefaults(
                kind, family, default_model, default_mode, model_names, mode_names,
            )
        result[key] = compiled
    return result
