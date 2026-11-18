"""CloudCLI's model library, read and written without a key ever crossing.

CloudCLI keeps, per provider, a library of model entries. Each entry carries
environment rows that are laid over the spawn environment when a session runs
that model (CloudCLI `model-launch-spec.service.ts`). A row is one of:

    value   the key is set to the given text
    secret  the key is set to a value CloudCLI stores and never reads back
    envref  the key is set from a variable of the CloudCLI SERVER process
    unset   the key is removed from the spawn environment

This module is how Fleet lets an agent choose and shape those entries while
holding to one rule: **no secret value is ever carried through Fleet.**

*   Reading projects every row to its key and kind. A `secret` row says only
    that it is set. A `value` row whose key looks like a credential has its
    text withheld too — CloudCLI would have accepted a token pasted as plain
    text, and this surface does not repeat that mistake on the way out.
*   Writing accepts `value`, `envref` and `unset`, and `secret` only WITHOUT a
    value (CloudCLI keeps the value it already stores). A credential-looking
    key may only be an `envref`: the agent names the variable, the device's
    own environment (later: the key center) holds the value.

The library is reached over CloudCLI's REST routes on the SAME machine, with a
login token the owner placed on that machine. That token never leaves it: the
node connector reads it, the hub never sees it. CloudCLI's MCP gateway has no
model-library verbs, and CloudCLI itself is not modified for this.

Pure apart from `LocalLibrary`, which does the HTTP; the projection and the
checks are plain functions so the hub and the device apply the same rules.
"""
from __future__ import annotations

import json
import os
import re
import stat
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

from .errors import BAD_REQUEST, PROVIDER_UNAUTHORIZED, PROVIDER_UNREACHABLE, FleetRefusal
# The rules for an entry are not CloudCLI's; they live in `model_entries` and
# are re-exported here so the device side reads one name for each.
from .model_entries import (  # noqa: F401
    PROVIDER_RUNTIME, ROW_KINDS, SCHEMA, WRITABLE_KINDS, _ENV_KEY, check_entry,
    declared_models, is_secretish, source_contradiction)

CATALOG_SCHEMA = "serveros/fleet-agent-catalog.v1"


TOKEN_MAX_BYTES = 8192
#: Written by `serveros-secret sync` on this machine: the NAMES of the variables
#: the key center delivered here, never their values.
SECRET_NAMES = Path("~/.local/state/serveros/secret-names.json")


def secret_names(path: Path | None = None) -> dict[str, Any]:
    """The variable names the key center delivered to this machine."""
    path = (path or SECRET_NAMES).expanduser()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"state": "NOT_SYNCED", "names": []}
    if not isinstance(data, dict) or data.get("schema") != "serveros/secret-names.v1":
        return {"state": "UNREADABLE", "names": []}
    names = sorted(n for n in data.get("names") or [] if isinstance(n, str) and _ENV_KEY.match(n))
    return {"state": "SYNCED", "names": names, "synced_at": data.get("synced_at"),
            "errors": len(data.get("errors") or [])}


# -- reading ---------------------------------------------------------------

def _project_row(row: Any) -> dict[str, Any] | None:
    if not isinstance(row, dict) or not isinstance(row.get("key"), str):
        return None
    key, kind = row["key"], row.get("kind")
    if kind not in ROW_KINDS:
        return {"key": key, "kind": "UNKNOWN"}
    if kind == "secret":
        return {"key": key, "kind": "secret", "is_set": True}
    if kind == "unset":
        return {"key": key, "kind": "unset"}
    out: dict[str, Any] = {"key": key, "kind": kind}
    value = row.get("value")
    if kind == "value" and is_secretish(key):
        out["withheld"] = True
        out["note"] = ("a credential stored as plain text; rewrite it as an envref "
                       "row naming the variable that holds it")
    elif isinstance(value, str):
        # An envref's value is a variable NAME, not a value: safe to show.
        out["value" if kind == "value" else "variable"] = value
    return out


def project(provider: str, payload: Any) -> dict[str, Any]:
    """CloudCLI's `GET /api/providers/:provider/models` answer, made safe to carry."""
    data = payload.get("data", payload) if isinstance(payload, dict) else {}
    models = data.get("models") if isinstance(data, dict) else None
    options = models.get("OPTIONS") if isinstance(models, dict) else None
    if not isinstance(options, list):
        raise FleetRefusal(BAD_REQUEST, "the device's model library answer has no OPTIONS list")
    entries = []
    for option in options:
        if not isinstance(option, dict) or not isinstance(option.get("value"), str):
            continue
        config = option.get("config") if isinstance(option.get("config"), dict) else None
        rows = [r for r in (_project_row(x) for x in (config or {}).get("env") or []) if r]
        entries.append({
            "model_id": option["value"],
            "label": option.get("label") if isinstance(option.get("label"), str) else None,
            "custom": bool(option.get("isCustom")),
            "env": rows,
        })
    return {
        "schema": SCHEMA,
        "provider": provider,
        "default": models.get("DEFAULT") if isinstance(models.get("DEFAULT"), str) else None,
        "entries": entries,
        "note": ("secret rows show only that they are set; an envref names a variable "
                 "of the CloudCLI service, whose presence CloudCLI checks at launch"),
    }


# -- the device side: CloudCLI's REST routes on this machine ----------------

def rest_base(mcp_endpoint: str) -> str:
    """`http://127.0.0.1:3001/mcp` -> `http://127.0.0.1:3001`; loopback only."""
    parts = urllib.parse.urlsplit(mcp_endpoint)
    if parts.hostname not in ("127.0.0.1", "localhost", "::1"):
        raise FleetRefusal(
            BAD_REQUEST,
            "the model library is reached on this machine only; the CloudCLI "
            "endpoint is not a loopback address")
    return f"{parts.scheme}://{parts.netloc}"


def read_token(path: Path) -> str:
    """The owner's CloudCLI login token: one open, no symlink, 0600, this account's."""
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(path, flags)
    except OSError:
        raise FleetRefusal(PROVIDER_UNAUTHORIZED,
                           "the CloudCLI login token for the model library is not readable "
                           "on this machine; the owner logs in once with "
                           "`python3 -m fleet_mcp.node.model_login`") from None
    try:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode):
            raise FleetRefusal(PROVIDER_UNAUTHORIZED, "the token path is not a regular file")
        if hasattr(os, "getuid") and (st.st_uid != os.getuid() or st.st_mode & 0o077):
            raise FleetRefusal(PROVIDER_UNAUTHORIZED,
                               "the token file must belong to this account and be mode 0600")
        raw = os.read(fd, TOKEN_MAX_BYTES + 1)
    finally:
        os.close(fd)
    if len(raw) > TOKEN_MAX_BYTES:
        raise FleetRefusal(PROVIDER_UNAUTHORIZED, "the token file is larger than a token")
    token = raw.decode("utf-8", "replace").strip()
    if not token:
        raise FleetRefusal(PROVIDER_UNAUTHORIZED, "the token file is empty")
    return token


def write_token(path: Path, token: str) -> None:
    """Replace the token atomically, 0600 from the first byte."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".model-token-")
    try:
        os.fchmod(fd, 0o600) if hasattr(os, "fchmod") else None
        os.write(fd, token.strip().encode("utf-8"))
    finally:
        os.close(fd)
    os.replace(tmp, path)


#: What CloudCLI says a Skill or command is called with. Anything else is not
#: an invocation this surface will ever put into a session.
_INVOCATION = re.compile(r"^[/$][A-Za-z0-9_.:/-]{1,160}$")
CATALOG_MAX = 500


def _text(value: Any, limit: int) -> str:
    return re.sub(r"\s+", " ", value).strip()[:limit] if isinstance(value, str) else ""


def project_skills(provider: str, payload: Any) -> dict[str, Any]:
    """`GET /api/providers/:provider/skills`, projected: names, invocations, no paths."""
    data = payload.get("data", payload) if isinstance(payload, dict) else {}
    rows = data.get("skills") if isinstance(data, dict) else None
    if not isinstance(rows, list):
        raise FleetRefusal(BAD_REQUEST, "the device's skill listing has no skills list")
    skills = []
    for row in rows[:CATALOG_MAX]:
        if not isinstance(row, dict) or not isinstance(row.get("name"), str):
            continue
        invocation = row.get("command")
        if not isinstance(invocation, str) or not _INVOCATION.match(invocation):
            continue
        skills.append({"name": _text(row["name"], 160), "invocation": invocation,
                       "description": _text(row.get("description"), 300),
                       "scope": _text(row.get("scope"), 20) or None,
                       "plugin": _text(row.get("pluginName"), 120) or None})
    return {"schema": CATALOG_SCHEMA, "kind": "skills", "provider": provider,
            "items": skills, "truncated": len(rows) > CATALOG_MAX}


def project_commands(payload: Any) -> dict[str, Any]:
    """`POST /api/commands/list`, projected. Built-in ones are CloudCLI's own
    screen actions (/config, /memory …), listed but not invocable in a session."""
    if not isinstance(payload, dict):
        raise FleetRefusal(BAD_REQUEST, "the device's command listing is not an object")
    commands = []
    for group, invocable in (("custom", True), ("builtIn", False)):
        for row in (payload.get(group) or [])[:CATALOG_MAX]:
            if not isinstance(row, dict):
                continue
            name = row.get("name")
            if not isinstance(name, str) or not _INVOCATION.match(name) or not name.startswith("/"):
                continue
            commands.append({"name": name, "invocation": name,
                             "description": _text(row.get("description"), 300),
                             "scope": _text(row.get("namespace"), 20) or None,
                             "invocable": invocable})
    return {"schema": CATALOG_SCHEMA, "kind": "commands", "provider": "claude",
            "items": commands}


class LocalLibrary:
    """CloudCLI's model routes on this machine, with the owner's token."""

    def __init__(self, mcp_endpoint: str, token_path: Path, *, timeout: float = 15.0,
                 opener=urllib.request.urlopen) -> None:
        self.base = rest_base(mcp_endpoint)
        self.token_path = token_path
        self.timeout = timeout
        self._open = opener

    def _call(self, method: str, path: str, body: dict | None = None) -> Any:
        token = read_token(self.token_path)
        data = json.dumps(body).encode("utf-8") if body is not None else None
        req = urllib.request.Request(
            self.base + path, data=data, method=method,
            headers={"Authorization": f"Bearer {token}", "Accept": "application/json",
                     "Content-Type": "application/json"})
        try:
            with self._open(req, timeout=self.timeout) as resp:
                refreshed = resp.headers.get("X-Refreshed-Token") if resp.headers else None
                raw = resp.read(4_000_001)
        except urllib.error.HTTPError as exc:
            if exc.code == 401:
                raise FleetRefusal(PROVIDER_UNAUTHORIZED,
                                   "CloudCLI refused the model-library token (expired or "
                                   "revoked); the owner logs in again on this machine") from None
            try:
                detail = json.loads(exc.read(65536).decode("utf-8", "replace"))
                message = (detail.get("error") or {}).get("message") if isinstance(
                    detail.get("error"), dict) else detail.get("error")
            except (ValueError, AttributeError):
                message = None
            raise FleetRefusal(BAD_REQUEST, f"CloudCLI answered {exc.code}"
                               + (f": {message}" if isinstance(message, str) else "")) from None
        except (urllib.error.URLError, OSError) as exc:
            raise FleetRefusal(PROVIDER_UNREACHABLE,
                               f"CloudCLI's REST routes are not reachable here: "
                               f"{type(exc).__name__}") from None
        if refreshed:
            write_token(self.token_path, refreshed)
        try:
            return json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, ValueError):
            raise FleetRefusal(BAD_REQUEST, "CloudCLI's answer is not JSON") from None

    def skills(self, provider: str, workspace_path: str | None) -> dict[str, Any]:
        """The Skills the provider's agent sees in this workspace (and globally)."""
        query = ("?" + urllib.parse.urlencode({"workspacePath": workspace_path})
                 if workspace_path else "")
        return project_skills(provider, self._call(
            "GET", f"/api/providers/{urllib.parse.quote(provider, safe='')}/skills{query}"))

    def commands(self, project_path: str | None) -> dict[str, Any]:
        """The slash commands: the project's `.claude/commands` and the user's."""
        return project_commands(self._call("POST", "/api/commands/list",
                                           {"projectPath": project_path} if project_path else {}))

    def raw_list(self, provider: str) -> Any:
        return self._call("GET", f"/api/providers/{provider}/models")

    def list(self, provider: str) -> dict[str, Any]:
        out = project(provider, self.raw_list(provider))
        # Which variables an envref row can name here: the key center's names.
        out["variables"] = secret_names()
        return out

    def upsert(self, provider: str, model_id: str, label: str,
               rows: list[dict[str, Any]]) -> dict[str, Any]:
        """Create or replace ONE custom entry, then read the library back."""
        before = self.raw_list(provider)
        options = (((before.get("data") or {}).get("models") or {}).get("OPTIONS") or []
                   if isinstance(before, dict) else [])
        existing = next((o for o in options if isinstance(o, dict)
                         and o.get("value") == model_id), None)
        if existing is not None and not existing.get("isCustom"):
            raise FleetRefusal(BAD_REQUEST,
                               f"{model_id} is one of CloudCLI's built-in models and cannot "
                               f"be rewritten; choose another id")
        body = {"id": model_id, "model": label, "config": {"env": rows}}
        if existing is None:
            self._call("POST", f"/api/providers/{provider}/models", body)
            action = "created"
        else:
            record = urllib.parse.quote(str(existing.get("recordId")), safe="")
            self._call("PATCH", f"/api/providers/{provider}/models/{record}", body)
            action = "updated"
        after = project(provider, self.raw_list(provider))
        known = secret_names()
        missing = sorted({r["value"] for r in rows if r["kind"] == "envref"}
                         - set(known["names"])) if known["state"] == "SYNCED" else []
        entry = next((e for e in after["entries"] if e["model_id"] == model_id), None)
        written = {(r["key"], r["kind"]) for r in rows}
        seen = {(r["key"], r["kind"]) for r in (entry or {}).get("env", [])}
        return {
            "schema": SCHEMA,
            "provider": provider,
            "action": action,
            "entry": entry,
            "verified": entry is not None and entry.get("label") == label and written == seen,
            # An envref naming a variable the key center has not delivered here
            # resolves to nothing at launch; said now rather than discovered then.
            **({"unknown_variables": missing,
                "warning": "these variables are not delivered to this machine by the key "
                           "center; add them with `serveros-secret set` first"} if missing else {}),
        }
