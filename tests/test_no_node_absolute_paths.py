"""A shipped Skill must not name one node's absolute path, and this says whether one does.

This is the serveros half of a rule that has two halves. `ability-management` carries the
other one, over its own catalogue, and the reason there are two is that they do not cover
the same files: the `serveros-linux-work` composition takes four of its five Skills from
`ability-management` at `--ability-rev` and takes `serveros` itself from THIS repository.
So a fix applied only in `ability-management` leaves the root Skill every session loads
still instructing an agent to write into one machine's data volume -- which is not
hypothetical, it is what happened.

WHY THE SCOPE IS `skills/` AND NOTHING ELSE. The rule is about GUIDANCE: text a session is
expected to follow. Where a node's absolute path appears elsewhere in this repository it
is usually there for a reason, and scanning it would produce markers rather than fixes:

  * `fleet/profiles.yaml` is the device-binding registry -- the one declared home of
    physical paths, and it lists both devices on purpose. `instructions/README.md` says so:
    the physical path belongs to the device profile.
  * `tests/` builds synthetic nodes; a test for path resolution that contains no path is
    testing nothing. `tests/data/` holds recorded live observations.
  * `docs/` and `workbench/` are records of what was measured, and the machine policy keeps
    historical evidence regardless of how it is named.
  * `runtime/*.py`, `abilities/*.yaml` and `context/` carry archaeology -- comments stating
    what a past revision did wrong, which is why the current one does it differently. The
    archaeology is the argument for the code; rewriting it would delete the evidence.

`node-path-ok` on a line exempts that line, for prose that must quote the string it forbids.

The controls are not decoration. The rule this file enforces was already declared elsewhere
in the tree, and the check meant to hold it down had been dead for a while -- it computed a
repo root with `../../..`, the file moved, and it walked a directory that does not exist and
reported clean. So `test_the_scan_actually_reaches_files` treats an empty scan as a failure,
and `TheControlTests` shows the scanner catching what it must catch.
"""
import os
import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]

# A path belonging to one machine, matched only at a real path start -- a URL such as
# `https://example.com/home/page` contains `/home/` and names no node.
NODE_PATH = re.compile(
    r"(?<![\w./-])(?:/home|/opt/applications|/data/home|/data/scratch|/Users)/[A-Za-z0-9_.-]+"
)
SCANNED_ROOT = ROOT / "skills"
SCANNED_SUFFIXES = frozenset(
    {".md", ".py", ".yaml", ".yml", ".json", ".txt", ".sh", ".toml", ".tmpl"}
)
SKIP_DIRS = frozenset({"__pycache__", ".git"})


def scan(text: str) -> list[tuple[int, str]]:
    """`(line number, matched path)` for every line naming a node, marker lines aside."""
    found: list[tuple[int, str]] = []
    for number, line in enumerate(text.splitlines(), 1):
        if "node-path-ok" in line:
            continue
        match = NODE_PATH.search(line)
        if match:
            found.append((number, match.group(0)))
    return found


def scan_tree(root: Path) -> tuple[list[str], int]:
    """Offenders as `relative:line path`, and how many files were actually read."""
    offenders: list[str] = []
    read = 0
    for base, directories, files in os.walk(root):
        directories[:] = sorted(d for d in directories if d not in SKIP_DIRS)
        for name in sorted(files):
            path = Path(base) / name
            if path.suffix not in SCANNED_SUFFIXES:
                continue
            try:
                text = path.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            read += 1
            offenders += [
                f"{path.relative_to(ROOT)}:{number} {hit}" for number, hit in scan(text)
            ]
    return offenders, read


class TheControlTests(unittest.TestCase):
    """The scanner has to be able to fail, and not to fail on the wrong thing."""

    def test_it_catches_a_node_path(self):
        for line in (
            "  --output-dir /opt/applications/data/x\n",
            "cd /home/hwz/workspace\n",
            "DATA_ROOT=/data/home/vince\n",
            "cp x /data/scratch/vince/y\n",
            "profile at /Users/someone/.config\n",
        ):
            with self.subTest(line=line.strip()):
                found = scan(line)
                self.assertEqual(len(found), 1, f"not caught: {line!r}")
                self.assertTrue(found[0][1].startswith("/"), found)

    def test_it_respects_the_marker_on_the_line_that_carries_it(self):
        self.assertEqual(scan("fallback = \"/home/hwz\"  # node-path-ok: archaeology\n"), [])
        # ...and only on that line: the next one is still watched.
        self.assertEqual(
            scan("# node-path-ok: the rule\n--output /home/hwz/ws\n"), [(2, "/home/hwz")]
        )

    def test_it_ignores_a_url_that_merely_contains_a_home_segment(self):
        self.assertEqual(scan("see https://example.com/home/docs and /home/ style\n"), [])


class TheShippedSkillsNameNoNodeTests(unittest.TestCase):
    def test_no_shipped_skill_names_a_node(self):
        """Fails naming the file and line, so the fix is a lookup rather than a search."""
        offenders, _ = scan_tree(SCANNED_ROOT)
        self.assertEqual(offenders, [], "these name one machine's absolute path")

    def test_the_scan_actually_reaches_files(self):
        """An empty scan is the failure mode this file was written after, not a clean result."""
        _, read = scan_tree(SCANNED_ROOT)
        self.assertGreater(read, 0, "scanned no files at all")


if __name__ == "__main__":
    unittest.main()
