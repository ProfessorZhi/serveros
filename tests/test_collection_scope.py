#!/usr/bin/env python3
"""裸 `pytest` 收集到哪里 —— 决定「跑什么」的两个量具必须说同一句话。

WHY THIS FILE EXISTS
--------------------
这个仓库里有两个东西在回答「套件是什么」。一个是 `scripts/verify.py` 的
census，它枚举 `tests/test_*.py`；另一个是裸 `pytest`，它按 `python_files`
的默认 glob 在**整棵树**里找。它们不一致的时候没有任何东西会说：多出来的
条目看起来就是套件的一部分，少掉的那些看起来就是没写。

2026-09-29 实测到的是前者。`runtime/test_contract.py` 是 Project Testing
Contract 的读取器，本轮新增；它不是测试，但它有一个**正常的公开 API 恰好叫
`testing_dir(roots)`**，名字命中 `python_functions` 的 `test*`。于是整个运
行时模块被当成测试文件收集，全套的第一个条目是：

    ERROR runtime/test_contract.py::testing_dir
    E  fixture 'roots' not found

**这件事坏在哪**：一个 collection error 与一条真的跑了并失败的用例，在 `-q`
里长得一样。「套件是绿的」这句话在两种情况下都会被说出来，而它底下少掉的
是一个模块的全部内容。同族最短形态是 `[ "" = "" ]` —— 两次查询都失败，比较
的结果是相等。

TWO THINGS HAVE TO HOLD, AND WHY BOTH ARE CHECKED
--------------------------------------------------
1. 盘上不该有 `tests/` 之外、而名字命中 `python_files` 的模块。
2. `pytest.ini` 的 `testpaths` 与 census 枚举的必须是同一片地方。

第一条防的是「有人再写一个 `runtime/test_*.py`」；第二条防的是「第一条被绕过
之后，裸 `pytest` 悄悄去看别处」。少任何一条，另一半都还能自己漂回去。

NEGATIVE CONTROLS
-----------------
`test_the_pattern_would_have_caught_it` 是这一套判据的正控：它拿**当时真的在
盘上的那个路径**去问同一个判据。没有这一步，上面那条扫描在一个取空了的
`python_files` 上也会绿 —— 「量具说 0」与「真的没有」之间隔着「量具看到了
多少」，同族见 `tests/test_no_node_absolute_paths.py` 的 `test_the_scan_
actually_reaches_files`。
"""
from __future__ import annotations

import configparser
import fnmatch
import importlib.util
import sys
import unittest
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
REPO = HERE.parent

#: 走树时要跳过的目录。`.pytest_cache` 会在仓库根自己长出来，`__pycache__`
#: 到处都有 —— 两者装的是由一个模块派生出来的字节，不是模块本身。
SKIPPED_DIRS = frozenset({".git", ".pytest_cache", "__pycache__", "node_modules"})


def _load(name: str, path: Path):
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def collection_patterns() -> list[str]:
    """pytest 自己认的收集 glob，从 pytest 自己那里读。

    不在这里另抄一份 `["test_*.py", "*_test.py"]`：抄一份就是造第二个真相，
    而将来改的是 pytest 那一个。`_pytest.python` 是注册 `python_files` 这个
    ini 选项的插件 —— 不 import 它，`Config.getini` 会说这个值根本不存在，
    那是「问错了人」，不是「值是空」。
    """
    manager = pytest.PytestPluginManager()
    manager.import_plugin("_pytest.python")
    return list(pytest.Config(manager).getini("python_files"))


def python_modules_outside_the_suite() -> list[str]:
    """`tests/` 之外的全部 `.py`，按仓库相对路径排序。"""
    found: list[str] = []
    for path in REPO.rglob("*.py"):
        rel = path.relative_to(REPO)
        if rel.parts[0] == HERE.name or SKIPPED_DIRS.intersection(rel.parts):
            continue
        found.append(rel.as_posix())
    return sorted(found)


def collected_by_name(rel: str, patterns: list[str]) -> bool:
    """pytest 是按**文件名**决定收不收的，所以这里也只问文件名。"""
    return any(fnmatch.fnmatch(Path(rel).name, pattern) for pattern in patterns)


def declared_collection_scope() -> list[str]:
    """`pytest.ini` 声明的 `testpaths`；没有这份文件就是空。"""
    ini = configparser.ConfigParser()
    if not ini.read(REPO / "pytest.ini"):
        return []
    return [line.strip()
            for line in ini.get("pytest", "testpaths", fallback="").splitlines()
            if line.strip()]


class CollectionScopeTest(unittest.TestCase):
    def test_nothing_outside_the_suite_is_named_like_a_test(self):
        patterns = collection_patterns()
        offenders = [rel for rel in python_modules_outside_the_suite()
                     if collected_by_name(rel, patterns)]
        self.assertEqual(
            offenders, [],
            f"这些在 tests/ 之外，而 pytest 会把它们当测试收集：{offenders}")

    def test_the_scan_actually_reaches_the_runtime(self):
        """空扫描是这份文件要防的那个失败，不是一次干净的结果。"""
        self.assertIn("runtime/cli.py", python_modules_outside_the_suite())

    def test_the_pattern_would_have_caught_it(self):
        """正控：判据认得出**真的发生过的那一个**，也认得出它的近亲。

        没有这一条，上面那条扫描在 `python_files` 取空时也会绿。
        """
        patterns = collection_patterns()
        self.assertNotEqual(patterns, [], "pytest 报不出自己的收集 glob，判据是空的")
        self.assertTrue(collected_by_name("runtime/test_contract.py", patterns))
        self.assertTrue(collected_by_name("runtime/contract_test.py", patterns))
        # 改名之后的那个名字，以及 `runtime/` 下其余模块的形状，都不该命中。
        self.assertFalse(collected_by_name("runtime/testing_contract.py", patterns))
        self.assertFalse(collected_by_name("runtime/cli.py", patterns))

    def test_the_declared_scope_is_the_one_the_census_enumerates(self):
        """两个量具说的必须是同一句话 —— 本次不一致的正是这两个。

        比的是**跑出来的那份枚举**，不是再抄一遍 `tests`：`discovered_tests()`
        是 `scripts/verify.py` 真正用来决定跑什么的那一个函数。
        """
        census = _load("serveros_scripts_verify_for_collection_scope",
                       REPO / "scripts" / "verify.py")
        enumerated = {Path(rel).parts[0] for rel in census.discovered_tests(REPO)}
        self.assertTrue(enumerated, "census 枚举到 0 个测试文件，这一条没有内容可比")
        self.assertEqual(
            set(declared_collection_scope()), enumerated,
            "pytest.ini 的 testpaths 与 scripts/verify.py 的 census 不是同一片地方")


if __name__ == "__main__":
    unittest.main()
