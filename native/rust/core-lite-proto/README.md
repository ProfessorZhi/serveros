# core-lite-proto — Rust benchmark prototype (not shipped)

Reads the same kernel files as `native/node-core`'s `serveros-node-resource-snapshot`
(two samples of /proc/stat, meminfo, vmstat, pressure, diskstats, net/dev, uptime,
boot_id, and statvfs per filesystem) and prints one JSON line. It exists only to put
C++ and Rust side by side on the same machine; its output is **not** the
`node-resource-snapshot.v1` contract and nothing installs or runs it.

Results and the order in which Fleet components move to Rust:
[docs/requirements/rust-evaluation-2026-11-13.md](../../../docs/requirements/rust-evaluation-2026-11-13.md).

```bash
cargo build --release --manifest-path native/rust/core-lite-proto/Cargo.toml
/usr/bin/time -f "%M KB %e s" native/rust/core-lite-proto/target/release/core-lite-proto --interval-ms 0
```
