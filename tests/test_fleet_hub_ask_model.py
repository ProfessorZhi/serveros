"""The butler's model call: one request to the gateway, the key never shown."""
from __future__ import annotations

import io
import json
import os
import sys
import threading
import unittest
from contextlib import redirect_stderr, redirect_stdout
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
for extra in (ROOT, ROOT / "integrations" / "fleet-hub-watch"):
    if str(extra) not in sys.path:
        sys.path.insert(0, str(extra))

from fleet_hub_watch import ask_model  # noqa: E402

KEY = "sk-test-0123456789abcdef"


class Gateway:
    def __init__(self, status=200, body=None):
        seen = self.seen = []
        reply = json.dumps(body if body is not None else
                           {"content": [{"type": "text", "text": '{"command": "status"}'}]}).encode()

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                length = int(self.headers.get("content-length", 0))
                seen.append((self.path, dict(self.headers), json.loads(self.rfile.read(length))))
                self.send_response(status)
                self.send_header("content-type", "application/json")
                self.end_headers()
                self.wfile.write(reply)

            def log_message(self, *args):
                pass
        self.server = HTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_port}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()


def run(argv, stdin="怎么样"):
    out, err = io.StringIO(), io.StringIO()
    with mock.patch.object(sys, "stdin", io.StringIO(stdin)), redirect_stdout(out), redirect_stderr(err):
        code = ask_model.main(argv)
    return code, out.getvalue(), err.getvalue()


class OneRequestToTheGateway(unittest.TestCase):
    def test_the_prompt_goes_in_and_the_text_comes_out(self):
        gateway = Gateway()
        try:
            with mock.patch.dict(os.environ, {"GW_KEY": KEY}):
                code, out, err = run(["--base-url", gateway.url, "--model",
                                      "deepseek-v4-flash-anthropic", "--key-env", "GW_KEY"])
        finally:
            gateway.close()
        self.assertEqual((code, err), (0, ""))
        self.assertIn('"command": "status"', out)
        path, headers, body = gateway.seen[0]
        self.assertEqual(path, "/v1/messages")
        self.assertEqual(body["model"], "deepseek-v4-flash-anthropic")
        self.assertEqual(body["messages"], [{"role": "user", "content": "怎么样"}])
        self.assertNotIn("tools", body, "the butler is given no tools")
        self.assertEqual({k.lower(): v for k, v in headers.items()}.get("x-api-key"), KEY)

    def test_a_base_url_that_already_ends_in_v1_is_not_doubled(self):
        gateway = Gateway()
        try:
            ask_model.ask(gateway.url + "/v1/", "m", KEY, "x")
        finally:
            gateway.close()
        self.assertEqual(gateway.seen[0][0], "/v1/messages")


class TheKeyIsNeverShown(unittest.TestCase):
    def test_it_is_named_not_passed(self):
        code, out, err = run(["--base-url", "http://127.0.0.1:9", "--model", "m",
                              "--key-env", "NO_SUCH_VARIABLE_HERE"])
        self.assertEqual(code, 2)
        self.assertIn("NO_SUCH_VARIABLE_HERE", err)
        with self.assertRaises(SystemExit):          # there is no flag that takes a key
            with redirect_stderr(io.StringIO()):
                ask_model.main(["--base-url", "u", "--model", "m", "--key", KEY])

    def test_a_refusal_an_unreachable_gateway_and_an_empty_answer_do_not_print_it(self):
        refused, empty = Gateway(status=401, body={"error": f"bad key {KEY}"}), Gateway(body={"content": []})
        try:
            with mock.patch.dict(os.environ, {"GW_KEY": KEY}):
                results = [run(["--base-url", url, "--model", "m", "--key-env", "GW_KEY",
                                "--timeout", "2"])
                           for url in (refused.url, empty.url, "http://127.0.0.1:9")]
        finally:
            refused.close()
            empty.close()
        for code, out, err in results:
            self.assertEqual(code, 1)
            self.assertEqual(out, "")
            self.assertNotIn(KEY, err)
        self.assertIn("HTTP 401", results[0][2])


if __name__ == "__main__":
    unittest.main()
