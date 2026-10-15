#!/usr/bin/env python3
"""Build the Fleet V1 **first-install** plugin package — or refuse, and say why.

This is the third builder in this repository, and the three are not
interchangeable. Reading the table is the fastest way to see why this one
exists:

| builder | produces | may carry MCP |
|---|---|---|
| `integrations/chatgpt-fleet-next/build_zip.py` | an instruction-only package | **no — refused** |
| `integrations/fleet-app-overlay/build_overlay.py` | an update overlay for an already-registered private plugin | yes, pinned to a baseline |
| **this** | the plugin a client registers **for the first time** | **yes, and the first release must** |

The last cell is the whole reason. A package that ships only Skills cannot be
turned into an MCP connector by a later update of the same plugin — the format
a client registers is the format it keeps — so the binding has to be in the
first ZIP or the connector never exists. That also means this builder is the
opposite of the instruction-only one: it **requires** `mcp.json`, while
`chatgpt-fleet-next/build_zip.py` refuses it.

What it will not do
-------------------
* **It will not invent an endpoint.** A binding is a claim that a server is
  reachable at a URL, so the URL must arrive with a *separate live
  observation* of that URL — the discovery documents read back, with the host
  named. No observation, no archive: `BLOCKED_MCP_ENDPOINT`.
* **It will not substitute an asset.** The two approved brand images are pinned
  by SHA-256 in `brand-assets.lock.json`. A missing file is
  `MISSING_BRAND_ASSET`; a file whose bytes are not the approved ones is
  `BRAND_ASSET_MISMATCH`. Neither produces a release ZIP, and neither is fixed
  by drawing something similar.
* **It will not claim a registration.** The App-backed path needs a plugin id
  and an App id *read back* from the client's Plugin Creator. Without that
  receipt it is `APP_BINDING_UNREGISTERED` — a name typed into a flag is not a
  registration.
* **It will not ship a placeholder.** When a gate blocks, the only archive it
  can still write is one whose filename ends
  `-SOURCE-ONLY-NOT-INSTALLABLE.zip` and which carries a `NOT-INSTALLABLE.md`
  member. A ZIP that *looks* like a release but cannot be installed is the one
  output this tool must never produce, because it is the one that gets
  installed.
* It will not talk to anything. No network, no Plugin Creator, no OAuth, no
  Feishu, no Tailscale. Every fact it accepts about the outside world is a
  receipt the operator collected.

The status set is closed
------------------------
`READY_TO_BUILD`, `BLOCKED_MCP_ENDPOINT`, `MISSING_BRAND_ASSET`,
`BRAND_ASSET_MISMATCH`, `MISSING_SKILLS`, `SOURCE_REFUSED`,
`APP_BINDING_UNREGISTERED`. A caller branches on the status and never on the
detail string; the detail is for a person.

Icons, and whose rules these are
--------------------------------
The 1.0.0 manifest schema defines no icon property and its property list is
closed, so the two slots are declared under the package's own `extensions`
namespace and shipped as files. Whether any client reads that block is
**UNVERIFIED**, and nothing here claims otherwise. The dimension and format
checks are this repository's own release rules, not the platform's.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import struct
import sys
import zipfile
from pathlib import Path
from urllib.parse import urlsplit

# -- the closed status set ------------------------------------------------

READY_TO_BUILD = "READY_TO_BUILD"
BLOCKED_MCP_ENDPOINT = "BLOCKED_MCP_ENDPOINT"
MISSING_BRAND_ASSET = "MISSING_BRAND_ASSET"
BRAND_ASSET_MISMATCH = "BRAND_ASSET_MISMATCH"
MISSING_SKILLS = "MISSING_SKILLS"
SOURCE_REFUSED = "SOURCE_REFUSED"
APP_BINDING_UNREGISTERED = "APP_BINDING_UNREGISTERED"

STATUSES = (READY_TO_BUILD, BLOCKED_MCP_ENDPOINT, MISSING_BRAND_ASSET,
            BRAND_ASSET_MISMATCH, MISSING_SKILLS, SOURCE_REFUSED,
            APP_BINDING_UNREGISTERED)

# -- what ships -----------------------------------------------------------

#: The portable layout the specification fixes, which is also the member set
#: `integrations/fleet-app-overlay/build_overlay.py` states as `ROOT_MEMBERS`.
#: Written down rather than derived, for the reason that builder gives: a
#: builder that packages the folder eventually packages its own previous
#: output, and stops being a function of the source.
PAYLOAD_ENTRIES = ("plugin.json", "mcp.json", "skills", "assets")

SCHEMA_URL = "https://agent-plugins.org/schemas/1.0.0/plugin.schema.json"
MCP_SCHEMA_URL = "https://agent-plugins.org/schemas/1.0.0/mcp.schema.json"

#: Every top-level manifest property the 1.0.0 schema defines; the root has
#: `additionalProperties: false`, so anything else is refused by a conforming
#: client and refused here first, where the message can name the stray key.
SCHEMA_PROPERTIES = frozenset({
    "$schema", "name", "version", "description", "author", "homepage",
    "repository", "license", "keywords", "extensions",
})

#: A tailnet hostname, which is what an off-host Fleet ingress is reached by.
#: Tailscale names a machine as `<machine>.<tailnet>.ts.net` — at least one
#: label before the tailnet's own — so a single-label pattern would refuse the
#: real host in `PACKAGING.md` and would not be a shape check at all.
TS_NET_HOST = re.compile(
    r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)+\.ts\.net$"
)

#: The V1 ingress host this repository has *observed*, recorded in
#: `integrations/chatgpt-fleet-next/PACKAGING.md` ("observed from a public probe
#: in #185"). It is here so that the Next binding cannot silently be the V1 one:
#: the two are separate releases with separate plugin ids, and a first install
#: that points at the old host is a re-pointing of V1 under a new name.
V1_OBSERVED_HOSTS = frozenset({"serveros-fleet.tail3d8b76.ts.net"})

#: Names and suffixes that never belong in an archive that leaves this machine.
FORBIDDEN_PARTS = frozenset({
    ".git", ".env", ".venv", "venv", "__pycache__", "node_modules",
    ".pytest_cache", "dist", "out", "tests",
})
FORBIDDEN_SUFFIXES = (".pem", ".key", ".p12", ".pfx", ".pyc", ".pyo")
FORBIDDEN_NAMES = frozenset({
    ".env", "credentials.json", "token.json", "id_rsa", "id_ed25519",
    "test_credentials.json",
})

#: Credential shapes and dump conventions. These are a floor, not a redactor:
#: the point is that a token pasted into a Skill is a *failed build*, because
#: the archive is uploaded, mirrored and cached, and a value that reaches it
#: has left the building.
FORBIDDEN_CONTENT = (
    ("cloudcli_pat", re.compile(r"\bccp_[A-Za-z0-9_\-]{8,}")),
    ("anthropic_key", re.compile(r"\bsk-ant-[A-Za-z0-9_\-]{12,}")),
    ("openai_key", re.compile(r"\bsk-[A-Za-z0-9]{20,}")),
    ("github_token", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{16,}")),
    ("aws_key", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("private_key", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
    ("bearer_header", re.compile(r"(?i)\bauthorization\s*[:=]\s*bearer\s+\S{12,}")),
    ("env_dump", re.compile(r"(?i)\b([A-Z0-9_]*(?:TOKEN|SECRET|PASSWORD|APIKEY|API_KEY))\s*[:=]\s*\S{8,}")),
)

#: Things that must not be referenced by a *first install* package. Neither is
#: secret; both are identity. The old V1 host means the binding is V1's, and a
#: native-plugin name means this package has absorbed a different release.
FORBIDDEN_MENTIONS = (
    ("v1_ingress_host", re.compile(r"serveros-fleet\.tail3d8b76\.ts\.net")),
    ("native_plugin_name", re.compile(r"\bhwz-server-os\b")),
)

#: A fixed timestamp, not "now": an archive stamped with the wall clock differs
#: every second and cannot be verified by rebuilding.
FIXED_DATE_TIME = (1980, 1, 1, 0, 0, 0)
FIXED_MODE = 0o644 << 16

NAME_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
SEMVER_RE = re.compile(r"^([0-9]+)\.([0-9]+)\.([0-9]+)$")

#: This repository's own icon rules — the platform documents none of them.
ICON_MIN_PIXELS = 48
ICON_MAX_BYTES = 5 * 1024 * 1024

SOURCE_BUNDLE_SUFFIX = "-SOURCE-ONLY-NOT-INSTALLABLE.zip"
NOT_INSTALLABLE_MEMBER = "NOT-INSTALLABLE.md"

ENDPOINT_OBSERVATION_SCHEMA = "serveros/fleet-next-endpoint-observation/v1"
APP_REGISTRATION_SCHEMA = "serveros/fleet-next-app-registration/v1"


class Refused(Exception):
    """A gate stopped the build. Carries a status and a reason, not a traceback."""

    def __init__(self, status: str, detail: str) -> None:
        super().__init__(f"{status}: {detail}")
        self.status = status
        self.detail = detail


# -- reading --------------------------------------------------------------

def _read_json(path: Path, what: str) -> dict:
    if not path.is_file():
        raise Refused(SOURCE_REFUSED, f"no {what} at {path}")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise Refused(SOURCE_REFUSED, f"{what} is not valid JSON: {exc}") from exc
    if not isinstance(value, dict):
        raise Refused(SOURCE_REFUSED, f"{what} must be a JSON object")
    return value


def load_manifest(root: Path) -> dict:
    manifest = _read_json(root / "plugin.json", "plugin.json")
    stray = sorted(set(manifest) - SCHEMA_PROPERTIES)
    if stray:
        raise Refused(
            SOURCE_REFUSED,
            f"plugin.json declares {stray}, which the 1.0.0 manifest schema does "
            f"not define and forbids (additionalProperties: false).",
        )
    if manifest.get("$schema") != SCHEMA_URL:
        raise Refused(SOURCE_REFUSED, f"plugin.json $schema must be {SCHEMA_URL!r}")
    name = manifest.get("name")
    if not isinstance(name, str) or not NAME_RE.match(name):
        raise Refused(SOURCE_REFUSED, f"plugin name {name!r} is not a lowercase hyphenated token")
    version = manifest.get("version")
    if not isinstance(version, str) or not SEMVER_RE.match(version):
        raise Refused(SOURCE_REFUSED, f"plugin version {version!r} is not MAJOR.MINOR.PATCH")
    return manifest


def audit_source(root: Path) -> list[str]:
    """Refuse what must not leave this machine, wherever it sits in the tree.

    Over the WHOLE tree and not over the selection: a member allowlist cannot
    see a file it did not select, so a check that only covered the shipped set
    could not tell "refused" from "quietly dropped".
    """
    notes: list[str] = []
    for path in sorted(root.rglob("*")):
        rel = path.relative_to(root)
        if set(rel.parts) & FORBIDDEN_PARTS:
            raise Refused(SOURCE_REFUSED,
                          f"refusing to package {rel.as_posix()}: a name in that path "
                          f"never belongs in a ZIP")
        if path.is_symlink():
            raise Refused(SOURCE_REFUSED,
                          f"refusing to package the symlink {rel.as_posix()}: its "
                          f"target is not in the archive and was never reviewed")
        if not path.is_file():
            continue
        if path.suffix.lower() in FORBIDDEN_SUFFIXES or path.name in FORBIDDEN_NAMES:
            raise Refused(SOURCE_REFUSED,
                          f"refusing to package {rel.as_posix()}: credential-shaped "
                          f"or by-product name")
        if path.name == "mcp.json":
            # The one file that would be a binding. It is generated, never
            # sourced, so that no committed URL can be loaded as one.
            raise Refused(
                SOURCE_REFUSED,
                f"{rel.as_posix()} is a committed `mcp.json`. The binding is "
                f"generated from mcp.template.json and the operator's observed "
                f"URL; a checked-in one would be a URL nobody observed.",
            )
        if path.suffix.lower() in (".md", ".json", ".txt", ".yaml", ".yml"):
            text = path.read_text(encoding="utf-8", errors="replace")
            for label, pattern in FORBIDDEN_CONTENT:
                if pattern.search(text):
                    raise Refused(
                        SOURCE_REFUSED,
                        f"{rel.as_posix()} matches {label!r}. The archive is "
                        f"uploaded and mirrored; a credential that reaches it has "
                        f"left the building.",
                    )
            for label, pattern in FORBIDDEN_MENTIONS:
                if pattern.search(text):
                    raise Refused(
                        SOURCE_REFUSED,
                        f"{rel.as_posix()} references {label!r}. A first-install "
                        f"package must not carry V1's host or the native plugin's "
                        f"name: either one makes it a different release.",
                    )
        notes.append(rel.as_posix())
    return notes


def check_skills(root: Path) -> list[str]:
    """`skills/<name>/SKILL.md`, immediate children only, each non-empty.

    The specification is explicit that clients do not search deeper
    descendants, so a skill one level further down is invisible. Checking the
    depth here is what stops a package that *looks* like it has five skills
    from shipping three.
    """
    skills_dir = root / "skills"
    if not skills_dir.is_dir():
        raise Refused(MISSING_SKILLS, "no skills/ directory")
    found: list[str] = []
    for child in sorted(skills_dir.iterdir()):
        if not child.is_dir():
            raise Refused(
                MISSING_SKILLS,
                f"skills/{child.name} is not a directory. Skills are immediate "
                f"subdirectories each holding a SKILL.md.",
            )
        skill_file = child / "SKILL.md"
        if not skill_file.is_file():
            raise Refused(MISSING_SKILLS, f"skills/{child.name}/SKILL.md is absent")
        if not skill_file.read_text(encoding="utf-8").strip():
            raise Refused(MISSING_SKILLS, f"skills/{child.name}/SKILL.md is empty")
        nested = [p for p in child.rglob("SKILL.md") if p.parent != child]
        if nested:
            raise Refused(
                MISSING_SKILLS,
                f"skills/{child.name} holds a nested SKILL.md "
                f"({nested[0].relative_to(root).as_posix()}). A conforming client "
                f"does not search deeper descendants, so it would never load it.",
            )
        found.append(child.name)
    if not found:
        raise Refused(MISSING_SKILLS, "skills/ holds no skill directory")
    return found


# -- the endpoint ---------------------------------------------------------

def _observation_problems(url: str, observation: Path | None) -> list[str]:
    problems: list[str] = []
    if observation is None:
        return ["no --observation receipt was supplied; a URL a caller typed is "
                "not an observation"]
    try:
        record = json.loads(Path(observation).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return [f"the observation receipt is unreadable: {exc}"]
    if not isinstance(record, dict) or record.get("schema") != ENDPOINT_OBSERVATION_SCHEMA:
        return [f"the observation receipt does not declare schema "
                f"{ENDPOINT_OBSERVATION_SCHEMA!r}"]
    if record.get("url") != url:
        problems.append(f"the receipt observes {record.get('url')!r}, not {url!r}; "
                        f"a binding may only be built for the URL that was observed")
    if record.get("source") != "LIVE":
        problems.append("the observation is not marked LIVE")
    if record.get("reachable") is not True:
        problems.append("the observation does not confirm the endpoint was reachable")
    tls = record.get("tls")
    if not isinstance(tls, dict) or tls.get("verified") is not True:
        problems.append("the observation does not confirm a verified TLS session")
    if not isinstance(record.get("observed_at"), str) or not record["observed_at"].strip():
        problems.append("the observation carries no timestamp")
    discovery = record.get("discovery")
    if not isinstance(discovery, dict):
        problems.append("the observation carries no OAuth discovery readback")
    else:
        for key in ("protected_resource", "authorization_server"):
            entry = discovery.get(key)
            if (not isinstance(entry, dict) or entry.get("status") != 200
                    or not isinstance(entry.get("receipt_ref"), str)
                    or not entry["receipt_ref"].strip()):
                problems.append(f"the observation carries no 200 readback for {key}")
    return problems


def check_endpoint(url: object, observation: Path | None, *,
                   v1_mcp_url: str | None = None) -> str:
    if not isinstance(url, str) or not url.strip():
        raise Refused(BLOCKED_MCP_ENDPOINT,
                      "no Next MCP URL was supplied. The Next ingress is not "
                      "reachable at a fabricated address; supply an observed one.")
    parts = urlsplit(url)
    host = parts.hostname or ""
    if parts.scheme != "https":
        raise Refused(BLOCKED_MCP_ENDPOINT,
                      f"{url!r} is not https. The Fleet ingress is non-loopback, "
                      f"and the specification requires HTTPS for a non-loopback "
                      f"endpoint.")
    if parts.username or parts.password or parts.query or parts.fragment or parts.port:
        raise Refused(BLOCKED_MCP_ENDPOINT,
                      f"{url!r} carries userinfo, a query, a fragment or an explicit "
                      f"port; a binding URL is a plain origin plus path.")
    if not TS_NET_HOST.match(host):
        raise Refused(BLOCKED_MCP_ENDPOINT,
                      f"{host!r} is not a <name>.ts.net tailnet hostname. The Next "
                      f"ingress is reached over the tailnet; a different host is a "
                      f"different ingress and has not been observed.")
    if parts.path != "/mcp":
        raise Refused(BLOCKED_MCP_ENDPOINT,
                      f"the path is {parts.path!r}; the Fleet MCP endpoint is /mcp.")
    if host in V1_OBSERVED_HOSTS:
        raise Refused(BLOCKED_MCP_ENDPOINT,
                      f"{host} is the V1 ingress this repository has already "
                      f"observed. The Next release is a different ingress with a "
                      f"different plugin id; binding it to V1's host would re-point "
                      f"V1 under a new name.")
    if v1_mcp_url and url.rstrip("/") == v1_mcp_url.rstrip("/"):
        raise Refused(BLOCKED_MCP_ENDPOINT,
                      "the supplied URL is the V1 MCP URL; Next and V1 may not share "
                      "an endpoint")

    problems = _observation_problems(url, observation)
    if problems:
        raise Refused(BLOCKED_MCP_ENDPOINT,
                      "the endpoint was not observed as a live Next ingress — "
                      + "; ".join(problems))
    return url.rstrip("/")


def render_mcp_binding(root: Path, url: str) -> str:
    """Generate `mcp.json` from the template. Returns the file's text."""
    template_path = root / "mcp.template.json"
    template = _read_json(template_path, "mcp.template.json")
    servers = template.get("mcpServers")
    if not isinstance(servers, dict) or not servers:
        raise Refused(SOURCE_REFUSED, "mcp.template.json declares no mcpServers")
    binding = {
        "$schema": MCP_SCHEMA_URL,
        "mcpServers": {
            name: {**dict(server), "url": url}
            for name, server in servers.items()
        },
    }
    for name, server in binding["mcpServers"].items():
        if set(server) - {"type", "url", "headers"}:
            raise Refused(SOURCE_REFUSED,
                          f"mcpServers.{name} carries a field the 1.0.0 mcp schema "
                          f"does not define")
        if server.get("type") != "streamable-http":
            raise Refused(SOURCE_REFUSED,
                          f"mcpServers.{name} is {server.get('type')!r}; the Fleet "
                          f"ingress is a streamable-http endpoint")
        if server.get("headers"):
            raise Refused(
                SOURCE_REFUSED,
                f"mcpServers.{name} declares headers. Header values are not a "
                f"portable secret mechanism and the specification says clients "
                f"must not forward them to another origin; a Fleet credential "
                f"does not belong in a package.",
            )
    return json.dumps(binding, indent=2, ensure_ascii=False) + "\n"


# -- the assets -----------------------------------------------------------

def _png_size(data: bytes) -> tuple[int, int] | None:
    if data[:8] != b"\x89PNG\r\n\x1a\n" or data[12:16] != b"IHDR":
        return None
    return struct.unpack(">II", data[16:24])


def check_assets(root: Path, lock: dict) -> dict:
    """Every pinned slot present and byte-identical, or a status that says which.

    Absent and wrong are separate statuses because they are separate fixes, and
    because reporting a file that IS there as "missing" would send an operator
    looking for something they already have.
    """
    report: dict[str, dict] = {}
    for slot in lock.get("slots", []):
        name = slot["slot"]
        expected = slot["expected_sha256"]
        path = root / slot["shipped_path"]
        if not path.is_file():
            raise Refused(
                MISSING_BRAND_ASSET,
                f"the approved {name} is not present at {slot['shipped_path']} "
                f"(expected sha256 {expected}). The approved bytes have not reached "
                f"this repository or this machine. Do not draw a substitute: an "
                f"asset that is not these bytes is a different asset.",
            )
        data = path.read_bytes()
        actual = hashlib.sha256(data).hexdigest()
        if actual != expected:
            raise Refused(
                BRAND_ASSET_MISMATCH,
                f"{slot['shipped_path']} hashes {actual}, and the approved {name} "
                f"is {expected}. A PNG of the right size is not the approved "
                f"image; only the pinned bytes are.",
            )
        if len(data) > ICON_MAX_BYTES:
            raise Refused(BRAND_ASSET_MISMATCH,
                          f"{slot['shipped_path']} is {len(data)} bytes, over the "
                          f"{ICON_MAX_BYTES} limit this repository applies")
        size = _png_size(data)
        if size is None:
            raise Refused(BRAND_ASSET_MISMATCH,
                          f"{slot['shipped_path']} is not a readable PNG")
        width, height = size
        if width < ICON_MIN_PIXELS or height < ICON_MIN_PIXELS:
            raise Refused(BRAND_ASSET_MISMATCH,
                          f"{slot['shipped_path']} is {width}x{height}, below "
                          f"{ICON_MIN_PIXELS}x{ICON_MIN_PIXELS}")
        if slot.get("square_required") and width != height:
            raise Refused(BRAND_ASSET_MISMATCH,
                          f"{slot['shipped_path']} is {width}x{height}; a composer "
                          f"avatar is drawn into a square slot")
        report[name] = {"sha256": actual, "width": width, "height": height,
                        "bytes": len(data)}
    return report


# -- the app binding ------------------------------------------------------

def check_app_binding(*, app_backed: bool, plugin_id: str | None,
                      registration: Path | None) -> dict | None:
    """The `--app-backed` path, which needs a receipt rather than a name.

    Refusing here rather than at submit time is the point: an App-backed package
    is submitted against an App id that the client's Plugin Creator assigned.
    An agent may not create that, may not guess it, and may not report that it
    succeeded.
    """
    if not app_backed:
        return None
    if not plugin_id or not isinstance(plugin_id, str) or not plugin_id.strip():
        raise Refused(APP_BINDING_UNREGISTERED,
                      "the App-backed path needs the plugin id that Plugin Creator "
                      "assigned. A name supplied on the command line is not a "
                      "registration.")
    if registration is None:
        raise Refused(APP_BINDING_UNREGISTERED,
                      "the App-backed path needs a registration receipt read back "
                      "from Plugin Creator (schema "
                      f"{APP_REGISTRATION_SCHEMA}). This tool does not create "
                      f"plugins and will not assume one exists.")
    try:
        record = json.loads(Path(registration).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise Refused(APP_BINDING_UNREGISTERED, f"the registration receipt is unreadable: {exc}")
    if not isinstance(record, dict) or record.get("schema") != APP_REGISTRATION_SCHEMA:
        raise Refused(APP_BINDING_UNREGISTERED,
                      f"the receipt does not declare schema {APP_REGISTRATION_SCHEMA!r}")
    if record.get("source") != "LIVE" or record.get("plugin_id") != plugin_id:
        raise Refused(APP_BINDING_UNREGISTERED,
                      "the receipt does not show this plugin id being read back LIVE "
                      "from Plugin Creator")
    if not isinstance(record.get("app_id"), str) or not record["app_id"].strip():
        raise Refused(APP_BINDING_UNREGISTERED, "the receipt names no App id")
    return record


# -- building -------------------------------------------------------------

def _stage(root: Path, stage: Path, *, mcp_text: str) -> list[Path]:
    """Write the shipped tree: the source members plus the generated binding."""
    stage.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    for entry in PAYLOAD_ENTRIES:
        target = stage / entry
        if entry == "mcp.json":
            target.write_text(mcp_text, encoding="utf-8")
            written.append(target)
            continue
        source = root / entry
        if source.is_file():
            target.write_bytes(source.read_bytes())
            written.append(target)
        elif source.is_dir():
            for path in sorted(source.rglob("*")):
                if not path.is_file():
                    continue
                dest = stage / path.relative_to(root)
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_bytes(path.read_bytes())
                written.append(dest)
        else:
            raise Refused(SOURCE_REFUSED, f"payload entry {entry!r} is absent from the source")
    return sorted(written)


def _write_archive(stage: Path, members: list[Path], destination: Path) -> str:
    destination.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(destination, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as zf:
        for path in sorted(members, key=lambda p: p.relative_to(stage).as_posix()):
            rel = path.relative_to(stage).as_posix()
            info = zipfile.ZipInfo(rel, date_time=FIXED_DATE_TIME)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = FIXED_MODE
            zf.writestr(info, path.read_bytes(),
                        compress_type=zipfile.ZIP_DEFLATED, compresslevel=9)
    return hashlib.sha256(destination.read_bytes()).hexdigest()


def _content_digest(stage: Path, members: list[Path]) -> str:
    accumulator = hashlib.sha256()
    for path in sorted(members, key=lambda p: p.relative_to(stage).as_posix()):
        accumulator.update(path.relative_to(stage).as_posix().encode())
        accumulator.update(path.read_bytes())
    return accumulator.hexdigest()


def preflight(root: Path, lock: dict, *, next_mcp_url, observation,
              app_backed=False, plugin_id=None, registration=None,
              v1_mcp_url=None) -> dict:
    """Run every gate and return what would be built, or raise the first refusal.

    Order matters and is deliberate: the source is read before anything else so
    that a tree carrying a credential is refused before a caller learns whether
    its endpoint was acceptable.
    """
    audited = audit_source(root)
    manifest = load_manifest(root)
    skills = check_skills(root)
    url = check_endpoint(next_mcp_url, observation, v1_mcp_url=v1_mcp_url)
    assets = check_assets(root, lock)
    app = check_app_binding(app_backed=app_backed, plugin_id=plugin_id,
                            registration=registration)
    mcp_text = render_mcp_binding(root, url)
    return {
        "status": READY_TO_BUILD,
        "name": manifest["name"],
        "version": manifest["version"],
        "mcp_url": url,
        "skills": skills,
        "assets": assets,
        "app_binding": app,
        "source_members": audited,
        "mcp_json": mcp_text,
    }


def build(root: Path, out_dir: Path, lock: dict, **kwargs) -> dict:
    """Write the release archive. Raises rather than writing a partial one."""
    plan = preflight(root, lock, **kwargs)
    out_dir.mkdir(parents=True, exist_ok=True)
    import tempfile
    with tempfile.TemporaryDirectory() as scratch:
        stage = Path(scratch) / "stage"
        members = _stage(root, stage, mcp_text=plan["mcp_json"])
        archive = out_dir / f"{plan['name']}-v{plan['version']}.zip"
        archive_sha = _write_archive(stage, members, archive)
        content_sha = _content_digest(stage, members)
    record = {
        "schema": "serveros/fleet-v1-first-install.v1",
        "distribution": "FIRST_INSTALL_RELEASE",
        "name": plan["name"],
        "version": plan["version"],
        "archive": archive.name,
        "archive_sha256": archive_sha,
        "content_sha256": content_sha,
        "members": sorted(p.relative_to(stage).as_posix() for p in members),
        "mcp_url": plan["mcp_url"],
        "assets": plan["assets"],
        "skills": plan["skills"],
        "app_binding": plan["app_binding"],
        "writes": [],
        "uploads": [],
        "installs": [],
        "creates_plugin": False,
        "warning": ("A built archive is a source artefact. Nothing here uploads, "
                    "registers, installs or authorises: the plugin id is assigned "
                    "by the client's Plugin Creator, and the OAuth consent is the "
                    "owner's."),
    }
    return record


def source_bundle(root: Path, out_dir: Path, manifest: dict, reason: str) -> dict:
    """The only archive this tool may write while blocked.

    Named so that it cannot be mistaken for a release, and carrying a member
    that says the same thing to anyone who opens it without reading the name.
    """
    name = manifest.get("name") or "unnamed-plugin"
    version = manifest.get("version") or "0.0.0"
    out_dir.mkdir(parents=True, exist_ok=True)
    archive = out_dir / f"{name}-v{version}{SOURCE_BUNDLE_SUFFIX}"
    notice = (
        "# NOT INSTALLABLE\n\n"
        "This archive is the plugin SOURCE, not a release. It is written when a\n"
        "release gate has blocked, and it is named so that it cannot be taken for\n"
        "one. It carries no generated `mcp.json`: a package that cannot bind a\n"
        f"server is not installable, and this is that package.\n\n"
        f"Blocked status: {reason}\n"
    )
    import tempfile
    with tempfile.TemporaryDirectory() as scratch:
        stage = Path(scratch) / "stage"
        members: list[Path] = []
        for path in sorted(root.rglob("*")):
            if not path.is_file() or path.suffix.lower() in (".pyc", ".pyo"):
                continue
            dest = stage / path.relative_to(root)
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(path.read_bytes())
            members.append(dest)
        notice_path = stage / NOT_INSTALLABLE_MEMBER
        notice_path.write_text(notice, encoding="utf-8")
        members.append(notice_path)
        archive_sha = _write_archive(stage, members, archive)
        content_sha = _content_digest(stage, members)
    return {"archive": archive.name, "archive_sha256": archive_sha,
            "content_sha256": content_sha,
            "members": sorted(p.relative_to(stage).as_posix() for p in members),
            "blocked_status": reason, "installable": False}


def main(argv: list[str] | None = None) -> int:
    here = Path(__file__).resolve().parent
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--source", default=str(here / "plugin"))
    parser.add_argument("--lock", default=str(here / "brand-assets.lock.json"))
    parser.add_argument("--next-mcp-url", default=None,
                        help="the observed Next ingress URL, https://<name>.ts.net/mcp")
    parser.add_argument("--observation", default=None,
                        help="the live endpoint observation receipt")
    parser.add_argument("--v1-mcp-url", default=None,
                        help="V1's MCP URL, refused as the Next binding")
    parser.add_argument("--app-backed", action="store_true",
                        help="build the private App-backed variant instead")
    parser.add_argument("--plugin-id", default=None)
    parser.add_argument("--app-registration", default=None)
    parser.add_argument("--out", default="dist")
    parser.add_argument("--source-bundle", action="store_true",
                        help="on a blocked build, also write the clearly-named "
                             "source-only archive")
    parser.add_argument("--check", action="store_true",
                        help="build twice and assert both digests agree")
    args = parser.parse_args(argv)

    root = Path(args.source).resolve()
    out_dir = Path(args.out).resolve()
    lock = _read_json(Path(args.lock).resolve(), "brand-assets.lock.json")
    kwargs = dict(next_mcp_url=args.next_mcp_url, observation=args.observation,
                  app_backed=args.app_backed, plugin_id=args.plugin_id,
                  registration=args.app_registration, v1_mcp_url=args.v1_mcp_url)

    try:
        if args.check:
            import tempfile
            with tempfile.TemporaryDirectory() as one, tempfile.TemporaryDirectory() as two:
                first = build(root, Path(one), lock, **kwargs)
                second = build(root, Path(two), lock, **kwargs)
            if first["archive_sha256"] != second["archive_sha256"]:
                raise Refused(SOURCE_REFUSED,
                              "two builds of one source produced different archive bytes")
            if first["content_sha256"] != second["content_sha256"]:
                raise Refused(SOURCE_REFUSED,
                              "two builds of one source produced different content digests")
            record = first
            record["stable"] = True
        else:
            record = build(root, out_dir, lock, **kwargs)
    except Refused as refusal:
        print(f"{refusal.status}: {refusal.detail}", file=sys.stderr)
        if args.source_bundle:
            try:
                manifest = load_manifest(root)
            except Refused:
                manifest = {"name": "unnamed-plugin", "version": "0.0.0"}
            bundle = source_bundle(root, out_dir, manifest, refusal.status)
            print(f"{bundle['archive']} sha256={bundle['archive_sha256']} "
                  f"(NOT INSTALLABLE)", file=sys.stderr)
        return 2

    print(f"{record['name']} v{record['version']}")
    print(f"archive={record['archive']} sha256={record['archive_sha256']}")
    print(f"content_sha256={record['content_sha256']}")
    print(f"mcp_url={record['mcp_url']}")
    print(f"members={len(record['members'])} skills={len(record['skills'])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
