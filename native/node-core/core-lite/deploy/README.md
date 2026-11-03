# Core-Lite deploy: publish the status file

`publish-status.sh` is the one missing hop between Core-Lite and Fleet: it runs
the collector (`serveros-node-resource-snapshot`) and the status builder
(`core-lite-status`) and atomically writes one owner-only status document.

```
collector ─► node-resource-snapshot.v1 ─► core-lite-status ─► status.json (0600)
                                                                  │
            Fleet node connector  /v1/core/status  ◄─ core_snapshot_path
            Fleet hub (local device)               ◄─ core_lite.snapshot_path
```

Install (owner's decision; observe only):

```bash
cmake -S native/node-core -B build && cmake --build build
BIN=$HOME/.local/lib/serveros-core-lite        # holds the two binaries + publish-status.sh
install -m 755 build/serveros-node-resource-snapshot build/core-lite/core-lite-status \
  native/node-core/core-lite/deploy/publish-status.sh "$BIN"/
PROFILE=native/node-core/core-lite/profiles/tokyo-vince.profile.json   # or dell-fjd
OUT=$XDG_STATE_HOME/serveros/core-lite/status.json
sed -e "s#@BIN@#$BIN#g" -e "s#@PROFILE@#$PROFILE#g" -e "s#@OUT@#$OUT#g" \
  native/node-core/core-lite/deploy/serveros-core-lite-publish.service \
  > ~/.config/systemd/user/serveros-core-lite-publish.service
cp native/node-core/core-lite/deploy/serveros-core-lite-publish.timer ~/.config/systemd/user/
systemctl --user daemon-reload && systemctl --user enable --now serveros-core-lite-publish.timer
```

Then point the reader at `$OUT`: on a node-connector device set
`core_snapshot_path` in the connector config; on the hub's own device set
`core_lite: {snapshot_path: ...}` in the Fleet binding. `fleet_status` and the
hub's resource cards then carry the readings; until then they say
`NOT_CONFIGURED`, never zero.

Windows uses `native/node-core/windows-core` (named pipe, SCM); it does not use
this script.
