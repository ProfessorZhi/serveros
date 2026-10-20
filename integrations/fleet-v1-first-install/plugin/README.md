# serveros-fleet-ingress — the first-install Fleet plugin package

This directory is the plugin's **source tree**, not a built release. It ships a
remote `streamable-http` MCP binding and the Operator Skills that teach an
assistant to use the Fleet surface correctly.

Build it with `../build_first_install.py`; nothing here is a ZIP and nothing
here is installable as it stands.

## Why the binding is a template

`mcp.template.json` is not `mcp.json`, and the difference is load-bearing. The
specification fixes `mcp.json` at the package root as *the* place a portable MCP
binding lives — so a committed `mcp.json` would be a committed claim that a
server is reachable at some URL. This package has no such URL: the Next ingress
has not been observed on a public hostname.

So the builder substitutes the operator-supplied URL into a generated
`mcp.json`, and **refuses to ship any `mcp.json` that came from source**. A
placeholder URL is therefore not merely discouraged; there is no file whose
contents could be mistaken for a real binding.

## Why the published package must contain MCP from the first release

A pure-Skills package cannot be turned into an MCP package by a later update of
the same plugin. The first ZIP that a client registers is the one that decides
whether this plugin is a connector at all, so the initial release carries
`mcp.json` or it is not released.

## Skills

Immediate children of `skills/`, each a directory holding `SKILL.md`. The
specification is explicit that clients do not search deeper descendants, so a
skill nested one level further is invisible rather than wrong.

| Skill | What it is for |
|---|---|
| `fleet-device-and-session-discovery` | find the real device, OS user, credential profile and runtime before naming one |
| `fleet-reuse-idle-session` | continue a session that already holds the context instead of minting a new one |
| `fleet-create-with-grants` | creation is owner + project + runtime + scope, and is tracked by run id |
| `fleet-evidence-review` | accept a delegated result only from evidence that was read |
| `fleet-safety-boundaries` | 33 tools are not 33 permissions; runtimes are separate; do not interrupt |
