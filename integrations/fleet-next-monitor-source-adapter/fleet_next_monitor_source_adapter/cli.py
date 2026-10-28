"""One bounded, fully offline tick: a JSON document in, a JSON observation out.

This executable exists so the adapter can be exercised without a service, a
credential or a scheduler. It reads a file, builds the injected fixture client,
performs at most one read and exits. It never imports anything the input names —
the input names no module at all.

Exit codes are a contract:

    0   an observation was produced (its `status` says what was established)
    2   refused: bad input, an ungranted view/device/session, or an import shape
    3   blocked upstream: no readable fixture document at the path
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .adapter import FixtureLegacyClient, LegacyReadOnlySourceAdapter
from .contract import (
    VIEW_CI,
    VIEW_RUN,
    VIEW_SESSION,
    SourceRefused,
)
from .serializer import MAX_INPUT_BYTES, dump_observation, dump_refusal, parse_envelope

EXIT_OK = 0
EXIT_REFUSED = 2
EXIT_BLOCKED_UPSTREAM = 3

DEFAULT_MAX_READINGS = 500


def _require(request: dict, *names: str) -> tuple[str, ...]:
    missing = [name for name in names if not request.get(name)]
    if missing:
        raise SourceRefused("BAD_REQUEST",
                            f"view {request.get('view')!r} requires {missing}")
    return tuple(request[name] for name in names)


def run_text(text: str) -> tuple[int, str]:
    """The whole tick, as a pure function of the input document."""
    parsed = parse_envelope(text)
    instance = parsed["instance"]
    max_readings = instance.get("maxReadings", DEFAULT_MAX_READINGS)
    if isinstance(max_readings, bool) or not isinstance(max_readings, int) or max_readings <= 0:
        raise SourceRefused("BAD_BUDGET", "`maxReadings` must be a positive integer")
    client = FixtureLegacyClient(
        parsed["fixture"],
        fleet_instance_id=str(instance.get("instanceId") or "fixture-instance"),
        route_id=str(instance.get("routeId") or "fixture-route"),
        boot_id=instance.get("bootId", "fixture-boot"),
        provider=instance.get("provider"),
    )
    adapter = LegacyReadOnlySourceAdapter(client, parsed["grant"],
                                          max_readings=max_readings)
    request = parsed["request"]
    view = request["view"]
    if view == VIEW_SESSION:
        device_ref, session_ref = _require(request, "device_ref", "session_ref")
        observation = adapter.observe_session(device_ref=device_ref,
                                              session_ref=session_ref,
                                              project=request.get("project"))
    elif view == VIEW_RUN:
        device_ref, session_ref = _require(request, "device_ref", "session_ref")
        observation = adapter.observe_run(device_ref=device_ref,
                                          session_ref=session_ref,
                                          run_id=request.get("run_id"),
                                          delivery_id=request.get("delivery_id"),
                                          project=request.get("project"),
                                          source_sha=request.get("source_sha"))
    elif view == VIEW_CI:
        source_sha, project = _require(request, "source_sha", "project")
        observation = adapter.observe_ci(source_sha=source_sha, project=project)
    else:  # parse_request already restricts this; kept as a second, local guard
        raise SourceRefused("BAD_REQUEST", f"unknown view {view!r}")
    return EXIT_OK, dump_observation(observation)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="fleet-next-monitor-source-adapter",
        description="One bounded, read-only Legacy Fleet source read. Offline.")
    parser.add_argument("--input", required=True,
                        help="path to the JSON input document")
    args = parser.parse_args(argv)

    path = Path(args.input)
    try:
        raw = path.read_bytes()
    except OSError as exc:
        print(dump_refusal(SourceRefused(
            "BLOCKED_UPSTREAM",
            f"input document {path} is not readable ({type(exc).__name__})")),
            file=sys.stderr)
        return EXIT_BLOCKED_UPSTREAM
    if len(raw) > MAX_INPUT_BYTES:
        print(dump_refusal(SourceRefused(
            "INPUT_TOO_LARGE", f"{path} exceeds {MAX_INPUT_BYTES} bytes")),
            file=sys.stderr)
        return EXIT_REFUSED
    try:
        code, output = run_text(raw.decode("utf-8"))
    except UnicodeDecodeError:
        print(dump_refusal(SourceRefused("BAD_JSON", "input is not UTF-8")),
              file=sys.stderr)
        return EXIT_REFUSED
    except SourceRefused as exc:
        print(dump_refusal(exc), file=sys.stderr)
        return EXIT_BLOCKED_UPSTREAM if exc.code == "BLOCKED_UPSTREAM" else EXIT_REFUSED
    print(output)
    return code


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
