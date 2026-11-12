#!/usr/bin/env python3
"""Measure this device's network paths and write one small JSON file.

    probe_network.py --out FILE --target NAME=HOST:PORT [--target ...]
                     [--samples 4] [--timeout 2.0]

For each target: SAMPLES TCP connects (no ICMP, so no privilege is needed),
the median connect time of those that succeeded and the share that failed.
The file is what `publish-status.sh --network FILE` folds into the Core-Lite
document as the `network.readonly` observation.

Only the path NAME leaves this machine: hosts and ports stay in the command
line of the unit that runs this, never in the file. Names are lower-case
words (`tokyo-public`, `tokyo-tailnet`, `local`), which is all the card shows.
Observe only: it opens and closes TCP connections and sends nothing on them.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import socket
import statistics
import sys
import tempfile
import time
from datetime import datetime, timezone

NAME = re.compile(r"[a-z0-9][a-z0-9-]{0,23}\Z")
MAX_TARGETS = 8


def parse_target(spec: str) -> tuple[str, str, int]:
    name, sep, address = spec.partition("=")
    host, colon, port = address.rpartition(":")
    if not sep or not colon or not NAME.match(name) or not host or not port.isdigit() \
            or not 0 < int(port) < 65536:
        raise argparse.ArgumentTypeError(f"not NAME=HOST:PORT with a lower-case name: {spec!r}")
    return name, host.strip("[]"), int(port)


def measure(host: str, port: int, samples: int, timeout: float,
            connect=socket.create_connection, clock=time.perf_counter) -> dict:
    times = []
    for _ in range(samples):
        started = clock()
        try:
            connect((host, port), timeout=timeout).close()
        except OSError:
            continue
        times.append((clock() - started) * 1000.0)
    loss = round(100.0 * (samples - len(times)) / samples, 1)
    return {"rtt_ms": round(statistics.median(times), 1) if times else None,
            "loss_percent": loss, "samples": samples}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0], allow_abbrev=False)
    parser.add_argument("--out", required=True)
    parser.add_argument("--target", action="append", type=parse_target, required=True)
    parser.add_argument("--samples", type=int, default=4)
    parser.add_argument("--timeout", type=float, default=2.0)
    args = parser.parse_args(argv)
    if not os.path.isabs(args.out):
        parser.error("--out must be absolute")
    if not 1 <= args.samples <= 20 or len(args.target) > MAX_TARGETS:
        parser.error(f"1-20 samples and at most {MAX_TARGETS} targets")
    if len({name for name, _, _ in args.target}) != len(args.target):
        parser.error("target names must differ")
    paths = [{"name": name, **measure(host, port, args.samples, args.timeout)}
             for name, host, port in args.target]
    doc = {"paths": paths,
           "sample_time": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")}
    directory = os.path.dirname(args.out)
    os.makedirs(directory, mode=0o700, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=directory, prefix=".network.")
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        json.dump(doc, handle)
    os.chmod(tmp, 0o600)
    os.replace(tmp, args.out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
