---
name: fleet-acceptance
description: One-click acceptance after an update — when the owner says 验收 / 一键验证 / 测一下更新, check by yourself that the update landed everywhere and that every Fleet function works, then dispatch one tiny read-only task per device as a batch whose Feishu card is the last check. Read-only except that batch; never waits; one table out.
---

# 一键验收: did the update land, and does everything work?

## At a glance

- **Use when:** the owner asks to 验收 / 一键验证 / 测一下 after the local agent updated devices and the owner uploaded the plugin zip.
- **Input:** nothing. Do not ask which device, model or task — the checks below are fixed.
- **Steps:** (1) update landed; (2) functions, read-only; (3) one acceptance batch; (4) one table, then end the turn.
- **Done when:** the table is out and the batch is open; the Feishu card closes the last row.
- **Never:** change a setting, tune, mute, delete, wait for a run, poll, retry a failing tool under another name, or read a secret value.

## 1. Did the update land? (same as `fleet-self-check`)

`fleet_status`:

- `release.plugin_version` — the plugin the hub ships. Say it plainly: "hub 是 1.x.y；你上传的压缩包应是同一版本".
- `surface.served_tool_names` vs the Fleet tools you can call → same set = OK;
  server has more = **STALE**: "ChatGPT 设置 → 应用与连接器 → ServerOS Fleet → 刷新，再新开对话", and stop here.

`fleet_device_versions`: every row on `release.serveros_commit` (or one
`distinct_serveros_commits`) = OK; otherwise name each device behind and give
the local agent's command — Linux
`python3 distribution/device_kit.py update --device <name> --ability-rev main --apply`,
Windows `distribution/windows/update.ps1 -Device <name> -Apply`.

## 2. Does every function work? (read-only)

Call each once. A refusal or error is that row's result — record it and go on.

| row | call | OK when |
|---|---|---|
| 设备在线 | `fleet_list_devices` | every company device reachable |
| 资源读数 | `fleet_status` → `core_lite` | each device's reading is from the last 3 minutes |
| 监控/播报 | `fleet_monitor_status` | heartbeat under 2 minutes, broadcast not paused (list any mutes) |
| 模型库 | `fleet_models_list` per device that has one | the policy default model is listed and allowed |
| 密钥中心 | same answer → `variables` | `state: SYNCED`; every `envref` an entry needs is present (names only) |
| 事件收件箱 | `fleet_inbox` limit 5 | readable |
| 自调参记录 | `fleet_tune` action `history` | readable (do NOT `set` or `revert`) |
| 需求看板 | `fleet_work_board` | readable |

## 3. The acceptance batch (the only write)

For every reachable device, `fleet_create_session` (no `model`, no project
unless one is required — then the device's usual acceptance project) with
this first message, exactly:

    只读验收：第一行用一句话写结论（主机名、运行时长、可用内存），然后输出 hostname、uptime、free -h 的结果。不要修改任何东西。

Then `fleet_batch` action `open`, name `验收 <release.plugin_version> <MM-DD HH:MM>`,
with every `session_ref` and `run_id` as returned. Do not wait for them.

## 4. Report, then end the turn

One table, one line per row above plus the two update rows, each `✅` / `❌ <what>` /
`⚠️ <what>`; then one line for the batch:

    验收批次已派发：<n> 台设备。飞书会收到「验收 … · 全部完成」卡片，卡上每台设备一行结论——那张卡是最后一项。

Then a verdict: **全部通过（待飞书卡片）** or **未通过 — 下一步：<谁做什么>**.
Nothing else: no background, no repeated numbers, no tables of IDs. If the
owner later asks for the result, read `fleet_batch` status once.
