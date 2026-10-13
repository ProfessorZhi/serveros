# One operator, three company devices, no per-session approval

This is the configuration template for the arrangement that was asked for: a
single GPT manager operating **its own** sessions on three company machines —
reading them, sending to them, creating them, and stopping a run in flight —
without naming each session in advance, and without that authority reaching a
personal machine or somebody else's account.

It is written against the code on this branch. Every key named here is one a
loader reads; §5 records the two keys an operator might expect to find here and
must **not** write, and why.

---

## 1. What "authorized" is made of

There is no single `allow: true`. Each verb is admitted by a short chain of
independent gates, and the chain differs per verb. The chains are independent on
purpose: an operator who has answered "may this runtime reach that machine" has
not thereby answered "may it mint a session there", and a design that folded the
two could not express *reachable, readable, not writable* — which is the state
two of the three devices below are in today.

| verb | scope the credential must carry | device gate (Fleet binding) | the narrow gate (Fleet binding) | device-local gate (node binding) |
|---|---|---|---|---|
| `fleet_list_*` / `fleet_get_*` / `fleet_read_session` / `fleet_status` | `fleet:read` | the device is a `providers:` entry | — | signed route |
| `fleet_send_to_session` | `fleet:send` | `providers[<dev>].allow_send` | `policy.operator_grants[<principal>][<dev>]` contains `send` **or** the session is in `policy.send_allow` | `allow_send_to`, or the owner floor |
| `fleet_create_session` | `fleet:create` | `providers[<dev>].allow_create` | `policy.create_allow[<project>]` | `allow_create` **and** `allow_create_projects` |
| `fleet_interrupt_session` | `fleet:control` | `providers[<dev>].allow_control` | the session is in `policy.control_allow` (or the device wildcard) | `allow_control` **and** (`allow_control_to` or the owner floor) |

Three properties of that table are load-bearing:

*   **Nothing inherits.** A grant of `send` admits `send` and nothing else.
    `control`, `approve` and `navigate` are named in
    `PLANNED_OPERATOR_GRANT_VERBS` and are *refused when the binding is read* —
    a file that lists one is a file whose author believes a control verb is
    live, and the refusal is the correction of that belief rather than a
    no-op that leaves it standing.
*   **Every gate defaults closed.** An omitted key is a refusal, not an
    inheritance. A provider entry with no `allow_create` has not authorized
    creation; a `policy:` with no `create_allow` has authorized no project;
    a node binding with no `allow_control` stops nothing.
*   **Device eligibility is a registry fact, not a binding fact.** A grant
    naming a device whose registry `ownership` is not `company` is **inert**,
    not narrowed, and the refusal says so and records it. `None`, `"Company"`
    and `"personal "` all fail the test — the rule is one allowed value, not
    "not personal".

## 2. The two files, and where they live

They are separate files on separate machines, and neither travels.

| file | lives on | holds | may enter git |
|---|---|---|---|
| `fleet-mcp.local.yaml` | the **Fleet host** (Tokyo) | which devices this runtime reaches, and who may ask it for what | **no** |
| `fleet-node.local.yaml` | **each** device | what *that* device will accept, and from whom | **no** |

The Fleet binding's default location is resolved by `fleet_mcp/local_roots.py`
and is deliberately not the working directory — a binding in a checkout is a
binding that gets committed. It is also deliberately not `~/.serveros`, whose
five-file allowlist `path.admit()` enforces; naming it there gets you a refusal
that says `move it rather than widen this list`.

```
POSIX    Fleet   $XDG_CONFIG_HOME/serveros-fleet-mcp/fleet-mcp.local.yaml
                 (~/.config/serveros-fleet-mcp/fleet-mcp.local.yaml)
Windows  Fleet   %LOCALAPPDATA%\ServerOS\bindings\fleet-mcp.local.yaml
Node     both    <binding root>/fleet-node.local.yaml
```

Set mode `0600` on both. The Fleet binding names the file that holds the
owner's PAT (`auth.pat_anchor`) and the file that holds each device's node
secret (`providers[].credential_anchor`); those files are the credentials, and
the binding points at them rather than quoting them.

## 3. Fleet side — `fleet-mcp.local.yaml`

Placeholders are in `ANGLE_CAPS`. Nothing in this block is a real identifier.

```yaml
schema: serveros/fleet-mcp.local/v1

# The machine THIS runtime is running on. It is not one of the three.
device_ref: device:tokyo-fleet-host

auth:
  # The owner's PAT. A file path; the value is read at the point of use and is
  # never written into this binding.
  pat_anchor: ~/.config/serveros-fleet-mcp/pat.token
  oauth_enabled: true
  # The ceiling the consent form may present. `fleet:create` and `fleet:control`
  # are listed here because they are wanted; each is still granted per client at
  # consent. Leaving this key out is NOT the same as listing read and send —
  # the default is (read, send), and it is narrow on purpose.
  grantable_scopes: [fleet:read, fleet:send, fleet:create, fleet:control]

server:
  bind: 127.0.0.1
  port: 3401
  public_base_url: https://<FLEET_PUBLIC_HOST>/mcp
  mcp_path: /mcp

registry_path: ~/.config/serveros-fleet-mcp/registry.yaml

providers:
  device:<COMPANY_DEVICE_A>:
    kind: fleet-node
    endpoint: http://127.0.0.1:<A_PORT>
    credential_anchor: ~/.config/serveros-fleet-mcp/<A>.secret
    note: company device A
    allow_send: true
    allow_create: true
    allow_control: true
  device:<COMPANY_DEVICE_B>:
    kind: fleet-node
    endpoint: http://127.0.0.1:<B_PORT>
    credential_anchor: ~/.config/serveros-fleet-mcp/<B>.secret
    note: company device B
    allow_send: true
    allow_create: true
    allow_control: true
  device:<COMPANY_DEVICE_C>:
    kind: fleet-node
    endpoint: http://127.0.0.1:<C_PORT>
    credential_anchor: ~/.config/serveros-fleet-mcp/<C>.secret
    note: company device C
    allow_send: true
    allow_create: true
    allow_control: true

policy:
  # --- who may send, without naming sessions -------------------------------
  # The principal name is `oauth:<client_id>` for an OAuth credential and
  # `owner-pat` for the PAT. This is the ONLY place the real OAuth client_id is
  # written down, and it is written here, in this local file, and nowhere else.
  operator_grants:
    "oauth:<REAL_CLIENT_ID>":
      device:<COMPANY_DEVICE_A>: [send]
      device:<COMPANY_DEVICE_B>: [send]
      device:<COMPANY_DEVICE_C>: [send]
    "owner-pat":
      device:<COMPANY_DEVICE_A>: [send]
      device:<COMPANY_DEVICE_B>: [send]
      device:<COMPANY_DEVICE_C>: [send]

  # A credential that already exists but is not the manager above, if any.
  # Empty is the right value for a single-operator deployment.
  send_allow: []

  # --- which projects may be minted on -------------------------------------
  # Keyed by project; the value is that project's ceiling on the caller-chosen
  # `model` and `permissionMode`. An empty mapping is no project, never every
  # project.
  create_allow:
    "<PROJECT_ID_A>":
      permission_modes: [default]
      models: []
    "<PROJECT_ID_B>":
      permission_modes: [default]
      models: []

  # --- control -------------------------------------------------------------
  # No per-principal arm exists for control; see §5, gap G1. Device-wide control
  # is therefore expressed as a wildcard behind its own flag, and the flag is
  # the sentence a reviewer reads.
  allow_wildcard_control: true
  control_allow: ["*"]

  allow_wildcard_send: false
  allow_wildcard_create: false

audit:
  path: ~/.local/state/serveros-fleet-mcp/audit.jsonl
  max_bytes: 8388608
  rate_limit_per_minute: 240
```

Two things to read off that block rather than assume:

*   **`allow_wildcard_send: false` beside `operator_grants`.** The wildcard send
    flag is the thing the grant table replaces. Turning both on does not add
    authority; it removes the ability to say *who*, which is the question an
    audit of a grant exists to answer. It is set false here on purpose.
*   **`control_allow: ["*"]` with `allow_wildcard_control: true`.** This is not
    laziness. It is the only way this build can say "stop any run on these three
    devices" without listing every session ref, and the reason it needs an
    explicit flag next to it is that the flag is where the decision is visible.
    It is also, precisely, gap G1.

## 4. Node side — `fleet-node.local.yaml`

One of these per device, on that device, owned by that device's account. This
is the half that protects the machine when the Fleet host is the thing that got
it wrong: a request that arrives correctly signed and correctly scoped still
stops here if this file does not name it.

```yaml
schema: serveros/fleet-node.local/v1

device_ref: device:<COMPANY_DEVICE_A>

# The secret the Fleet host signs with. Same value as
# providers[device:<COMPANY_DEVICE_A>].credential_anchor on the Fleet host.
secret_anchor: ~/.config/serveros-fleet-mcp/<A>.secret

# The CloudCLI gateway on THIS machine, and the credential to reach it.
cloudcli_endpoint: http://127.0.0.1:<CLOUDCLI_PORT>
cloudcli_credential_anchor: ~/.config/serveros-fleet-mcp/<A>.cloudcli-token

bind: 127.0.0.1
port: 3402

# --- the owner floor, for send and for control ---------------------------
# One key, and its value is a list of verbs. This is the "no per-session
# approval" answer: it says every session this device's owner has. Its
# vocabulary is closed — `send` and `control` are the only words it accepts, and
# anything else (`"*"`, `"all"`, `"root"`) is refused when the file is read
# rather than ignored when a request arrives.
#
# There is no second floor key for control. Two keys naming one concept are the
# pair that gets edited by mistake, and a floor that silently meant nothing
# would refuse every request with nothing to point at but a binding that reads
# as correct.
allow_all_sessions_for_owner: [send, control]

# --- create --------------------------------------------------------------
allow_create: true
# Required whenever `allow_create` is true. `allow_create: true` with no project
# named is refused at load: the project id decides which working tree and which
# credentials a minted session inherits, so the device flag alone grants
# creation for nothing.
allow_create_projects:
  - "<PROJECT_ID_A>"
  - "<PROJECT_ID_B>"

# --- control -------------------------------------------------------------
# The device switch, checked before the floor is consulted: with this false the
# floor is not reached and the answer is a plain refusal.
allow_control: true
# A list of session names, or empty. `*` is NOT accepted: the device-wide
# control grant is the floor above, and a wildcard in a list of session names is
# refused with a message that says exactly that.

max_body_bytes: 1000000
notes: company device A, owner-grant deployment
```

## 5. What is deliberately not in these files

**Gap G1 — control has no per-principal grant.** `OPERATOR_GRANT_VERBS` is
`("send",)`; `control` sits in `PLANNED_OPERATOR_GRANT_VERBS` and a binding
naming it is refused at load. So unlike `send`, control is not scoped to *who*
at the Fleet boundary. It is scoped to the device (`allow_control`), the session
list, and the node's own floor — but any credential carrying `fleet:control`
gets the same reach on those three devices. In a one-manager deployment nothing
is lost. In a deployment with two operators who should stop different things, it
is the difference that matters, and it is not expressible today. The seam is
named in `fleet.py#_may_control`'s own docstring: a device-wide owner grant for
control is meant to plug in there, and nothing in this revision implements it.

**Gap G2 — `auth.owner_grant_anchor` is parsed and read by nothing.**
`AuthBinding` declares it, `load_config` populates it, the comment beside
`operator_grants` calls it "the anchor that lets an operator become the owner",
and no code path in this build reads it. A file kept there expecting it to
matter is a credential park, not a grant. Do not use it. Separately,
`load_operator_grants`'s docstring points a reader at `fleet.py#_owner_grant_verbs`,
which does not exist; the function is `_operator_grant_verbs`, and the name
changed when this axis stopped being about the owner and became about the
operator.

**Gap G3 — there is no `close`, and it is not an oversight.** `fleet_interrupt_session`
stops a run: the session row, the transcript and the resident host's process all
survive, which is what makes it safe to expose. `session_close` (and its
`force`) ends the resident host and removes the session and its transcript, and
`force` skips the refusal when something holds the lease — none of which this
surface can put back. So there is no `close` route in the node's `ROUTES`, no
`close` method on the device provider protocol, and no `fleet_close_session`
tool; the contract's `not_implemented` names `session_delete_or_force_stop` with
that reason, and a test asserts all four of those absences at once so that
adding one of them cannot happen quietly. If bulk transcript deletion is ever
wanted it needs a confirmation step and an audit record of its own, and that is
a separate change with its own argument.

**Gap G4 — `session_start` is spawn, not resume.** CloudCLI's `session_start`
creates a new resident session; it does not resume an existing transcript. So
"start" in the sense of *continue the conversation that exists* is not what this
surface does. What it does is `fleet_create_session` (mint) and
`fleet_send_to_session` (deliver into an existing one). The contract's
`not_implemented` names `session_start` for that reason.

## 6. Order of operations

A device's node binding is read by that device; the Fleet binding is read by the
Fleet host. They have to agree on the device ref and on the secret, and neither
loads the other's file.

1. On each device: write `fleet-node.local.yaml`, `chmod 600`, put the node
   secret where `secret_anchor` points, and start the connector. It refuses to
   start with no device identity rather than running unbound, so a successful
   start is the check.
2. On the Fleet host: add the three `providers:` entries — **`enabled: true`,
   `allow_send: false`, `allow_create: false`, `allow_control: false`** — and
   reload. `fleet_list_devices` now names all three and can act on none of them.
   That is the state to stop at if anything about the registry looks wrong: the
   `ownership` field on all three has to be `company`, and a device whose owner
   is unknown is not eligible, so a grant against it would be inert.
3. Add `policy.operator_grants` and confirm `fleet_send_to_session` reaches a
   session the grant names and no session it does not.
4. Add `auth.grantable_scopes`, then re-consent the connector (see the release
   plan — this is the one step that cannot be done by editing a file).
5. Turn on `allow_create` / `allow_control` per device, last, one device at a
   time.
