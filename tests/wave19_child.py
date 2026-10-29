"""One process, one sidecar operation, optionally killed at a commit point.

Why a separate process and a real `os._exit`: a `try/except` in the same process
would run the module's own compensation, which is exactly the behaviour under
test. What a machine does is stop. `os._exit` stops without unwinding, so the
root keeps whatever the transaction had actually done — the state `recover()`
has to be able to judge from disk alone.

The kill point is counted on `os.replace` *into the root, outside the staging
tree*: that is `_commit`'s sequence (the journal is written the same way, so it
is counted first, which shifts every index by one — deliberately, because a kill
before the journal is written is a state the repair has to survive too).

Usage (all paths absolute; the parent supplies them):

    wave19_child.py install  --spec spec.json [--engine E] [--die-at N]
    wave19_child.py upgrade  --spec spec.json [--engine E] [--die-at N]
    wave19_child.py restore  --spec spec.json --previous prev.json
    wave19_child.py recover  --spec spec.json
    wave19_child.py revoke   --spec spec.json
    wave19_child.py sleep    --spec spec.json        (holds the lock, stays alive)

It prints one JSON object on stdout. `ensure_ascii=True`, because a child's
stdout encoding is the console codepage and a localized OS error would otherwise
arrive as replacement characters — readable-looking evidence that is not.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import sys
from pathlib import Path


def load_engine(path: Path, name: str = "sidecar_release"):
    """Load the module the way the suite does: one module object per file."""
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def read_spec(path: Path) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def descriptor_of(engine, spec: dict):
    files = tuple((path, digest) for path, digest in spec["files"])
    return engine.Descriptor(name=spec["name"], version=spec["version"],
                             commit=spec["commit"], files=files,
                             entrypoint=spec["entrypoint"])


def sources_of(spec: dict) -> dict[str, bytes]:
    return {path: bytes.fromhex(body) for path, body in spec["sources"].items()}


def grant_of(engine, spec: dict, action: str):
    return engine.Grant(grant_id=f"grant-{action}-wave19",
                        operator="operator:wave19-transactions",
                        action=action, device_ref=spec["device_ref"])


def arm_kill(root: Path, die_at: int, staging_name: str) -> None:
    """Make the `die_at`-th commit-time rename kill this process instead.

    The wrapper delegates every other call, so the earlier renames *happen*: the
    root under test is a real partial commit, not a simulation of one.
    """
    root_prefix = os.path.abspath(str(root)) + os.sep
    staging_prefix = root_prefix + staging_name + os.sep
    real = os.replace
    seen = {"n": 0}

    def counting(src, dst, *args, **kwargs):
        target = os.path.abspath(str(dst))
        if target.startswith(root_prefix) and not target.startswith(staging_prefix):
            seen["n"] += 1
            if seen["n"] == die_at:
                # No unwind, no flush of anything but this line: the process ends
                # exactly where the kill lands.
                sys.stdout.write(json.dumps({"killed_at": die_at}) + "\n")
                sys.stdout.flush()
                os._exit(86)
        return real(src, dst, *args, **kwargs)

    os.replace = counting


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("op", choices=("install", "upgrade", "restore", "recover",
                                       "revoke", "sleep"))
    parser.add_argument("--spec", required=True)
    parser.add_argument("--previous", default="")
    parser.add_argument("--engine", default="")
    parser.add_argument("--die-at", type=int, default=0)
    args = parser.parse_args(argv)

    spec = read_spec(Path(args.spec))
    engine = load_engine(Path(args.engine or spec["engine"]))
    root = Path(spec["root"])
    device, boot = spec["device_ref"], spec["boot_id"]

    if args.op == "sleep":
        engine._take_lock(root, "install", boot, spec["lock_token"])
        sys.stdout.write(json.dumps({"locked": True, "pid": os.getpid()}) + "\n")
        sys.stdout.flush()
        import time
        time.sleep(30)
        return 0

    if args.op == "recover":
        result = engine.recover(root, grant=grant_of(engine, spec, "recover"),
                                device_ref=device, boot_id=boot)
    elif args.op == "revoke":
        result = engine.revoke(descriptor_of(engine, spec), root,
                               grant=grant_of(engine, spec, "revoke"),
                               device_ref=device, boot_id=boot)
    elif args.op == "restore":
        previous = read_spec(Path(args.previous))
        result = engine.restore(descriptor_of(engine, previous), root,
                                grant=grant_of(engine, spec, "restore"),
                                device_ref=device, boot_id=boot)
    else:
        if args.die_at:
            arm_kill(root, args.die_at, engine.STAGING_DIR)
        desktop = descriptor_of(engine, spec)
        sources = sources_of(spec)
        kwargs = dict(grant=grant_of(engine, spec, "install"), device_ref=device,
                      boot_id=boot)
        if args.op == "upgrade":
            kwargs["replace"] = engine.read_receipt(root)
        result = engine.install(desktop, root, sources, **kwargs)

    payload = {"decision": result.decision, "code": result.code,
               "reasons": list(result.reasons), "pid": os.getpid()}
    for field in ("written", "deleted", "restored", "resumed", "removed"):
        if hasattr(result, field):
            payload[field] = list(getattr(result, field))
    sys.stdout.write(json.dumps(payload, ensure_ascii=True) + "\n")
    sys.stdout.flush()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
