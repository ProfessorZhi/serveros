# Fleet node connector — the device side

This directory holds everything that runs **on a device**, as opposed to on the
Fleet host. On this machine it is a Windows corporate workstation, so the
launchers are PowerShell and the scheduled tasks are Windows scheduled tasks.

On a POSIX device the same connector runs under shell launchers and systemd
user units; the wire protocol, the binding schema and the send allow list are
unchanged. `README.linux.md` covers that half, including an explicit list of
where the two platforms differ.

Read `fleet/fleet-mcp-contract.yaml` first if you want the reasoning. This file
is the operator's half: what to run, what it does, and what to check.

## What it is for

The Fleet host is off-host and has to ask a session on this machine questions:
what sessions exist, what one of them is doing, what a page of its transcript
says, and — the only write — deliver a message to one of them.

The obvious way to do that is to forward this machine's CloudCLI port to the
Fleet host. That was rejected, because CloudCLI's personal access token is a
credential for everything CloudCLI can do here, and forwarding the port puts
that credential on the Fleet host. The token does not leave this machine.

What leaves this machine instead is a **reverse tunnel this machine opens
outward**, carrying signed requests to a connector that serves seven named
questions and nothing else.

```
   this machine                                    the Fleet host
   ------------                                    --------------

   CloudCLI MCP gateway                             Fleet MCP surface
     http://127.0.0.1:3001/mcp                             |
            ^                                              | signed request
            |  CloudCLI PAT stays here                     v
            |                                      127.0.0.1:3402
   fleet_mcp.node.connector                                 ^
     127.0.0.1:3402                                        |
            ^                                              |
            |                                     ssh -R 127.0.0.1:3402:127.0.0.1:3402
            +----------------------------------------------+
                        opened by THIS machine (start-tunnel.ps1)
```

Nothing here listens on a routable address. Both ports are loopback on both
ends. The tunnel is the only path between them, and this machine owns it.

## Files

| File | Runs where | What it does |
|---|---|---|
| `connector.py` | this machine | serves the seven routes on `127.0.0.1:3402` |
| `start-connector.ps1` | this machine | starts the connector; does not return until it exits |
| `start-tunnel.ps1` | this machine | keeps the reverse tunnel open, with backoff |
| `install.ps1` | this machine | writes the binding + secret, registers two tasks |
| `uninstall.ps1` | this machine | unregisters the tasks; leaves the binding and secret |
| `../node_protocol.py` | both | the wire format: routes, signing, verification |

On POSIX the launchers are `install.sh`, `start-connector.sh`, `start-tunnel.sh`,
`stop.sh` and `uninstall.sh`, sharing their path derivation in `lib_paths.sh`;
see `README.linux.md`.

## Installing

From a PowerShell prompt on this machine:

```powershell
cd D:\projects\serveros-fleet-work\repo
.\fleet_mcp\node\install.ps1
```

It writes:

* `%LOCALAPPDATA%\ServerOS\bindings\fleet-node.local.yaml` — which device this
  is and where its credential anchors point. Never committed.
* `%LOCALAPPDATA%\ServerOS\state\fleet-node\secret` — the node secret, 32
  random bytes, hex. Generated here and never printed.
* two scheduled tasks, `ServerOS-FleetNode-Connector` and
  `ServerOS-FleetNode-Tunnel`, both triggered at logon and set to run
  indefinitely.

It prints a **fingerprint** of the secret (the first 12 hex characters of its
SHA-256). That is so the two ends can be confirmed to hold the same value
without either of them writing it to a terminal, a log or a transcript. Compare
the fingerprint with the value on the Fleet host; do not print the secret
itself to compare it, and do not paste it into a chat window.

It will not create the CloudCLI credential. The binding points at
`%USERPROFILE%\.cloudcli\mcp-pat-claude-code.txt`, which is CloudCLI's own
personal access token file — an anchor, not a copy, so that there is exactly
one of it to revoke. If it is absent the connector refuses to start and says
so in the log.

Re-running the installer is safe: an existing binding and an existing secret
are left exactly as they are. Overwriting a binding would silently re-point
this device at a different Fleet host.

Then start them without logging off:

```powershell
Start-ScheduledTask -TaskName 'ServerOS-FleetNode-Connector'
Start-ScheduledTask -TaskName 'ServerOS-FleetNode-Tunnel'
```

## Uninstalling

```powershell
.\fleet_mcp\node\uninstall.ps1
```

Removes the tasks and stops any process they started. Leaves the binding, the
secret and the logs — a removal that also destroys the values makes
re-provisioning guesswork, and the tasks are what makes this machine reachable,
not the files. `-PurgeSecret` and `-PurgeBinding` remove those too.

After this, the Fleet host reports the device as having no route. That is the
correct answer, not an error.

## The send allow list

`allow_send_to` in the binding controls the one write this connector can
perform. It ships **empty**, which means every send is refused — including a
send from the Fleet owner.

That is the point. The Fleet host decides whether a caller may drive *the
device*; this file decides whether anything may drive *the sessions on it*.
They are different questions, asked by different parties, on different
machines. If the connector trusted the Fleet host's answer it would be one
configuration file away from being someone else's.

To allow a send, name the session here, by native id or by full
`device:<id>|<provider>|<id>` ref:

```yaml
allow_send_to:
  - device:work-fj|claude|00000000-0000-0000-0000-000000000000
```

A refusal is visible from the Fleet side as `WRITE_NOT_GRANTED`, and the error
text names this list and this machine, because the fix is a file here and not
a change over there.

Reads are unaffected by this list. Withdrawing send access does not stop the
Fleet host reading, and does not require the connector to be restarted
cooperatively — this machine can refuse every send whether or not the other end
is up.

### The owner floor: every session on this machine, named in none of them

A named list cannot express *any* session, and the case it cannot express is the
one that keeps it from being maintained: a session that does not exist yet has
no name to add. So there is a second grant, in a different key, which the person
at this keyboard can open for the whole machine at once:

```yaml
allow_all_sessions_for_owner:
  - send
```

Empty is the default and means nothing changes. `send` here means "a send to any
session on this device, including one created after this file was last read".
The named list still works and still wins: a session it names is attributed to
the list, not to the floor, so withdrawing the floor cannot silently move a
named session into the wider bucket.

It is **not** a wildcard you can put inside `allow_send_to`, and it is not a
statement about who is calling. Nothing on this side can see a remote principal
— the signature proves possession of this device's secret, not a person, and the
Fleet host's scope-and-device decision is made on another machine. A floor is
the owner of *this* machine saying what may be driven *here*. Reported by
`GET /v1/self` under `control.send_owner_floor`, alongside `control.send_default`,
which spells out the resulting answer for a session this file does not name.

Only the words in `OWNER_FLOOR_VERBS` are accepted. A word outside it is refused
when the binding is read, not ignored — a floor that silently meant nothing
would look exactly like a floor that was never needed, and this file would still
read as correct.

Two consequences worth knowing before turning it on:

* A send admitted by the floor is the only send that leaves no trace in this
  binding, so it is journalled to the connector's own stderr
  (`send admitted by the owner floor, session not named in this binding: <ref>`)
  — that log, and `GET /v1/self`, are where "what went out under the floor" is
  answered.
* Withdrawing it is an edit to this file plus a restart of the connector, the
  same latency as every other binding here. There is no path by which the Fleet
  host, or anything holding the node secret, can turn it on or off from outside.

## What it will not do

* No route executes a command. There is no shell route, no exec route, and no
  route that takes a path. That is a property of the route table, not of the
  documentation, and a test asserts it.
* No route forwards to the CloudCLI gateway generically. Each of the seven
  routes maps to one named operation.
* No response carries the CloudCLI credential, and no request can ask for it.
* The connector does not decide which devices exist. That is
  `fleet/registry.yaml` on the Fleet host, read through `fleet/transport.py`.

## Checking it

Logs are appended to `%LOCALAPPDATA%\ServerOS\logs\`:

* `fleet-node-connector.log` — the connector's stderr
* `fleet-node-connector.out.log` — its stdout
* `fleet-node-tunnel.log` — tunnel opens, exits and retries

A tunnel that exits within five seconds of opening is treated as a
configuration problem (wrong host, refused key) and backs off to a longer
retry, so the log stays readable.

`port already bound` is deliberately not in that list, because it is not a
configuration problem and it does not clear itself. It is what a tunnel that
died without a clean teardown leaves behind on the Fleet host: the sshd
session belonging to a client that no longer exists keeps the remote port
bound, so every later attempt fails at the bind. On 2026-10-10 that state
lasted fifteen hours and the log said nothing but `retrying`. The supervisor
now reports it as `INCIDENT remote-port-stuck`, once and then every half hour,
together with the command that clears it.

The listener belongs to the **root monitor** of that sshd session, which is
why the Fleet host cannot clear it from the account the tunnel logs in as, and
why the session has to end:

```bash
# on the Fleet host, as the account the tunnel logs in as
ssh <host> "ps -u <user> -o pid,etimes,cmd | grep 'sshd: <user>$'"   # find the -N login
ssh <host> "pkill -f '^sshd: <user>\s*$'"
```

`sshd: <user>` with no `@notty`, no `@pts` and no command is a session-less
`ssh -N` login, which is what every tunnel here is. The `pkill` therefore ends
*all* of them, including any other tunnel that account is running — such as a
forward to the production query broker — which reopens on its own schedule.
Kill the pid from the first command instead if that matters more than the
convenience. Killing the unprivileged session is enough; the root monitor
follows it.

After the stale session ends, the retry loop below binds the port on its next
attempt without being restarted or touched.

The connector logs method and path only. Query strings are not logged: they
carry session filters, and a log is a place data accumulates without anyone
deciding it should.
