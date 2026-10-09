"""The outbound node connector — the half that runs on the device itself.

A device behind a corporate network dials out and serves a closed, signed API on
the far end of a tunnel it owns. Nothing on that device is reachable through it
except the seven named routes in `fleet_mcp.node_protocol.ROUTES`.
"""
