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
  reachable at a URL, so the URL must arrive with a *separate observation* of
  that URL — the OAuth discovery documents read back at 200, an issuer and a
  `scopes_supported` that advertises the six Fleet scopes, all on the endpoint's
  own origin. No observation, no archive: `BLOCKED_MCP_ENDPOINT`.

  **That receipt is an operator's claim, not a probe.** Everything in it was
  typed by a person, including `source: LIVE`, and this tool has no network and
  cannot check any of it. The builder's job is to refuse a *malformed* or
  *inconsistent* claim and to make the release gate's remaining work visible;
  it is not an authenticated verifier, and nothing it emits should be read as
  one. The `endpoint_evidence` and `endpoint_probe_performed_by_this_tool`
  fields in the record say so where a reader will actually see it.
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

Which namespace the client actually reads
-----------------------------------------
The presentation fields live under `extensions.com.openai.interface`, and the
MCP server's OAuth declaration under
`mcpServers.<name>.extensions.com.openai.auth`. Both are required here, and for
the same reason: a field declared only under this repository's own namespace is
a field no client is documented to read, so a package that kept its approved
artwork there would have asked nobody to render it — and would still have
looked complete.

The auth declaration is validated shape by shape. `type` must be `oauth` (this
server is an OAuth 2.1 authorization server with RFC 7591 dynamic client
registration; `none`, `api_key` and `mixed` describe a different service), the
client mode must be `dcr` (`provided` needs a real client id, which does not
exist before registration, and one written here would be invented), no field
may name a credential, and any scope list must stay inside the six scopes this
surface mints. `headers` remains refused everywhere.

The status set is closed
------------------------
`READY_TO_BUILD`, `BLOCKED_MCP_ENDPOINT`, `MISSING_BRAND_ASSET`,
`BRAND_ASSET_MISMATCH`, `MISSING_SKILLS`, `SOURCE_REFUSED`,
`APP_BINDING_UNREGISTERED`. A caller branches on the status and never on the
detail string; the detail is for a person.

Icons, and whose rules these are
--------------------------------
The 1.0.0 manifest schema has no root-level icon property. Presentation is
declared in the supported `extensions.com.openai.interface` object; its
`composerIcon` and `logo` values name shipped files pinned by SHA-256.
Static checks verify the manifest path and bytes. Whether the live ChatGPT
client actually renders the approved assets is **UNVERIFIED** until client
acceptance. The platform requires icons and logos to be square and at least
48 by 48 pixels. The additional file-byte limit is a repository release rule.
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
PAYLOAD_ENTRIES = ("plugin.json", "mcp.json", "README.md", "PRIVACY.md",
                   "skills", "assets")

SCHEMA_URL = "https://agent-plugins.org/schemas/1.0.0/plugin.schema.json"
MCP_SCHEMA_URL = "https://agent-plugins.org/schemas/1.0.0/mcp.schema.json"

#: Every top-level manifest property the 1.0.0 schema defines; the root has
#: `additionalProperties: false`, so anything else is refused by a conforming
#: client and refused here first, where the message can name the stray key.
SCHEMA_PROPERTIES = frozenset({
    "$schema", "name", "version", "description", "author", "homepage",
    "repository", "license", "keywords", "extensions",
})

#: The namespace the client is documented to read for presentation, and the
#: object the icon slots live in. Declaring `composerIcon` under this
#: repository's own namespace — which is what the first revision did — is not a
#: claim any client is documented to honour, and a package whose approved
#: artwork sits only there has not asked the client to render it at all.
OPENAI_NS = "com.openai"
PRESENTATION_OBJECT = "interface"
#: Required inside `extensions.com.openai.interface`. `shortDescription` and the
#: rest of the documented set are optional here; these three are what a first
#: release has no reason to omit.
REQUIRED_PRESENTATION_FIELDS = ("displayName", "composerIcon", "logo")
EXPECTED_DISPLAY_NAME = "ServerOS Fleet"

#: The six scopes this surface mints. Named here so the observation can be
#: checked against them; `tests/test_fleet_v1_first_install.py` asserts they
#: still equal `fleet_mcp.scopes.IMPLEMENTED_SCOPES`, so a scope added there
#: fails the build here rather than drifting out of the release with nobody
#: noticing.
FLEET_SCOPES = ("fleet:read", "fleet:send", "fleet:control", "fleet:create",
                "fleet:approve", "fleet:navigate")

#: What a remote MCP server may say about its own authorization. The platform
#: documents four `auth.type` values; this server offers exactly one of them,
#: and the other three are refused by name rather than tolerated. An `api_key`
#: or `none` declaration on the Fleet ingress would describe a different
#: service, and a package that describes a different service is worse than one
#: that describes none.
DOCUMENTED_AUTH_TYPES = ("none", "oauth", "mixed", "api_key")
ALLOWED_AUTH_TYPES = ("oauth",)

#: Every field the OAuth declaration may carry, reviewed one at a time. The
#: platform documents these; anything outside the set is refused, so a field
#: added to the template arrives as a build failure with a name rather than as
#: an unread blob in a release.
AUTH_FIELDS = frozenset({
    "type", "client",
    "registrationUrl", "authorizationServerBase", "resource",
    "defaultScopes", "baseScopes",
    "authorizationUrl", "tokenUrl",
    "oidcEnabled", "oidcConfigurationUrl", "oidcUserinfoEndpoint", "oidcScopesSupported",
})
#: URL-valued auth fields that name the ingress's own endpoints, so they must
#: be on the same origin as the MCP URL — an authorization server the client is
#: sent to on another host is not one this observation observed.
SAME_ORIGIN_AUTH_FIELDS = ("registrationUrl", "authorizationServerBase")
#: URL-valued auth fields that name endpoints of the authorization server. They
#: may be on another host, but they must still be plain HTTPS origins.
ABSOLUTE_AUTH_URL_FIELDS = ("authorizationUrl", "tokenUrl",
                            "oidcConfigurationUrl", "oidcUserinfoEndpoint")
#: Scope-list fields.
AUTH_SCOPE_FIELDS = ("defaultScopes", "baseScopes", "oidcScopesSupported")

#: `client.mode` is required when `client` is present. `provided` needs a real
#: client id, which does not exist before registration; `cimd` is not a
#: mechanism this server has. `dcr` is what it actually does (RFC 7591).
CLIENT_MODES = ("provided", "dcr", "cimd")
ALLOWED_CLIENT_MODES = ("dcr",)
TOKEN_ENDPOINT_AUTH_METHODS = ("none", "client_secret_basic", "client_secret_post",
                               "private_key_jwt")
#: The two that do not imply a shared secret in the package. The platform has no
#: `clientSecret` field at all, so a declaration that asked for one form of
#: client authentication would be asking for something the format cannot carry.
ALLOWED_TOKEN_ENDPOINT_AUTH_METHODS = ("none", "private_key_jwt")

#: Keys that would carry a credential. Refused by name wherever they appear in
#: the auth block, including nested under `client`. `clientId` is deliberately
#: not here: it is an identifier rather than a secret, and it gets its own
#: refusal below because the fix for it is different — it is assigned at
#: registration, not withheld.
FORBIDDEN_AUTH_KEYS = frozenset({"clientSecret", "client_secret", "secret", "password"})

#: A scope token in the `namespace:action` shape this deployment mints.
SCOPE_TOKEN = re.compile(r"^[A-Za-z][A-Za-z0-9._-]*(?::[A-Za-z0-9._-]+)+$")

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

def _origin(value: str) -> str | None:
    """`scheme://host[:port]`, or None. Used only for comparing origins."""
    try:
        parts = urlsplit(value)
    except ValueError:
        return None
    if not parts.scheme or not parts.hostname:
        return None
    port = f":{parts.port}" if parts.port else ""
    return f"{parts.scheme}://{parts.hostname}{port}"


def _plain_https_url(value: object) -> bool:
    """An absolute HTTPS URL with no userinfo, query or fragment.

    The documentation's own rule for endpoint overrides. A URL carrying a
    credential component is the shape a token arrives in, and a query string is
    where an authorization code rides.
    """
    if not isinstance(value, str):
        return False
    try:
        parts = urlsplit(value)
    except ValueError:
        return False
    return (parts.scheme == "https" and bool(parts.hostname)
            and not (parts.username or parts.password or parts.query or parts.fragment))


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
        return problems

    origin = _origin(url)
    for key in ("protected_resource", "authorization_server"):
        entry = discovery.get(key)
        if (not isinstance(entry, dict) or entry.get("status") != 200
                or not isinstance(entry.get("receipt_ref"), str)
                or not entry["receipt_ref"].strip()):
            problems.append(f"the observation carries no 200 readback for {key}")
            continue
        if not _plain_https_url(entry.get("url")):
            problems.append(f"the {key} readback is not an absolute HTTPS URL "
                            f"without a credential component")
        elif _origin(entry["url"]) != origin:
            # A discovery document on another origin describes another
            # authorization server. Binding the MCP URL while advertising
            # somewhere else is exactly the reach the binding is meant to fix.
            problems.append(f"the {key} readback is on {_origin(entry['url'])}, and "
                            f"the MCP endpoint is on {origin}; discovery must be the "
                            f"ingress's own")

    authorization = discovery.get("authorization_server")
    if isinstance(authorization, dict):
        issuer = authorization.get("issuer")
        if not _plain_https_url(issuer):
            problems.append("the authorization server readback names no absolute "
                            "HTTPS issuer without a credential component")
        elif _origin(issuer) != origin:
            problems.append(f"the issuer is on {_origin(issuer)} and the MCP endpoint "
                            f"is on {origin}; RFC 8414 issuer and RFC 9728 resource "
                            f"must be the pair that was observed together")
        scopes = authorization.get("scopes_supported")
        if not isinstance(scopes, list) or not scopes or any(
                not isinstance(s, str) or not SCOPE_TOKEN.match(s) for s in scopes):
            problems.append("the authorization server readback carries no well-formed "
                            "scopes_supported list")
        else:
            missing = [s for s in FLEET_SCOPES if s not in scopes]
            if missing:
                # A client cannot request a scope the AS does not advertise, so a
                # missing one makes the Skill that needs it unusable — and the
                # package would still build, which is the failure this catches.
                problems.append(f"the authorization server does not advertise "
                                f"{missing}; the six Fleet scopes are what this "
                                f"package exists to use")
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


def _validate_auth(name: str, auth: object, url: str) -> dict:
    """The one auth declaration this package may carry, or a refusal by name.

    Everything here is a refusal of a *shape*, not of a value: the builder has
    no way to check that an OAuth server behaves, so what it checks is that the
    package asks for the mechanism the server actually offers and carries
    nothing that would put a credential in an uploaded file.
    """
    where = f"mcpServers.{name}.extensions.{OPENAI_NS}.auth"
    if not isinstance(auth, dict):
        raise Refused(SOURCE_REFUSED, f"{where} must be an object")

    stray = sorted(set(auth) - AUTH_FIELDS)
    if stray:
        raise Refused(SOURCE_REFUSED,
                      f"{where} declares {stray}, which the documented auth shape "
                      f"does not define. Known fields: {sorted(AUTH_FIELDS)}.")

    forbidden = sorted(set(auth) & FORBIDDEN_AUTH_KEYS)
    if forbidden:
        raise Refused(SOURCE_REFUSED,
                      f"{where} declares {forbidden}. The format has no field for a "
                      f"client secret; secrets arrive through the connection setup "
                      f"flow, not through a package.")
    _reject_nested_credentials(where, auth)
    _reject_client_id(where, auth)

    auth_type = auth.get("type")
    if auth_type not in DOCUMENTED_AUTH_TYPES:
        raise Refused(SOURCE_REFUSED,
                      f"{where}.type is {auth_type!r}, which is not one of the "
                      f"documented {list(DOCUMENTED_AUTH_TYPES)}.")
    if auth_type not in ALLOWED_AUTH_TYPES:
        # `none` would ship the connector unauthenticated; `api_key` and `mixed`
        # describe a server this is not. Refusing rather than tolerating, because
        # a package that declares the wrong mechanism still installs.
        raise Refused(SOURCE_REFUSED,
                      f"{where}.type is {auth_type!r}. The Fleet ingress offers "
                      f"{list(ALLOWED_AUTH_TYPES)}: an OAuth 2.1 authorization "
                      f"server with dynamic client registration. A different "
                      f"mechanism here describes a different service.")

    for field in SAME_ORIGIN_AUTH_FIELDS:
        if field not in auth:
            continue
        if not _plain_https_url(auth[field]):
            raise Refused(SOURCE_REFUSED, f"{where}.{field} must be an absolute HTTPS "
                                          f"URL with no query, fragment or userinfo")
        if _origin(auth[field]) != _origin(url):
            raise Refused(SOURCE_REFUSED,
                          f"{where}.{field} is on {_origin(auth[field])} and the MCP "
                          f"endpoint is on {_origin(url)}; the ingress's own "
                          f"endpoints are on its own origin.")
    for field in ABSOLUTE_AUTH_URL_FIELDS:
        if field in auth and not _plain_https_url(auth[field]):
            raise Refused(SOURCE_REFUSED, f"{where}.{field} must be an absolute HTTPS "
                                          f"URL with no query, fragment or userinfo")

    for field in AUTH_SCOPE_FIELDS:
        if field not in auth:
            continue
        value = auth[field]
        if not isinstance(value, list) or not value:
            raise Refused(SOURCE_REFUSED, f"{where}.{field} must be a non-empty list")
        for scope in value:
            if not isinstance(scope, str) or not SCOPE_TOKEN.match(scope):
                raise Refused(SOURCE_REFUSED,
                              f"{where}.{field} names {scope!r}, which is not a "
                              f"`namespace:action` scope token")
        outside = [s for s in value if s not in FLEET_SCOPES]
        if outside:
            # Consistent with the observation transitively: the observation is
            # required to advertise all of FLEET_SCOPES, so a declaration
            # confined to FLEET_SCOPES cannot ask for one the server does not
            # offer. That is why the two checks do not need to meet.
            raise Refused(SOURCE_REFUSED,
                          f"{where}.{field} asks for {outside}, which this surface "
                          f"does not mint ({list(FLEET_SCOPES)}). A scope no code "
                          f"path honours reads to a client as authority it has.")

    if "client" in auth:
        client = auth["client"]
        if not isinstance(client, dict):
            raise Refused(SOURCE_REFUSED, f"{where}.client must be an object")
        mode = client.get("mode")
        if mode not in CLIENT_MODES:
            raise Refused(SOURCE_REFUSED,
                          f"{where}.client.mode is {mode!r}, which is not one of the "
                          f"documented {list(CLIENT_MODES)}.")
        if mode not in ALLOWED_CLIENT_MODES:
            raise Refused(SOURCE_REFUSED,
                          f"{where}.client.mode is {mode!r}. `provided` needs a real "
                          f"client id and this plugin has not been registered, so one "
                          f"written here would be invented; this server registers "
                          f"clients dynamically.")
        method = client.get("tokenEndpointAuthMethod")
        if method is not None and method not in TOKEN_ENDPOINT_AUTH_METHODS:
            raise Refused(SOURCE_REFUSED,
                          f"{where}.client.tokenEndpointAuthMethod is {method!r}, not "
                          f"one of {list(TOKEN_ENDPOINT_AUTH_METHODS)}.")
        if method is not None and method not in ALLOWED_TOKEN_ENDPOINT_AUTH_METHODS:
            raise Refused(SOURCE_REFUSED,
                          f"{where}.client.tokenEndpointAuthMethod is {method!r}, which "
                          f"implies a shared secret. The format carries no client "
                          f"secret, so a package declaring this would be declaring a "
                          f"credential it cannot hold.")
    return auth


def _reject_client_id(where: str, auth: dict) -> None:
    """A client id has to be assigned before it can be written down.

    `client.mode: dcr` is what this server does, and the platform's own rule for
    a dynamic registration is that no client id appears. One typed into the
    template would be an invented identifier that a client would then present.
    """
    client = auth.get("client")
    if "clientId" in auth or (isinstance(client, dict) and "clientId" in client):
        raise Refused(
            SOURCE_REFUSED,
            f"{where} names a `clientId`. This plugin has not been registered, so "
            f"there is no client id to name and one written here would be invented; "
            f"registration assigns it. A client id is not a secret, but an invented "
            f"one is still a false declaration.",
        )


def _reject_nested_credentials(where: str, value: object, path: str = "") -> None:
    """Refuse a credential-shaped key anywhere inside the auth block."""
    if isinstance(value, dict):
        for key, item in value.items():
            if key in FORBIDDEN_AUTH_KEYS and path:
                raise Refused(SOURCE_REFUSED,
                              f"{where}{path}.{key} would carry a credential. The MCP "
                              f"schema has no field for one; secrets arrive through "
                              f"the connection setup flow, not through a package.")
            _reject_nested_credentials(where, item, f"{path}.{key}")
    elif isinstance(value, list):
        for index, item in enumerate(value):
            _reject_nested_credentials(where, item, f"{path}[{index}]")


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
            name: {**_validated_server(name, dict(server), url)}
            for name, server in servers.items()
        },
    }
    return json.dumps(binding, indent=2, ensure_ascii=False) + "\n"


def _validated_server(name: str, server: dict, url: str) -> dict:
    if set(server) - {"type", "url", "headers", "extensions"}:
        raise Refused(SOURCE_REFUSED,
                      f"mcpServers.{name} carries a field the 1.0.0 mcp schema does "
                      f"not define")
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
    server["url"] = url
    extensions = server.get("extensions")
    if extensions is None:
        return server
    if not isinstance(extensions, dict) or set(extensions) != {OPENAI_NS}:
        raise Refused(SOURCE_REFUSED,
                      f"mcpServers.{name}.extensions must carry exactly the "
                      f"{OPENAI_NS!r} block, which is the one the client is "
                      f"documented to read. An auth declaration under any other "
                      f"namespace asks nobody in particular.")
    block = extensions[OPENAI_NS]
    if not isinstance(block, dict) or set(block) - {"auth"}:
        raise Refused(SOURCE_REFUSED,
                      f"mcpServers.{name}.extensions.{OPENAI_NS} may carry only "
                      f"`auth`; it is the field this release reviewed.")
    if "auth" not in block:
        raise Refused(SOURCE_REFUSED,
                      f"mcpServers.{name}.extensions.{OPENAI_NS} declares no `auth`. "
                      f"A first install must say how the connector authenticates; "
                      f"an unauthenticated remote MCP is not this server.")
    server["extensions"] = {OPENAI_NS: {"auth": _validate_auth(name, block["auth"], url)}}
    return server


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
            # The message names the rule rather than one slot's reason: the
            # documented requirement is "Icons and logos must be square and at
            # least 48 by 48 pixels", and it applies to the listing logo as much
            # as to the composer avatar.
            raise Refused(BRAND_ASSET_MISMATCH,
                          f"{slot['shipped_path']} is {width}x{height}; icons and "
                          f"logos are required to be square")
        report[name] = {"sha256": actual, "width": width, "height": height,
                        "bytes": len(data)}
    return report


# -- the presentation block -----------------------------------------------

def check_presentation(manifest: dict, lock: dict) -> dict:
    """`extensions.com.openai.interface`, and the paths it must point at.

    Declaring the icon slots under this repository's own namespace — which the
    first revision did — is not a declaration any client is documented to read.
    So the supported block is required here, the two paths in it are required
    to be the shipped paths the lock pins, and the custom namespace is allowed
    to say anything it likes about *provenance* but is not where a presentation
    field may live.

    Passing this says the manifest asks the documented way. It says nothing
    about whether a client renders the image, which is a client behaviour and
    has not been observed.
    """
    extensions = manifest.get("extensions")
    if not isinstance(extensions, dict) or OPENAI_NS not in extensions:
        raise Refused(
            SOURCE_REFUSED,
            f"plugin.json declares no `extensions.{OPENAI_NS}`. That namespace is "
            f"where the client is documented to read `displayName`, `composerIcon` "
            f"and `logo`; a package that declares them only under its own namespace "
            f"has not asked the client to render the approved artwork.",
        )
    block = extensions[OPENAI_NS]
    if not isinstance(block, dict) or not isinstance(block.get(PRESENTATION_OBJECT), dict):
        raise Refused(
            SOURCE_REFUSED,
            f"`extensions.{OPENAI_NS}.{PRESENTATION_OBJECT}` is absent or is not an "
            f"object. The presentation fields are nested there, not at the top of "
            f"the namespace.",
        )
    presentation = block[PRESENTATION_OBJECT]

    missing = [f for f in REQUIRED_PRESENTATION_FIELDS if not presentation.get(f)]
    if missing:
        raise Refused(
            SOURCE_REFUSED,
            f"`extensions.{OPENAI_NS}.{PRESENTATION_OBJECT}` is missing {missing}. A "
            f"first release declares its display name and both approved icon slots, "
            f"or it does not ask for them at all.",
        )
    if presentation["displayName"] != EXPECTED_DISPLAY_NAME:
        raise Refused(
            SOURCE_REFUSED,
            f"displayName is {presentation['displayName']!r}; the approved name is "
            f"{EXPECTED_DISPLAY_NAME!r}. The name a user sees is part of what was "
            f"approved, not a label this build may choose.",
        )

    shipped = {}
    for slot in lock.get("slots", []):
        value = presentation.get(slot["slot"])
        # The shape of the path is checked before the path itself, so a
        # declaration that leaves the plugin is refused as an escape rather
        # than as a mismatch — the two have different fixes, and telling an
        # operator to check a digest for a path that can never resolve is
        # sending them to the wrong place.
        if (not isinstance(value, str) or not value.startswith("./")
                or ".." in value.split("/") or "\\" in value):
            raise Refused(
                SOURCE_REFUSED,
                f"`{PRESENTATION_OBJECT}.{slot['slot']}` is {value!r}; it must be a "
                f"root-relative `./` path that does not escape the plugin.",
            )
        if value != "./" + slot["shipped_path"]:
            raise Refused(
                SOURCE_REFUSED,
                f"`{PRESENTATION_OBJECT}.{slot['slot']}` is {value!r}; the approved "
                f"asset is shipped at {slot['shipped_path']!r} and the declaration "
                f"must name it as `./{slot['shipped_path']}`. A path that resolves "
                f"anywhere else is a different image.",
            )
        shipped[slot["slot"]] = value

    # A presentation field under the custom namespace would be a second answer
    # to a question the supported block already answers.
    custom = extensions.get("com.serveros.fleet-ingress")
    if isinstance(custom, dict):
        duplicated = sorted(set(custom) & set(REQUIRED_PRESENTATION_FIELDS))
        if duplicated:
            raise Refused(
                SOURCE_REFUSED,
                f"`extensions.com.serveros.fleet-ingress` repeats {duplicated}. The "
                f"supported block is the one a client reads; a second copy is how "
                f"the two come to disagree.",
            )
    return shipped


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
    presentation = check_presentation(manifest, lock)
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
        "presentation": presentation,
        "app_binding": app,
        "source_members": audited,
        "mcp_json": mcp_text,
    }


def build(root: Path, out_dir: Path, lock: dict, **kwargs) -> dict:
    """Write the release archive. Raises rather than writing a partial one.

    The output is refused at or below the source, before `preflight` reads
    anything. The audit sees the source as it is *now*, so an archive written
    into it afterwards is part of the tree the next run reads — and
    `audit_source` accepts a `.zip`, so the next `--source-bundle` would carry a
    release archive inside the archive that claims to be the source. The `dist`
    and `out` names are refused by the audit on the *next* run, which makes this
    look guarded when it is not.
    """
    root = Path(root).resolve()
    out_dir = Path(out_dir).resolve()
    if out_dir == root or root in out_dir.parents:
        raise Refused(
            SOURCE_REFUSED,
            f"--out {out_dir} is the source {root}, or is inside it. The source is "
            f"audited and then copied, so a release written into it changes the tree "
            f"the next run reads and ends up inside the next source bundle. Keep the "
            f"output outside the source.",
        )
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
        "presentation": plan["presentation"],
        "skills": plan["skills"],
        "app_binding": plan["app_binding"],
        # What the endpoint evidence actually is. `source: LIVE` inside the
        # receipt is a field an operator filled in, and this tool has no network
        # to check it against; naming that here keeps a reader from reading the
        # record's own confidence as a measurement.
        "endpoint_evidence": "OPERATOR_SUPPLIED_RECEIPT",
        "endpoint_probe_performed_by_this_tool": False,
        "writes": [],
        "uploads": [],
        "installs": [],
        "creates_plugin": False,
        "warning": ("A built archive is a source artefact. Nothing here uploads, "
                    "registers, installs or authorises: the plugin id is assigned "
                    "by the client's Plugin Creator, and the OAuth consent is the "
                    "owner's. The endpoint receipt is an operator's claim, not a "
                    "probe this tool performed — the release gate must query the "
                    "Next endpoint and its discovery documents itself, and verify "
                    "the audience, before anyone calls this live."),
    }
    return record


def source_bundle(root: Path, out_dir: Path, manifest: dict, reason: str) -> dict:
    """The only archive this tool may write while blocked.

    Named so that it cannot be mistaken for a release, and carrying a member
    that says the same thing to anyone who opens it without reading the name.

    The audit runs here too, and it may refuse. `SOURCE-ONLY` is a label, not a
    permission: this archive lands in a directory an operator uploads and
    mirrors, so a tree that carries a credential must not be archived at all —
    least of all by the fallback that a refusal triggered. Without this the
    member set would be the whole tree minus a `*.pyc` filter, and a build
    refused for a `.env` would hand back a bundle carrying that `.env`: the
    same bytes, under a name that reads as safe.
    """
    audit_source(root)
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
    kwargs = dict(next_mcp_url=args.next_mcp_url, observation=args.observation,
                  app_backed=args.app_backed, plugin_id=args.plugin_id,
                  registration=args.app_registration, v1_mcp_url=args.v1_mcp_url)

    try:
        # Inside the `try`, with every other gate. `Refused` documents itself as
        # carrying a status and a reason rather than a traceback, and the lock is
        # the brand-asset authority — a mistyped `--lock` path is the likeliest
        # operator error there is, and it arrived as a stack trace.
        lock = _read_json(Path(args.lock).resolve(), "brand-assets.lock.json")
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
            try:
                bundle = source_bundle(root, out_dir, manifest, refusal.status)
            except Refused as unarchivable:
                # The tree is unfit to archive at all — the same audit that
                # stopped the release stopped its fallback. Say so; writing
                # nothing is the point, and a silent skip would read as a bug.
                print(f"no source bundle was written: {unarchivable.status}: "
                      f"{unarchivable.detail}", file=sys.stderr)
            else:
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
