# Release plan — seven tools to nine, and the one step that is not a file edit

Scope: put this branch's Fleet MCP in front of the three company devices so that
one operator can read, send, create and interrupt on all three, without naming
sessions one at a time. Everything below is either measured on the Tokyo host on
2026-10-13 or read out of the code on this branch.

**This plan is not executed by this change.** No PR is merged, no service is
restarted, nothing on the Tokyo host is edited. It is a plan and one measured
blocker.

---

## 1. What is live now (measured, not recalled)

Read from `/data/home/vince/.config/serveros-fleet-mcp/fleet-mcp.local.yaml`
(mode `0600`) and the registry it names.

| | live value |
|---|---|
| this runtime | `device:tokyo-shared-dev-01` |
| providers | `device:work-fj`, `device:tokyo-shared-dev-01`, `device:llm-lab-dell` |
| provider grants | `allow_send: true` on all three; **`allow_create` and `allow_control` absent** (so: false) |
| `policy.send_allow` | `['*']` |
| `policy.allow_wildcard_send` | `true` |
| `policy.operator_grants` | absent (0 principals) |
| `policy.control_allow` | absent |
| `policy.create_allow` | absent |
| `auth` | `pat_anchor` only — **no `grantable_scopes`**, so the default `(fleet:read, fleet:send)` |
| tools served | 7 |

The seven it serves today are the read-and-send set; this branch serves nine.
The delta is exactly two tools, and each one is also the whole reason for one
half of this plan:

```
already live   fleet_list_devices      fleet_get_session    fleet_get_run
               fleet_list_sessions     fleet_read_session   fleet_status
               fleet_send_to_session
                                        ^ seven

added here     fleet_create_session     <- needs auth.grantable_scopes + allow_create
               fleet_interrupt_session  <- needs auth.grantable_scopes + allow_control
                                        ^ nine
```

Neither new tool is **callable** by any credential today, and neither becomes
callable merely by deploying the code: each needs the scope in the token (§5),
which is not a file edit. But both become **visible**, and that distinction is
the useful one. `tools/list` returns `[tool.as_mcp() for tool in TOOLS]`
unconditionally (`fleet_mcp/server.py:93`), with the comment "static surface: no
cached list can go stale" — the list is not filtered by the caller's scopes. So
the moment step 4 restarts onto this branch, the connector already sees nine and
the two new ones answer `OAUTH_REQUIRED` (§5) until step 7. That is the cheapest
checkpoint in the plan: nine listed, two refused, and no file has been edited to
make it so.

The registry named by that binding authorizes five devices, and the ownership
field splits them exactly the way this deployment needs:

```
device:work-fj               ownership=company     <- in scope
device:tokyo-shared-dev-01   ownership=company     <- in scope
device:llm-lab-dell          ownership=company     <- in scope
device:server-beijing        ownership=personal    <- not eligible
device:personal-dell         ownership=personal    <- not eligible
```

That split is not a comment about intent; it is what
`GRANT_ELIGIBLE_OWNERSHIP = "company"` reads. A grant naming
`device:personal-dell` or `device:server-beijing` is **inert**, and the refusal
records that it was inert rather than narrowing it. The requirement "must not
reach a personal Dell, the Beijing machine, or anybody else's account on Tokyo"
is therefore answered by a registry field rather than by a promise.

Deploy layout, from the running unit itself:

```
ExecStart   .../serveros-fleet-mcp/ingress/current/fleet_mcp/bin/serveros-fleet-mcp
                                          --http --config ~/.config/serveros-fleet-mcp/fleet-mcp.local.yaml
current  -> .../ingress/revisions/fleet-mcp-20261013@8a5faa5
previous -> .../ingress/revisions/fleet-mcp-20261011a@f4f728c
```

A revision is a directory and `current` is a symlink, so rollback is a symlink
swap plus a restart. That is the existing mechanism and this plan uses it rather
than inventing one.

## 2. The blocker. Read this before anything else

**The live binding is refused by this branch's loader.** Measured on the host,
with the branch's own `load_config` pointed at the real file:

```
policy.`send_allow` contains '*' and `allow_wildcard_send` is set. This grant is
global, not per principal: every credential holding `fleet:send` receives it. It
is refused rather than honoured, because the replacement is a named table and a
working wildcard is a second way to say the same thing. Replace it with
`policy.operator_grants: {<principal>: {<device_ref>: [send]}}` naming the
devices individually — for example `device:tokyo-shared-dev-01`,
`device:work-fj`, `device:llm-lab-dell` — and drop `allow_wildcard_send`.
Per-session `send_allow` entries that name a session ref and not '*' are
unaffected.
```

**That is the refusal verbatim, and it names this deployment's three devices.**
The example list in the message is not generic — those are the three refs in
this binding. So the operator reading the failure does not have to work out what
the migration is; the failure states it, in the same three names §1 measured.

`load_config` runs before any listener is created (`fleet_mcp/runtime.py:248`),
and a `FleetRefusal` there writes the code and the detail to stderr and
**returns 3** — the module's own comment is "Startup refusals are the ones an
operator must read, so they go to stderr with the code and a non-zero exit —
never a silent empty fleet". So the mis-ordered deploy is a **unit that fails to
start**, not a service that comes up missing a verb. That is the good failure,
and it is the reason `previous` is a complete answer.

This is not a warning and not a deprecation: the wildcard is refused **because**
a named table is what replaces it, and a wildcard that still worked would be a
second way to say the same thing — which is the door this change exists to
close.

The two revisions state it as one inverted condition, which is the cleanest way
to see what the deploy does. Both lines are read off the trees on the host:
`ingress/previous` (the running revision) and this branch:

```python
# ingress/previous   fleet-mcp-20261011a@f4f728c  — lines 259-263
if "*" in send_allow and not bool(policy_raw.get("allow_wildcard_send", False)):
    raise ...  "grant meaningless; set `allow_wildcard_send: true` if that is ..."

# this branch
if wildcard_send or "*" in send_allow:
    raise ...  "refused rather than honoured, because the replacement is a ..."
```

So the flag did not change meaning; it **stopped being consulted**. Old
behaviour: the wildcard is legal *if you set the flag*. New behaviour: the
wildcard is refused *whether or not* you set it. And the previous revision has
no `operator_grants` anywhere in its `config.py` — the key this plan migrates
to does not exist in the code that is running. That is the whole breaking
change, in two lines.

So the order is forced:

```
   edit the live binding to the granted form        <- first, while old code runs
   restart / swap `current`, restart                <- second
```

The reverse order is survivable but pointless: you would cut the revision, watch
the unit fail to start, and then have to fix the binding on a host whose fleet
surface is currently down — which is the one tool you would want for checking
whether the failure touched anything else. Edit first, while the old code is
still serving, and the load check in §3 tells you the answer before anything
moves.

**The migration is a deletion as much as an addition.** `send_allow: ['*']` and
`allow_wildcard_send: true` both have to go; leaving either one in place is the
same refusal. What replaces them is §3.

A second consequence worth stating: the live binding has **no
`grantable_scopes`**, so it currently defaults to `(fleet:read, fleet:send)`.
That default is narrow on purpose — "a deployment that has not said the word
create has not authorized creation" — and it stays narrow until the key is
written. Nothing about deploying the code widens it.

## 3. The binding edit, in full

This is the whole migration, expressed against the live device refs. It is an
edit to the local file, not to the repository, and the file never enters git.

```yaml
# delete these two lines
#   policy:
#     send_allow: ['*']
#     allow_wildcard_send: true

policy:
  # replaces the wildcard: three devices, named, one principal.
  # The key is the principal the credential presents: `oauth:<cid>` for the
  # ChatGPT connector, `owner-pat` for the PAT. Both spellings are the code's
  # own literals — `name=f"oauth:{claims.get('cid', 'unknown')}"`
  # (http_transport.py:152) and `name="owner-pat"` (http_transport.py:135).
  #
  # The real client id is written here and nowhere else. No one has to be told
  # what it is in order to write this key: the OAuth handshake records the
  # principal, and `principal` is a `ref` in the disclosure table
  # (disclosure.py:170) — kept as an identifier, unlike `client_name`
  # (disclosure.py:173), which is a client's own free text and is withheld. So
  # the host's own audit log is where the exact key comes from, and the id
  # never has to be pasted into a message, a document, or a transcript.
  operator_grants:
    "oauth:<REAL_CLIENT_ID>":
      device:work-fj: [send]
      device:tokyo-shared-dev-01: [send]
      device:llm-lab-dell: [send]
    "owner-pat":
      device:work-fj: [send]
      device:tokyo-shared-dev-01: [send]
      device:llm-lab-dell: [send]

  # any session refs that used to be reached through the wildcard and still
  # need to be reachable by a credential NOT named above. If the wildcard was
  # only ever there to serve the two principals above, this stays empty.
  send_allow: []

  # create and control stay closed at this step. §4 opens them.
  create_allow: {}
```

The block above is an excerpt — a `policy:` section without the `schema`,
`device_ref`, `auth`, `providers` and `audit` sections around it — so it is not
itself a loadable file and nothing here pretends otherwise. What *is* loadable
is the same key set spelled with placeholders:
`fleet/owner-grant-3devices.md` carries the whole file, and
`tests/test_fleet_owner_grant_template.py` takes both of its blocks **out of the
document, verbatim**, fills the `ANGLE_CAPS` placeholders and runs them through
the real loaders — so the keys there are keys the parser accepts, not keys the
prose believes in. The only difference between that tested block and this
excerpt is `device:<COMPANY_DEVICE_A>` → `device:work-fj` and so on.

**Check before restarting**, on the host, with the branch's code:

```
python -c "import sys; sys.path.insert(0, '<revision dir>'); \
  from fleet_mcp.config import load_config; load_config('<binding>'); print('LOADS')"
```

A `LOADS` here is the difference between a restart and a rollback.

## 4. Opening create and control

Two independent things, and both are per device.

**Fleet side** — in the same local binding:

```yaml
auth:
  grantable_scopes: [fleet:read, fleet:send, fleet:create, fleet:control]

providers:
  device:work-fj:               { ..., allow_create: true, allow_control: true }
  device:tokyo-shared-dev-01:   { ..., allow_create: true, allow_control: true }
  device:llm-lab-dell:          { ..., allow_create: true, allow_control: true }

policy:
  create_allow:
    "<PROJECT_ID>": { permission_modes: [default], models: [] }
  allow_wildcard_control: true
  control_allow: ["*"]
```

**Node side** — on each device, in `fleet-node.local.yaml`, the switch that
makes the device-wide grant real rather than per session:

```yaml
allow_all_sessions_for_owner: [send, control]
allow_create: true
allow_create_projects: ["<PROJECT_ID>"]
allow_control: true
```

Note what is *not* there: no wildcard session list. `allow_control_to: ["*"]`
is refused at load, and the refusal names the field to use instead:

```
allow_control_to contains '*'. The device-wide control grant is written as
`allow_all_sessions_for_owner: [control]`, not as a wildcard in a list of
sessions. Name the sessions, or move the grant to the field that means it.
```

The floor is the field that means "all of this owner's sessions"; a wildcard in
a list of names is a different claim wearing the same clothes, so it gets its
own sentence rather than being quietly honoured.

`control_allow: ["*"]` with `allow_wildcard_control: true` is on the Fleet side
instead, and it is the one place this plan spends a wildcard. Reason, and it is
the honest one: **there is no per-principal arm for control.** `control` is in
`PLANNED_OPERATOR_GRANT_VERBS` and a binding naming it in `operator_grants` is
refused at load — so `send` can be scoped to *who*, and `control` cannot. On a
single-operator deployment this costs nothing. Where two operators should be
able to stop different things, it is the difference that matters, and it is
written up as gap **G1** in `fleet/owner-grant-3devices.md`.

## 5. The one step that is not a file edit

**Scopes are minted into the token at authorization and cannot be widened
afterwards.** `validate_authorize` refuses a requested scope that is not in
`grantable_scopes`, and `rotate_refresh_token` carries `scopes=record.scopes`
forward unchanged — a refresh preserves the scope set on purpose, because a
refresh that could widen privilege is a privilege escalation with a friendly
name.

So the ChatGPT connector's current token carries `(fleet:read, fleet:send)` and
always will. To get `fleet:create` and `fleet:control`:

1. put both scopes in `auth.grantable_scopes` in the local binding (above), and
   restart — a scope cannot be requested that the binding does not offer;
2. **remove the connector and add it again** in ChatGPT, so a fresh
   authorization runs and the consent form is presented. The boxes for
   `fleet:create` and `fleet:control` are new on that form; ticking them is the
   grant.

That is the only step a person has to perform twice, and it is the only one the
new tools depend on. Until it is done the new tools refuse, and they refuse in
the one way that is self-diagnosing. `Credential.require` raises
`SCOPE_DENIED` (`fleet_mcp/fleet.py:134`), and the transport maps that
particular code to JSON-RPC `-32001` `OAUTH_REQUIRED`
(`fleet_mcp/server.py:145`) rather than to a generic invalid-params error — the
comment there says it is signalled as an authorization condition "so a client
can tell 'ask for a better token' from 'fix your arguments'". So the failure
names its own remedy: the token is not good enough, get a better one. That is
why the ordering is binding first, re-consent second, tools usable third.

If the connector is not re-added, the seven existing tools behave exactly as they
do today, because the token is unchanged. The connector's view does widen from
seven to nine (§1) — that is the static `tools/list` and it happens at step 4,
not here — and the two new ones are the ones that answer `OAUTH_REQUIRED`. A
client that surfaces tool errors will show that; a client that does not will
simply never call a tool it has no use for.

## 6. Order of operations

| # | step | where | reversible by |
|---|---|---|---|
| 0 | record the current `current` symlink target | host | — |
| 1 | edit the binding: `operator_grants` in, `send_allow: ['*']` and `allow_wildcard_send` out | host | restore the file |
| 2 | run the load check from §3 | host | — |
| 3 | cut a revision and move `current` to it | host | `current` back to `previous` |
| 4 | restart the unit; confirm 9 tools and `fleet_status` | host | symlink + restart |
| 5 | add `grantable_scopes`, `allow_create`, `allow_control`, `create_allow`, `control_allow` | host | restore the file |
| 6 | node bindings on the three devices | each device | restore the file |
| 7 | re-add the ChatGPT connector | ChatGPT | remove it again |

Steps 1–3 are the ones that can take the surface down. Steps 5–7 are additive:
each key defaults to closed, so a deployment that stops after 4 is exactly
today's behaviour with the wildcard replaced by a named table.

## 7. Rollback

`current` → `previous` (`fleet-mcp-20261011a@f4f728c`) and restart. **But the
symlink is only half of it, and the other half is the binding.**

Step 1 rewrote a file the old code also reads, and the old code is on the host
at `ingress/previous`, so the two consequences were read rather than guessed:

* `send_allow: []` with the wildcard gone is a binding in which **nothing can
  send** — the running revision consults `send_allow` and it is now empty.
* `operator_grants` does not exist in the previous revision's `config.py` — and
  it does not refuse unknown keys either. It reads the keys it knows and
  ignores the rest, so `operator_grants` is **inert** to it. The rollback is
  therefore not *louder* than the forward step, which is the worse case; it is
  merely **silent**: the old code comes up, serves its seven tools, and a
  `fleet_send_to_session` that used to work is now refused with nothing in the
  file that reads as wrong. That is the failure this whole plan is shaped
  around, and it is the reason the binding copy is not optional.

So: keep a copy of the binding as it was before step 1, outside the revisions
tree, and a rollback is the symlink **and** the file.

## 8. What this plan deliberately does not do

* **It does not add a `close`.** No route, no tool, no provider method; the
  contract's `not_implemented` names `session_delete_or_force_stop` with the
  reason (`session_close` ends the resident host and removes the transcript,
  `force` skips the lease-holder refusal, and neither can be put back), and a
  test asserts all four absences at once. Removing transcripts in bulk is a
  separate change with its own confirmation step and its own argument.
* **It does not add `session_start` as a resume.** CloudCLI's `session_start`
  spawns a new resident session; continuing an existing conversation is
  `fleet_send_to_session`. The contract names `session_start` in
  `not_implemented` so that the difference is visible rather than discovered.
* **It does not touch the Windows node's binding.** `device:work-fj` gets its
  node-side edit at step 6, on that machine, by whoever owns it.
* **It does not restart the Tokyo service.** That authorization was spent once,
  on the Dell onboarding, and this change does not ask for it again.
