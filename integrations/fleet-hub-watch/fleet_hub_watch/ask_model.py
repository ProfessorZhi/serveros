"""Ask a model one question over an Anthropic-compatible gateway. Stdlib only.

    python -m fleet_hub_watch.ask_model --base-url URL --model ID --key-env NAME < prompt

The prompt is read from stdin; the model's text is printed. This is what the
butler (`intent.py`) runs when the hub is given

    SERVEROS_FLEET_INTENT_COMMAND="python3 -m fleet_hub_watch.ask_model \\
        --base-url http://127.0.0.1:4000 --model deepseek-v4-flash-anthropic \\
        --key-env LITELLM_GATEWAY0_KEY"

Why not `claude -p`: the owner's rule is that every device's agents go through
the gateway's DeepSeek route, and a full Claude Code start (plugins, hooks, MCP
servers, tools) is seconds of work and a set of tools the butler must not have.
One HTTP request has neither.

The key is never an argument: `--key-env` names the environment variable that
holds it (the key center writes it to `~/.config/serveros/cloudcli.env`, which
the unit loads with `EnvironmentFile=`). It is never printed, logged or put in
an error message; a failure names the kind of failure and the HTTP status only.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request

MAX_PROMPT = 20000


def ask(base_url: str, model: str, key: str, prompt: str, *, max_tokens: int = 600,
        timeout: float = 15.0, opener=urllib.request.urlopen) -> str:
    """The model's text for one user message. Raises RuntimeError with a
    reason that carries no credential."""
    url = base_url.rstrip("/")
    url += "/messages" if url.endswith("/v1") else "/v1/messages"
    body = json.dumps({"model": model, "max_tokens": max_tokens,
                       "messages": [{"role": "user", "content": prompt[:MAX_PROMPT]}]}).encode()
    request = urllib.request.Request(url, data=body, method="POST", headers={
        "content-type": "application/json", "anthropic-version": "2023-06-01",
        "x-api-key": key, "authorization": f"Bearer {key}"})
    try:
        with opener(request, timeout=timeout) as response:
            doc = json.loads(response.read(1_000_000).decode("utf-8"))
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f"the gateway answered HTTP {exc.code}") from None
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise RuntimeError(f"the gateway could not be reached ({type(exc).__name__})") from None
    except ValueError:
        raise RuntimeError("the gateway's answer was not JSON") from None
    parts = doc.get("content") if isinstance(doc, dict) else None
    text = "".join(p.get("text", "") for p in parts or []
                   if isinstance(p, dict) and p.get("type") == "text")
    if not text.strip():
        raise RuntimeError("the gateway's answer had no text")
    return text


def main(argv: list[str] | None = None) -> int:
    # No abbreviations: `--key VALUE` must be an error, not a spelling of --key-env.
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0], allow_abbrev=False)
    parser.add_argument("--base-url", required=True, help="the gateway, e.g. http://127.0.0.1:4000")
    parser.add_argument("--model", required=True, help="the model id the gateway serves")
    parser.add_argument("--key-env", required=True,
                        help="the NAME of the environment variable holding the key")
    parser.add_argument("--max-tokens", type=int, default=600)
    parser.add_argument("--timeout", type=float, default=15.0)
    args = parser.parse_args(argv)
    key = os.environ.get(args.key_env, "")
    if not key:
        print(f"ask_model: the variable {args.key_env} is not set in this environment",
              file=sys.stderr)
        return 2
    try:
        print(ask(args.base_url, args.model, key, sys.stdin.read(),
                  max_tokens=args.max_tokens, timeout=args.timeout))
    except RuntimeError as exc:
        print(f"ask_model: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
