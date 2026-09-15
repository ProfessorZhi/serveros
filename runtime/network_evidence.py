#!/usr/bin/env python3
"""What this host's network topology is, as a record.

The producer half of `network-contract.yaml`'s `evidence` block. It reads and
it decides nothing: every function here returns rows or a named reason it could
not, and `network_policy.py` is the only thing that turns the rows into a
verdict.

Why this is not the monitor
---------------------------
`ops/scripts/monitor/checks.py` reads the same four things and answers a
different question — "is this host already wrong" — on a timer, for a person.
This answers "may this subnet be created", on the critical path of an action,
for the executor. The facts are shared; the conclusion and the failure
direction are not.

Three reads the product did not have
------------------------------------
`docker network ls`/`inspect` were already reachable through `LiveHost`, and
`ip` was not reachable at all. Both are reads and both go through the same
chokepoint (`probes.LiveHost._run`) as every other host measurement, so the
number of places this product can reach the host did not change — which is what
`actuator-contract.yaml` checks.

What "could not read" means here
--------------------------------
Every reader returns `None` and an entry in `unknown` rather than an empty
list. The difference is the whole point: an empty list is a claim that the host
has no docker networks, and a gate that believed it would authorize creating a
duplicate of one.
"""
from __future__ import annotations

import argparse
import importlib.util
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent


# This module is loaded by path (`cli.py` and `network_mutation.py` both do it),
# so a relative import is not available. Same loader, same reason, as
# `probes.py` and `resource_policy.py`.
def _load(name: str, filename: str):
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, _HERE / filename)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


# Devices that belong to docker and are therefore not "the host's own
# addresses". `docker0` and `docker_gwbridge` are created by the daemon rather
# than by a `docker network create`, so no network object names them.
DOCKER_FIXED_DEVICES = ("docker0", "docker_gwbridge")

# Interface address scopes that are NOT this host answering on a range.
#
# `host` is loopback: 127.0.0.1/8 is not an address a docker network could
# usefully shadow. `link` is the per-interface neighbourhood — `fe80::/64`
# exists on every bridge docker creates, so counting it would make every IPv6
# candidate "collide with the host" and the gate would refuse everything.
# `global` is the only scope that means what this source is for.
GLOBAL_SCOPE_ONLY = ("global",)

# Route types that are not destinations. `local` is this host's own address in
# the local table, `multicast` and `broadcast` are group and directed-broadcast
# entries, and all three restate something already known from the interface
# addresses — counting them would triple every finding.
NON_DESTINATION_ROUTE_TYPES = ("local", "multicast", "broadcast")


def _reason(exc: Exception) -> str:
    return f"{type(exc).__name__}: {exc}"


# ---------------------------------------------------------------------------
# Docker networks
# ---------------------------------------------------------------------------

def bridge_device(network: dict) -> str | None:
    """The host device a docker network is on, or None.

    The bridge is named by an option when the network asked for one and is
    `br-<first 12 of the id>` otherwise. Twelve and not six: the daemon
    truncates to twelve, and a name derived from the first six matches no
    device on any host — which reads as "every bridge is down" rather than as a
    bug, and was shipped once already in this project's monitor.
    """
    options = network.get("Options") or {}
    named = options.get("com.docker.network.bridge.name")
    if named:
        return named
    if network.get("Driver") == "bridge" and network.get("Id"):
        return f"br-{network['Id'][:12]}"
    return None


def docker_rows(host) -> tuple[list[dict] | None, list[str]]:
    """One row per (network, subnet). `None` if the listing could not be made.

    One row per SUBNET rather than per network because a network may carry an
    IPv4 and an IPv6 range, and the overlap question is asked of a range. A
    network whose inspect fails makes the whole list `None`: a partial list
    would let the gate conclude "nothing overlaps" from a set it knows is
    missing a member.
    """
    try:
        listing = host.docker_network_ls()
    except Exception as exc:  # noqa: BLE001 - ProbeUnavailable and anything else
        return None, [f"docker network ls could not be read — {_reason(exc)}"]

    rows: list[dict] = []
    for entry in listing:
        name = entry.get("Name")
        if not name:
            continue
        try:
            detail = host.docker_network_inspect(name)
        except Exception as exc:  # noqa: BLE001
            return None, [f"docker network inspect {name} could not be read — "
                          f"{_reason(exc)}"]
        configs = ((detail.get("IPAM") or {}).get("Config")) or []
        subnets = [c for c in configs if c.get("Subnet")] or [None]
        for config in subnets:
            rows.append({
                "name": detail.get("Name") or name,
                "id": (detail.get("Id") or entry.get("ID") or "")[:12] or None,
                "driver": detail.get("Driver"),
                "scope": detail.get("Scope"),
                "internal": bool(detail.get("Internal")),
                "subnet": (config or {}).get("Subnet"),
                "gateway": (config or {}).get("Gateway"),
                "dev": bridge_device(detail),
                "containers": len(detail.get("Containers") or {}),
            })
    return rows, []


def docker_devices(rows: list[dict] | None) -> set[str]:
    return set(DOCKER_FIXED_DEVICES) | {
        row["dev"] for row in (rows or []) if row.get("dev")}


# ---------------------------------------------------------------------------
# Host interfaces and routes
# ---------------------------------------------------------------------------

def iface_rows(host, *, exclude_devs: set[str]) -> tuple[list[dict] | None, list[str]]:
    """Global-scope addresses this host answers on, docker's own excluded.

    The exclusion is load-bearing rather than tidy. Every docker bridge holds
    the gateway address of its own subnet (`172.20.0.1/16` on
    `br-9ed5e74e097f`), so leaving them in would make every existing docker
    subnet collide with "a host interface" — a gate that refuses every request
    with a true-sounding reason, which is indistinguishable from one that
    works.
    """
    try:
        rows = host.interface_cidrs()
    except Exception as exc:  # noqa: BLE001
        return None, [f"ip addr could not be read — {_reason(exc)}"]

    out: list[dict] = []
    for iface in rows:
        dev = iface.get("ifname")
        if not dev or dev == "lo" or dev in exclude_devs:
            continue
        for addr in iface.get("addr_info") or []:
            if addr.get("family") not in ("inet", "inet6"):
                continue
            if addr.get("scope") not in GLOBAL_SCOPE_ONLY:
                continue
            prefix = addr.get("prefixlen")
            local = addr.get("local")
            if not local or prefix is None:
                continue
            out.append({"dev": dev, "cidr": f"{local}/{prefix}",
                        "family": addr["family"]})
    return out, []


def route_rows(host) -> tuple[list[dict] | None, list[str]]:
    """Explicit destinations, filtered to the ones a subnet could shadow.

    Filtered the way the monitor filters it, plus two more the monitor does not
    need: the `local`, `multicast` and `broadcast` tables restate addresses
    this module already read from the interfaces.
    """
    try:
        rows = host.route_table()
    except Exception as exc:  # noqa: BLE001
        return None, [f"ip route could not be read — {_reason(exc)}"]

    out: list[dict] = []
    for route in rows:
        dst = route.get("dst")
        if not dst or dst == "default":
            continue
        if route.get("type") in NON_DESTINATION_ROUTE_TYPES:
            continue
        # `protocol: kernel` + `scope: link` is the on-link neighbourhood the
        # kernel derived from an interface address. It is not a destination
        # anybody configured, and the monitor excludes it for the same reason.
        if route.get("protocol") == "kernel" and route.get("scope") == "link":
            continue
        out.append({
            "dst": dst,
            "dev": route.get("dev"),
            "gateway": route.get("gateway"),
            "protocol": route.get("protocol"),
            "scope": route.get("scope"),
            "metric": route.get("metric"),
            "type": route.get("type"),
            "linkdown": "linkdown" in (route.get("flags") or []),
        })
    return out, []


# ---------------------------------------------------------------------------
# The route decision probe
# ---------------------------------------------------------------------------

def probe(host, addresses) -> dict:
    """`ip route get` for each address. Never raises; failures are recorded.

    A failed probe is `{"ok": False, "why": ...}` and NOT a missing key, so a
    caller cannot tell "we did not ask" from "we asked and it failed" — both
    deny, and the record says which happened.
    """
    out: dict = {}
    for address in addresses:
        try:
            row = host.route_get(address)
        except Exception as exc:  # noqa: BLE001
            out[address] = {"ok": False, "why": _reason(exc)}
            continue
        out[address] = {"ok": True, **row}
    return out


# ---------------------------------------------------------------------------
# The record
# ---------------------------------------------------------------------------

def sources(host) -> dict:
    """Everything but the probes. Safe to call with no candidate in mind."""
    subnets, unknown = docker_rows(host)
    ifaces, unknown_i = iface_rows(host, exclude_devs=docker_devices(subnets))
    routes, unknown_r = route_rows(host)
    return {
        "docker_subnets": subnets,
        "host_iface_cidrs": ifaces,
        "host_routes": routes,
        "route_decisions": {},
        "unknown": unknown + unknown_i + unknown_r,
    }


def collect(host, *, targets=None) -> dict:
    """Sources plus a route decision for each address in `targets`."""
    evidence = sources(host)
    if targets:
        evidence["route_decisions"] = probe(host, list(targets))
    return evidence


def as_text(evidence: dict) -> str:
    """A few lines for a person, in the shape the monitor's report uses."""
    def count(key):
        value = evidence.get(key)
        return "读不到" if value is None else str(len(value))

    lines = [f"docker 网段 {count('docker_subnets')}",
             f"主机接口（global，排除 docker 自己的网桥）{count('host_iface_cidrs')}",
             f"显式路由 {count('host_routes')}"]
    for row in evidence.get("host_iface_cidrs") or []:
        lines.append(f"  {row['cidr']}  {row['dev']}")
    for row in evidence.get("host_routes") or []:
        via = f"via {row['gateway']}" if row.get("gateway") else "on-link"
        lines.append(f"  {row['dst']}  {via} dev {row['dev']}"
                     + ("  (link down)" if row.get("linkdown") else ""))
    for reason in evidence.get("unknown") or []:
        lines.append(f"  ? {reason}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--targets", nargs="*", default=[],
                        help="addresses to ask the kernel about")
    args = parser.parse_args(argv)

    probes_mod = _load("serveros_runtime_probes", "probes.py")
    evidence = collect(probes_mod.LiveHost(), targets=args.targets)
    print(as_text(evidence))
    for address, row in evidence["route_decisions"].items():
        if row.get("ok"):
            print(f"  {address} → dev {row.get('dev')}"
                  + (f" via {row['via']}" if row.get("via") else ""))
        else:
            print(f"  {address} → 没问到（{row.get('why')}）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
