"""Install, update or check serveros-eventd on THIS machine (D-4 event push).

    python3 distribution/eventd/install.py --check                 # what is installed, is it alive
    python3 distribution/eventd/install.py                          # plan only: say what would happen
    python3 distribution/eventd/install.py --apply                  # install / update and start
    python3 distribution/eventd/install.py --apply --binary PATH    # use a binary built here

What it does, for the account that runs it (never another account, never /etc):

1. finds this machine's CloudCLI address and the PATH of its credential file
   from the existing binding — the node connector's `fleet-node.local.yaml`, or
   on the hub the Fleet binding's own-device provider. The credential's value is
   never read here;
2. takes the binary from the `fleet-latest` release (sha256-checked against the
   release's own `.sha256` file), or from `--binary`;
3. installs it under the user's own directory and runs it as a user service:
   a systemd **user** unit on Linux, a per-user scheduled task (at logon,
   restarted on failure) on Windows;
4. waits for the pusher's status file to say it polled, and prints the result.

Idempotent: running it again replaces the binary and restarts the service.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

RELEASE = "https://github.com/ProfessorZhi/serveros/releases/download/fleet-latest"
WINDOWS = os.name == "nt"
ASSET = "serveros-eventd-windows-x64.exe" if WINDOWS else "serveros-eventd-linux-x64"
UNIT = "serveros-eventd.service"
TASK = "ServerOS-eventd"


def lib_dir() -> Path:
    if WINDOWS:
        base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
        return Path(base) / "ServerOS" / "bin"
    return Path.home() / ".local" / "lib" / "serveros-eventd"


def target_binary() -> Path:
    return lib_dir() / ("serveros-eventd.exe" if WINDOWS else "serveros-eventd")


def find_upstream(binding: str | None) -> tuple[str, Path]:
    """(CloudCLI MCP endpoint, credential file path) of THIS machine."""
    from fleet_mcp.node.connector import DEFAULT_CONFIG_PATHS, load_node_config

    candidates = [Path(binding).expanduser()] if binding else list(DEFAULT_CONFIG_PATHS)
    for path in candidates:
        if path.is_file():
            try:
                config = load_node_config(path)
            except Exception:  # noqa: BLE001 - maybe it is the hub binding instead
                break
            return config.cloudcli_endpoint, config.cloudcli_credential_anchor
    # The hub: its own device's provider in the Fleet binding.
    from fleet_mcp.config import load_config

    config = load_config(binding) if binding else load_config()
    own = config.provider_for(config.local_device_ref) or config.providers.get(config.local_device_ref)
    if own is None:
        raise SystemExit("no binding names this machine's CloudCLI: pass --binding")
    return own.endpoint, own.credential_anchor


def fetch(name: str) -> bytes:
    with urllib.request.urlopen(f"{RELEASE}/{name}", timeout=60) as resp:
        return resp.read()


def get_binary(local: str | None) -> Path:
    if local:
        return Path(local)
    expected = fetch(ASSET + ".sha256").decode().split()[0].strip().lower()
    data = fetch(ASSET)
    actual = hashlib.sha256(data).hexdigest()
    if actual != expected:
        raise SystemExit(f"refused: {ASSET} sha256 {actual} does not match the release's {expected}")
    tmp = Path(tempfile.mkdtemp()) / ASSET
    tmp.write_bytes(data)
    return tmp


def run(cmd: list[str], *, check: bool = True) -> subprocess.CompletedProcess:
    done = subprocess.run(cmd, capture_output=True, text=True)
    if check and done.returncode != 0:
        raise SystemExit(f"failed: {' '.join(cmd[:3])}…: {(done.stderr or done.stdout).strip()[:300]}")
    return done


def unit_text(binary: Path, endpoint: str, credential: Path, spool: Path) -> str:
    return f"""[Unit]
Description=ServerOS event pusher (D-4): CloudCLI run events -> spool for the hub
After=default.target

[Service]
Type=simple
ExecStart={binary} --endpoint {endpoint} --credential-file {credential} --spool {spool} --interval-ms 1000
Restart=always
RestartSec=5
MemoryMax=32M
CPUQuota=10%
Nice=10
NoNewPrivileges=yes

[Install]
WantedBy=default.target
"""


def install_linux(binary: Path, endpoint: str, credential: Path, spool: Path) -> None:
    unit_dir = Path.home() / ".config" / "systemd" / "user"
    unit_dir.mkdir(parents=True, exist_ok=True)
    (unit_dir / UNIT).write_text(unit_text(binary, endpoint, credential, spool), encoding="utf-8")
    run(["systemctl", "--user", "daemon-reload"])
    run(["systemctl", "--user", "enable", UNIT])
    run(["systemctl", "--user", "restart", UNIT])


def install_windows(binary: Path, endpoint: str, credential: Path, spool: Path) -> None:
    args = (f'--endpoint {endpoint} --credential-file "{credential}" --spool "{spool}" '
            f'--interval-ms 1000')
    script = f"""
$ErrorActionPreference = 'Stop'
Stop-ScheduledTask -TaskName '{TASK}' -ErrorAction SilentlyContinue
$action = New-ScheduledTaskAction -Execute '{binary}' -Argument '{args}'
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -RestartCount 999 `
  -RestartInterval (New-TimeSpan -Minutes 1) -ExecutionTimeLimit ([TimeSpan]::Zero) `
  -MultipleInstances IgnoreNew -Hidden
Register-ScheduledTask -TaskName '{TASK}' -Action $action -Trigger $trigger -Settings $settings -Force | Out-Null
Start-ScheduledTask -TaskName '{TASK}'
"""
    run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", script])


def stop_service() -> None:
    if WINDOWS:
        run(["powershell", "-NoProfile", "-Command",
             f"Stop-ScheduledTask -TaskName '{TASK}' -ErrorAction SilentlyContinue; "
             f"Get-Process serveros-eventd -ErrorAction SilentlyContinue | Stop-Process -Force"],
            check=False)
    else:
        run(["systemctl", "--user", "stop", UNIT], check=False)


def check(spool: Path) -> dict:
    from fleet_mcp.node import events

    target = target_binary()
    version = None
    if target.exists():
        done = run([str(target), "--version"], check=False)
        version = done.stdout.strip() or None
    return {"binary": str(target), "version": version, "spool": str(spool),
            "pusher": events.eventd_status(spool),
            "recent": events.read_since(spool, max(0, (events.eventd_status(spool).get("seq") or 0) - 5))}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--apply", action="store_true", help="install / update and start")
    parser.add_argument("--check", action="store_true", help="report only")
    parser.add_argument("--binary", help="a serveros-eventd built here instead of the release")
    parser.add_argument("--binding", help="the connector or hub binding (default: found)")
    args = parser.parse_args(argv)
    from fleet_mcp.node import events

    spool = events.default_spool()
    if args.check:
        print(json.dumps(check(spool), ensure_ascii=False, indent=2))
        return 0
    endpoint, credential = find_upstream(args.binding)
    plan = {"endpoint": endpoint, "credential_file": str(credential),
            "credential_file_exists": credential.is_file(), "spool": str(spool),
            "binary": str(target_binary()), "service": TASK if WINDOWS else UNIT,
            "source": args.binary or f"{RELEASE}/{ASSET}"}
    if not args.apply:
        print(json.dumps({"plan": plan, "note": "add --apply to install"}, ensure_ascii=False, indent=2))
        return 0
    source = get_binary(args.binary)
    stop_service()
    target = target_binary()
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, target)
    if not WINDOWS:
        target.chmod(0o755)
    spool.parent.mkdir(parents=True, exist_ok=True)
    (install_windows if WINDOWS else install_linux)(target, endpoint, credential, spool)
    deadline = time.time() + 20
    status = {}
    while time.time() < deadline:
        status = events.eventd_status(spool)
        if status.get("state") in ("OK", "DEGRADED"):
            break
        time.sleep(1)
    print(json.dumps({"installed": plan, "pusher": status}, ensure_ascii=False, indent=2))
    return 0 if status.get("state") == "OK" else 1


if __name__ == "__main__":
    sys.exit(main())
