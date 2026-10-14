# Fleet node connector — the device side, on Linux

This is the POSIX counterpart of `README.md` in this directory. Read that file
first: the wire protocol, the two gates, the send allow list and the reasoning
about the tunnel are the same on both platforms, and none of it is repeated
here. What differs is the launcher, the paths and the service manager.

Everything in this directory runs **on a device**, as one unprivileged account.
Nothing here needs `sudo`, nothing writes outside that account's home, and no
privileged port is used.

## What this is for

The Fleet host is off-host and has to ask a session on this machine questions:
which sessions exist, what one of them is doing, what a page of its transcript
says, and — the only write — deliver a message to one of them.

```
   this machine (Linux, unprivileged)                the Fleet host
   ---------------------------------                --------------

   CloudCLI MCP gateway                              Fleet MCP surface
     http://127.0.0.1:<cloudcli-port>/mcp                    |
            ^                                                | signed request
            |  CloudCLI PAT stays here                       v
            |                                       127.0.0.1:<assigned-port>
   fleet_mcp.node.connector                                  ^
     127.0.0.1:3402                                          |
            ^                                                |
            |                            ssh -R 127.0.0.1:<assigned-port>:127.0.0.1:3402
            +------------------------------------------------+
                          opened by THIS machine (start-tunnel.sh)
```

Nothing here listens on a routable address. Both ports are loopback on both
ends. The tunnel is the only path between them, and this machine owns it.

The local port is this device's own choice (3402, the same value the Windows
node uses). The **remote** port is assigned per device by whoever runs the Fleet
host; two devices must not be given the same one. Nothing in these scripts picks
it.

## Files

| File | Runs where | What it does |
|---|---|---|
| `connector.py` | this machine | serves the eight routes on `127.0.0.1:3402` (unchanged, platform-neutral) |
| `lib_paths.sh` | sourced | the one place the paths are derived; checked against `local_roots.py` by the test suite |
| `install.sh` | this machine | writes the binding + secret, optionally writes unit files |
| `start-connector.sh` | this machine | starts the connector; does not return until it exits |
| `start-tunnel.sh` | this machine | keeps the reverse tunnel open, with backoff |
| `stop.sh` | this machine | stops both, removes nothing |
| `uninstall.sh` | this machine | removes the units; leaves the binding and secret |
| `systemd/user/*.service.in` | rendered by `install.sh --systemd` | templates; not installed anywhere by themselves |
| `../node_protocol.py` | both | the wire format: routes, signing, verification |

## What you need before you start

* A **Python 3** interpreter that can import `fleet_mcp.node.connector` — which
  in practice means PyYAML is installed for it. Set `SERVEROS_FLEET_PYTHON` if
  the right interpreter is not the one on `PATH`.
* An **SSH identity** for the tunnel: a private key provisioned for this device,
  whose public half is authorized on the Fleet host. These scripts will not
  generate one, on purpose — a key minted locally is a trust relationship the
  Fleet host has not agreed to, and the failure would appear there as a refused
  key.
* The Fleet host's **host key**, verified out of band, in a `known_hosts` file.
  These scripts will not ask for it on first sight and do not set
  `StrictHostKeyChecking=no` or `accept-new`; a tunnel that accepts whoever
  answers is not a tunnel.
* A **`device:<id>` ref** that has been added to `fleet/registry.yaml` on the
  Fleet host, and a **remote port** assigned to this device.

The last two are decisions, not steps. `install.sh` will not invent either one.

## Preflight

Writes nothing. Reports everything it is going to need, in one pass:

```bash
cd <checkout>
fleet_mcp/node/install.sh \
  --device-ref device:llm-lab-dell \
  --cloudcli-endpoint http://127.0.0.1:3001/mcp \
  --ssh-host <fleet-host> \
  --remote-port <assigned-port> \
  --systemd \
  --preflight
```

Exit status is non-zero while any check fails. The four things this script will
not fix for you are a missing CloudCLI credential, a missing SSH identity, a
missing `known_hosts`, and a local port already in use. The first three are
reported; the fourth is a warning, because the usual cause is that this
connector is already installed and running.

## Install

```bash
fleet_mcp/node/install.sh \
  --device-ref device:llm-lab-dell \
  --cloudcli-endpoint http://127.0.0.1:3001/mcp \
  --ssh-host <fleet-host> \
  --remote-port <assigned-port> \
  --systemd
```

It writes:

* `$XDG_CONFIG_HOME/serveros-fleet-mcp/fleet-node.local.yaml` (or
  `~/.config/serveros-fleet-mcp/…`) — which device this is, where its
  credential anchors point, and what it may be sent. Mode 0600. Never committed
  and never inside a repository: the script refuses if the target path is in one.
* `$XDG_STATE_HOME/serveros-fleet-node/secret` (or
  `~/.local/state/serveros-fleet-node/secret`) — the node secret, 32 random
  bytes as 64 hex characters, **no trailing newline**. Mode 0600, in a 0700
  directory.
* `$XDG_STATE_HOME/serveros-fleet-node/logs/` — the tunnel's log.
* with `--systemd`, two unit files under `$XDG_CONFIG_HOME/systemd/user/`.

`FLEET_MCP_HOME` relocates the binding root, exactly as it does for the Python
resolver. `FLEET_NODE_SSH_DIR` relocates the default key directory if you would
rather keep the key in `~/.ssh`.

It prints a **fingerprint**: the first 12 hex characters of the secret's
SHA-256. Compare that with the value the Fleet host holds for this device. That
is the only way to confirm the two ends agree — neither end prints the secret,
and it must not be pasted into a chat window to compare it.

It will **not** create the CloudCLI credential; the binding points at
`~/.cloudcli/mcp-pat-claude-code.txt` as an *anchor*, so that there is exactly
one copy of that credential to revoke. If it is absent, the connector refuses to
start and says so.

Re-running is safe: an existing binding and an existing secret are left exactly
as they are. Overwriting a binding would silently re-point this device at a
different Fleet host.

### `device:llm-lab-dell` is not enrolled by any of this

The scripts take the ref as an argument and write it down. Only a person can add
it to `fleet/registry.yaml` on the Fleet host, and until that happens the Fleet
host will not route to this device however healthy the connector looks. The
identity still needs human approval; nothing here grants it.

## Running it

### Under systemd (user units)

`install.sh` writes the unit files and stops. It does not enable, start or reload
anything, because enabling a service is a change to the account:

```bash
systemctl --user daemon-reload
systemctl --user enable --now serveros-fleet-node-connector.service
systemctl --user enable --now serveros-fleet-node-tunnel.service
```

A user unit stops when the last session of that account ends, unless the account
is allowed to keep user services alive:

```bash
loginctl enable-linger $USER     # operator decision — see below
```

That command makes this device reachable while nobody is logged in, which is a
change to the account rather than to this connector. Run it only if that is what
you mean. Neither `install.sh` nor any other script here runs it, and none of
them adds anything to a crontab.

### Without systemd

```bash
fleet_mcp/node/start-tunnel.sh --ssh-host <fleet-host> --remote-port <assigned-port> &
fleet_mcp/node/start-connector.sh
```

`start-tunnel.sh` supervises the tunnel in the foreground: it opens the tunnel,
reads ssh's reason when the tunnel exits, decides whether to back off, and opens
it again. `start-connector.sh` does not return until the connector exits. Both
keep running until you stop them, and neither survives a logout — which is
honest rather than convenient, and is the state the scripts leave you in unless
you ask for more.

## Verifying

```bash
# 1. Is the connector listening?  (ss does not show secrets or unit Descriptions)
ss -ltn | grep 127.0.0.1:3402

# 2. Does it REFUSE an unsigned request?  A 401 here is the correct answer.
#    A 200 would mean the signature check is not running, which is the failure
#    that matters.
curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:3402/v1/health   # expect 401

# 3. Is the tunnel up?
cat ~/.local/state/serveros-fleet-node/tunnel.pid
tail -n 20 ~/.local/state/serveros-fleet-node/logs/fleet-node-tunnel.log

# 4. Under systemd, the last few lines each unit wrote:
journalctl --user -u serveros-fleet-node-connector.service -n 20 --no-pager
journalctl --user -u serveros-fleet-node-tunnel.service -n 20 --no-pager
```

Do **not** reach for `systemctl --user list-units` to see what is running: it
renders every unit's Description, a scope unit's Description can carry a Bearer
token, and that output lands in whatever transcript is watching. `is-active`,
`show -p` and `journalctl -u` answer the same question without printing one.
No script in this directory calls it.

## The tunnel port is stuck

If the Fleet host's sshd keeps the assigned remote port bound to a connection
whose client is gone, every attempt exits in seconds with `remote port
forwarding failed for listen port <N>`, and the supervisor reports:

```
INCIDENT remote-port-stuck: 127.0.0.1:<N> on <host> is held by the sshd session of a tunnel that is gone ...
```

It says that once and then every thirtieth attempt, so a long outage is a few
loud lines rather than hundreds of quiet ones. Waiting does not fix it; the
listener belongs to the root monitor of an sshd session that has to end. The
clearing commands are in the log line and in `README.md`, and they end *every*
session-less `ssh -N` login for that account on the Fleet host — including any
other tunnel it is running. Kill the specific pid instead if that matters more
than the convenience.

## Stopping and rolling back

```bash
fleet_mcp/node/stop.sh                # stop both; remove nothing
fleet_mcp/node/stop.sh --dry-run      # say what would be stopped
fleet_mcp/node/uninstall.sh           # also remove the unit files
fleet_mcp/node/uninstall.sh --purge-secret --purge-binding
```

`uninstall.sh` leaves the binding, the secret and the logs unless asked
otherwise, for the same reason `uninstall.ps1` does: the units are what makes
this machine reachable, and the secret is the only record of which value to
re-provision. After either, the Fleet host reports the device as having no
route, which is the correct answer rather than an error.

Both stop the tunnel **by the pid `start-tunnel.sh` wrote down**, confirmed
against the forward spec recorded beside it. That confirmation exists because a
pid is a number that was ours once: if the recorded pid has been recycled, the
command line will not carry the forward spec, and the script says so instead of
killing a stranger. The connector is matched on its module name **and** this
binding path together. A filter over the process table would match this host's
other tunnels — one CloudCLI port forward and one read-only production query
broker — which is what `uninstall.ps1`'s comment records nearly happening once.

## Where this does not fully align with the Windows node

* **The connector and the wire protocol do not differ at all.** `connector.py`,
  `node_protocol.py` and the eight routes are the same code on both platforms.
  The difference is entirely in the four launcher scripts and the paths.
* **Paths.** Windows keeps everything under `%LOCALAPPDATA%\ServerOS\`:
  `bindings\`, `state\fleet-node\` and a flat `logs\`. POSIX has no single
  application directory, so this follows XDG: the binding goes to the config
  root the Python resolver already declares in `fleet-mcp-contract.yaml`, and the
  secret, pid file and logs go under the state root. The binding path is
  therefore *identical* to the one the Python resolver computes — this is the
  one path where a divergence would be a silent failure, and a test pins it.
  Logs land under the node's state directory rather than beside it, because
  XDG's own rule is that logs are state; on Windows they sit beside both roots.
* **The secret file is written identically** (32 bytes, 64 hex characters, no
  trailing newline) so that the fingerprint means the same thing on both ends.
* **Service manager.** Scheduled tasks become systemd user units. The units are
  written but never enabled by the installer, and `loginctl enable-linger` is
  printed with its warning rather than run. Windows' "restart 999/min" becomes
  `Restart=always` with `RestartSec=15`.
* **Tunnel failure is reported the same way**, including the
  `INCIDENT remote-port-stuck` line and the same backoff rule (a tunnel that
  lived less than five seconds backs off to the larger of the slow retry and
  four times the normal retry).
* **Two things are Linux-only.** There is a `stop.sh`, because
  `systemctl --user stop` and "remove the unit file" are different acts with
  different reversibility. And `start-tunnel.sh` writes a `tunnel.forward` file
  beside the pid, so the stopper can confirm a recorded pid against what it was
  asked to forward — Windows kills on the pid alone.
* **The SSH identity and host key requirements are stricter here.** The Windows
  tunnel lets ssh fall back to the user's own ssh config; this one passes an
  explicit `-i`, `IdentitiesOnly=yes`, `UserKnownHostsFile=` and
  `StrictHostKeyChecking=yes`, so the trust settings are in the file you are
  reading rather than in a config file on the device.
* **`--max-attempts` exists only on Linux.** It bounds the supervisor's retry
  loop so a test can exercise the backoff and the incident reporting without
  waiting. It is not for deployments, and the log says so when it fires.

## What it will not do

The same list as `README.md`, and it holds here because it is a property of the
same code: no route executes a command, no route takes a path, no route forwards
to CloudCLI generically, no response carries the CloudCLI credential, and the
connector does not decide which devices exist.

To which this file adds: no script here writes a credential into a unit file, a
unit Description, a command line argument or a log line; the secret is generated
once, printed never, and read only to hash it.
