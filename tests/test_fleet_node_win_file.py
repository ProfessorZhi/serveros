"""The connector's Windows reader keeps the POSIX reader's promises.

On Linux the owner check is injected (there is no SID); on the hosted Windows
runner the same tests run with the real token check, plus one that proves the
real check accepts a file this account just wrote.
"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from fleet_mcp.errors import FleetRefusal
from fleet_mcp.node import connector as node_connector
from fleet_mcp.node import win_file


def mine(_path):
    return True


def not_mine(_path):
    return False


class WindowsReaderPromises(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp()).resolve()
        self.file = self.tmp / "status.json"
        self.file.write_bytes(b'{"schema": "serveros/core-lite-status.v1"}')

    def test_an_owned_regular_file_is_read(self):
        raw = win_file.read_owned_bounded(self.file, 1024, owner_check=mine)
        self.assertIn(b"core-lite-status", raw)

    def test_another_accounts_file_is_refused(self):
        with self.assertRaisesRegex(win_file.WindowsReadRefused, "different account"):
            win_file.read_owned_bounded(self.file, 1024, owner_check=not_mine)

    def test_a_missing_file_is_named_as_missing(self):
        with self.assertRaisesRegex(win_file.WindowsReadRefused, "no Core snapshot"):
            win_file.read_owned_bounded(self.tmp / "absent.json", 1024, owner_check=mine)

    def test_a_directory_is_not_a_snapshot(self):
        with self.assertRaisesRegex(win_file.WindowsReadRefused, "not a regular file"):
            win_file.read_owned_bounded(self.tmp, 1024, owner_check=mine)

    @unittest.skipIf(sys.platform == "win32", "creating a symlink needs a privilege on Windows")
    def test_a_link_in_the_path_is_refused(self):
        link = self.tmp / "link.json"
        os.symlink(self.file, link)
        with self.assertRaisesRegex(win_file.WindowsReadRefused, "does not resolve to itself"):
            win_file.read_owned_bounded(link, 1024, owner_check=mine)

    def test_past_the_cap_is_refused_on_the_bytes_read(self):
        self.file.write_bytes(b"x" * 2048)
        with self.assertRaisesRegex(win_file.WindowsReadRefused, "past the 1024 bytes"):
            win_file.read_owned_bounded(self.file, 1024, owner_check=mine)

    def test_a_refusal_never_echoes_the_path(self):
        with self.assertRaises(win_file.WindowsReadRefused) as caught:
            win_file.read_owned_bounded(self.file, 1024, owner_check=not_mine)
        self.assertNotIn(str(self.file), str(caught.exception))

    def test_the_connector_uses_this_reader_on_windows(self):
        with mock.patch.object(os, "O_NOFOLLOW", None, create=True), \
                mock.patch.object(node_connector.os, "name", "nt"), \
                mock.patch.object(win_file, "read_owned_bounded", return_value=b"{}") as read:
            self.assertEqual(node_connector._read_bounded_no_follow(self.file, 1024), b"{}")
        read.assert_called_once()

    def test_the_connector_maps_a_windows_refusal_to_bad_request(self):
        with mock.patch.object(os, "O_NOFOLLOW", None, create=True), \
                mock.patch.object(node_connector.os, "name", "nt"), \
                mock.patch.object(win_file, "read_owned_bounded",
                                  side_effect=win_file.WindowsReadRefused("owned by a different account")):
            with self.assertRaises(FleetRefusal) as caught:
                node_connector._read_bounded_no_follow(self.file, 1024)
        self.assertEqual(caught.exception.code, "BAD_REQUEST")

    @unittest.skipUnless(sys.platform == "win32", "the real owner check needs a Windows token")
    def test_the_real_owner_check_accepts_a_file_this_account_wrote(self):
        raw = win_file.read_owned_bounded(self.file, 1024)
        self.assertIn(b"core-lite-status", raw)


if __name__ == "__main__":
    unittest.main()
