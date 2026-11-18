"""A model entry, as Fleet judges it: the rules, not where the library lives.

An entry is a model id, a label and environment rows (`value`, `secret`,
`envref`, `unset`). These are the checks the hub and the device both apply
before an entry is written — above all, that **no secret value is ever carried
through Fleet**. Nothing here knows which implementation holds the library
(today CloudCLI, see `cloudcli_models.py`); Fleet's own service reads only
this module, so a different connection layer keeps the same rules (D-2).
"""
from __future__ import annotations

import re
from typing import Any

from .errors import BAD_REQUEST, FleetRefusal

SCHEMA = "serveros/fleet-model-library.v1"

#: CloudCLI provider name -> the runtime kind runtime-policy.yaml judges.
PROVIDER_RUNTIME = {"claude": "claude-code", "codex": "codex"}

ROW_KINDS = ("value", "secret", "envref", "unset")
WRITABLE_KINDS = ("value", "envref", "unset", "secret")

#: Keys whose value is a credential however it is stored. Matched on the key
#: name only — never on the value, which this module does not inspect.
_SECRETISH = re.compile(r"(TOKEN|SECRET|PASSWORD|PASSWD|API_?KEY|AUTH|CREDENTIAL|PRIVATE)",
                        re.IGNORECASE)
_ENV_KEY = re.compile(r"^[A-Z_][A-Z0-9_]{0,127}$")
_MODEL_ID = re.compile(r"^\S{1,200}$")
_BASE_URL_KEYS = ("ANTHROPIC_BASE_URL", "OPENAI_BASE_URL")
_MODEL_KEYS = ("ANTHROPIC_MODEL", "ANTHROPIC_DEFAULT_SONNET_MODEL",
               "ANTHROPIC_DEFAULT_OPUS_MODEL", "ANTHROPIC_DEFAULT_HAIKU_MODEL")

def is_secretish(key: str) -> bool:
    return bool(_SECRETISH.search(key or ""))


# -- writing ---------------------------------------------------------------

def check_entry(provider: str, model_id: Any, label: Any, env: Any) -> list[dict[str, Any]]:
    """The env rows to write, or a refusal. Never accepts a secret value."""
    if provider not in PROVIDER_RUNTIME:
        raise FleetRefusal(BAD_REQUEST, f"provider must be one of {sorted(PROVIDER_RUNTIME)}")
    if not isinstance(model_id, str) or not _MODEL_ID.match(model_id):
        raise FleetRefusal(BAD_REQUEST, "model_id must be 1-200 characters with no whitespace")
    if not isinstance(label, str) or not label.strip() or len(label) > 80:
        raise FleetRefusal(BAD_REQUEST, "label must be 1-80 characters")
    if not isinstance(env, list) or len(env) > 64:
        raise FleetRefusal(BAD_REQUEST, "env must be a list of at most 64 rows")
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for i, row in enumerate(env):
        if not isinstance(row, dict) or set(row) - {"key", "kind", "value", "variable"}:
            raise FleetRefusal(BAD_REQUEST, f"env[{i}] must be {{key, kind, value|variable}}")
        key, kind = row.get("key"), row.get("kind")
        if not isinstance(key, str) or not _ENV_KEY.match(key):
            raise FleetRefusal(BAD_REQUEST, f"env[{i}].key is not an environment variable name")
        if key in seen:
            raise FleetRefusal(BAD_REQUEST, f"env key {key} appears twice")
        seen.add(key)
        if kind not in WRITABLE_KINDS:
            raise FleetRefusal(BAD_REQUEST, f"env[{i}].kind must be one of {list(WRITABLE_KINDS)}")
        if kind == "secret":
            if "value" in row or "variable" in row:
                raise FleetRefusal(
                    BAD_REQUEST,
                    f"{key}: a secret value is never written through Fleet. Name the "
                    f"variable that holds it as an envref row, or send {{key, kind: "
                    f"secret}} with no value to keep the one the device already stores.")
            rows.append({"key": key, "kind": "secret"})
        elif kind == "unset":
            rows.append({"key": key, "kind": "unset"})
        elif kind == "envref":
            var = row.get("variable", row.get("value"))
            if not isinstance(var, str) or not _ENV_KEY.match(var):
                raise FleetRefusal(BAD_REQUEST, f"{key}: an envref names a variable, e.g. GW0_TOKEN")
            rows.append({"key": key, "kind": "envref", "value": var})
        else:
            value = row.get("value")
            if is_secretish(key):
                raise FleetRefusal(
                    BAD_REQUEST,
                    f"{key} holds a credential, so it may only be an envref row naming "
                    f"the variable that holds it — never a plain value.")
            if not isinstance(value, str) or len(value) > 2048:
                raise FleetRefusal(BAD_REQUEST, f"{key}: value must be text of at most 2048 characters")
            rows.append({"key": key, "kind": "value", "value": value})
    return rows


def declared_models(model_id: str, rows: list[dict[str, Any]]) -> list[str]:
    """Every model name this entry would run, for the runtime policy to judge."""
    names = [model_id]
    for row in rows:
        if row["kind"] == "value" and row["key"] in _MODEL_KEYS and row["value"] not in names:
            names.append(row["value"])
    return names


def source_contradiction(source: str, rows: list[dict[str, Any]]) -> str | None:
    """A declared source the rows plainly contradict, said in one sentence."""
    sets_base = any(r["key"] in _BASE_URL_KEYS and r["kind"] in ("value", "envref") for r in rows)
    if source in ("gateway", "self_hosted") and not sets_base:
        return (f"source {source} needs a base URL row ({' or '.join(_BASE_URL_KEYS)}); "
                f"without one the session would go to the vendor")
    if source in ("official_subscription", "official_api") and sets_base:
        return f"source {source} cannot carry a base URL row; that routes elsewhere"
    return None
