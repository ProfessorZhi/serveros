#!/usr/bin/env python3
"""One step of the Wave 18 offline drill, in its own operating-system process.

Every step is invoked as `python -I drill_step.py --step <name> --world <dir>`
by `wave18_acceptance.py`, writes `<world>/out/<name>.json`, and prints the same
object on one stdout line. Nothing is shared with the parent except that
directory: no port, no socket, no lock, no service, no credential, no network.

Why separate processes rather than functions
--------------------------------------------
The release claim is about what a *process* leaves behind for another process.
A single interpreter would share module state, an open file handle, a cached
receipt — and would therefore be able to pass while a real upgrade, run by a
different process at a different time, left the root unusable. Six processes
also make a step that silently did nothing visible: its pid line is missing.

Steps, in the order the acceptance harness runs them
----------------------------------------------------
seed            create the Legacy tree (one session, one run, one delivery)
install         install the composed package into `<world>/next`
observe         load the installed contract, tick a fake observer, re-read Legacy
failed-upgrade  refuse a bad upgrade, then fail one mid-write on purpose
rollback        revoke the installed package and check what was deleted
probe           one refusal per injected fault, each on a fresh root
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import shutil
import sys
from pathlib import Path

# `-I` implies `-E`, so `PYTHONDONTWRITEBYTECODE` in the environment is ignored
# and importing the *installed* vocabulary module would drop a `__pycache__`
# into the root under test — a file the receipt does not own, which then makes
# the revoke refuse `BLOCKED_FOREIGN_PRESENT`. The drill must not create the
# very orphan it is measuring for.
sys.dont_write_bytecode = True

HERE = Path(__file__).resolve().parent
# `-I` implies `-P`: the script's own directory is not on `sys.path`, so a
# sibling import has to be arranged rather than assumed.
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import legacy_gateway  # noqa: E402  (path arranged above)

STEPS = ("seed", "install", "observe", "failed-upgrade", "rollback", "probe")


class Refusal(Exception):
    """A drill-policy refusal, carrying the code the harness expects."""

    def __init__(self, code: str, reasons=()):
        super().__init__(code)
        self.code = code
        self.reasons = tuple(reasons)


# --------------------------------------------------------------------------- #
# plumbing
# --------------------------------------------------------------------------- #
def load_module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise SystemExit(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def read_pin(path: Path) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def world_of(args) -> Path:
    return Path(args.world)


def engine_of(args):
    return load_module(Path(args.engine), "w18_engine")


def preflight_of(args):
    return load_module(Path(args.preflight), "w18_preflight")


def device_of(pin: dict) -> str:
    return str(pin["device"]["device_ref"])


def boot_of(pin: dict) -> str:
    return str(pin["device"]["boot_id"])


def grant_of(engine, pin: dict, action: str, *, device: str | None = None,
             grant_id: str | None = None):
    return engine.Grant(grant_id=grant_id or f"grant-{action}-1",
                        operator="operator:wave18-acceptance",
                        action=action,
                        device_ref=device or device_of(pin))


def _sha(body: bytes) -> str:
    return "sha256:" + hashlib.sha256(body).hexdigest()


def package_members(pin: dict) -> list[dict]:
    return [{"path": member["path"],
             "from": member.get("from") or member["path"],
             "source": member["source"]}
            for member in pin["release_package"]["files"]]


def bump(version: str) -> str:
    """0.1.0+wave18 -> 0.1.1+wave18. An upgrade has to be a different release."""
    core, _, build = version.partition("+")
    parts = core.split(".")
    try:
        parts[-1] = str(int(parts[-1]) + 1)
    except (ValueError, IndexError):  # pragma: no cover - pins are semver
        parts.append("1")
    return ".".join(parts) + (f"+{build}" if build else "")


def descriptor(engine, world: Path, pin: dict, *, version: str | None = None,
               extra: tuple[tuple[str, bytes], ...] = ()):
    """Build the descriptor from the composed bytes, not from git or a guess."""
    package = world / "package"
    members = [(member["path"], (package / member["path"]).read_bytes())
               for member in package_members(pin)]
    members.extend(extra)
    sources: dict[str, bytes] = {}
    files: list[tuple[str, str]] = []
    for path, body in members:
        sources[path] = body
        files.append((path, _sha(body)))
    files.sort()
    commit = (world / "package.commit").read_text(encoding="utf-8").strip()
    returned = engine.Descriptor(
        name=pin["release_package"]["name"],
        version=version or pin["release_package"]["version"],
        commit=commit,
        files=tuple(files),
        entrypoint=pin["release_package"]["revoke_entrypoint"],
    )
    return returned, sources


def files_under(root: Path) -> list[str]:
    if not root.is_dir():
        return []
    return sorted(path.relative_to(root).as_posix() for path in root.rglob("*")
                  if path.is_file() and not path.is_symlink())


def owned_entries(engine, receipt) -> set[str]:
    owned = {record.path for record in receipt.files}
    owned |= {engine.RECEIPT_NAME, engine.OWNER_MARKER_NAME}
    return owned


# --------------------------------------------------------------------------- #
# steps
# --------------------------------------------------------------------------- #
def step_seed(args) -> dict:
    world = world_of(args)
    pin = read_pin(Path(args.pin))
    legacy_pin = dict(pin["legacy"])
    legacy_pin["device_ref"] = device_of(pin)
    root = world / "legacy"
    if root.exists():
        shutil.rmtree(root)
    baseline = legacy_gateway.seed(root, legacy_pin)
    return {"baseline": baseline, "tree_digest": baseline["tree_digest"]}


def step_install(args) -> dict:
    world = world_of(args)
    pin = read_pin(Path(args.pin))
    engine = engine_of(args)
    desc, sources = descriptor(engine, world, pin)
    root = world / "next"
    result = engine.install(desc, root, sources,
                            grant=grant_of(engine, pin, "install"),
                            device_ref=device_of(pin), boot_id=boot_of(pin),
                            legacy_roots=(world / "legacy",))
    receipt = engine.read_receipt(root)
    return {"code": result.code, "decision": result.decision,
            "written": list(result.written),
            "manifest": desc.manifest_digest(),
            "files": len(desc.files),
            "receipt_intact": bool(receipt and receipt.is_intact()),
            "snapshot": legacy_gateway.snapshot(world / "legacy")}


def _contract_module(installed: Path):
    """The installed vocabulary module: the file that defines STATUS_UNKNOWN."""
    for path in sorted(installed.rglob("*.py")):
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:  # pragma: no cover - unreadable file
            continue
        if "STATUS_UNKNOWN" in text and "NOT_SUCCESS" in text:
            return path
    raise SystemExit(f"no installed vocabulary module under {installed}")


def step_observe(args) -> dict:
    world = world_of(args)
    pin = read_pin(Path(args.pin))
    engine = engine_of(args)
    installed = world / "next"
    contract = load_module(_contract_module(installed), "w18_contract")
    gateway = legacy_gateway.LegacyGateway(world / "legacy")

    def fake_observer(events, *, offline: bool) -> str:
        """A fake tick: it may only say TERMINAL_VERIFIED about native activity."""
        if offline:
            return contract.STATUS_OFFLINE
        for event in events:
            body = event.get("body") or {}
            if body.get("native_activity") and body.get("verified"):
                return contract.STATUS_TERMINAL_VERIFIED
        return contract.STATUS_UNKNOWN

    def dispatch(subject: dict) -> dict:
        if not subject.get("authorization"):
            return {"code": contract.BLOCKED_SUBJECT, "provider_calls": 0}
        return {"code": "OK", "provider_calls": 0}

    vocabulary = {
        "unknown_is_not_success": contract.STATUS_UNKNOWN in contract.NOT_SUCCESS,
        "offline_is_not_success": contract.STATUS_OFFLINE in contract.NOT_SUCCESS,
        "terminal_verified_is_not_a_not_success":
            contract.STATUS_TERMINAL_VERIFIED not in contract.NOT_SUCCESS,
        "blocked_subject_is_a_refusal_word":
            contract.BLOCKED_SUBJECT in contract.BLOCKED_WORDS,
    }
    if not all(vocabulary.values()):
        raise SystemExit(f"the installed vocabulary contradicts itself: "
                         f"{vocabulary}")

    events = gateway.events()
    receipt = engine.read_receipt(installed)
    return {
        "fake_observer": True,
        "vocabulary": vocabulary,
        "completed_without_activity": {"status": fake_observer((), offline=False)},
        "offline": {"status": fake_observer(events, offline=True)},
        "native_activity": {"status": fake_observer(events, offline=False)},
        "typed_subject_without_authorization":
            dispatch({"kind": "typed", "authorization": None}),
        "typed_subject_with_authorization":
            dispatch({"kind": "typed", "authorization": "grant-typed-1"}),
        "deliveries": list(gateway.deliveries()),
        "receipt_intact": bool(receipt and receipt.is_intact()),
        "snapshot": gateway.snapshot(),
    }


def step_failed_upgrade(args) -> dict:
    world = world_of(args)
    pin = read_pin(Path(args.pin))
    engine = engine_of(args)
    legacy = world / "legacy"
    root = world / "next"
    desc_v1, sources = descriptor(engine, world, pin)
    version_v2 = bump(pin["release_package"]["version"])

    receipt_before = engine.read_receipt(root)
    before = {record.path: record.sha256 for record in receipt_before.files}
    refused_desc, refused_sources = descriptor(engine, world, pin, version=version_v2)
    first = sorted(refused_sources)[0]
    refused_sources[first] = b"this is not the pinned byte\n"
    refused = engine.install(refused_desc, root, refused_sources,
                             grant=grant_of(engine, pin, "install"),
                             device_ref=device_of(pin), boot_id=boot_of(pin),
                             legacy_roots=(legacy,), replace=receipt_before)
    receipt_after = engine.read_receipt(root)
    after = {record.path: record.sha256 for record in receipt_after.files}

    partial_root = world / "partial-root"
    if partial_root.exists():
        shutil.rmtree(partial_root)
    seeded = engine.install(desc_v1, partial_root, sources,
                            grant=grant_of(engine, pin, "install"),
                            device_ref=device_of(pin), boot_id=boot_of(pin),
                            legacy_roots=(legacy,))
    if seeded.decision != "OK":  # pragma: no cover - fixture precondition
        raise SystemExit(f"the partial-write root could not be seeded: {seeded.code}")
    # A stale regular file where the upgrade needs a directory. This is an
    # obstacle on the filesystem, not a monkeypatch: the engine's own write path
    # meets it, after it has already written the files sorted before it.
    (partial_root / "zz").write_bytes(b"a stale file where a directory is needed\n")
    extras = (("next/aaa-extra.py", b"EXTRA = 'wave18-drill'\n"),
              ("zz/blocked.py", b"BLOCKED = False\n"))
    partial_desc, partial_sources = descriptor(engine, world, pin,
                                               version=version_v2, extra=extras)
    receipt_partial = engine.read_receipt(partial_root)
    outcome, error = "NO_FAILURE", ""
    try:
        engine.install(partial_desc, partial_root, partial_sources,
                       grant=grant_of(engine, pin, "install"),
                       device_ref=device_of(pin), boot_id=boot_of(pin),
                       legacy_roots=(legacy,), replace=receipt_partial)
    except OSError as exc:
        outcome = "PARTIAL_WRITE"
        error = f"{type(exc).__name__}: {exc}"
    after_partial = engine.read_receipt(partial_root)
    owned = owned_entries(engine, after_partial) if after_partial else set()
    orphans = [rel for rel in files_under(partial_root) if rel not in owned]
    revoke_after_partial = engine.revoke(
        desc_v1, partial_root, grant=grant_of(engine, pin, "revoke"),
        device_ref=device_of(pin), boot_id=boot_of(pin), legacy_roots=(legacy,))

    return {
        "refused": {"code": refused.code, "reasons": list(refused.reasons)},
        "receipt_still_names": receipt_after.version if receipt_after else None,
        "installed_bytes_unchanged": after == before,
        "partial": {
            "outcome": outcome,
            "error": error,
            "orphans": orphans,
            "receipt_written": bool(after_partial
                                    and after_partial.version == version_v2),
            "receipt_still_names": after_partial.version if after_partial else None,
            "revoke_after_partial": {"code": revoke_after_partial.code,
                                     "reasons": list(revoke_after_partial.reasons)},
        },
        "snapshot": legacy_gateway.snapshot(legacy),
    }


def step_rollback(args) -> dict:
    world = world_of(args)
    pin = read_pin(Path(args.pin))
    engine = engine_of(args)
    root = world / "next"
    desc, _sources = descriptor(engine, world, pin)
    receipt = engine.read_receipt(root)
    owned = sorted(record.path for record in receipt.files) if receipt else []
    backup = root / engine.BACKUP_DIR
    result = engine.revoke(desc, root, grant=grant_of(engine, pin, "revoke"),
                           device_ref=device_of(pin), boot_id=boot_of(pin),
                           legacy_roots=(world / "legacy",))
    return {"code": result.code, "deleted": list(result.deleted),
            "owned": owned,
            "receipt_gone": not (root / engine.RECEIPT_NAME).exists(),
            "backup_gone": not backup.exists(),
            "left_behind": files_under(root),
            "snapshot": legacy_gateway.snapshot(world / "legacy")}


def _fresh(world: Path, name: str) -> Path:
    root = world / "probe" / name
    if root.exists():
        shutil.rmtree(root)
    root.mkdir(parents=True)
    return root / "install"


def _footprint(preflight, pin: dict, **overrides):
    base = {
        "track": "legacy",
        "install_path": "/opt/fleet-legacy",
        "port": int(pin["legacy"]["port"]),
        "socket": "/run/fleet-legacy.sock",
        "backend_id": "fleet-legacy",
        "oauth_audience": str(pin["legacy"]["oauth_audience"]),
        "cred_state_root": "/var/lib/fleet-legacy",
        "lock": "/run/fleet-legacy.lock",
        "unit": "fleet-legacy.service",
        "device_ref": device_of(pin),
        "version": str(pin["legacy"]["fleet_instance_id"]),
        "artifact_digest": "sha256:" + "1" * 64,
    }
    base.update(overrides)
    return preflight.Footprint(**base)


def _next_footprint(preflight, pin: dict, **overrides):
    base = {
        "track": "next",
        "install_path": "/opt/fleet-next",
        "port": int(pin["legacy"]["port"]) + 1,
        "socket": "/run/fleet-next.sock",
        "backend_id": "fleet-next",
        "oauth_audience": "https://next.invalid/mcp",
        "cred_state_root": "/var/lib/fleet-next",
        "lock": "/run/fleet-next.lock",
        "unit": "fleet-next.service",
        "device_ref": device_of(pin),
        "version": pin["release_package"]["version"],
        "artifact_digest": "sha256:" + "2" * 64,
    }
    base.update(overrides)
    return preflight.Footprint(**base)


def step_probe(args) -> dict:
    world = world_of(args)
    pin = read_pin(Path(args.pin))
    engine = engine_of(args)
    preflight = preflight_of(args)
    legacy = world / "legacy"
    install_grant = grant_of(engine, pin, "install")
    revoke_grant = grant_of(engine, pin, "revoke")
    device, boot = device_of(pin), boot_of(pin)
    desc, sources = descriptor(engine, world, pin)
    injections: dict[str, dict] = {}

    # 1. bytes that are not the pinned bytes
    root = _fresh(world, "bad_sha")
    tampered = dict(sources)
    tampered[sorted(tampered)[0]] = b"not the pinned byte\n"
    result = engine.install(desc, root, tampered, grant=install_grant,
                            device_ref=device, boot_id=boot,
                            legacy_roots=(legacy,))
    injections["bad_sha"] = {"code": result.code, "reasons": list(result.reasons)}

    # 2. an upgrade attempted from another boot
    root = _fresh(world, "foreign_boot")
    first = engine.install(desc, root, sources, grant=install_grant,
                           device_ref=device, boot_id=boot, legacy_roots=(legacy,))
    if first.decision != "OK":  # pragma: no cover - fixture precondition
        raise SystemExit(f"foreign_boot precondition failed: {first.code}")
    upgrade_desc, upgrade_sources = descriptor(engine, world, pin,
                                               version=bump(
                                                   pin["release_package"]["version"]))
    result = engine.install(upgrade_desc, root, upgrade_sources,
                            grant=install_grant, device_ref=device,
                            boot_id="boot-from-another-machine",
                            legacy_roots=(legacy,),
                            replace=engine.read_receipt(root))
    injections["foreign_boot"] = {"code": result.code,
                                  "reasons": list(result.reasons)}

    # 3. a revoke asked for by a device the receipt does not belong to
    root = _fresh(world, "unauthorized_device")
    first = engine.install(desc, root, sources, grant=install_grant,
                           device_ref=device, boot_id=boot, legacy_roots=(legacy,))
    if first.decision != "OK":  # pragma: no cover - fixture precondition
        raise SystemExit(f"unauthorized_device precondition failed: {first.code}")
    other = "device:someone-elses-laptop"
    result = engine.revoke(desc, root, grant=grant_of(engine, pin, "revoke",
                                                      device=other),
                           device_ref=other, boot_id=boot, legacy_roots=(legacy,))
    injections["unauthorized_device"] = {"code": result.code,
                                         "reasons": list(result.reasons)}

    # 4. a directory that looks installed but carries no receipt
    root = _fresh(world, "missing_receipt")
    (root / "sidecar" ).mkdir(parents=True)
    (root / "sidecar" / "release.py").write_bytes(b"LOOKS_INSTALLED = True\n")
    result = engine.revoke(desc, root, grant=revoke_grant, device_ref=device,
                           boot_id=boot, legacy_roots=(legacy,))
    injections["missing_receipt"] = {"code": result.code,
                                     "reasons": list(result.reasons)}

    # 5. a Next root placed inside the Legacy tree
    result = engine.install(desc, legacy / "sidecar", sources,
                            grant=install_grant, device_ref=device, boot_id=boot,
                            legacy_roots=(legacy,))
    injections["legacy_root_collision"] = {"code": result.code,
                                           "reasons": list(result.reasons)}

    # 6. the resource matrix, on footprints (a rule, not this host's layout)
    colliding = preflight.resource_matrix(
        _footprint(preflight, pin),
        _next_footprint(preflight, pin, port=int(pin["legacy"]["port"])))
    clean = preflight.resource_matrix(
        _footprint(preflight, pin), _next_footprint(preflight, pin))
    injections["resource_collision"] = {
        "code": colliding.code,
        "resources": [collision.resource for collision in colliding.collisions],
        "reasons": [collision.detail for collision in colliding.collisions],
    }
    injections["resource_clean"] = {"code": clean.code,
                                    "decision": clean.decision,
                                    "collisions": len(clean.collisions)}

    # 7. no reachable upstream: refuse to call the pin resolved
    injections["offline"] = _injection_offline()

    # 8. a grant minted for a different action
    root = _fresh(world, "no_grant")
    result = engine.install(desc, root, sources,
                            grant=grant_of(engine, pin, "revoke"),
                            device_ref=device, boot_id=boot,
                            legacy_roots=(legacy,))
    injections["no_grant"] = {"code": result.code, "reasons": list(result.reasons)}

    # 9. a descriptor whose version is a moving tag
    root = _fresh(world, "not_pinned")
    floating = engine.Descriptor(name=desc.name, version="latest",
                                 commit=desc.commit, files=desc.files,
                                 entrypoint=desc.entrypoint)
    result = engine.install(floating, root, sources, grant=install_grant,
                            device_ref=device, boot_id=boot,
                            legacy_roots=(legacy,))
    injections["not_pinned"] = {"code": result.code,
                                "reasons": list(result.reasons)}

    return {"injections": injections, "network": "none",
            "snapshot": legacy_gateway.snapshot(legacy)}


def _injection_offline() -> dict:
    """The drill's own policy: with no upstream, the pin is not a resolution.

    This is not a claim about the engine. It is the harness refusing to turn
    "I could not check" into "I checked" — the same shape as `UNKNOWN` in the
    monitor vocabulary, applied to package resolution.
    """
    try:
        require_online(online=False)
    except Refusal as refusal:
        return {"code": refusal.code, "descriptor": None,
                "reasons": list(refusal.reasons), "source": "drill policy"}
    return {"code": "OK", "descriptor": "resolved"}  # pragma: no cover


def require_online(*, online: bool) -> bool:
    if not online:
        raise Refusal("OFFLINE_REFUSED",
                      ("no upstream is reachable, so the drill will not report "
                       "the release pin as resolved or as absent",))
    return True


DISPATCH = {
    "seed": step_seed,
    "install": step_install,
    "observe": step_observe,
    "failed-upgrade": step_failed_upgrade,
    "rollback": step_rollback,
    "probe": step_probe,
}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="one Wave 18 drill step")
    parser.add_argument("--step", required=True, choices=STEPS)
    parser.add_argument("--world", required=True)
    parser.add_argument("--pin", required=True)
    parser.add_argument("--engine", required=True)
    parser.add_argument("--preflight", required=True)
    parser.add_argument("--repo", default="")
    args = parser.parse_args(argv)

    world = world_of(args)
    out = world / "out"
    out.mkdir(parents=True, exist_ok=True)
    try:
        report = DISPATCH[args.step](args)
    except SystemExit as exc:
        report = {"error": str(exc)}
        _emit(out, args.step, report)
        return 1
    except Exception as exc:  # noqa: BLE001 - a step failure is a result
        report = {"error": f"{type(exc).__name__}: {exc}"}
        _emit(out, args.step, report)
        return 1
    report = dict(report)
    report["step"] = args.step
    report["pid"] = os.getpid()
    report["engine"] = str(Path(args.engine))
    _emit(out, args.step, report)
    return 0


def report_line(report: dict) -> str:
    """The one stdout line, pure ASCII.

    A child's stdout encoding is the host's console codepage, not UTF-8. On
    Windows a `FileExistsError` carries a localized message, GBK encodes it on
    the way out, and a UTF-8 parent decodes it into replacement characters — the
    drill's own record of why an upgrade failed becomes unreadable while still
    looking like evidence. Escaping to ASCII makes the wire encoding irrelevant.
    """
    return json.dumps(report, sort_keys=True, ensure_ascii=True)


def _emit(out: Path, step: str, report: dict) -> None:
    # The file is UTF-8 by declaration, so it can keep the real characters.
    (out / f"{step}.json").write_text(
        json.dumps(report, sort_keys=True, ensure_ascii=False), encoding="utf-8")
    sys.stdout.write(report_line(report) + "\n")
    sys.stdout.flush()


if __name__ == "__main__":
    raise SystemExit(main())
