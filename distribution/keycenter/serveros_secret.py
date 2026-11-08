#!/usr/bin/env python3
"""serveros-secret — the fleet's key center on SOPS + age, Git as the authority.

Layout of the private secrets repository (ProfessorZhi/serveros-secrets):

    recipients.yaml                      owner + one age PUBLIC key per device
    .sops.yaml                           generated from recipients.yaml
    devices/<device-id>/<bundle>.sops.yaml   NAME: value, encrypted
    devices/<device-id>/<bundle>.target.yaml env_file + units to restart (plain)

Who can read what: each bundle is encrypted to the owner and to that device
only. A device's age PRIVATE key never leaves the device; the owner's never
leaves the owner's machine. No agent ever sees a value:

*   `set` runs at the owner's keyboard: the value is read without echo and
    passed to sops through a pipe — never an argument, never a file;
*   `sync` runs on a device (timer): decrypts its own bundles into 0600 env
    files and restarts what reads them when a value changed;
*   Fleet only ever sees NAMES (`names` / the state file `sync` writes).

Commands:
    serveros-secret keygen                       (device) make this device's age key, print its public key
    serveros-secret recipients --repo R --write  (owner)  regenerate .sops.yaml from recipients.yaml
    serveros-secret set NAME --device D [--bundle B] --repo R   (owner) add/replace one value
    serveros-secret rm NAME --device D [--bundle B] --repo R    (owner)
    serveros-secret sync [--no-pull] [--no-restart]            (device) pull, decrypt, write, restart
    serveros-secret names                        (anyone) print the names this device holds
    serveros-secret doctor                       (device) check binaries, key, config, repo

Device config: ~/.config/serveros/secrets.yaml
    repo: /path/to/serveros-secrets checkout
    device: device:<id>
    key: ~/.config/serveros/age.key            (default)

The backend is this file's `decrypt_bundle`/`encrypt_bundle`; a device only
relies on "a set of variables written to an env file". Moving to OpenBao later
replaces those two functions and nothing that reads the env files.
"""
from __future__ import annotations

import argparse
import getpass
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

import yaml

CONFIG = Path("~/.config/serveros/secrets.yaml").expanduser()
DEFAULT_KEY = Path("~/.config/serveros/age.key").expanduser()
STATE = Path("~/.local/state/serveros/secret-names.json").expanduser()
NAME_RE = re.compile(r"^[A-Z_][A-Z0-9_]{0,127}$")
BUNDLE_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,47}$")
DEVICE_RE = re.compile(r"^device:([a-z0-9][a-z0-9-]{1,62})$")
UNIT_RE = re.compile(r"^[A-Za-z0-9@_.:-]+\.(service|timer)$")


class SecretError(RuntimeError):
    pass


# -- small helpers ---------------------------------------------------------

def _device_id(device: str) -> str:
    match = DEVICE_RE.match(device or "")
    if not match:
        raise SecretError(f"device must be device:<id>, not {device!r}")
    return match.group(1)


def _atomic_write(path: Path, text: str, mode: int = 0o600) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=f".{path.name}.")
    try:
        os.fchmod(fd, mode)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def _run(argv: list[str], *, stdin: str | None = None, env: dict | None = None,
         cwd: Path | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(argv, input=stdin, text=True, capture_output=True,
                          env={**os.environ, **(env or {})}, cwd=cwd)


def _need(binary: str) -> str:
    found = shutil.which(binary)
    if not found:
        raise SecretError(f"{binary} is not installed (see distribution/keycenter/README.md)")
    return found


def dotenv(values: dict[str, str]) -> str:
    """NAME="value" lines, double-quoted and escaped, as systemd EnvironmentFile reads them."""
    lines = []
    for name in sorted(values):
        value = str(values[name])
        escaped = value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")
        lines.append(f'{name}="{escaped}"')
    return "\n".join(lines) + ("\n" if lines else "")


# -- the backend (SOPS + age) ------------------------------------------------

def decrypt_bundle(path: Path, key: Path) -> dict[str, str]:
    done = _run([_need("sops"), "--decrypt", "--output-type", "json", str(path)],
                env={"SOPS_AGE_KEY_FILE": str(key)})
    if done.returncode != 0:
        raise SecretError(f"sops could not decrypt {path.name}: "
                          f"{(done.stderr or '').strip().splitlines()[-1:] or ['?']}")
    data = json.loads(done.stdout or "{}")
    if not isinstance(data, dict):
        raise SecretError(f"{path.name} is not a mapping")
    return {k: str(v) for k, v in data.items() if NAME_RE.match(str(k))}


def encrypt_bundle(repo: Path, rel: str, values: dict[str, str], key: Path | None) -> None:
    """Encrypt `values` to `rel` using the repo's creation rules; plaintext via a pipe only."""
    plain = yaml.safe_dump(values, allow_unicode=True, sort_keys=True)
    env = {"SOPS_AGE_KEY_FILE": str(key)} if key else {}
    done = _run([_need("sops"), "--encrypt", "--input-type", "yaml", "--output-type", "yaml",
                 "--filename-override", rel, "/dev/stdin"], stdin=plain, env=env, cwd=repo)
    if done.returncode != 0:
        raise SecretError(f"sops could not encrypt {rel}: {done.stderr.strip()[-300:]}")
    _atomic_write(repo / rel, done.stdout, 0o644)


# -- owner side ------------------------------------------------------------------

def write_sops_config(repo: Path) -> str:
    recipients = yaml.safe_load((repo / "recipients.yaml").read_text(encoding="utf-8")) or {}
    owner = recipients.get("owner")
    if not isinstance(owner, str) or not owner.startswith("age1"):
        raise SecretError("recipients.yaml needs `owner: age1...` (the owner's public key)")
    rules = []
    for device, pub in sorted((recipients.get("devices") or {}).items()):
        did = _device_id(device)
        if not isinstance(pub, str) or not pub.startswith("age1"):
            raise SecretError(f"recipients.yaml: {device} needs an age1... public key")
        rules.append({"path_regex": rf"^devices/{re.escape(did)}/.*\.sops\.yaml$",
                      "age": f"{owner},{pub}"})
    rules.append({"path_regex": r".*\.sops\.yaml$", "age": owner})
    text = ("# Generated by `serveros-secret recipients --write` from recipients.yaml.\n"
            + yaml.safe_dump({"creation_rules": rules}, sort_keys=False))
    _atomic_write(repo / ".sops.yaml", text, 0o644)
    return text


def _bundle_paths(repo: Path, device: str, bundle: str) -> tuple[str, Path]:
    if not BUNDLE_RE.match(bundle):
        raise SecretError("bundle is lowercase letters, digits, - and _")
    rel = f"devices/{_device_id(device)}/{bundle}.sops.yaml"
    return rel, repo / rel


def set_value(repo: Path, device: str, bundle: str, name: str, value: str,
              owner_key: Path | None) -> dict[str, Any]:
    if not NAME_RE.match(name):
        raise SecretError("NAME is an environment variable name, e.g. GW0_TOKEN")
    if not value:
        raise SecretError("empty value; use `rm` to remove a name")
    rel, path = _bundle_paths(repo, device, bundle)
    values = decrypt_bundle(path, owner_key) if path.exists() else {}
    existed = name in values
    values[name] = value
    encrypt_bundle(repo, rel, values, owner_key)
    return {"bundle": rel, "name": name, "action": "replaced" if existed else "added",
            "names": sorted(values)}


def remove_value(repo: Path, device: str, bundle: str, name: str,
                 owner_key: Path | None) -> dict[str, Any]:
    rel, path = _bundle_paths(repo, device, bundle)
    values = decrypt_bundle(path, owner_key)
    if values.pop(name, None) is None:
        raise SecretError(f"{name} is not in {rel}")
    encrypt_bundle(repo, rel, values, owner_key)
    return {"bundle": rel, "name": name, "action": "removed", "names": sorted(values)}


# -- device side -------------------------------------------------------------------

def load_config(path: Path = CONFIG) -> dict[str, Any]:
    if not path.is_file():
        raise SecretError(f"no {path}; write repo:, device: (and key:) there")
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    repo = Path(str(data.get("repo") or "")).expanduser()
    if not (repo / ".git").exists():
        raise SecretError(f"secrets repo checkout not found at {repo}")
    _device_id(str(data.get("device")))
    return {"repo": repo, "device": data["device"],
            "key": Path(str(data.get("key") or DEFAULT_KEY)).expanduser()}


def _check_key(key: Path) -> None:
    try:
        st = key.stat()
    except OSError:
        raise SecretError(f"no age key at {key}; run `serveros-secret keygen`") from None
    if hasattr(os, "getuid") and (st.st_uid != os.getuid() or st.st_mode & 0o077):
        raise SecretError(f"{key} must belong to this account and be mode 0600")


def _target(path: Path) -> dict[str, Any]:
    target = path.with_name(path.name.replace(".sops.yaml", ".target.yaml"))
    if not target.is_file():
        raise SecretError(f"{target.name} is missing (env_file + restart)")
    data = yaml.safe_load(target.read_text(encoding="utf-8")) or {}
    env_file = data.get("env_file")
    if not isinstance(env_file, str) or not env_file.strip():
        raise SecretError(f"{target.name}: env_file is required")
    units = data.get("restart") or []
    if not isinstance(units, list) or not all(isinstance(u, str) and UNIT_RE.match(u) for u in units):
        raise SecretError(f"{target.name}: restart is a list of systemd user units")
    return {"env_file": Path(env_file).expanduser(), "restart": units}


def sync(config: dict[str, Any], *, pull: bool = True, restart: bool = True,
         state: Path = STATE, runner=None) -> dict[str, Any]:
    """Pull, decrypt this device's bundles, write env files, restart on change."""
    runner = runner or (lambda argv: _run(argv))
    repo, key = config["repo"], config["key"]
    _check_key(key)
    if pull:
        done = runner(["git", "-C", str(repo), "pull", "--ff-only", "-q"])
        if done.returncode != 0:
            raise SecretError(f"git pull failed: {(done.stderr or '').strip()[-200:]}")
    head = runner(["git", "-C", str(repo), "rev-parse", "HEAD"]).stdout.strip() or None
    folder = repo / "devices" / _device_id(config["device"])
    bundles, restarted, errors = [], [], []
    for path in sorted(folder.glob("*.sops.yaml")) if folder.is_dir() else []:
        try:
            values = decrypt_bundle(path, key)
            target = _target(path)
            text = dotenv(values)
            env_file = target["env_file"]
            before = env_file.read_text(encoding="utf-8") if env_file.is_file() else None
            changed = before != text
            if changed:
                _atomic_write(env_file, text, 0o600)
                if restart:
                    for unit in target["restart"]:
                        runner(["systemctl", "--user", "restart", unit])
                        restarted.append(unit)
            bundles.append({"bundle": path.name.removesuffix(".sops.yaml"),
                            "env_file": str(env_file), "names": sorted(values),
                            "changed": changed,
                            "digest": hashlib.sha256(text.encode()).hexdigest()[:12]})
        except SecretError as exc:
            errors.append({"bundle": path.name, "error": str(exc)})
    record = {"schema": "serveros/secret-names.v1", "device": config["device"],
              "synced_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
              "repo_commit": head,
              "bundles": [{k: b[k] for k in ("bundle", "env_file", "names")} for b in bundles],
              "names": sorted({n for b in bundles for n in b["names"]}),
              "errors": errors}
    _atomic_write(state, json.dumps(record, ensure_ascii=False, indent=2) + "\n", 0o600)
    return {**record, "restarted": restarted,
            "changed": [b["bundle"] for b in bundles if b["changed"]]}


def read_names(state: Path = STATE) -> dict[str, Any] | None:
    """What a device holds, by name only — what Fleet may show."""
    try:
        data = json.loads(state.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict) or data.get("schema") != "serveros/secret-names.v1":
        return None
    return {"names": [n for n in data.get("names") or [] if isinstance(n, str) and NAME_RE.match(n)],
            "synced_at": data.get("synced_at"), "repo_commit": data.get("repo_commit"),
            "errors": len(data.get("errors") or [])}


def keygen(key: Path = DEFAULT_KEY) -> str:
    if key.exists():
        raise SecretError(f"{key} already exists; its public key: {public_key(key)}")
    done = _run([_need("age-keygen")])
    if done.returncode != 0:
        raise SecretError("age-keygen failed")
    _atomic_write(key, done.stdout, 0o600)
    return public_key(key)


def public_key(key: Path) -> str:
    done = _run([_need("age-keygen"), "-y", str(key)])
    if done.returncode != 0:
        raise SecretError(f"cannot read the public key of {key}")
    return done.stdout.strip()


# -- CLI ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="serveros-secret", description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="cmd", required=True)
    k = sub.add_parser("keygen"); k.add_argument("--key", default=str(DEFAULT_KEY))
    r = sub.add_parser("recipients"); r.add_argument("--repo", required=True)
    r.add_argument("--write", action="store_true")
    for name in ("set", "rm"):
        p = sub.add_parser(name)
        p.add_argument("name"); p.add_argument("--device", required=True)
        p.add_argument("--bundle", default="cloudcli"); p.add_argument("--repo", required=True)
        p.add_argument("--owner-key", default=os.environ.get("SOPS_AGE_KEY_FILE"))
        p.add_argument("--no-commit", action="store_true")
    s = sub.add_parser("sync"); s.add_argument("--no-pull", action="store_true")
    s.add_argument("--no-restart", action="store_true"); s.add_argument("--config", default=str(CONFIG))
    sub.add_parser("names")
    d = sub.add_parser("doctor"); d.add_argument("--config", default=str(CONFIG))
    args = parser.parse_args(argv)
    try:
        if args.cmd == "keygen":
            print(keygen(Path(args.key).expanduser()))
        elif args.cmd == "recipients":
            text = write_sops_config(Path(args.repo).expanduser()) if args.write else None
            print(text or "(dry run; add --write)")
        elif args.cmd in ("set", "rm"):
            repo = Path(args.repo).expanduser()
            owner_key = Path(args.owner_key).expanduser() if args.owner_key else None
            if args.cmd == "set":
                value = getpass.getpass(f"value for {args.name} (not echoed, not stored in plain): ")
                result = set_value(repo, args.device, args.bundle, args.name, value, owner_key)
            else:
                result = remove_value(repo, args.device, args.bundle, args.name, owner_key)
            if not args.no_commit:
                _run(["git", "-C", str(repo), "add", result["bundle"]])
                _run(["git", "-C", str(repo), "commit", "-q", "-m",
                      f"{result['action']} {args.name} for {args.device} ({args.bundle})"])
            print(json.dumps(result, ensure_ascii=False))
        elif args.cmd == "sync":
            result = sync(load_config(Path(args.config).expanduser()), pull=not args.no_pull,
                          restart=not args.no_restart)
            print(json.dumps({k: result[k] for k in ("device", "names", "changed", "restarted",
                                                    "errors", "repo_commit")}, ensure_ascii=False))
            return 1 if result["errors"] else 0
        elif args.cmd == "names":
            print(json.dumps(read_names() or {"names": [], "state": "NOT_SYNCED"}, ensure_ascii=False))
        elif args.cmd == "doctor":
            report = {b: bool(shutil.which(b)) for b in ("sops", "age", "age-keygen", "git")}
            try:
                cfg = load_config(Path(args.config).expanduser())
                _check_key(cfg["key"])
                report.update(config="OK", device=cfg["device"], public_key=public_key(cfg["key"]))
            except SecretError as exc:
                report["config"] = str(exc)
            print(json.dumps(report, ensure_ascii=False, indent=2))
            return 0 if report.get("config") == "OK" and all(
                report[b] for b in ("sops", "age-keygen", "git")) else 1
    except SecretError as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
