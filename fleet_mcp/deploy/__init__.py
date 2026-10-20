"""Putting this surface on a host, and taking it back off.

The package exists so the installer is importable rather than only runnable:
`tests/test_fleet_mcp_contract.py` asserts the names in it against
`fleet/fleet-mcp-contract.yaml`, which is the only way "the declared unit name
is the one that gets installed" stays true after one of the two is edited.

Nothing here runs at import. `install.py` is a program with subcommands, and
importing it must not install anything.
"""
