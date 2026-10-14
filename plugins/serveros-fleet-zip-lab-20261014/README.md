# ServerOS Fleet ZIP Lab (private / non-production)

This is a private packaging regression test. It contains two **instruction-only** skills. It does **not** declare a live MCP server, use credentials, or grant access to ServerOS devices.

The purpose is to demonstrate that GitHub holds plugin source, a deterministic ZIP can be generated, and Plugin Creator can update the same private plugin ID from v0.1.0 to v0.1.1 without losing the prior skill.

The production Fleet Next plugin must be built separately after independent HTTPS, OAuth, and MCP tool-discovery validation. Do not copy production secrets or permission grants into this repository.

To build:

```sh
python3 scripts/build_fleet_plugin_zip.py plugins/serveros-fleet-zip-lab-20261014
```

The output is `dist/<plugin-name>-v<version>.zip` and its SHA-256 digest. Plugin Creator's `update_plugin` updates an existing plugin ID from a ZIP with a guarded expected release ID. A ZIP package update does not change the live MCP server or repair its network.
