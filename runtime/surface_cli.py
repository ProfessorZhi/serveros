#!/usr/bin/env python3
"""The managed entrypoint for the two surfaces — three modes, and one of them
will not run when the host is already in trouble.

    --preview            decide + render, print the component tree. Touches NO
                         store and sends NOTHING.
    --dry-run            run the whole round against a SANDBOX store with a
                         virtual sender: every receipt is synthetic, nothing
                         leaves the process.
    --canary-user-vince  the real one. Requires BOTH an explicit flag and the
                         preflight below, and delivers only to the owner's own
                         DM.

WHY CANARY HAS A PREFLIGHT THAT CAN SAY NO
------------------------------------------
A monitoring card is not worth making a loaded host worse, and a run that begins
under memory pressure is a run whose own receipts are least trustworthy. So the
canary refuses — visibly, with numbers — when load per CPU or swap use is past a
bound. `--dry-run` ignores the gate on purpose: it allocates almost nothing and
is exactly what one wants to do while the host is busy.

WHAT IT DOES NOT DO
-------------------
It does not touch `runtime/monitoring-policy.yaml`, does not enable, start or
reload any unit, does not linger, and does not create a resident daemon. The
timer templates it ships with are DISABLED files a human installs.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import sys
import tempfile
from pathlib import Path

_HERE = Path(__file__).resolve().parent


def _load(name: str, path: Path):
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


surface = _load("serveros_runtime_card_surface", _HERE / "card_surface.py")
runner_mod = _load("serveros_runtime_card_surface_runner",
                   _HERE / "card_surface_runner.py")
journal_mod = _load("serveros_runtime_snapshot_journal", _HERE / "snapshot_journal.py")

#: The bounds the canary preflight enforces. Deliberately conservative: this is
#: a SHARED host, and «busy but not on fire» is not a green light.
MAX_LOAD_PER_CPU = 2.0
MAX_SWAP_USED = 0.90


class PreflightRefused(RuntimeError):
    """The host is not in a state where a real send should be attempted."""


def load_gate(*, loadavg_path="/proc/loadavg", meminfo_path="/proc/meminfo",
              max_load_per_cpu=MAX_LOAD_PER_CPU, max_swap_used=MAX_SWAP_USED) -> dict:
    """Whether the host may take a real send. Returns the evidence either way."""
    facts = {"load_per_cpu": None, "swap_used": None, "ok": True, "why": ""}
    reasons: list[str] = []
    try:
        load_text = Path(loadavg_path).read_text(encoding="utf-8").split()
        one_minute = float(load_text[0])
        cpus = os.cpu_count() or 1
        facts["load_per_cpu"] = round(one_minute / cpus, 3)
        if facts["load_per_cpu"] > max_load_per_cpu:
            reasons.append(f"load/CPU {facts['load_per_cpu']} > {max_load_per_cpu}")
    except (OSError, ValueError, IndexError):
        facts["ok"] = False
        reasons.append("读不到 loadavg：无法证明主机有余量")
    try:
        meminfo = Path(meminfo_path).read_text(encoding="utf-8")
        total = free = None
        for line in meminfo.splitlines():
            if line.startswith("SwapTotal:"):
                total = int(line.split()[1])
            elif line.startswith("SwapFree:"):
                free = int(line.split()[1])
        if total and free is not None:
            facts["swap_used"] = round((total - free) / total, 3)
            if facts["swap_used"] > max_swap_used:
                reasons.append(f"swap 已用 {facts['swap_used']} > {max_swap_used}")
    except (OSError, ValueError):
        facts["ok"] = False
        reasons.append("读不到 meminfo")
    if reasons:
        facts["ok"] = False
        facts["why"] = "；".join(reasons) + "：主机余量不足，不在这里发真实消息"
    return facts


# ---------------------------------------------------------------------------
# the three modes
# ---------------------------------------------------------------------------

def _make_runner(*, store, sender, unknown_peers=True):
    source = runner_mod.local_model_source(
        unknown=((("device:work-fj", "FJPCOS 工作站"),
                  ("device:llm-lab-dell", "A6000 推理机")) if unknown_peers else ()))
    return runner_mod.ShadowRunner(
        store=store, sender=sender, model_source=source,
        period_source=lambda *, at: _current_period(at),
        recipient=_owner_or_placeholder(),
        test_mark="[灰度测试]")


def _owner_or_placeholder() -> str:
    """The verified owner recipient when it can be read; a placeholder otherwise.

    The placeholder exists only so `--preview`/`--dry-run` can render a card on a
    host whose config has moved; it is NOT a delivery target, and the canary mode
    never uses it.
    """
    try:
        live = _load("serveros_runtime_live_sender", _HERE / "live_sender.py")
        return live.owner_recipient()
    except Exception:
        return "user:ou_unresolved_placeholder"


def _current_period(at):
    calendar = _load("serveros_runtime_business_calendar", _HERE / "business_calendar.py")
    card_period = _load("serveros_runtime_card_period", _HERE / "card_period.py")
    try:
        reading = calendar.read_declaration()
        return card_period.period_at(
            at, declaration=reading.get("declaration") if isinstance(reading, dict) else None)
    except Exception:
        return {"period_ref": None, "in_period": False}


def _at(args) -> str:
    import datetime
    return args.at or datetime.datetime.now(datetime.timezone.utc).astimezone().isoformat()


def mode_preview(args) -> dict:
    """Read-only: decide and render, write nothing, send nothing."""
    with tempfile.TemporaryDirectory() as tmp:
        store = surface.SurfaceStore(Path(tmp) / "surfaces.json")
        runner = _make_runner(store=store, sender=runner_mod.PreviewSender())
        return runner.preview(at=_at(args))


def mode_dry_run(args) -> dict:
    """The whole round, on a SANDBOX store, with a virtual sender."""
    with tempfile.TemporaryDirectory() as tmp:
        store = surface.SurfaceStore(Path(tmp) / "surfaces.json")
        sender = runner_mod.RecordingSender(remote_id="om_sandbox")
        runner = _make_runner(store=store, sender=sender)
        trace = runner.tick(at=_at(args), events=_events(args))
        trace["sandbox"] = True
        trace["would_send"] = len(sender.calls)
        return trace


def mode_canary(args) -> dict:
    """The real one. Explicit flag + preflight + the verified owner recipient."""
    if not args.i_understand_this_sends:
        raise PreflightRefused(
            "--canary-user-vince also needs --i-understand-this-sends; a mode that "
            "delivers to a person is entered on purpose or not at all")
    gate = load_gate()
    if not gate["ok"]:
        return {"schema": "serveros/surface-cli.v1", "mode": "canary",
                "sent": False, "blocked": "PREFLIGHT", "gate": gate,
                "says": f"预检拒绝：{gate['why']}"}
    live = _load("serveros_runtime_live_sender", _HERE / "live_sender.py")
    recipient = live.owner_recipient()
    store = surface.SurfaceStore(args.state or surface.default_path())
    journal = journal_mod.Journal(journal_mod.journal_path(args.state or
                                                           surface.default_path()))
    lock = None
    if args.state:
        lock_mod = _load("serveros_runtime_surface_lock", _HERE / "surface_lock.py")
        lock = lock_mod.SurfaceLock(lock_mod.lock_path(args.state, surface="live"))
        if not lock.acquire():
            return {"schema": "serveros/surface-cli.v1", "mode": "canary",
                    "sent": False, "blocked": "BUSY", "gate": gate,
                    "says": "另一个进程正持有 LIVE 的独占锁：本次不发送"}
    try:
        sender = live.LiveSender(enabled=True, recipient=recipient)
        runner = _make_runner(store=store, sender=sender)
        trace = runner.tick(at=_at(args), events=_events(args))
        trace["gate"] = gate
        return trace
    finally:
        if lock is not None:
            lock.release()


def _events(args) -> list[dict]:
    """Synthetic events for a hand check. A REAL event source is a separate step;
    this never invents an event identity from nothing but a count."""
    import json
    if not args.events:
        return []
    try:
        parsed = json.loads(args.events)
    except ValueError:
        raise SystemExit("--events must be a JSON array of {kind, episode, version}")
    return parsed if isinstance(parsed, list) else []


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--preview", action="store_true")
    mode.add_argument("--dry-run", action="store_true")
    mode.add_argument("--canary-user-vince", action="store_true")
    parser.add_argument("--i-understand-this-sends", action="store_true")
    parser.add_argument("--at", default="")
    parser.add_argument("--events", default="")
    parser.add_argument("--state", default="")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    if args.canary_user_vince:
        try:
            result = mode_canary(args)
        except PreflightRefused as exc:
            print(f"refused: {exc}", file=sys.stderr)
            return 2
    elif args.dry_run:
        result = mode_dry_run(args)
    else:
        result = mode_preview(args)

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    else:
        print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
