import subprocess
import sys
import tomllib
import unittest
from pathlib import Path

import workengine


ROOT = Path(__file__).resolve().parents[1]


class ServerOSV3IdentityContractTest(unittest.TestCase):
    def test_distribution_metadata_declares_v3_release(self):
        metadata = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
        self.assertEqual(metadata["project"]["version"], "0.3.0")
        self.assertIn("ServerOS v3", metadata["project"]["description"])
        self.assertEqual(workengine.__version__, "0.3.0")

    def test_cli_help_identifies_the_v3_control_plane(self):
        result = subprocess.run(
            [sys.executable, "-m", "workengine", "--help"],
            cwd=ROOT,
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("ServerOS v3 control plane", result.stdout)

    def test_readme_describes_v3_information_to_feedback_and_federation_entrypoints(self):
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        self.assertIn("ServerOS v3 runtime control plane", readme)
        self.assertIn("Source → Observation → Claim → Candidate Item → Item", readme)
        self.assertIn("→ Evidence → Deliverable → Feedback", readme)
        self.assertIn("serverosctl --json federation", readme)


if __name__ == "__main__":
    unittest.main()
