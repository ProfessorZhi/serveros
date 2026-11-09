---
name: fleet-self-check
description: Before testing or after any plugin or device update, check by yourself that this client, the Fleet server and every device run the same release — and say exactly who must do what if not (refresh this app, or have the local agent update a named device). Never guess; read fleet_status and fleet_device_versions.
---

# Check the fleet is on one release before you rely on it

## At a glance

- **Use when:** a new plugin version was installed, a device was updated, a tool you expect is missing, or before any acceptance test.
- **Input:** nothing; two read-only calls.
- **Steps:** compare tools → compare devices → check the key center → report one verdict with the owner's next action.
- **Done when:** you have said READY, or NOT READY with who does what.
- **Never:** update anything yourself; retry a missing tool under another name; read or ask for a secret value.

## 1. Tools: does this client see what the server serves?

Call `fleet_status`. In `surface` read `tool_surface_revision`, `served_tools`
and `served_tool_names`. Compare `served_tool_names` with the Fleet tools you
can actually call.

- Same set → tools OK.
- The server serves tools you do not have → this client holds a **stale tool
  list**. Say: "请在 ChatGPT 设置 → 应用与连接器 → ServerOS Fleet 里点刷新，然后新开对话". Do not continue a test that needs a missing tool.
- You have tools the server does not serve → the server is behind; the local
  agent must update the hub (`device_kit.py update --device tokyo …`).

## 2. Devices: is every machine on the same commit?

Call `fleet_device_versions`. Read `distinct_serveros_commits` and each row.

- One commit, every row `RECORDED` → devices OK.
- More than one commit → name each device on an older one and give the local
  agent the exact command for it: Linux `python3 distribution/device_kit.py update --device <name> --ability-rev main --apply`, Windows `distribution/windows/update.ps1 -Device <name> -Apply`.
- A row `NOT_RECORDED` or `UNREACHABLE` → say which device and that it has not
  reported, not that it is fine.

## 3. Key center: are the variables there?

The same `fleet_device_versions` answer carries, per device, `secrets`: the
NAMES the key center delivered there (never values), `synced_at` and `errors`.

- `state: SYNCED`, `errors: 0`, and the names a model entry's `envref` rows
  need are present → OK.
- An empty list on the hub is expected: the hub is the key center's owner, not
  a recipient.
- `NOT_SYNCED`, errors, or a needed name missing → the owner adds it at their
  own keyboard: `serveros-secret set <NAME> --device <device> --repo <serveros-secrets>`
  then pushes; the device picks it up within five minutes. You never see the value.

## 4. Report

One line per check, then one verdict:

    tools   OK | STALE (refresh the app) | SERVER BEHIND
    devices OK | BEHIND: <device> (<command>)
    keys    OK | MISSING: <NAME> on <device>
    READY | NOT READY — next: <who does what>
