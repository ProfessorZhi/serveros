# Directory governance (R-1..R-3)

Every device has a layout, `layouts/<device>.json`, and one tool, `ws.py`.

| class | what | what the tool does |
|---|---|---|
| managed | `work/` and its zones: `src/<repo>` (one main clone per repo), `worktrees/<repo>/<item>`, `scratch`, plus the device's role zones | moves old directories in; reclaims by the zone's rules |
| artifact | `work/artifacts/<project>/<item>/<run>` (FJPCOS also `client-tests`) | kept for the zone's days after the item closes |
| registered | personal projects, application data, caches, hidden directories | listed only; never moved or removed |
| protected | production services and data (A6000 `/srv/llm-lab`) | never touched |
| legacy | old directories, each named by a rule that says what it is | migrated into `work/` on an approved plan |

```bash
python3 distribution/workspace/ws.py scan  --device tokyo --out scan.json      # read only
python3 distribution/workspace/ws.py plan  --device tokyo --scan scan.json --out plan.json
python3 distribution/workspace/ws.py apply --device tokyo --plan plan.json --approve <plan_id>            # dry run
python3 distribution/workspace/ws.py apply --device tokyo --plan plan.json --approve <plan_id> --apply    # do it
python3 distribution/workspace/ws.py check --device tokyo                      # the daily summary
```

The plan, step by step, with its reason and its undo:

- **MOVE_REPO** one main clone per remote to `work/src/<repo>` (the one the worktrees hang from, else the most recently worked); a second clone with work of its own moves beside it as `<repo>.<old name>`.
- **REMOVE_CLONE** a second clone holding nothing that is not on a remote (no uncommitted change, no stash, no unpushed commit, no worktree).
- **MOVE_WORKTREE** with `git worktree move` to `work/worktrees/<repo>/<item id or name>`; **REMOVE_WORKTREE** with `git worktree remove` when idle past the zone's limit and nothing in it is unique (its branch stays).
- **MOVE_DIR / REMOVE_DIR** scratch and named directories; scratch is removed only past its limit and when no checkout inside holds anything unique.
- **DEFER** anything a service unit, scheduled task, cron line or running process refers to: change that first.
- **REPORT** what a rule asks to look at first, and anything the layout does not know.

`apply` does nothing without the plan's id and `--apply`. It re-checks every step
against the disk (a checkout that gained work since the plan is skipped), never
crosses a filesystem, leaves a link (symlink / junction) at the old path after a
move, repairs a moved repository's worktree links, and writes a receipt to
`work/.governance/receipts/`.

## Daily check → journal → weekly card

Each device runs the check once a day with `--record`, which writes
`workspace-check.json` beside the device's versions record (Linux
`~/.local/state/serveros/`, Windows `%LOCALAPPDATA%\ServerOS\state\`). The node
connector serves it with the versions record, and the hub writes a page per day
to the journal (`workspace/<yyyy>/<mm>/<dd>.md`, `workspace/history.jsonl`), then
sends 「目录治理周报」 on Mondays. It is read-only end to end.

```bash
# Tokyo / A6000 (user units; replace the device name)
cp distribution/workspace/timers/serveros-ws-check.{service,timer} ~/.config/systemd/user/
sed -i 's/@DEVICE@/a6000/' ~/.config/systemd/user/serveros-ws-check.service
systemctl --user daemon-reload && systemctl --user enable --now serveros-ws-check.timer
```

```powershell
# FJPCOS
powershell -ExecutionPolicy Bypass -File distribution\workspace\timers\register-ws-check.ps1 -Repo D:\work\src\serveros
```

The unit runs from `~/work/src/serveros`; edit `WorkingDirectory` if the checkout
is still elsewhere before migration.
