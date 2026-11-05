# Privacy notice — `serveros-fleet-ingress`

This notice describes the plugin package in this directory and the Claude Code
adapter generated from it. It states what the package does and does not do with
data. It is not a statement about any particular Fleet deployment: the operator
who runs the MCP server you connect to decides what that server records.

## What the package contains

A manifest, an MCP binding, five Skill documents (Markdown), and two PNG images.
There is **no executable code** in the package, no background process, and no
telemetry or analytics client. The Skills are instructions a model reads; they
do not run on their own.

## What leaves your device

The package itself sends nothing. When you use the plugin, Claude Code connects
to the MCP server named in the binding (`mcp.json` in this package, `.mcp.json`
in the Claude Code adapter) at the URL the installing operator supplies. Over
that connection Claude Code sends:

- OAuth credentials, to establish and refresh the session;
- the MCP tool calls you or the model invoke, including their arguments (for
  example a device reference, a project id, or a message to deliver);
- tool results the server returns, which are then part of the model context.

That traffic goes to the Fleet server and, as with any Claude Code session, to
the model provider configured in your Claude Code installation. The plugin does
not route it anywhere else and does not add a second destination.

## What is stored locally

- The plugin files, under the plugin or marketplace directory you installed
  from.
- OAuth access and refresh tokens, in Claude Code's own credential store. They
  are not written by the plugin into this package or into a project directory.

## What this notice does not decide

Retention, access control, audit logging and deletion on the **server** side are
set by whoever operates that Fleet deployment. The server's audit log is
described in its own documentation; this package neither reads nor ships it.

## Contact

Open an issue at <https://github.com/ProfessorZhi/serveros/issues>.
