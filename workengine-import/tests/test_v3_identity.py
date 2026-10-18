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

    def test_readme_documents_the_work_objects_and_the_federation_entrypoint(self):
        """README 是读者找到「这些名词是什么、入口怎么跑」的地方。

        断言的是不变量，不是某一版措辞。`a0f370f` 有意重写了 README（旧版把 v3 当
        既成事实陈述，新版把它的缺口写成 target compatibility gap），旧断言冻结的那
        四个字符串随之从文档里消失 —— 实测它们在**任何**契约里都不存在，只在那条断言
        里。所以这里改成问文档现在必须回答的两个问题。
        """
        readme = (ROOT / "README.md").read_text(encoding="utf-8")

        # 工作对象的词表：语义段必须逐个点名，否则读者无法把文档与别处的状态机对上。
        semantics = readme.split("## Flexible work semantics", 1)[1].split("```")[1]
        for term in ("Candidate", "Item", "Activity", "Run", "Outcome"):
            self.assertRegex(semantics, rf"(?m)^{term}\s",
                             f"README 的语义段没有点名 {term}")

        # 一个 Run 留下什么、放在哪里：证据落点是文档必须给出的那个地址。
        self.assertIn("work-items/<project>/<ITEM>/<RUN>", readme)

        # 入口必须是能跑的命令，而不是一句「存在 federation 命令」。
        self.assertIn("serverosctl --json federation", readme)


if __name__ == "__main__":
    unittest.main()
