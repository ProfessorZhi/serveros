#!/usr/bin/env python3
"""Build, check, plan and install the per-device ServerOS kits.

One declaration (`devices.yaml`), one kit per device, the same four layers on
every device: core plugin → ability family → monitoring → (hub) commander.

    python3 distribution/device_kit.py build              # write distribution/devices/*
    python3 distribution/device_kit.py check              # CI: committed == regenerated
    python3 distribution/device_kit.py plan    --device tokyo --ability-rev SHA
    python3 distribution/device_kit.py install --device tokyo --ability-rev SHA [--apply] [--enable]
    python3 distribution/device_kit.py update  --device tokyo [--ability-rev main] [--apply] [--enable]
    python3 distribution/device_kit.py versions --device tokyo

`update` is the in-place upgrade of all four layers on a Linux device: it pulls
this checkout to origin/main, re-runs `install` with the NEW code, prints the
restart command for each unit that is already running (it restarts nothing
itself: product code runs no stopping verb, see tests/test_mutation_guard.py),
and records what is installed in
`~/.local/state/serveros/device-versions.json`. A Windows device runs
`distribution/windows/update.ps1` instead.

`plan` and `install` without `--apply` only print. `install --apply` runs the
steps in order on THIS machine and stops at the first failure; it writes unit
files but does not enable or start anything unless `--enable` is given. A
Windows kit is always plan-only here: its steps are PowerShell for the device.
Nothing in this file reads, prints or copies a credential.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path

import yaml

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
DECLARATION = HERE / "devices.yaml"
KITS = HERE / "devices"
SCHEMA = "serveros/fleet-device-distribution.v1"
KIT_SCHEMA = "serveros/fleet-device-kit.v1"
LAYERS = ("core", "family", "monitor", "commander")

UNIT_TEMPLATES = {
    "serverosd": "runtime/serverosd.service",
    "core_lite_service": "native/node-core/core-lite/deploy/serveros-core-lite-publish.service",
    "core_lite_timer": "native/node-core/core-lite/deploy/serveros-core-lite-publish.timer",
    "core_lite_publish": "native/node-core/core-lite/deploy/publish-status.sh",
    "node_connector": "fleet_mcp/node/systemd/user/serveros-fleet-node-connector.service.in",
    "hub_watch": "integrations/fleet-hub-watch/serveros-fleet-watch.service.template",
    "node_connector_windows": "fleet_mcp/node/install.ps1",
    "core_lite_publish_windows": "native/node-core/core-lite/windows/deploy/publish-status.ps1",
    "core_lite_task_windows": "native/node-core/core-lite/windows/deploy/register-publish-task.ps1",
}


class KitError(RuntimeError):
    pass


# -- the declaration -----------------------------------------------------------

def declaration() -> dict:
    doc = yaml.safe_load(DECLARATION.read_text(encoding="utf-8"))
    if doc.get("schema") != SCHEMA:
        raise KitError(f"devices.yaml: schema must be {SCHEMA}")
    hubs = [name for name, d in doc["devices"].items() if d["monitor"].get("hub_watch")]
    if hubs != [doc["hub"]["device"]]:
        raise KitError(f"exactly the hub ({doc['hub']['device']}) runs hub_watch; found {hubs}")
    for name, dev in doc["devices"].items():
        if dev["commander"] and name != doc["hub"]["device"]:
            raise KitError(f"{name}: the commander plugin is installed on the hub only")
        if dev["os"] not in ("linux", "windows"):
            raise KitError(f"{name}: os must be linux or windows")
        if dev["os"] == "windows" and (dev["core"]["composition"] or dev["monitor"]["serverosd"]):
            raise KitError(f"{name}: the Linux plugin and serverosd do not run on Windows")
        if dev["monitor"]["hub_watch"] and dev["monitor"]["node_connector"]:
            raise KitError(f"{name}: the hub reads its own readings; no node connector")
    return doc


def composition(path: str) -> dict:
    """The plugin identity a composition builds (profile merged onto its parent)."""
    profile_path = ROOT / path
    profile = yaml.safe_load(profile_path.read_text(encoding="utf-8"))
    parent = {}
    if profile.get("extends"):
        parent = yaml.safe_load((profile_path.parent / profile["extends"]).read_text(encoding="utf-8"))
    merged = {**parent, **profile}
    skills = [s["name"] for s in parent.get("skills", [])] + [
        s["name"] for s in profile.get("skills", []) if s["name"] not in
        {p["name"] for p in parent.get("skills", [])}]
    return {
        "source": path,
        "composition": merged["composition"],
        "name": merged["name"],
        "display_name": merged["display_name"],
        "version": f"{merged['version']}+{merged['cachebuster']}",
        "skills": skills,
        "claude_code": f"{merged['name']}@{merged['name']}",
        "codex": f"{merged['name']}@{merged['name']}-local",
    }


def _digest(rel: str) -> str:
    return "sha256:" + hashlib.sha256((ROOT / rel).read_bytes()).hexdigest()


def _profile_node(rel: str) -> dict:
    return json.loads((ROOT / rel).read_text(encoding="utf-8"))


def kit(doc: dict, name: str) -> dict:
    dev = doc["devices"][name]
    mon = dev["monitor"]
    hub = doc["hub"]
    layers: dict = {}
    if dev["core"]["composition"]:
        layers["core"] = {**composition(dev["core"]["composition"]), "agents": dev["core"]["agents"],
                          "node_runtime": "serverosd + serverosctl (same payload, promoted as node-runtime)"}
    else:
        layers["core"] = {"composition": None, "agents": dev["core"]["agents"],
                          "note": "core skills arrive as standalone skills through the family profile"}
    layers["family"] = {"repo": doc["ability_management"]["repo"], "profile": dev["family"]["profile"],
                        "agents": dev["core"]["agents"], "revision": "pinned at install (--ability-rev)"}
    monitor: dict = {}
    if mon["serverosd"]:
        monitor["serverosd"] = {"what": "basic: resources, anomalies, runtime status (python daemon)",
                                "unit": "serverosd.service", "template": _pin(UNIT_TEMPLATES["serverosd"])}
    if not mon["core_lite"]:
        monitor["core_lite"] = {
            "what": "GAP: no resource collector for this OS yet — the hub shows 未接入资源读数",
            "evidence": _pin("native/node-core/core-lite/windows/README.md")}
    else:
        prof = _profile_node(mon["core_lite"])
        if prof["node"] != dev["device_ref"]:
            raise KitError(f"{name}: core-lite profile is for {prof['node']}, not {dev['device_ref']}")
        if prof.get("platform") != dev["os"]:
            raise KitError(f"{name}: core-lite profile is for {prof.get('platform')}, not {dev['os']}")
    if mon["core_lite"] and dev["os"] == "windows":
        monitor["core_lite"] = {
            "what": "native: C++ Windows collector (Win32) → status.json every minute, observe only",
            "profile": _pin(mon["core_lite"]),
            "build": "native/node-core/core-lite/windows (MSVC, static)",
            "task": "\\ServerOS\\ServerOS Core-Lite publish (scheduled task, this user)",
            "templates": [_pin(UNIT_TEMPLATES[k]) for k in ("core_lite_publish_windows",
                                                             "core_lite_task_windows")],
            "status_file": "%LOCALAPPDATA%\\ServerOS\\state\\core-lite\\status.json"}
    elif mon["core_lite"]:
        monitor["core_lite"] = {
            "what": "native: C++ collector → status.json (0600) every 30 s, observe only",
            "profile": _pin(mon["core_lite"]),
            "units": ["serveros-core-lite-publish.service", "serveros-core-lite-publish.timer"],
            "templates": [_pin(UNIT_TEMPLATES[k]) for k in
                          ("core_lite_service", "core_lite_timer", "core_lite_publish")],
            "status_file": "~/.local/state/serveros/core-lite/status.json"}
    if mon["node_connector"]:
        key = "node_connector" if dev["os"] == "linux" else "node_connector_windows"
        monitor["node_connector"] = {"what": f"link: reports this device to the hub ({hub['device']})",
                                     "template": _pin(UNIT_TEMPLATES[key])}
    if mon["hub_watch"]:
        monitor["hub_watch"] = {
            "what": "hub: agent monitor — finds Fleet runs, records outcomes once, "
                    "Feishu completion / alert / daily cards",
            "unit": "serveros-fleet-watch.service", "template": _pin(UNIT_TEMPLATES["hub_watch"]),
            "binding": hub["binding"], "card_spec": _pin("integrations/fleet-hub-watch/card-spec.yaml"),
            "binding_needs": "core_lite.snapshot_path: ~/.local/state/serveros/core-lite/status.json",
            # Not a default any more: the install refuses without it. Named here
            # so a reader of the kit knows the decision the hub was installed
            # with is *somebody's*, not a fallback.
            "notify": "required at install: lark:user:<open_id> | lark:chat:<chat_id> | "
                      "journal (writes the cards, sends nothing) | off"}
    layers["monitor"] = monitor
    if dev["commander"]:
        channels = yaml.safe_load((HERE / "channels.yaml").read_text(encoding="utf-8"))
        layers["commander"] = {"client": "claude-code",
                               "install_id": channels["clients"]["claude-code"]["install_id"],
                               "tree": channels["clients"]["claude-code"]["tree"],
                               "mcp_url": hub["mcp_url"]}
    out = {"schema": KIT_SCHEMA, "device": name, "device_ref": dev["device_ref"],
           "principal": dev["principal"], "os": dev["os"], "role": dev["role"],
           "hub": hub["device"], "layers": layers}
    if dev.get("wiring"):
        out["wiring"] = dict(dev["wiring"])
    if name == hub["device"] and hub.get("binding_settings"):
        out["hub_binding_settings"] = [dict(item) for item in hub["binding_settings"]]
    return out


def _pin(rel: str) -> dict:
    if not (ROOT / rel).is_file():
        raise KitError(f"missing source {rel}")
    return {"path": rel, "digest": _digest(rel)}


# -- steps -----------------------------------------------------------------------

@dataclass
class Step:
    layer: str
    title: str
    argv: list[str] | None = None
    write: tuple[Path, str] | None = None
    ok_fail: bool = False                  # an expected failure (e.g. "already added")
    enable: bool = False                   # only with --enable
    notes: list[str] = field(default_factory=list)

    def show(self) -> str:
        if self.write:
            return f"write {self.write[0]}"
        return " ".join(shlex.quote(a) for a in self.argv or [])


@dataclass
class Context:
    ability_repo: Path
    ability_rev: str
    data_root: Path
    home: Path
    python: str
    notify: str | None
    lib_dir: Path = None
    unit_dir: Path = None
    state_dir: Path = None

    def __post_init__(self):
        self.lib_dir = self.home / ".local/lib/serveros-core-lite"
        self.unit_dir = self.home / ".config/systemd/user"
        self.state_dir = self.home / ".local/state/serveros"


def _render(rel: str, values: dict[str, str]) -> str:
    text = (ROOT / rel).read_text(encoding="utf-8")
    for key, value in values.items():
        text = text.replace(f"@{key}@", value)
    left = re.findall(r"@[A-Z][A-Z_]*@", text)
    if left:
        raise KitError(f"{rel}: unrendered placeholder(s) {sorted(set(left))}")
    return text


def linux_steps(k: dict, ctx: Context) -> list[Step]:
    steps: list[Step] = []
    rev = ctx.ability_rev
    py = ctx.python
    core = k["layers"]["core"]
    runtime_root = ctx.data_root / "runtime-assets" / "hwz-server-os"
    am = str(ctx.ability_repo / "skills/ability-management/payload/control_plane.py")

    if core.get("composition"):
        label = f"am-{rev[:12]}"
        staging = runtime_root / f".staging-{label}"
        current = runtime_root / core["composition"] / "current"
        steps += [
            Step("core", f"build {core['name']} {core['version']} at ability-management {rev[:12]}",
                 [py, str(ROOT / "scripts/build.py"), "--write", "--bundle", str(ROOT / core["source"]),
                  "--ability-repo", str(ctx.ability_repo), "--ability-rev", rev,
                  "--output", str(staging),
                  "--marketplace-path", str(runtime_root / ".agents/plugins/marketplace.json")]),
            Step("core", "promote it as the node runtime (installs serverosd.service + serverosctl)",
                 [py, str(ROOT / "scripts/release.py"), "promote", "--runtime-root", str(runtime_root),
                  "--composition", core["composition"], "--kind", "node-runtime",
                  "--artifact", str(staging), "--revision", label]),
        ]
        if "claude-code" in core["agents"]:
            steps += [
                Step("core", "Claude Code: add the local marketplace (fails harmlessly if present)",
                     ["claude", "plugin", "marketplace", "add", str(current)], ok_fail=True),
                Step("core", "Claude Code: refresh it",
                     ["claude", "plugin", "marketplace", "update", core["name"]]),
                Step("core", "Claude Code: install (fails harmlessly if installed)",
                     ["claude", "plugin", "install", core["claude_code"]], ok_fail=True),
                Step("core", "Claude Code: update to this version",
                     ["claude", "plugin", "update", core["claude_code"]]),
            ]
        if "codex" in core["agents"]:
            steps += [
                Step("core", "Codex: add the local marketplace (fails harmlessly if present)",
                     ["codex", "plugin", "marketplace", "add", str(runtime_root)], ok_fail=True),
                Step("core", "Codex: add the plugin (replaces the installed version)",
                     ["codex", "plugin", "add", core["codex"]]),
            ]

    fam = k["layers"]["family"]
    steps += [
        Step("family", f"roll out ability family `{fam['profile']}` at {rev[:12]} (skills + norms)",
             [py, am, "rollout", "--profile", fam["profile"], "--rev", rev, "--agent", "all", "--apply"]),
        Step("family", "read it back (installed == desired)",
             [py, am, "readback", "--profile", fam["profile"], "--agent", "all"]),
    ]

    mon = k["layers"]["monitor"]
    units: list[str] = []
    if "serverosd" in mon:
        units.append("serverosd.service")
    if "core_lite" in mon:
        build = ctx.data_root / "build" / "node-core"
        out = ctx.state_dir / "core-lite" / "status.json"
        profile = str(ROOT / mon["core_lite"]["profile"]["path"])
        unit = _render(UNIT_TEMPLATES["core_lite_service"],
                       {"BIN": str(ctx.lib_dir), "PROFILE": profile, "OUT": str(out)})
        steps += [
            Step("monitor", "core-lite: configure the C++ build",
                 ["cmake", "-S", str(ROOT / "native/node-core"), "-B", str(build),
                  "-DCMAKE_BUILD_TYPE=Release"]),
            Step("monitor", "core-lite: build", ["cmake", "--build", str(build), "-j", "4"]),
            Step("monitor", "core-lite: install the collector, status builder and publisher",
                 ["install", "-D", "-m", "755", "-t", str(ctx.lib_dir),
                  str(build / "serveros-node-resource-snapshot"),
                  str(build / "core-lite" / "core-lite-status"),
                  str(ROOT / UNIT_TEMPLATES["core_lite_publish"])]),
            Step("monitor", "core-lite: publish unit",
                 write=(ctx.unit_dir / "serveros-core-lite-publish.service", unit)),
            Step("monitor", "core-lite: timer",
                 write=(ctx.unit_dir / "serveros-core-lite-publish.timer",
                        (ROOT / UNIT_TEMPLATES["core_lite_timer"]).read_text(encoding="utf-8"))),
        ]
        units.append("serveros-core-lite-publish.timer")
    if "node_connector" in mon:
        steps.append(Step(
            "monitor", "node connector: preflight (says what is missing; writes nothing)",
            ["bash", str(ROOT / "fleet_mcp/node/install.sh"), "--device-ref", k["device_ref"],
             "--systemd", "--preflight"],
            notes=["the real install needs this device's CloudCLI credential anchor and tunnel key: "
                   "fleet_mcp/node/install.sh --device-ref " + k["device_ref"] +
                   " --cloudcli-credential <anchor> --identity <key> --systemd"]))
        units.append("serveros-fleet-node-connector.service")
    if "hub_watch" in mon:
        if not ctx.notify:
            # The unit is the only thing that decides whether a run's completion
            # ever reaches a person. Rendered without a destination it would
            # install a watcher that records everything and reports nothing,
            # and the service would look healthy while doing it — so this is
            # refused here, before a unit file exists, rather than defaulted.
            raise KitError(
                f"{name}: the hub watcher needs a destination — pass --notify "
                f"lark:user:<open_id>, lark:chat:<chat_id>, journal (writes the "
                f"cards, sends nothing) or off")
        binding = str(Path(mon["hub_watch"]["binding"].replace("~", str(ctx.home), 1)))
        unit = _render(UNIT_TEMPLATES["hub_watch"], {
            "HUB_REPO": str(ROOT), "HUB_PYTHON": py, "HUB_BINDING": binding,
            "HUB_NOTIFY": ctx.notify,
            "HUB_DOC_URL": "https://github.com/ProfessorZhi/serveros/blob/main/distribution/README.md"})
        steps += [
            Step("monitor", "agent monitor: check the card spec renders",
                 [py, "-m", "fleet_hub_watch", "--check-spec",
                  str(ROOT / "integrations/fleet-hub-watch/card-spec.yaml")]),
            Step("monitor", f"agent monitor: unit (notify {ctx.notify})",
                 write=(ctx.unit_dir / "serveros-fleet-watch.service", unit),
                 notes=[f"the hub binding {binding} must set core_lite.snapshot_path to "
                        f"{ctx.state_dir / 'core-lite' / 'status.json'}"]),
        ]
        units.append("serveros-fleet-watch.service")
    steps.append(Step("monitor", "systemd: reload unit files", ["systemctl", "--user", "daemon-reload"]))
    for name in units:
        steps.append(Step("monitor", f"enable {name}",
                          ["systemctl", "--user", "enable", "--now", name], enable=True))

    if "commander" in k["layers"]:
        steps.append(Step("commander", "commander plugin (Claude Code, V1)",
                          [py, str(HERE / "build.py"), "install", "--client", "claude-code"]))
    return steps


def windows_plan(k: dict, rev: str) -> list[str]:
    fam = k["layers"]["family"]
    core = k["layers"]["monitor"].get("core_lite", {})
    native = [
        "# native monitor: build the Windows collector (needs Visual Studio Build Tools + CMake)",
        "cmake -S native\\node-core\\core-lite\\windows -B $env:LOCALAPPDATA\\ServerOS\\build\\core-lite -A x64",
        "cmake --build $env:LOCALAPPDATA\\ServerOS\\build\\core-lite --config Release",
        "$bin = \"$env:LOCALAPPDATA\\ServerOS\\build\\core-lite\\Release\"",
        f"$prof = (Resolve-Path {core['profile']['path'].replace('/', chr(92))}).Path",
        "# the device declaration core-lite-status reads (one line, this account's own file)",
        "New-Item -ItemType Directory -Force \"$env:USERPROFILE\\.config\\serveros\" | Out-Null",
        f"Set-Content -NoNewline \"$env:USERPROFILE\\.config\\serveros\\node-identity\" '{k['device_ref']}'",
        "powershell -ExecutionPolicy Bypass -File native\\node-core\\core-lite\\windows\\deploy\\publish-status.ps1 -Bin $bin -ProfilePath $prof",
        "powershell -ExecutionPolicy Bypass -File native\\node-core\\core-lite\\windows\\deploy\\register-publish-task.ps1 -Bin $bin -ProfilePath $prof",
        "# then set core_snapshot_path in the connector binding to %LOCALAPPDATA%\\ServerOS\\state\\core-lite\\status.json",
    ] if "profile" in core else [
        "# resource readings: none yet — no collector configured for this device",
    ]
    return [
        "# run in PowerShell on the device, from a checkout of serveros and ability-management",
        f"python ..\\ability-management\\skills\\ability-management\\payload\\control_plane.py rollout "
        f"--profile {fam['profile']} --rev {rev} --agent all --apply",
        f"python ..\\ability-management\\skills\\ability-management\\payload\\control_plane.py readback "
        f"--profile {fam['profile']} --agent all",
        *native,
        f"powershell -ExecutionPolicy Bypass -File fleet_mcp\\node\\install.ps1 -DeviceRef {k['device_ref']} "
        "-CloudCliCredential <anchor>",
    ]


# -- build / check -------------------------------------------------------------------

def readme(k: dict) -> str:
    L = k["layers"]
    core = L["core"]
    lines = [
        f"# {k['device']} — ServerOS device kit",
        "",
        "<!-- GENERATED by distribution/device_kit.py from distribution/devices.yaml. Do not edit. -->",
        "",
        f"- device: `{k['device_ref']}` ({k['os']}, account `{k['principal']}`)",
        f"- role: {k['role']}",
        f"- hub: `{k['hub']}`",
        "",
        "| layer | what this device gets |",
        "|---|---|",
    ]
    if core.get("composition"):
        lines.append(f"| 1 core plugin | `{core['name']}` {core['version']} for {', '.join(core['agents'])}"
                     f" — skills {', '.join(core['skills'])}; same payload runs serverosd |")
    else:
        lines.append(f"| 1 core plugin | {core['note']} |")
    lines.append(f"| 2 ability family | `{L['family']['profile']}` from {L['family']['repo']}, "
                 "one pinned revision, read back after rollout |")
    for key, item in L["monitor"].items():
        lines.append(f"| 3 monitor · {key} | {item['what']} |")
    if "commander" in L:
        lines.append(f"| 4 commander | `{L['commander']['install_id']}` → {L['commander']['mcp_url']} |")
    if k.get("wiring") or k.get("hub_binding_settings"):
        lines += ["", "## Configuration outside this repository", "",
                  "These live in local binding files, not in git. Re-apply them after a reinstall.", ""]
        for item in k.get("hub_binding_settings", []):
            lines.append(f"- hub binding (`{k['hub']}`): `{item['setting']}` — {item['why']}")
        wiring = k.get("wiring", {})
        if "hub_binding" in wiring:
            lines.append(f"- hub binding (`{k['hub']}`): `{wiring['hub_binding']}`")
        if "connector_binding" in wiring:
            lines.append(f"- this device's connector binding: `{wiring['connector_binding']}`")
    lines += ["", "## Install", ""]
    if k["os"] == "linux":
        lines += [
            "```bash",
            "cd ~/src/serveros && git pull",
            "REV=$(git -C ../ability-management rev-parse origin/main)",
            f"python3 distribution/device_kit.py plan    --device {k['device']} --ability-rev $REV",
            f"python3 distribution/device_kit.py install --device {k['device']} --ability-rev $REV --apply",
            f"python3 distribution/device_kit.py install --device {k['device']} --ability-rev $REV --apply --enable"
            "   # also enables the units",
            "```",
            "",
            "## Update in place",
            "",
            "```bash",
            f"python3 distribution/device_kit.py update   --device {k['device']} --data-root $DATA_ROOT            # dry run",
            f"python3 distribution/device_kit.py update   --device {k['device']} --data-root $DATA_ROOT --apply",
            f"python3 distribution/device_kit.py versions --device {k['device']}",
            "```",
        ]
    else:
        lines += ["```powershell", *windows_plan(k, "<ability-management rev>"), "```",
                  "", "## Update in place", "",
                  "```powershell",
                  f"powershell -ExecutionPolicy Bypass -File distribution\\windows\\update.ps1 -Device {k['device']}"
                  "                       # dry run",
                  f"powershell -ExecutionPolicy Bypass -File distribution\\windows\\update.ps1 -Device {k['device']}"
                  " -FromRelease -Apply  # collector from the fleet-latest Release (needs gh)",
                  "Get-Content \"$env:LOCALAPPDATA\\ServerOS\\state\\device-versions.json\"",
                  "```"]
    lines += ["", "Rollback: rerun `install` with the previous `--ability-rev`; "
              "`scripts/release.py rollback` for the node runtime; "
              "`systemctl --user disable --now <unit>` for a monitor unit.", ""]
    return "\n".join(lines)


def _files(doc: dict, name: str) -> dict[str, bytes]:
    k = kit(doc, name)
    return {"DEVICE.json": (json.dumps(k, indent=2, ensure_ascii=False) + "\n").encode(),
            "README.md": readme(k).encode()}


def build() -> int:
    doc = declaration()
    if KITS.exists():
        shutil.rmtree(KITS)
    for name in doc["devices"]:
        for rel, body in _files(doc, name).items():
            path = KITS / name / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(body)
        print(f"wrote distribution/devices/{name}")
    return 0


def stale() -> list[str]:
    doc = declaration()
    want = {f"{n}/{rel}": body for n in doc["devices"] for rel, body in _files(doc, n).items()}
    have = {p.relative_to(KITS).as_posix(): p.read_bytes()
            for p in KITS.rglob("*") if p.is_file()} if KITS.is_dir() else {}
    return sorted(rel for rel in set(want) | set(have) if want.get(rel) != have.get(rel))


def check() -> int:
    found = stale()
    if found:
        print("device kits are stale; run `python3 distribution/device_kit.py build`:", file=sys.stderr)
        for rel in found:
            print(f"  distribution/devices/{rel}", file=sys.stderr)
        return 1
    print("every device kit matches devices.yaml")
    return 0


# -- plan / install --------------------------------------------------------------------

def _context(args) -> Context:
    if not args.ability_rev or len(args.ability_rev) < 7:
        raise KitError("--ability-rev must name an ability-management commit")
    data_root = args.data_root or os.environ.get("SERVEROS_DATA_ROOT")
    if not data_root:
        raise KitError("--data-root (or SERVEROS_DATA_ROOT) is required: the node's DATA_ROOT")
    return Context(ability_repo=Path(args.ability_repo).expanduser().resolve(),
                   ability_rev=args.ability_rev, data_root=Path(data_root).expanduser(),
                   home=Path(args.home).expanduser(), python=args.python, notify=args.notify)


def plan(args) -> tuple[dict, list[Step] | list[str]]:
    doc = declaration()
    if args.device not in doc["devices"]:
        raise KitError(f"unknown device {args.device}; known: {sorted(doc['devices'])}")
    k = kit(doc, args.device)
    if k["os"] == "windows":
        return k, windows_plan(k, args.ability_rev or "<ability-management rev>")
    return k, linux_steps(k, _context(args))


def print_plan(k: dict, steps, enable: bool) -> None:
    print(f"# {k['device']} ({k['device_ref']}) — {k['os']}")
    if steps and isinstance(steps[0], str):
        print("\n".join(steps))
        return
    for i, s in enumerate(steps, 1):
        flag = "" if (not s.enable or enable) else "   [needs --enable]"
        print(f"{i:2}. [{s.layer}] {s.title}{flag}\n      {s.show()}")
        for note in s.notes:
            print(f"      note: {note}")


def install(args) -> int:
    k, steps = plan(args)
    print_plan(k, steps, args.enable)
    if k["os"] == "windows":
        print("\nrefused: a Windows kit runs on the device itself (PowerShell above)", file=sys.stderr)
        return 2
    if not args.apply:
        print("\n(dry run — add --apply to run these steps)")
        return 0
    receipt = {"schema": "serveros/fleet-device-install-receipt.v1", "device": k["device"],
               "ability_rev": args.ability_rev, "steps": []}
    code = 0
    for s in steps:
        if s.enable and not args.enable:
            receipt["steps"].append({"title": s.title, "result": "SKIPPED (no --enable)"})
            continue
        if s.write:
            path, body = s.write
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(body, encoding="utf-8")
            receipt["steps"].append({"title": s.title, "result": "WRITTEN", "path": str(path)})
            continue
        env = dict(os.environ)
        if s.argv[1:3] == ["-m", "fleet_hub_watch"]:
            env["PYTHONPATH"] = os.pathsep.join(
                [str(ROOT), str(ROOT / "integrations/fleet-next-agent-monitor"),
                 str(ROOT / "integrations/fleet-hub-watch")])
        proc = subprocess.run(s.argv, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                              env=env)
        result = "OK" if proc.returncode == 0 else ("OK (already)" if s.ok_fail else "FAILED")
        receipt["steps"].append({"title": s.title, "result": result, "exit": proc.returncode})
        print(f"[{result}] {s.title}")
        if result == "FAILED":
            print(proc.stdout[-4000:], file=sys.stderr)
            code = 1
            break
    out = Path(args.home).expanduser() / ".local/state/serveros/device-kit"
    out.mkdir(parents=True, exist_ok=True)
    (out / f"{k['device']}.receipt.json").write_text(json.dumps(receipt, indent=2) + "\n",
                                                     encoding="utf-8")
    print(f"receipt: {out / (k['device'] + '.receipt.json')}")
    return code


# -- update / versions --------------------------------------------------------------

#: Units whose restart an update PRINTS when they are already running here. The
#: update runs no stopping verb itself (the mutation guard's rule, the same one
#: that keeps `release.py promote` to a daemon-reload); the person or agent
#: running the update runs the printed line.
RESTART_IF_RUNNING = ("serverosd.service", "serveros-fleet-node-connector.service",
                      "serveros-fleet-watch.service", "serveros-fleet-v1.service")


def _git(*args: str, cwd: Path) -> str:
    return subprocess.run(["git", *args], cwd=cwd, text=True, check=True,
                          stdout=subprocess.PIPE, stderr=subprocess.STDOUT).stdout.strip()


def _versions_path(home: Path) -> Path:
    return home / ".local/state/serveros/device-versions.json"


def resolve_ability_rev(repo: Path, rev: str | None) -> str:
    """`main` (or nothing) means ability-management's origin/main, fetched now."""
    if rev and rev != "main":
        return rev
    _git("fetch", "-q", "origin", "main", cwd=repo)
    return _git("rev-parse", "origin/main", cwd=repo)


def device_versions(k: dict, *, home: Path, ability_rev: str) -> dict:
    """What this device now runs, layer by layer, as this checkout can state it."""
    core = k["layers"]["core"]
    collector = home / ".local/lib/serveros-core-lite/serveros-node-resource-snapshot"
    collector_version = None
    if collector.is_file():
        run = subprocess.run([str(collector), "--version"], text=True,
                             stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        collector_version = run.stdout.strip() or None
    try:
        serveros = _git("rev-parse", "HEAD", cwd=ROOT)
    except (subprocess.CalledProcessError, FileNotFoundError):
        serveros = None
    return {
        "schema": "serveros/device-versions.v1",
        "device": k["device"], "device_ref": k["device_ref"],
        "recorded_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "serveros_commit": serveros,
        "core_plugin": ({"name": core["name"], "version": core["version"]}
                        if core.get("composition") else None),
        "ability_family": {"profile": k["layers"]["family"]["profile"], "revision": ability_rev},
        "core_lite_collector": collector_version,
        "commander": (k["layers"]["commander"]["install_id"] if "commander" in k["layers"] else None),
    }


def update(args) -> int:
    doc = declaration()
    if args.device not in doc["devices"]:
        raise KitError(f"unknown device {args.device}; known: {sorted(doc['devices'])}")
    k = kit(doc, args.device)
    if k["os"] == "windows":
        raise KitError("a Windows device updates with distribution/windows/update.ps1 on the device")
    home = Path(args.home).expanduser()
    repo = Path(args.ability_repo).expanduser().resolve()
    steps = [f"git -C {ROOT} pull --ff-only origin main",
             f"ability-management: {args.ability_rev or 'main'} → a pinned commit (git fetch)",
             f"device_kit.py install --device {k['device']} --apply"
             + (" --enable" if args.enable else "") + "   (the pulled code)",
             "print the restart command for those already running: " + ", ".join(RESTART_IF_RUNNING),
             f"write {_versions_path(home)}"]
    print(f"# update {k['device']} ({k['device_ref']})")
    for i, line in enumerate(steps, 1):
        print(f"{i:2}. {line}")
    if not args.apply:
        print("\n(dry run — add --apply to run these steps)")
        return 0

    if not args.no_pull:
        print(_git("pull", "--ff-only", "origin", "main", cwd=ROOT))
    rev = resolve_ability_rev(repo, args.ability_rev)
    print(f"ability-management at {rev}")
    install_argv = [args.python, str(HERE / "device_kit.py"), "install", "--device", k["device"],
                    "--ability-rev", rev, "--ability-repo", str(repo), "--home", str(home),
                    "--python", args.python, "--apply"]
    if args.notify:
        install_argv += ["--notify", args.notify]
    if args.data_root:
        install_argv += ["--data-root", args.data_root]
    if args.enable:
        install_argv.append("--enable")
    # A fresh process, so the steps come from the code the pull just brought.
    if subprocess.run(install_argv).returncode != 0:
        print("install failed; versions were not recorded", file=sys.stderr)
        return 1
    running = [unit for unit in RESTART_IF_RUNNING
               if subprocess.run(["systemctl", "--user", "is-active", "--quiet", unit]).returncode == 0]
    record = device_versions(kit(declaration(), k["device"]), home=home, ability_rev=rev)
    path = _versions_path(home)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
    print(f"versions: {path}")
    if running:
        print("\nthe new code takes effect after a restart of what is running here:")
        print("  systemctl --user restart " + " ".join(running))
    return 0


def versions(args) -> int:
    path = _versions_path(Path(args.home).expanduser())
    if not path.is_file():
        print(f"no versions recorded at {path}; run `update --apply` once", file=sys.stderr)
        return 1
    print(path.read_text(encoding="utf-8"), end="")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("command", choices=("build", "check", "plan", "install", "update", "versions"))
    parser.add_argument("--device")
    parser.add_argument("--ability-rev")
    parser.add_argument("--ability-repo", default=str(ROOT.parent / "ability-management"))
    parser.add_argument("--data-root")
    parser.add_argument("--home", default=str(Path.home()))
    parser.add_argument("--python", default=sys.executable)
    parser.add_argument("--notify", default=None,
                        help="hub only, required there: lark:user:<open_id> or "
                             "lark:chat:<chat_id> for Feishu, journal (writes the cards, "
                             "sends nothing), off (no notifier)")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--enable", action="store_true", help="also enable and start the units")
    parser.add_argument("--no-pull", action="store_true", help="update: use this checkout as it is")
    args = parser.parse_args(argv)
    try:
        if args.command == "build":
            return build()
        if args.command == "check":
            return check()
        if not args.device:
            raise KitError("--device is required")
        if args.command == "update":
            return update(args)
        if args.command == "versions":
            return versions(args)
        if args.command == "plan":
            k, steps = plan(args)
            print_plan(k, steps, args.enable)
            return 0
        return install(args)
    except KitError as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
