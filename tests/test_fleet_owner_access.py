"""Strict owner configuration and project planning; no provider or config writes."""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from dataclasses import FrozenInstanceError
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from fleet_mcp.owner_access import (  # noqa: E402
    OwnerAccessError, OwnerAccessProfile, build_project_path, parse_owner_access,
)


def config(roots=None, **overrides):
    return dict(owner_approved=True,
                project_roots={"work": "/owner/projects"} if roots is None else roots,
                **overrides)


class ParseOwnerAccess(unittest.TestCase):
    def test_absence_preserves_legacy(self):
        self.assertIsNone(parse_owner_access(None))

    def test_maximum_mode_is_explicit_owner_default(self):
        result = parse_owner_access(config())
        self.assertTrue(result.owner_approved)
        self.assertEqual(result.permission_mode, "bypassPermissions")
        self.assertEqual(dict(result.project_roots), {"work": "/owner/projects"})
        self.assertEqual(parse_owner_access(config(permission_mode="bypassPermissions")), result)

    def test_approval_is_exact_true(self):
        for value in (False, None, 0, 1, "true", "yes", [], {}):
            raw = config()
            raw["owner_approved"] = value
            with self.subTest(value=value), self.assertRaises(OwnerAccessError):
                parse_owner_access(raw)

    def test_missing_unknown_fields_and_wrong_shapes(self):
        cases = [[], "true", True, 1, {}, {"owner_approved": True},
                 {"project_roots": {"work": "/x"}}, config(extra="unsupported"),
                 config(permission_mode="default"), config(permission_mode=None)]
        for raw in cases:
            with self.subTest(raw=raw), self.assertRaises(OwnerAccessError):
                parse_owner_access(raw)

    def test_roots_and_references_are_strict(self):
        for roots in ({}, [], None, {"": "/x"}, {"../x": "/x"},
                      {"root:work": "/x"}, {"*": "/x"}, {1: "/x"},
                      {"a" * 65: "/x"}, {"work": None}, {"work": 1},
                      {"work": Path("/x")}):
            raw = {"owner_approved": True, "project_roots": roots}
            with self.subTest(roots=roots), self.assertRaises(OwnerAccessError):
                parse_owner_access(raw)

    def test_posix_roots_reject_relative_aliases_and_padding(self):
        for path in ("", "relative", "~/work", "$HOME/work", "/a/../b", "/a/./b",
                     "/a//b", "//server/share", " /work", "/work ",
                     "/a\x00b", "/a\nb", "/a\\b"):
            with self.subTest(path=path), self.assertRaises(OwnerAccessError):
                parse_owner_access(config({"work": path}))

    def test_windows_absolute_is_parsed_on_linux(self):
        profile = parse_owner_access(config({"work": "D:/owner/projects/"}))
        self.assertEqual(profile.project_roots["work"], "D:\\owner\\projects")
        self.assertEqual(build_project_path(profile, "work", "hello.world", local_check=False),
                         "D:\\owner\\projects\\hello.world")

    def test_windows_rejects_drive_relative_devices_unc_and_aliases(self):
        for path in ("D:work", "D:", "\\work", "\\\\host\\share\\work",
                     "\\\\?\\D:\\work", "\\\\.\\D:\\work", "D:\\a\\..\\b",
                     "D:\\a\\.\\b", "D:\\a\\\\b", "D:\\a:stream",
                     "D:\\NUL\\work", "D:\\COM1.txt\\work", "D:\\work.\\x",
                     "D:\\work \\x", "D:\\a?b", "D:\\CONIN$", "D:\\LPT¹"):
            with self.subTest(path=path), self.assertRaises(OwnerAccessError):
                parse_owner_access(config({"work": path}))

    def test_duplicate_or_overlapping_roots_are_rejected(self):
        for roots in ({"a": "/x", "b": "/x/"}, {"a": "/x", "b": "/x/sub"},
                      {"a": "D:/x", "b": "d:\\X"},
                      {"a": "D:/x", "b": "D:/x/sub"}):
            with self.subTest(roots=roots), self.assertRaises(OwnerAccessError):
                parse_owner_access(config(roots))
        self.assertEqual(len(parse_owner_access(config({"a": "/x", "b": "/xy"})).project_roots), 2)

    def test_frozen_profile_copies_mutable_input(self):
        raw = config()
        profile = parse_owner_access(raw)
        raw["project_roots"]["work"] = "/unreviewed"
        self.assertEqual(profile.project_roots["work"], "/owner/projects")
        with self.assertRaises(TypeError):
            profile.project_roots["work"] = "/changed"
        with self.assertRaises(FrozenInstanceError):
            profile.permission_mode = "default"

    def test_direct_construction_cannot_bypass_validation(self):
        for kwargs in (dict(owner_approved=1, project_roots={"work": "/x"}),
                       dict(owner_approved=True, project_roots={}),
                       dict(owner_approved=True, project_roots={"work": "relative"}),
                       dict(owner_approved=True, project_roots={"work": "/x"}, permission_mode="default")):
            with self.subTest(kwargs=kwargs), self.assertRaises(OwnerAccessError):
                OwnerAccessProfile(**kwargs)


class LexicalPlanning(unittest.TestCase):
    def setUp(self):
        self.profile = parse_owner_access(config())

    def test_valid_name_and_configured_root(self):
        self.assertEqual(build_project_path(self.profile, "work", "hello.world-2_ok", local_check=False),
                         "/owner/projects/hello.world-2_ok")

    def test_invalid_names_never_escape_or_alias(self):
        names = (None, 1, "", ".", "..", ".hidden", "../x", "x/y", "x\\y", "/x", "C:x",
                 "foo:stream", "x.", "x ", " x", "x\x00", "x\n", "x?", "x*", "x|", "é",
                 "CON", "con.txt", "NUL", "aux", "PRN", "COM1.log", "LPT9", "a" * 129)
        for name in names:
            with self.subTest(name=name), self.assertRaises(OwnerAccessError):
                build_project_path(self.profile, "work", name, local_check=False)

    def test_unknown_root_and_invalid_mode_are_rejected(self):
        for root_ref in ("missing", "../work", None, 1):
            with self.subTest(root_ref=root_ref), self.assertRaises(OwnerAccessError):
                build_project_path(self.profile, root_ref, "new", local_check=False)
        for mode in (None, 0, 1, "false"):
            with self.subTest(mode=mode), self.assertRaises(OwnerAccessError):
                build_project_path(self.profile, "work", "new", local_check=mode)
        with self.assertRaises(OwnerAccessError):
            build_project_path(config(), "work", "new", local_check=False)

    def test_lexical_mode_does_not_probe_filesystem(self):
        with patch("os.lstat", side_effect=AssertionError("filesystem IO")), \
                patch("os.stat", side_effect=AssertionError("filesystem IO")):
            parse_owner_access(config({"remote": "C:/unavailable"}))
            self.assertEqual(build_project_path(self.profile, "work", "new", local_check=False),
                             "/owner/projects/new")


class LocalPlanning(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.root = self.base / "projects"
        self.root.mkdir()

    def profile(self, root=None):
        return parse_owner_access(config({"work": str(self.root if root is None else root)}))

    def test_valid_path_is_only_planned_never_created(self):
        profile = self.profile()
        with patch("os.mkdir", side_effect=AssertionError("helper must not create")):
            target = build_project_path(profile, "work", "new", local_check=True)
        self.assertEqual(target, str(self.root / "new"))
        self.assertFalse((self.root / "new").exists())

    def test_missing_root_or_file_root_is_rejected(self):
        file_root = self.base / "file"
        file_root.write_text("owned fixture")
        for path in (self.base / "missing", file_root):
            with self.subTest(path=path), self.assertRaises(OwnerAccessError):
                build_project_path(self.profile(path), "work", "new", local_check=True)

    def test_symlink_root_and_ancestor_are_rejected(self):
        alias = self.base / "alias"
        alias.symlink_to(self.root, target_is_directory=True)
        for root in (alias, alias / "nested"):
            with self.subTest(root=root), self.assertRaises(OwnerAccessError):
                build_project_path(self.profile(root), "work", "new", local_check=True)

    def test_dangling_root_is_rejected(self):
        alias = self.base / "dangling"
        alias.symlink_to(self.base / "absent")
        with self.assertRaises(OwnerAccessError):
            build_project_path(self.profile(alias), "work", "new", local_check=True)

    def test_all_existing_targets_are_rejected(self):
        (self.root / "directory").mkdir()
        (self.root / "file").write_text("preserved")
        (self.root / "link").symlink_to(self.base / "absent")
        for name in ("directory", "file", "link"):
            with self.subTest(name=name), self.assertRaises(OwnerAccessError):
                build_project_path(self.profile(), "work", name, local_check=True)
        self.assertEqual((self.root / "file").read_text(), "preserved")

    def test_reparse_attribute_is_rejected_without_following(self):
        original = os.lstat
        for reparse in (self.root, self.root.parent):
            def observe(path, *args, **kwargs):
                result = original(path, *args, **kwargs)
                if str(path) == str(reparse):
                    return SimpleNamespace(st_mode=result.st_mode,
                                           st_file_attributes=0x400)
                return result

            with self.subTest(reparse=reparse), patch("os.lstat", side_effect=observe), \
                    self.assertRaises(OwnerAccessError):
                build_project_path(self.profile(), "work", "new", local_check=True)

    def test_filesystem_error_is_a_policy_error(self):
        with patch("os.lstat", side_effect=PermissionError("denied")), self.assertRaises(OwnerAccessError):
            build_project_path(self.profile(), "work", "new", local_check=True)

    def test_target_stat_error_does_not_mean_available(self):
        original = os.lstat

        def observe(path, *args, **kwargs):
            if str(path) == str(self.root / "new"):
                raise PermissionError("target unavailable")
            return original(path, *args, **kwargs)

        with patch("os.lstat", side_effect=observe), self.assertRaises(OwnerAccessError):
            build_project_path(self.profile(), "work", "new", local_check=True)

    def test_no_ancestor_is_followed_before_its_lstat(self):
        original = os.lstat
        calls = []

        def observe(path, *args, **kwargs):
            calls.append(str(path))
            return original(path, *args, **kwargs)

        with patch("os.lstat", side_effect=observe):
            build_project_path(self.profile(), "work", "new", local_check=True)
        self.assertEqual(calls, [str(p) for p in reversed((self.root, *self.root.parents))]
                         + [str(self.root / "new")])

    @unittest.skipIf(os.name == "nt", "non-native path probe is Linux-only")
    def test_windows_local_check_on_linux_cannot_claim_success(self):
        profile = parse_owner_access(config({"work": "C:/owner/projects"}))
        with self.assertRaises(OwnerAccessError):
            build_project_path(profile, "work", "new", local_check=True)


if __name__ == "__main__":
    unittest.main()
