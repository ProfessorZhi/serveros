# serveros-eventd — device event push (D-4)

The first Rust component of Fleet ([rust-evaluation](../../../docs/requirements/rust-evaluation-2026-11-13.md) §3, item 1).

```
CloudCLI `changes` (loopback, 1/s) ──▶ serveros-eventd ──▶ ~/.local/state/serveros/events.jsonl
                                                               │  (+ .state.json cursor, .status.json liveness)
node connector  GET /v1/events?after=N&wait=20  ◀──────────────┘  signed, long-poll
       ▲
Tokyo hub: one long-poll thread per device (fleet_hub_watch/events.py) → the run is
observed and its card sent at once; the 30 s tick stays as the safety net.
```

- **Emits** `run.started`, `run.ended` (with CloudCLI's status word) and `interaction.waiting`
  — ids, kinds, states and times only; never message text.
- **Loopback only**: any other endpoint is refused at start (`ENDPOINT_NOT_LOOPBACK_HTTP`).
- **The credential** is read from its file for each (re)initialise and only ever sent in the
  `Authorization` header; it is never printed, logged or written.
- **Bounded**: the spool is trimmed to its newest 4 000 lines past 4 MB; the dedupe window
  holds 4 000 keys; on repeated failure the poll backs off up to 30 s.
- **Measured** (2026-11-19, 1 poll/s): CPU 0.07 %, RSS 2.3 MB.

Install / update / check on a device (user scope only — no sudo, no other account):

```bash
python3 distribution/eventd/install.py            # plan
python3 distribution/eventd/install.py --apply    # install from the fleet-latest release, start, verify
python3 distribution/eventd/install.py --check    # version, liveness, last events
```

Build and test here: `cargo test --release && cargo build --release`.
