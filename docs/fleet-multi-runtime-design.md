# One physical device, several runtimes

Status of this document: **design + offline naming rule. Nothing here is live.**
No device, binding, connector, credential or running surface was changed to
produce it, and no part of it has been exercised against a real machine.

The case: a Dell A6000 where **`vince` and `fjd` each run their own CloudCLI**,
under their own OS account, with their own credential, their own state and their
own Fleet Node Runtime. The Fleet owner must be able to read, create, message,
control, approve and navigate **both** sets of sessions — without the machine
growing a second, competing ServerOS core.

## 1. What already carries this, and what does not

| Layer | Today | Verdict |
|---|---|---|
| `fleet/registry.yaml` | a device declares **many** runtimes (`runtime_bindings`: `agent-runtime:claude-code`, `agent-runtime:codex`) | already models it |
| `identity.SessionKey` | `device_ref · provider · native_session_id`; provider grammar `^[a-z][a-z0-9._-]{0,31}$` | already models it — `claude-fjd` is a legal provider |
| `fleet_mcp/runtime_identity.py` | the naming rule that joins the two | **added by this change** |
| `FleetConfig.providers` | `dict[str, ProviderBinding]`, keyed by **`device_ref` alone** | **GAP G2** |
| `node_protocol.base_string` | `METHOD\nPATH\nhash(body)\ndevice_ref\nts\nnonce` — **no runtime** | **GAP G1** |
| node routes | device-scoped (`/v1/sessions/{id}`) | **GAP G1** |

## 2. The naming rule

    the device's own account keeps the unqualified tag     device:llm-lab-dell|claude|<uuid>
    every other account gets a suffixed one                device:llm-lab-dell|claude-fjd|<uuid>

The unqualified tag is **not** cosmetic. Every session ref already stored says
`|claude|`, so adding the second runtime is additive: no stored reference is
re-addressed, and there is no window in which a live session's address is
ambiguous. This is asserted by
`tests/test_fleet_runtime_identity.py::TheOwnersRuntimeKeepsItsOldNameTest`.

The tag is written by the host **from an account name**, never accepted from a
caller. A name that reduces to no tag characters, or whose tag would not be a
legal provider name, is **refused rather than truncated** — a truncated name is
a tag that reads as a different account. `|`, uppercase, whitespace and shell
metacharacters cannot survive into a tag, and the reference that results still
parses to exactly three parts.

## 3. The two gaps, and why neither is closed here

**G1 — the node signature does not carry the runtime.** `base_string` takes a
`device_ref` and nothing else, so two runtimes on one machine sign and verify
identically: whoever holds the device's node secret can present a request for
either runtime, and the *device* decides which one it was. With one runtime per
device that was the same statement. With two OS accounts on one machine it is
the isolation the `node_secret` row of the contract cannot yet deliver. Closing
it means the signature covers the runtime as well as the device — a wire-protocol
change, so it is deliberately left to its own reviewed change. Pinned today by
`TheIsolationContractIsOnTheDeviceTest.test_the_signature_does_not_yet_carry_the_runtime`,
which fails the moment the signature grows the field.

**G2 — the host holds one provider per `device_ref`.** `provider_for(device_ref)`
and `_require_provider(device_ref)` both key on the device alone, and
`ProviderBinding` has no runtime field. Two runtimes on one device cannot both be
reached, and the `provider` already inside every `SessionKey` is carried but
never used for routing. Closing it means keying providers by
`(device_ref, provider)` and routing on the tag the reference already carries.
This is the change that would touch the running surface, and it is not made here.

**Why neither is made here.** Both alter behaviour of a surface that is live.
The naming rule is the part that can be settled first: it is pure, it has no
callers, and once its invariants are pinned, the routing change is a change to
*how* a tag is dispatched rather than a change to *what a tag is*.

## 4. The isolation contract

Four facts make two runtimes on one machine isolated. Three are the **device's**
to arrange, and this host cannot see them; the fourth is shared.

| fact | what must be true | owner |
|---|---|---|
| `os_account` | each runtime runs as its own OS account; the connector never reads another account's home, environment or process tree | device |
| `cloudcli_credential` | each runtime holds its own CloudCLI credential, readable only by its own account; nothing shares or proxies another's | device |
| `state_root` | each runtime's sessions, queue and SQLite live under its own account's root and are not world-readable | device |
| `node_secret` | each runtime signs with its own node secret, so a secret for one cannot authenticate a request for the other | device **and host** (G1) |

**The host must not claim an isolation it cannot check.** A request that merely
*says* `provider=claude-fjd` is a claim, not a proof; the host routes by the tag
it was given and refuses a tag it was not. Whether `fjd`'s runtime actually runs
as `fjd`, with `fjd`'s credential and `fjd`'s state, is a device fact and is
reported as one. `ISOLATION_CONTRACT` in `fleet_mcp/runtime_identity.py` is that
table as data, and the tests assert the host is not named as the owner of the
three it cannot see.

## 5. Negative cases covered by this change

| case | assertion |
|---|---|
| same native UUID under two runtimes | two distinct references; unequal and non-collapsing as keys |
| old session ref compatibility | `device:X\|claude\|<uuid>` is unchanged, still parses, still round-trips |
| cross-account claim | a tag is written from an account name; `\|`, uppercase, metacharacters and empty results cannot enter one |
| runtime offline independence | naming reads no registry, no binding, no socket — asserted by inspecting the module's own source |
| default-deny | an unnamed account, an unnamed device owner, an untyped device ref and an over-long name are all refused |
| signed Node API | the signature's parameters are pinned, so the G1 gap is a fact with a test rather than a note |

## 6. Production GO gate

Nothing below is done. In dependency order:

1. **G1** — extend the signed base string (and the connector's verification) to
   cover the runtime. Wire-protocol change; needs its own review and a
   version bump, and both halves must ship together or one side will refuse the
   other.
2. **G2** — key `FleetConfig.providers` by `(device_ref, provider)` and route on
   `SessionKey.provider`. Touches `fleet.py`'s provider lookups.
3. **Registry** — declare the second runtime on the device
   (`runtime_bindings`), and a second binding for the same `device_ref` with the
   second runtime's endpoint and credential anchor.
4. **Device** — a second connector, as `fjd`, with `fjd`'s own CloudCLI
   credential, state root and node secret. **This is `fjd`'s or the user's work,
   not this host's**, and until it exists the second runtime is a name with
   nothing behind it.
5. **Host grants** — `operator_grants[<oauth principal>][<device>]` extended per
   verb, and `allow_*` on the second binding. Default-deny throughout: nothing
   is granted by adding a runtime.

**What this host cannot do for you:** create or read `fjd`'s OS account,
credential, state or node secret. A `claude-fjd` reference that resolves to
nothing is the expected state until step 4 is done, and reporting it as
unreachable is the correct answer rather than a failure.

## 7. Who does what

- **User / Fleet owner**: approve G1/G2, decide which verbs each principal gets
  on the second runtime, and confirm no personal device is in scope.
- **`fjd` (or an operator with that account)**: create `fjd`'s CloudCLI
  credential, its state root, its node secret and its connector binding, as
  `fjd`. None of this can be done from `vince`.
- **This host**: the naming rule, the routing change once G1/G2 are approved,
  and the tests that keep both honest.

## 8. Scope

The design covers **one physical device with two runtimes**. It is not a
mechanism for adding a second *device*, and `runtime_for` refuses a `device_ref`
that is not typed rather than inventing one: one machine is one authenticated
device, and giving it two identities would be two things to keep in step.
Personal devices remain out of scope — nothing here widens the eligible set,
which stays `ownership: company` through the existing operator-grant filter.
