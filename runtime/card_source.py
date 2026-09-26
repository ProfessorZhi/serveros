#!/usr/bin/env python3
"""Gathering for the status card, as a layer that shapes and does not decide.

Why this file is not inside `presentation.py`
---------------------------------------------
`presentation.py` turns already-gathered facts into a model, and it takes no
measurement of its own. That split is what makes the model testable against a
fixture and the gathering testable against a stub. Rolling the two together
would mean every test of the card ran the host, and a card that only renders on
the host it was written on is not a renderer.

Why it exists at all
--------------------
`presentation.status_model` consumes a snapshot with `mine` / `host` / `gateway`
blocks, and nothing in the tree produced one. A model with no producer is a
schema, and a schema is one of the placeholder forms this round forbids: the
card would render, look right, and be fed by nothing but its own fixtures.

What "gathering" means here: `serverosctl` is asked what it already knows. Every
number below is read from a command that ships today — `resource summary` for
the host and for this account's share, `resource user-services` for the units
this account runs, `runtime list` for what is registered. No `/proc` walk, no
`docker ps`, no second measurement stack: the live Tokyo card already gathers
this way, and the reason to keep it that way is that a card which measures
things itself will eventually disagree with the commands it duplicates.

The one thing deliberately NOT gathered
---------------------------------------
The shared gateway. §2 forbids choosing it by container name or by `owner=ubuntu`,
and every convenient selector is one of those two. So there is no code here that
finds a gateway: a binding names it or the section renders UNBOUND. See
`presentation.gateway_section`.

What this module refuses to do
------------------------------
It computes no verdict about whether something *should* be running, and it
performs no operation on anything it reads. The state words it emits
(`OK`/`ATTENTION`/`UNKNOWN`) are comparisons against the thresholds the summary
command itself publishes — read from that payload, not restated here, so a
threshold changed in one place does not need changing in two.
"""
from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_REPO = _HERE.parent

SCHEMA = "serveros/card-snapshot.v1"

#: The binding document §2 asks for. `load_binding` returning None is what
#: makes the gateway block UNBOUND rather than filled from a lookalike, and
#: None is the honest answer on a node where nobody has authored one.
#:
#: WHERE IT LIVES, AND WHY NOT HERE. This comment used to say the proposed
#: file "ships as a reviewable artifact for approval". It did not, and it still
#: does not: no `service-bindings.yaml` exists anywhere in this tree. The
#: sentence described an intention as an artifact, which is the same defect as
#: a placeholder that renders — a reader goes looking for a file that was never
#: written. The instance contract gives bindings to the ops repository (§6
#: "ops 只配置 binding/recipient/cadence", §7 lists the values that must be
#: bound rather than guessed), so the artifact belongs there and nothing in
#: this repository writes one. `--gateway-binding PATH` is how an authored
#: binding reaches this module.
BINDING_SCHEMA = "serveros/service-bindings.v1"
DEFAULT_BINDING_PATH = Path.home() / ".serveros" / "service-bindings.yaml"

#: The reads, in the order they appear on the card. Named tuples rather than a
#: dict because the NAME reaches a person: when a read fails, the card says
#: which read failed, and "summary" is a word somebody can act on where
#: "command 2" is not.
DEFAULT_READS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("summary", ("resource", "summary")),
    ("top", ("resource", "top")),
    ("user_services", ("resource", "user-services")),
    ("runtime_list", ("runtime", "list")),
)

UNKNOWN = "UNKNOWN"
OK = "OK"
ATTENTION = "ATTENTION"
CRITICAL = "CRITICAL"


def _load(name: str, filename: str):
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, _HERE / filename)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


business_calendar = _load("serveros_runtime_business_calendar",
                          "business_calendar.py")

#: Where the instance's business-calendar declaration lives: the same
#: `~/.serveros/` the binding lives in, one directory for what this node
#: declares about itself. This is an ALIAS of `business_calendar.DEFAULT_PATH`
#: and not a second literal, so the two names cannot drift apart.
DEFAULT_CALENDAR_PATH = business_calendar.DEFAULT_PATH

card_day = _load("serveros_runtime_card_day", "card_day.py")

#: The day's own card, beside the delivery ledger under `~/.serveros/cards/`.
#: An ALIAS for the same reason as the calendar's: one fact, one literal.
DEFAULT_DAY_STORE_PATH = card_day.DEFAULT_PATH


# ---------------------------------------------------------------------------
# Reading
# ---------------------------------------------------------------------------

def _default_run(argv: tuple[str, ...]) -> tuple[dict | None, str | None]:
    """Ask `serverosctl` for one payload.

    The subprocess boundary is the seam the tests replace. It is a subprocess
    rather than an in-process call for the same reason the live Tokyo card does
    it that way: the command is the supported interface, and a card that
    imported the internals would break silently the day those change shape
    while the command kept its promise.
    """
    proc = subprocess.run([sys.executable, str(_REPO / "bin" / "serverosctl"),
                           *argv, "--json"],
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip().splitlines()
        return None, (detail[-1] if detail else f"exit {proc.returncode}")
    try:
        return json.loads(proc.stdout), None
    except json.JSONDecodeError as exc:
        return None, f"unparseable output: {exc}"


def gather(*, run=None, reads=DEFAULT_READS) -> dict:
    """Every read, plus a named gap for each one that did not arrive.

    A failed read is a gap and never an empty result. This is the same rule the
    observation envelope applies to a timestamp and the parity check applies to
    a surface: "nobody looked" and "looked, found nothing" produce the same
    blank in a payload and are different facts, and the card is where the
    difference is readable by a person.
    """
    run = run or _default_run
    payloads: dict[str, dict] = {}
    gaps: list[dict] = []
    for name, argv in reads:
        data, err = run(tuple(argv))
        if err:
            gaps.append({"read": name, "command": " ".join(argv), "why": err})
        else:
            payloads[name] = data
    return {"schema": SCHEMA, "reads": payloads, "gaps": gaps,
            "read_names": [n for n, _ in reads]}


# ---------------------------------------------------------------------------
# Shaping
# ---------------------------------------------------------------------------

def _bytes(value) -> str:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return "未读到"
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if abs(number) < 1024 or unit == "TiB":
            return f"{number:.0f} {unit}" if unit == "B" else f"{number:.1f} {unit}"
        number /= 1024
    return f"{number:.1f} TiB"


def _percent(value) -> str:
    try:
        return f"{float(value):.1f}%"
    except (TypeError, ValueError):
        return "未读到"


def _thresholds(summary: dict) -> dict:
    """The limits, from the payload that publishes them.

    Read rather than restated. A threshold that lives in two places is a
    threshold that will be changed in one of them, and the card is the surface
    where that is least likely to be noticed.
    """
    return ((summary.get("advisory") or {}).get("thresholds") or {})


def _filesystems(summary: dict) -> list[dict]:
    """One row per filesystem, not one per declared role.

    The summary reports roles, and several roles routinely resolve to the same
    filesystem — on this node `DOCKER_FS`, `STATE_FS` and `ARTIFACTS_FS` are all
    `/data` on `/dev/vdb`. Printed as they arrive, a person reads three disks
    where there is one, and the block gets long enough that they stop reading
    it. Merging loses nothing: the roles are kept and named in the same line,
    which is the part that says *why* each of them is watched.
    """
    merged: dict[tuple, dict] = {}
    for role in (summary.get("disk") or {}).get("roles") or []:
        if role.get("state") != "PRESENT":
            continue
        key = (role.get("mount_point"), role.get("source"))
        row = merged.get(key)
        if row is None:
            merged[key] = {**role, "roles": [role.get("role")]}
        else:
            row["roles"].append(role.get("role"))
            # The worst reading wins the row, so merging can never soften a
            # number that a person needs to see.
            if (role.get("used_percent") or 0) > (row.get("used_percent") or 0):
                row.update({k: role.get(k) for k in ("used_percent", "inodes",
                                                     "free_bytes", "used_bytes")})
    return list(merged.values())


def _disk_findings(summary: dict) -> list[dict]:
    """Every filesystem over a published threshold, worst first."""
    limits = _thresholds(summary)
    high = float(limits.get("filesystem_percent_high") or 85.0)
    inode_high = float(limits.get("inode_percent_high") or 85.0)
    found = []
    for role in _filesystems(summary):
        used = role.get("used_percent")
        inodes = (role.get("inodes") or {}).get("used_percent")
        if used is not None and float(used) >= 100.0:
            found.append({"mount": role.get("mount_point"), "kind": "full",
                          "value": used, "limit": high})
        elif used is not None and float(used) >= high:
            found.append({"mount": role.get("mount_point"), "kind": "high",
                          "value": used, "limit": high})
        if inodes is not None and float(inodes) >= inode_high:
            found.append({"mount": role.get("mount_point"), "kind": "inodes",
                          "value": inodes, "limit": inode_high})
    return sorted(found, key=lambda f: -float(f["value"]))


def _host_section(summary: dict | None, gaps: list[dict]) -> dict:
    if summary is None:
        return {"state": UNKNOWN, "lines": [],
                "why": "本机的 resource summary 没有到达，因此不声称这台机器自身的数字"}
    host = summary.get("host") or {}
    load = host.get("load") or {}
    memory = host.get("memory") or {}
    swap = memory.get("swap") or {}
    cpu = host.get("cpu") or {}
    limits = _thresholds(summary)
    lines = [
        f"CPU {cpu.get('logical_cpus') or '?'} 逻辑核"
        + (f" · 型号 {cpu.get('model')}" if cpu.get("model") else ""),
        f"负载 {load.get('load1')} / {load.get('load5')} / {load.get('load15')}"
        f" · 可运行 {load.get('runnable') or '未读到'}",
        f"内存 已用 {_bytes(memory.get('used_bytes'))} / {_bytes(memory.get('total_bytes'))}"
        f" · 可用 {_bytes(memory.get('available_bytes'))}"
        f"（缓存 {_bytes(memory.get('cached_bytes'))}）",
        f"交换区 已用 {_percent(swap.get('used_percent'))}"
        f"（{_bytes(swap.get('used_bytes'))} / {_bytes(swap.get('total_bytes'))}）"
        f" · 阈值 {limits.get('swap_percent_high')}%",
    ]
    for role in _filesystems(summary):
        lines.append(
            f"文件系统 {role['mount_point']}"
            f"（{' / '.join(role['roles'])}）"
            f" 已用 {_percent(role.get('used_percent'))}"
            f" · inode {_percent((role.get('inodes') or {}).get('used_percent'))}"
            f" · 可用 {_bytes(role.get('free_bytes'))}")
    docker = summary.get("docker") or {}
    if docker:
        lines.append(f"Docker {docker.get('ServerVersion')} · 容器 "
                     f"运行 {docker.get('ContainersRunning')} / "
                     f"停止 {docker.get('ContainersStopped')} / "
                     f"共 {docker.get('Containers')} · 镜像 {docker.get('Images')}")
    for note in (summary.get("advisory") or {}).get("notes") or []:
        lines.append(f"提醒 {note}")

    findings = _disk_findings(summary)
    swap_high = float(limits.get("swap_percent_high") or 90.0)
    swap_hot = (swap.get("used_percent") is not None
                and float(swap["used_percent"]) >= swap_high)
    if any(f["kind"] == "full" for f in findings):
        state = CRITICAL
    elif findings or swap_hot:
        state = ATTENTION
    else:
        state = OK
    why = ""
    if findings:
        why = "；".join(f"{f['mount']} 已用 {_percent(f['value'])}"
                        f"（阈值 {f['limit']}%）" for f in findings[:3])
    elif swap_hot:
        why = f"交换区已用 {_percent(swap.get('used_percent'))}（阈值 {swap_high}%）"
    return {"state": state, "lines": lines, "why": why,
            "detail": {"findings": findings, "reads": ["summary"]}}


def _mine_section(summary: dict | None, user_services: dict | None,
                  runtime_list: dict | None, gaps: list[dict]) -> dict:
    """This account's resources, selected by the uid the process already has.

    Not by a name. The live Tokyo card selects the shared gateway's database by
    matching an owner prefix against a home directory, and that selector is the
    reason §2 exists — so this block uses the identity the kernel already gave
    the process. `resource summary`'s `user` block is computed from this uid's
    processes; nothing here compares a string to "vince".
    """
    if summary is None:
        return {"state": UNKNOWN, "lines": [],
                "why": "本账户的 resource summary 没有到达，因此不声称本账户占用的份额"}
    user = summary.get("user") or {}
    share = user.get("summary") or {}
    quota = user.get("quota") or {}
    limits = _thresholds(summary)
    lines = [
        # The uid is named because "my resources" is only meaningful with the
        # identity it was computed for, and a card forwarded to a colleague
        # otherwise reads as theirs.
        f"账户 uid {os.getuid()}"
        + (f" · 进程数 {share.get('process_count')}" if share else ""),
        f"CPU 占整机 {_percent(share.get('cpu_percent_of_host'))}"
        f"（{share.get('cpu_cores')} 核等效 / 共 {share.get('logical_cpus')} 核）",
        f"内存 {_bytes(share.get('memory_rss_bytes'))}"
        f" · 占整机 {_percent(share.get('memory_percent_of_host'))}",
    ]
    if share.get("provenance"):
        lines.append(f"账户数字的来源 {share['provenance']}")
    if quota:
        lines.append(
            f"配额 {quota.get('mount')}（{quota.get('filesystem')}）"
            f" 已用 {_bytes(quota.get('used_bytes'))}"
            f" / 软限 {_bytes(quota.get('soft_limit_bytes'))}"
            f"（{_percent(quota.get('used_percent'))}）"
            f" · 硬限 {_bytes(quota.get('limit_bytes'))}")
    if user_services is not None:
        units = list(user_services.get("units") or [])
        active = [u for u in units
                  if str(u.get("active") or u.get("state") or "").startswith("active")]
        lines.append(f"本账户 systemd 用户单元 {len(units)} 个"
                     f"（其中 active {len(active)}）"
                     f" · linger {user_services.get('linger')}"
                     f" · supervisor {user_services.get('supervisor')}")
        # Active first. The list arrives alphabetically and the alphabet has no
        # opinion about what is running: on this node the first six units are
        # dbus, dirmngr, gpg-agent, keyboxd — all inactive — so a person reading
        # top-down sees six things that are off and stops before the ones that
        # are on.
        for unit in (active or [])[:8] if active else units[:8]:
            lines.append(f"  {unit.get('name') or unit.get('unit')}"
                         f" — {unit.get('active') or unit.get('state') or '未读到'}")
    else:
        lines.append("本账户的 systemd 用户单元未读到 —— 不是没有，是没人问")
    if runtime_list is not None:
        resources = runtime_list.get("resources") or []
        lines.append(f"本节点登记的受管运行时 {len(resources)} 个"
                     f"（登记 ≠ 正在运行，也 ≠ 已批准自动修复）")

    state = OK
    why = ""
    if quota and quota.get("hard_limit_bytes"):
        used = float(quota.get("used_bytes") or 0)
        hard = float(quota["hard_limit_bytes"])
        if hard and used / hard >= 0.95:
            state = ATTENTION
            why = f"本账户配额已用 {_percent(quota.get('used_percent'))}，接近硬限"
    if user_services is None or runtime_list is None:
        # A section with one unread part is not a section that is fine. The
        # lines above still show what was read; the state says the block is
        # short, which is what makes `env_health` mark the top line partial.
        state = UNKNOWN if state == OK else state
        why = why or "本板块有部分未读到：见下方逐条说明"
    return {"state": state, "lines": lines, "why": why,
            "detail": {"uid": os.getuid(),
                       "reads": [n for n, p in (("summary", summary),
                                                ("user_services", user_services),
                                                ("runtime_list", runtime_list))
                                 if p is not None]}}


def snapshot(collected: dict, *, at: str, binding: dict | None = None,
             node_label: str | None = None, calendar: dict | None = None,
             day: dict | None = None,
             reads_order=("summary", "top", "user_services", "runtime_list")) -> dict:
    """The three blocks, in the shape `presentation.status_model` consumes.

    `top` is gathered and carried through but not rendered into a line yet:
    it is the read that backs the per-account process question, and dropping it
    from the gather would mean re-adding a command to the card's read list later
    to answer a question the card already asked once. It appears in `detail` so
    that the number of reads the card made is inspectable.

    `calendar` is `load_calendar`'s READING rather than a declaration, so the
    three states survive as far as the model: §9's UNKNOWN has two causes and
    §10's window depends on which one it is. A reading that is not `READ` also
    becomes a collection gap — the same treatment every other unread thing on
    this card gets, for the same reason: a person reading `calendar=UNKNOWN`
    needs to know whether nobody imported a calendar or the one on disk does not
    validate, and a gap line is where this card already answers that kind of
    question.

    `day` is `load_day_store`'s READING — today's card and the hours it folded.
    A READING that says ABSENT renders the day's own sentence, because the
    caller asked and «今天还没有当天的卡» is the answer: it is the normal state
    of a workday before its first tick, and after the first tick it is the
    visible sign that nothing is advancing the day. Only INVALID becomes a
    collection gap, because that is the state somebody has to fix.

    It is treated differently from the calendar in one respect, and the
    difference is not an inconsistency: §9's acceptance criterion says the word
    `calendar=UNKNOWN` must appear on the diagnostic, so an unread calendar can
    never be silent AND is always a gap. No criterion says a day store's absence
    is a fault, and it is not one — an empty day is what a day looks like before
    it starts — so it speaks without a gap.

    A caller that supplies NO reading gets no schedule block and no day block,
    and this is the shape that keeps the function honest about where it reads:
    it shapes what it was handed and opens no file of its own. That distinction —
    «handed a reading that says absent» versus «handed nothing» — is the one the
    two branches below carry. A default here that went and read
    `~/.serveros/business-calendar.yaml` would make every caller's answer depend
    on the machine the caller happened to be on, which is the property that
    separates a renderer from a report about one host. The callers that do read
    it — `serverosctl card status` and this module's own CLI — pass the reading
    in, and `presentation._schedule` names the unread calendar case in words.
    """
    reads = collected.get("reads") or {}
    gaps = list(collected.get("gaps") or [])
    summary = reads.get("summary")
    schedule = None
    if calendar is not None:
        schedule = business_calendar.routine_window(at, reading=calendar)
        if calendar.get("state") != "READ":
            gaps.append({
                "read": "business_calendar",
                "command": calendar.get("path") or str(DEFAULT_CALENDAR_PATH),
                "why": (calendar.get("reason") or "工作日历未读到")
                       + (f"：{calendar['detail']}" if calendar.get("detail") else "")})
    day_card = None
    if day is not None:
        if day.get("state") == "INVALID":
            gaps.append({
                "read": "card_day_store",
                "command": day.get("path") or str(DEFAULT_DAY_STORE_PATH),
                "why": (day.get("reason") or "当天卡状态读不到")
                       + (f"：{day['detail']}" if day.get("detail") else "")})
        else:
            day_card = day.get("day")
    return {
        "schema": SCHEMA,
        "at": at,
        "node_label": node_label,
        "gaps": [f"{g['read']}（{' '.join(g['command'].split())}）未读到：{g['why']}"
                 for g in gaps],
        **({"schedule": schedule} if schedule else {}),
        **({"day": day_card} if day_card else {}),
        "mine": _mine_section(summary, reads.get("user_services"),
                              reads.get("runtime_list"), gaps),
        "host": _host_section(summary, gaps),
        # §2: present only when a binding names it. An absent key here is what
        # `presentation.gateway_section` reports as UNBOUND with a reason —
        # which is a different statement from "there is no gateway".
        **({"gateway": binding.get("gateway") or {}}
           if binding and binding.get("gateway") else {}),
        "coverage": {
            "reads_attempted": list(collected.get("read_names") or reads_order),
            "reads_answered": sorted(reads),
            "reads_missing": [g["read"] for g in gaps],
        },
    }


# ---------------------------------------------------------------------------
# The binding
# ---------------------------------------------------------------------------

def load_binding(path=None, *, device_ref: str | None = None) -> dict | None:
    """The service binding for this node's shared gateway, or None.

    None is a first-class answer. There is no fallback here and there must not
    be one: every fallback available — a container name, an owner's home
    directory, "the one with the most providers" — is a guess that renders as a
    fact, and §2 exists because that guess already shipped once.
    """
    target = Path(path) if path else DEFAULT_BINDING_PATH
    if not target.is_file():
        return None
    try:
        import yaml
        document = yaml.safe_load(target.read_text(encoding="utf-8")) or {}
    except Exception:  # noqa: BLE001 - an unreadable binding is not a binding
        return None
    if document.get("schema") != BINDING_SCHEMA:
        return None
    for entry in document.get("bindings") or []:
        if not isinstance(entry, dict):
            continue
        if device_ref and entry.get("device_ref") not in (None, device_ref):
            continue
        if entry.get("role") != "shared-ai-gateway":
            continue
        return entry
    return None


def load_calendar(path=None) -> dict:
    """The instance's business-calendar reading, never a declaration-or-None.

    Unlike `load_binding` this does NOT collapse to None, and the difference is
    deliberate rather than an inconsistency: a binding that is absent means the
    gateway block says UNBOUND, which is one statement, whereas a calendar that
    is absent means «this node cannot say whether today is a workday», which
    §9 names as its own state with its own consequence. Returning None here
    would throw away which of «not imported» and «did not validate» this node
    is in, and the window that depends on it.
    """
    return business_calendar.read_declaration(Path(path) if path else None)


def load_day_store(path=None) -> dict:
    """Today's card as a READING — never a raise, never a silent empty day.

    Three states, and the middle one is why this wrapper exists rather than the
    caller holding a `DayStore`: a store that is not on disk means today's card
    has not been created, which is an answer; a store that IS on disk and cannot
    be read is a fault somebody has to fix, and `DayStore` raises for it so that
    nothing quietly starts over. A renderer cannot afford the raise — it would
    turn an unreadable card memory into no card at all — so the fault becomes a
    state and the state becomes a collection gap.
    """
    target = Path(path) if path else DEFAULT_DAY_STORE_PATH
    try:
        store = card_day.DayStore(target)
    except card_day.CardDayError as exc:
        return {"state": "INVALID", "day": None, "path": str(target),
                "reason": "当天卡状态文件读不到", "detail": str(exc)}
    absent = not target.exists()
    return {"state": "ABSENT" if absent else "READ", "day": store.summary(),
            "path": str(target), "reason": "" if not absent else "今天还没有当天的卡",
            "detail": ""}


def main(argv: list[str] | None = None) -> int:  # pragma: no cover - hand check
    import argparse
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--gateway-binding", default="")
    parser.add_argument("--business-calendar", default="")
    parser.add_argument("--day-store", default="")
    parser.add_argument("--device-ref", default="")
    parser.add_argument("--out", default="")
    parser.add_argument("--at", default="")
    args = parser.parse_args(argv)
    collected = gather()
    snap = snapshot(collected, at=args.at, node_label=args.device_ref or None,
                    binding=load_binding(args.gateway_binding or None,
                                         device_ref=args.device_ref or None),
                    calendar=load_calendar(args.business_calendar or None),
                    day=load_day_store(args.day_store or None))
    text = json.dumps(snap, ensure_ascii=False, indent=2)
    if args.out:
        Path(args.out).write_text(text, encoding="utf-8")
    else:
        print(text)
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
