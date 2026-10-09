# Fleet node connector — the device side

This directory holds everything that runs **on a device**, as opposed to on the
Fleet host. On this machine it is a Windows corporate workstation, so the
launchers are PowerShell and the scheduled tasks are Windows scheduled tasks.

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
configuration problem (wrong host, refused key, port already bound) and backs
off to a longer retry, so the log stays readable.

The connector logs method and path only. Query strings are not logged: they
carry session filters, and a log is a place data accumulates without anyone
deciding it should.
