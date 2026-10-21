#!/usr/bin/env python3
"""Independent QA Snapshot F — P1 Claude Code dev plugin packaging (SOURCE/LOCAL).

Reads the exact #230 tree (QA_TREE) and builds the plugin OFFLINE into
QA_SCRATCH with a synthetic `.invalid` endpoint. No login, no provider, no
model, no live profile, no process argv/env, no receipt.

Checks: byte identity to the shared 5-Skill source; documented manifest keys;
typed HTTP binding with a read-only default; deterministic member hashes across
two builds; no credential/token/env/argv/path leakage in generated metadata;
no permission-bypass or installable artefact; refusals for no-scope,
unsupported scope, non-HTTPS endpoint, out-inside-source, second generation.

Exit 0 = every check held. Synthetic output only; not CLIENT_E2E evidence.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import re
import sys
from pathlib import Path

TREE = Path(os.environ["QA_TREE"]).resolve()
SCRATCH = Path(os.environ["QA_SCRATCH"]).resolve()
PLUGIN_DIR = TREE / "integrations" / "fleet-cc-dev-plugin"
SHARED = TREE / "integrations" / "fleet-v1-first-install" / "plugin" / "skills"
sys.path.insert(0, str(TREE))

failures: list[str] = []


def check(label: str, got, expected, *, security=False) -> None:
    ok = got == expected
    tag = "PASS" if ok else ("SECURITY_FAIL" if security else "FAIL")
    print(f"{tag} {label}: got={got!r} expected={expected!r}")
    if not ok:
        failures.append(label)


def load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


cc = load("fleet_cc_dev_plugin", PLUGIN_DIR / "build_cc_plugin.py")
URL = "https://fleet-qa.invalid/mcp"

SCRATCH.mkdir(parents=True, exist_ok=True)
out_a = SCRATCH / "plugin-a"
out_b = SCRATCH / "plugin-b"
for out in (out_a, out_b):
    if out.exists():
        import shutil
        shutil.rmtree(out)

# ---------------------------------------------------------------------------
# F1 — the shared source is the five Operator Skills.
# ---------------------------------------------------------------------------
source_skills = sorted(p.name for p in SHARED.iterdir() if p.is_dir())
check("F1 exactly five shared Skills", len(source_skills), 5)
check("F1 the five expected names", source_skills,
      ["fleet-create-with-grants", "fleet-device-and-session-discovery",
       "fleet-evidence-review", "fleet-reuse-idle-session",
       "fleet-safety-boundaries"])

# ---------------------------------------------------------------------------
# F2 — one offline build + byte identity + manifest/binding.
# ---------------------------------------------------------------------------
record_a = cc.generate(skills_source=SHARED, out=out_a, next_mcp_url=URL,
                       scopes="fleet:read")
record_b = cc.generate(skills_source=SHARED, out=out_b, next_mcp_url=URL,
                       scopes="fleet:read")

check("F2 generated skills are the source skills", record_a["skills"], source_skills)
for name in source_skills:
    check(f"F2 {name} byte-identical to the shared source",
          (out_a / "skills" / name / "SKILL.md").read_bytes(),
          (SHARED / name / "SKILL.md").read_bytes())

manifest = json.loads((out_a / ".claude-plugin" / "plugin.json").read_text())
mcp = json.loads((out_a / ".mcp.json").read_text())
documented = {
    "$schema", "name", "displayName", "version", "description", "author",
    "homepage", "repository", "license", "keywords", "metadata", "icon",
    "documentationUrl", "supportUrl", "privacyPolicyUrl", "termsOfServiceUrl",
    "defaultEnabled", "dependencies", "settings", "userConfig", "types",
    "channels", "skills", "commands", "agents", "hooks", "mcpServers",
    "lspServers", "outputStyles", "workflows", "experimental",
}
check("F2 only documented manifest keys", sorted(set(manifest) - documented), [])
check("F2 manifest name", manifest["name"], "serveros-fleet-dev")
check("F2 no icon key", "icon" in manifest, False)
server = mcp["mcpServers"]["serveros-fleet"]
check("F2 binding is typed http", server["type"], "http")
check("F2 synthetic endpoint is pinned", server["url"], URL)
check("F2 default scope is read-only", server["oauth"]["scopes"], "fleet:read")
check("F2 no other MCP server", list(mcp["mcpServers"]), ["serveros-fleet"])
check("F2 nothing installable or branded",
      sorted(p.suffix.lower() for p in out_a.rglob("*")
             if p.is_file() and p.suffix.lower() in
             (".png", ".jpg", ".jpeg", ".webp", ".svg", ".ico", ".zip", ".mcpb")),
      [])

# ---------------------------------------------------------------------------
# F3 — determinism: same source + same inputs => same bytes and hashes.
# ---------------------------------------------------------------------------
check("F3 member_sha256 maps are identical",
      record_a["member_sha256"], record_b["member_sha256"])
for member in record_a["members"]:
    check(f"F3 {member} bytes identical across builds",
          (out_a / member).read_bytes(), (out_b / member).read_bytes())
check("F3 skills_source is a relative repo path",
      not Path(record_a["skills_source"]).is_absolute(), True)
check("F3 the record carries no out/argv/env/cwd/user field",
      [k for k in ("out", "argv", "env", "environ", "cwd", "user")
       if k in record_a], [])

# ---------------------------------------------------------------------------
# F4 — generated metadata carries no secret, identity, path or bypass.
# ---------------------------------------------------------------------------
SECRET = re.compile(
    r"(?i)(client_secret|access_token|refresh_token|bearer\s+[A-Za-z0-9._-]{8,}"
    r"|api[_-]?key\s*[:=]|password\s*[:=]|-----BEGIN [A-Z ]*PRIVATE KEY-----"
    r"|gh[opsu]_[A-Za-z0-9]{20,}|xox[baprs]-[A-Za-z0-9-]{10,})")
PATHY = re.compile(
    r"(?<![\w./-])(/(?:home|data/home|opt/applications|Users|tmp|scratch)/"
    r"[A-Za-z0-9_.-]+|[A-Za-z]:\\\\Users\\\\)")
ENVVY = re.compile(r"(\bos\.environ\b|\bsys\.argv\b|\$\{?HOME\}?|\$\{?USER\}?)")
BYPASS = re.compile(r"(?i)(dangerously[-_]skip[-_]permissions|bypassPermissions"
                    r'|"permissions"\s*:|\ballowedTools\b)')

generated = sorted(p for p in out_a.rglob("*") if p.is_file())
for path in generated:
    text = path.read_text(encoding="utf-8")
    label = path.relative_to(out_a).as_posix()
    check(f"F4 no secret pattern in {label}", SECRET.search(text) is not None, False,
          security=True)
    check(f"F4 no absolute path in {label}", PATHY.search(text) is not None, False,
          security=True)
    check(f"F4 no env/argv reference in {label}", ENVVY.search(text) is not None, False,
          security=True)

# Manifest + binding specifically: no permission bypass at all.
for label, text in (("plugin.json", (out_a / ".claude-plugin/plugin.json").read_text()),
                    (".mcp.json", (out_a / ".mcp.json").read_text())):
    check(f"F4 no permission-bypass key in {label}",
          BYPASS.search(text) is not None, False, security=True)

# The scratch root must not appear anywhere in the generated metadata.
for path in generated:
    check(f"F4 no build-machine path in {path.name}",
          str(SCRATCH) in path.read_text(encoding="utf-8"), False, security=True)

# ---------------------------------------------------------------------------
# F5 — refusal paths: no scope, unsupported scope, bad endpoint, out-in-source,
#      second generation. None of these may write.
# ---------------------------------------------------------------------------
def refused(**overrides):
    kwargs = dict(skills_source=SHARED, out=SCRATCH / "never", next_mcp_url=URL,
                  scopes="fleet:read")
    kwargs.update(overrides)
    try:
        cc.generate(**kwargs)
        return False
    except cc.Refused:
        return True

check("F5 no scope is refused", refused(scopes=""), True)
check("F5 unsupported scope is refused", refused(scopes="fleet:root"), True)
check("F5 a supported write scope is accepted as a request",
      cc.check_scopes(["fleet:send"]), "fleet:send")
check("F5 non-HTTPS endpoint is refused", refused(next_mcp_url="http://x.invalid/mcp"), True)
check("F5 endpoint without /mcp is refused",
      refused(next_mcp_url="https://x.invalid/not-mcp"), True)
check("F5 endpoint with userinfo is refused",
      refused(next_mcp_url="https://u:p@x.invalid/mcp"), True)
check("F5 out inside the skills source is refused",
      refused(out=SHARED / "generated"), True)
check("F5 out-inside-source wrote nothing", (SHARED / "generated").exists(), False)
check("F5 second generation into an existing out is refused",
      refused(out=out_a), True)

print()
print("scratch:", SCRATCH)
print("members:", json.dumps(record_a["members"], sort_keys=True))
print("member_sha256:", json.dumps(record_a["member_sha256"], sort_keys=True))
if failures:
    print(f"PROBE FAILED: {len(failures)} check(s): {failures}")
    sys.exit(1)
print("PROBE OK: every packaging check held (offline/SOURCE-LOCAL only)")
