# QA2 Round 4 — test-only #374 独立复核（2026-10-21）

**结论：`#374 PASS`（仅限其声明的 test-only 范围：`LOCAL` + `SYNTHETIC_WIN32`）；`REAL_WINDOWS_NOT_RUN`。总体仍 `QA_PARTIAL` / `NOT_RELEASE`；无 P0/P1；P2×3（probe 进程缓存限制、SYNTHETIC_WIN32 边界、117P source 未复现）。不签 `QA_SIGNED` / `V1_RELEASE`。**

| 项 | 值 |
|---|---|
| #374 exact head | `9e84be33fb29628fa70d63aa52bac950f436feb1` |
| base RC-v9 / #372 exact | `31bfc28d9ff4a1d0a56914fb8e4b82ec6ebf85fd` |
| diff | 3 test/probe files, +155/−18；无 runtime/workengine/Grant/Owner policy 产品代码 |
| #372 RED | epoch 单文件 `ModuleNotFoundError: fcntl`；旧 probe repo-only **rc0 空跑 0/0/0**；无参 **IndexError rc1** |
| #374 GREEN | epoch 13P→13P；changed set **46P/1S**；probe 回归 **5P**；SYNTHETIC_WIN32 **4 SKIPPED / 1 COLLECTED / 0 ERROR** |
| 真实 Windows | **NOT_RUN**（FJPCOS 未测 #374；不能把 SYNTHETIC_WIN32 记成真实 Windows） |

---

## 1. 精确范围

#374 只修改：

```text
tests/test_v1_grant_epoch_authority.py      gate only, no assertion change
tests/probes/s1_win32_collection_probe.py   argument/existence/zero-collected guards
tests/test_s1_win32_collection_probe.py     new, cross-platform regression
```

没有修改 `runtime/`、`workengine/`、Grant、owner policy、credential、OAuth 或
服务；产品树与 #372 相同。QA 工作树只新增
`integrations/fleet-v1-qa/qa2/round4/**`。

---

## 2. #372 RED 复现

### 2.1 epoch 单文件

用 #372 自带 probe 对 epoch 文件：

```text
ERROR tests/test_v1_grant_epoch_authority.py: ModuleNotFoundError: No module named 'fcntl'
summary skipped=0 errors=1 collected=0
rc=1
```

日志：`logs/s1-374-372-probe-epoch.log`。符合真实 Windows 的 collection
失败。

### 2.2 旧 probe 的空跑与无参

```text
python tests/probes/s1_win32_collection_probe.py <repo>
  summary skipped=0 errors=0 collected=0
  rc=0                                  ← vacuous green（#374 要修的缺陷）

python tests/probes/s1_win32_collection_probe.py
  IndexError: list index out of range
  rc=1                                  ← 没有原因
```

日志：`logs/s1-374-372-probe-repoonly.log`、
`logs/s1-374-372-probe-noargs.log`。原因在 #372 的
`tests/probes/s1_win32_collection_probe.py:36-37,88-89`：`FILES` 可以为空，
`return 0 if errors == 0 else 1` 让 0 文件/0 collected 成为 rc0。

---

## 3. #374 GREEN 与 probe 守卫

### 3.1 参数/收集矩阵（SYNTHETIC_WIN32）

| 调用 | 输出 | rc |
|---|---|---|
| 无参 | `ERROR no test files ... usage` | **2** |
| repo only | `ERROR no test files ... usage` | **2** |
| 缺失文件 | `ERROR test file does not exist ...` | **2** |
| 非 `.py`（`pytest.ini`） | `ERROR test file is not a .py file ...` | **2** |
| epoch 单文件（1 skip / 0 collected） | `ERROR zero modules collected ...` | **2** |
| 3 POSIX + adapter | 3 SKIPPED / 1 COLLECTED / 0 ERROR | **0** |
| epoch + adapter | 1 SKIPPED / 1 COLLECTED / 0 ERROR | **0** |
| epoch + 3 POSIX + adapter（changed set） | 4 SKIPPED / 1 COLLECTED / 0 ERROR | **0** |

日志：`logs/s1-374-probe-matrix.log`。`_parse_args`
（`tests/probes/s1_win32_collection_probe.py:67-85`）检查 repo 目录、文件存在、
`.py` 后缀；`main`（`:88-136`）在 `errors>0` 时 rc1、`collected==0` 时 rc2、
否则 rc0。0 collected 不再空跑成功。

### 3.2 Epoch gate

`tests/test_v1_grant_epoch_authority.py:24-37`：

```python
import os
import pytest

if os.name == "nt":
    pytest.skip(
        "POSIX_ONLY: runtime/daemon.py and runtime/agent_control_store.py "
        "import fcntl; the epoch read uses the daemon route and peer",
        allow_module_level=True)

import importlib.util
import sys
from pathlib import Path
import owner_policy_fixtures as opf
...
daemon_mod = _load("serveros_runtime_daemon", "daemon.py")   # line 66
```

gate 在 line 33，daemon 的 module-level load 在 line 66；`agent_control_store`
由 daemon 在 module scope 加载，也 import fcntl。gate 早于二者，且早于
`importlib/sys/Path/owner_policy_fixtures`。`import os`/`pytest` 是跨平台
import；没有 try/except 包裹，其他 import/runtime 错误不会被吞。

### 3.3 有没有跨平台安全断言被整文件吞掉？

**没有。** 13 个 epoch 测试逐一检查：

- 11 个使用 `daemon` fixture（`daemon_mod.Daemon` → agent-control store →
  `fcntl`）；
- `test_the_epoch_survives_a_restart(tmp_path)` 直接创建
  `daemon_mod.Daemon`；
- `test_the_read_mutates_no_lease_and_no_grant(daemon, tmp_path)` 使用
  daemon + sqlite3。

`verify` helper（`:203`）调用
`daemon_mod.agent_control_store_mod.verify_live_grant`，而 agent-control
store 自身顶层 import fcntl；没有只依赖纯 `OwnerPolicy`/纯函数的测试。
因此整文件 skip 是合理的，**没有可跨平台的安全断言被吞掉**；#371 的
`test_owner_commit_fence_adapter.py` 仍保留 Windows 可执行的
COMMIT_FENCED 拒绝断言。故不满足 P1 条件。

---

## 4. Linux 断言计数（LOCAL）

| Gate | 结果 | 日志 |
|---|---|---|
| #372 epoch 单文件 | **13 passed** | `logs/s1-374-372-epoch13.log` |
| #374 epoch 单文件 | **13 passed** | `logs/s1-374-374-epoch13.log` |
| #374 changed set（epoch + 3 POSIX + adapter + new probe test） | **46 passed, 1 skipped** | `logs/s1-374-374-changed46.log` |
| #374 new probe regression | **5 passed** | `logs/s1-374-374-probe-regression5.log` |
| broad 9-file（含 adapter），#372 | **97 passed, 23 skipped, 23 subtests** | `logs/s1-374-372-broad9.log` |
| broad 9-file（含 adapter），#374 | **97 passed, 23 skipped, 23 subtests** | `logs/s1-374-374-broad9.log` |

唯一的 Linux skip 是既有的
`BLOCKED_MULTIPRINCIPAL_NOT_RUN`（uid 1002，无 passwordless sudo/setuid）；
没有新增 Linux skip。

作者 receipt 的 broad S1 是 `117P/1S/23 subtests`。本环境同一 9-file 集为
`97P/23S/23`：23 个 skip 是 22 个 `#272 source`
（`SERVEROS_LEASE_AUTHORITY_REF`/`fc935900…`）缺失 + 1 个 two-uid。
**#372 与 #374 计数完全相同，23 subtests 与作者一致**；117P/1S 是 #272
source 存在时的 receipt，未在本文环境独立复现。这是
`SOURCE_NOT_PRESENT`，不是 #374 的 FAIL，也不冒充已复现。

---

## 5. 缓存/进程污染复核

用户特别要求“同一 Python 进程已有旧 fcntl 部分初始化模块时不能用缓存掩盖
RED”。独立 wrapper（`probes/cache_contamination_wrapper.py`）结果：

| 预加载状态 | probe / 文件 | 结果 | rc |
|---|---|---|---|
| `fcntl` 完整 import | #372 probe / #372 epoch | **ERROR fcntl** | 1 |
| `fcntl` partial（`ModuleType` 占位） | #372 probe / #372 epoch | **ERROR fcntl** | 1 |
| `serveros_runtime_daemon` 完整 import | #372 probe / #372 epoch | **COLLECTED** | **0** |
| `serveros_runtime_daemon` 完整 import | #374 probe / #372 epoch（无 gate） | **COLLECTED** | **0** |
| `serveros_runtime_daemon` 完整 import | #374 probe / #374 epoch（有 gate） | **SKIPPED + zero collected** | **2** |

解读：

- **`fcntl` 缓存不会掩盖 RED**：probe 在 `sys.modules.pop("fcntl", None)` 后
  安装 `_NoFcntl` blocker；完整/部分 fcntl 都被清除，daemon 的 `import
  fcntl` 仍触发 `ModuleNotFoundError`。
- **daemon 缓存可以掩盖旧文件的 RED**：test modules 的 `_load` 在
  `sys.modules` 命中时直接返回缓存，probe 没有清除
  `serveros_runtime_daemon`/`serveros_runtime_agent_control_store`；若同一进程
  已完整 import daemon，则无 gate 的 #372 epoch 文件会显示 COLLECTED/rc0。
  这是 **P2 probe 进程污染限制**，不是 #374 gate 的问题：在 #374 tree 上
  epoch 的 gate 在 daemon 使用前执行，cached daemon 下仍 SKIPPED，且
  zero collected 使 probe 返回 rc2。probe 的规范调用是
  `python probe ...`（独立进程）；`test_s1_win32_collection_probe.py` 用
  `subprocess.run` 也是独立进程，故标准回归不受影响。
- 最小修复（若要让 probe 对 in-process 调用也 fail-closed）：在
  `main` 开始时检查 `serveros_runtime_daemon`/
  `serveros_runtime_agent_control_store`（必要时 registry/actor）是否已在
  `sys.modules`；若是则 rc2 拒绝，或显式清除；并加一条预加载 daemon 的回归
  测试。若 CI 以 in-process 方式把它当 gate，这条应升为 P1；当前文档/回归
  都是独立进程，故记为 P2。

---

## 6. Probe 的 SYNTHETIC_WIN32 边界（P2）

- `_WindowsOS` 让被导入模块看到 `os.name="nt"`，但保留 POSIX `os.path`，
  只屏蔽 `fcntl`；它不是完整 Windows 环境。
- `COLLECTED` 是 module import 成功，不是 pytest 在该 OS 上收集/执行。
- 合成 `os.name` 可以触发 gate；gate 是 test-only collection guard，不是
  OS attestation。Linux 真实 pytest 的 `os.name="posix"` 不触发：13P。
- **REAL_WINDOWS_NOT_RUN**：FJPCOS 尚未测 #374；真实 pytest 的
  epoch 1 SKIP/0 ERROR、canonical probe rc0、adapter 执行必须由 FJPCOS
  给出。不能把本报告的 SYNTHETIC_WIN32 或 Linux 结果写成真实 Windows
  PASS。

---

## 7. 生产未改 / 状态

- `git diff 31bfc28..9e84be3` 仅 3 个 test/probe 文件；`runtime/`、
  `workengine/`、Grant、owner policy、Provider、OAuth、服务均未改。
- #374 的 test-only fix 不影响 RC-v9 产品内容；下一 RC 集成仍等真实 Windows
  证据与独立 QA。
- **总体 `QA_PARTIAL` / `NOT_RELEASE`；#374 在 test-only 范围 PASS；
  无 P0/P1；P2×3；不签 `QA_SIGNED`。**

### 证据分类

| 级别 | 结果 |
|---|---|
| `LOCAL` | epoch 13P→13P；changed 46P/1S；probe 回归 5P；broad 97P/23S/23 两版一致 |
| `SYNTHETIC_WIN32` | #372 epoch ERROR fcntl；#374 4 SKIP/1 COLLECTED/0 ERROR；probe 参数/零 collected 守卫全部正确 |
| `REAL_WINDOWS_NOT_RUN` | FJPCOS 未测 #374；无真实 Windows PASS 声明 |
| `SOURCE_NOT_PRESENT` | 22 个 #272 source skip；117P/1S 未独立复现；23 subtests 匹配 |
