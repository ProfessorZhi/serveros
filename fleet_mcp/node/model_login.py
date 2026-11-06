"""Log in to this machine's CloudCLI once, so Fleet can manage its model library.

Run at THIS machine's keyboard, as the account the connector runs as:

    python3 -m fleet_mcp.node.model_login [--config <connector binding>]

It asks for the CloudCLI user name and password (the password is read without
echo and never stored), logs in to the CloudCLI the connector's binding names,
and writes the login token to `model_library_token_path` from that binding,
mode 0600. The connector refreshes the token as CloudCLI rotates it; when it
finally lapses, run this again.

The token never leaves this machine: the connector reads it, the hub does not.
"""
from __future__ import annotations

import argparse
import getpass
import json
import sys
import urllib.error
import urllib.request

from ..cloudcli_models import rest_base, write_token
from ..errors import FleetRefusal
from .connector import load_node_config


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--config", default=None, help="the connector binding (default: its usual paths)")
    args = parser.parse_args(argv)
    try:
        config = load_node_config(args.config)
        base = rest_base(config.cloudcli_endpoint)
    except FleetRefusal as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return 2
    if config.model_library_token_path is None:
        print("refused: the connector binding has no model_library_token_path; add one "
              "(e.g. ~/.config/serveros/cloudcli-model-token, absolute) and run again",
              file=sys.stderr)
        return 2
    username = input("CloudCLI user name: ").strip()
    password = getpass.getpass("CloudCLI password (not stored): ")
    req = urllib.request.Request(
        base + "/api/auth/login", method="POST",
        data=json.dumps({"username": username, "password": password}).encode("utf-8"),
        headers={"Content-Type": "application/json", "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            answer = json.loads(resp.read(65536).decode("utf-8"))
    except urllib.error.HTTPError as exc:
        print(f"refused: CloudCLI answered {exc.code}", file=sys.stderr)
        return 1
    except (urllib.error.URLError, OSError, ValueError) as exc:
        print(f"refused: CloudCLI not reachable at {base}: {type(exc).__name__}", file=sys.stderr)
        return 1
    token = answer.get("token") if isinstance(answer, dict) else None
    if not isinstance(token, str) or not token:
        print("refused: CloudCLI's login answer carried no token", file=sys.stderr)
        return 1
    write_token(config.model_library_token_path, token)
    print(f"wrote the model-library token to {config.model_library_token_path} (0600)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
