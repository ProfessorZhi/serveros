# QA2 Round 3 — S1 Windows collection fix #371 独立复核（2026-10-21）

**结论：`#371 PASS`（LOCAL + SYNTHETIC_WIN32，按 PR 声明的 test-only 收集修复范围）；`REAL_WINDOWS_NOT_RUN`。总体仍 `QA_PARTIAL` / `NOT_RELEASE`；无 P0/P1，不签 `QA_SIGNED` / `V1_RELEASE`。**

| 项 | 值 |
|---|---|
| #371 exact head | `dbb1f9f1f7ca5fb38a1591b4171504b21478e71a` |
| base RC-v8 / #369 exact | `a6663075a886093b5ea2e28cd6baf8ca63365f2f` |
| diff | 5 files, +197/−43；仅 tests/probe/doc，无 runtime/workengine 产品代码 |
| #369 RED | 3×`ModuleNotFoundError: No module named 'fcntl'`，0 SKIP / 3 ERROR，rc1 |
| #371 GREEN | 3×`SKIPPED POSIX_ONLY`，0 ERROR；adapter `COLLECTED`，rc0 |
| Linux | #369 三文件 **28P/1S**；#371 三文件 **27P/1S** + adapter **1P** = **28P/1S** |
| 广义 S1（同一 9-file 集） | #369 **97P/23S/23 subtests**；#371 **同一计数**；29 tests collected 两版一致 |
| 真实 Windows | **NOT_RUN**（交 FJPCOS；不能擅称通过） |

---

## 1. 精确范围

#371 只修改：

```text
tests/test_owner_policy_binding.py        gate only
tests/test_owner_commit_fence.py          gate; adapter test moved out
tests/test_owner_policy_reduced_gate.py   gate only
tests/test_owner_commit_fence_adapter.py  new, cross-platform
tests/probes/s1_win32_collection_probe.py new, test-only probe
```

没有修改 `runtime/daemon.py`、`workengine/`、grant/owner policy/credential/
OAuth/服务；没有 merge、deploy、force-push/reset/clean。QA 工作树只新增
`integrations/fleet-v1-qa/qa2/round3/**`。

---

## 2. RED — #369 exact（SYNTHETIC_WIN32）

用 #371 的 test-only probe 对 #369 的三个模块逐文件执行（每个文件独立进程，
模拟 pytest 的单文件收集）：

```text
ERROR tests/test_owner_policy_binding.py: ModuleNotFoundError: No module named 'fcntl'
ERROR tests/test_owner_commit_fence.py:  ModuleNotFoundError: No module named 'fcntl'
ERROR tests/test_owner_policy_reduced_gate.py: ModuleNotFoundError: No module named 'fcntl'
summary skipped=0 errors=1 collected=0   rc=1   (each file)
```

对应日志：`logs/s1-371-red-369.log`（rc1）。这与作者/真实 Windows 报告的
collection failure 一致。级别：**SYNTHETIC_WIN32**，不是真实 Windows。

---

## 3. GREEN — #371 exact（SYNTHETIC_WIN32）

```text
SKIPPED tests/test_owner_policy_binding.py: POSIX_ONLY: runtime/daemon.py imports fcntl ...
SKIPPED tests/test_owner_commit_fence.py:  POSIX_ONLY: ...
SKIPPED tests/test_owner_policy_reduced_gate.py: POSIX_ONLY: ...
COLLECTED tests/test_owner_commit_fence_adapter.py
summary skipped=3 errors=0 collected=1   rc=0
```

对应日志：`logs/s1-371-green-371.log`（rc0）。三个 POSIX-only 模块在
module-level 跳过；adapter 在同样的 synthetic-Windows 环境（`fcntl`
不可用）下仍可 import/收集，没有被错误地跳过。

---

## 4. Gate 的正确性

### 4.1 位于任何 POSIX import 之前

三个文件的顺序均为：

```python
from __future__ import annotations
import os
import pytest

if os.name == "nt":
    pytest.skip("POSIX_ONLY: ...", allow_module_level=True)

import importlib.util / socket / subprocess / ...
daemon_mod = _load("serveros_runtime_daemon", REPO / "runtime" / "daemon.py")
```

- gate 在 line 35/25/25；`runtime/daemon.py` 的 module-level load 在
  line 67/76/51。`fcntl` 只在 daemon 内 import，因此 gate 早于唯一的
  POSIX-only import。
- gate 只依赖 `os`/`pytest` 两个跨平台 import；没有 try/except，不会吞掉
  其他 import/runtime 错误。
- 触发条件只有 `os.name == "nt"`；Linux 的 `os.name == "posix"` 不触发，
  原断言全部运行（27P/1S + adapter 1P）。

### 4.2 只由真 Windows 判断触发？

- 正常 CPython Windows 的 `os.name == "nt"`；正常 Linux 为 `"posix"`。
- 但 `os.name` 是进程内可替换的：probe 正是把 `sys.modules["os"]` 换成
  `name="nt"` 的 proxy 来复现 Windows 路径。也就是说，**能执行进程内代码的
  test harness 可以合成 `os.name` 让三个模块跳过**。这是 test-only 收集
  guard 的固有限制，不是产品安全边界：gate 只跳过本来就无法在 Windows
  收集的 POSIX-only 模块；Linux 的真实 pytest（未替换 os）不跳过；probe
  文件名为 `s1_win32_collection_probe.py`，不在默认 `test_*` 收集中。
- 因此本报告把这一面列为 **SYNTHETIC_WIN32 限制（P2）**，而不是 P0/P1；
  真实 Windows 结论必须由 FJPCOS 的 pytest 给出。

### 4.3 Probe 不会把非 skip 错误伪装成 skip

用三个临时模块对 probe 做分类测试（`logs/s1-371-probe-masking.log`）：

| module-level 行为 | probe 输出 | rc |
|---|---|---|
| `pytest.skip(..., allow_module_level=True)` | `SKIPPED` | 0 |
| `raise RuntimeError("QA2 non-skip failure")` | `ERROR RuntimeError` | 1 |
| `import qa2_definitely_missing_module` | `ERROR ModuleNotFoundError` | 1 |

probe 先捕获 `pytest.skip.Exception`，其余 `BaseException` 一律计为 ERROR；
没有 `except ImportError: skip`、没有按错误文本降级为 skip。错误计数直接
决定 rc（`0 if errors == 0 else 1`）。

### 4.4 Adapter 的跨平台性

`tests/test_owner_commit_fence_adapter.py`：

- 不 import `runtime/daemon.py`、`socket`、`fcntl`、`SO_PEERCRED` 或
  `os.getuid()`；只 import `time`、`pytest`、`workengine.authority`、
  `workengine.serveros_authority`。
- grant payload 使用固定的 `"os_principal": "uid:4242"`；adapter 只检查
  `os_principal` 非空，不需要当前 OS uid。
- Linux 真实 pytest：**1 passed**；SYNTHETIC_WIN32：**COLLECTED**。
- 它保留原断言：owner-backed `verified:true` + `point_in_time` +
  `atomicity=BLOCKED_OWNER_COMMIT_SEAM` 必须抛
  `AuthorityError("BLOCKED_OWNER_COMMIT_SEAM")`。没有减少断言。
- 真实 Windows pytest 的 **执行** 仍是 `NOT_RUN`；`COLLECTED` 只证明
  module import，不等于 Windows 上测试运行通过。

---

## 5. 原 Linux 负测 / no-lease / revoke-after-read / COMMIT_FENCED 断言未减少

### 5.1 三文件计数

| revision | 三文件 | adapter | 合计 |
|---|---|---|---|
| #369 `a666307` | **28P/1S** | （adapter test 在 `test_owner_commit_fence.py` 内） | 28P/1S |
| #371 `dbb1f9f` | **27P/1S** | **1P** | **28P/1S** |

`--collect-only` 两版均为 **29 tests collected**；#371 只是把
`test_the_adapter_refuses_an_owner_backed_positive_without_commit_fence`
从 POSIX 文件移到 cross-platform 文件，函数与断言相同。唯一 skip 是既有的
`BLOCKED_MULTIPRINCIPAL_NOT_RUN`（uid 1002，无 passwordless sudo/setuid）。

### 5.2 具体断言保留

- `tests/test_owner_commit_fence.py:255`
  `test_a_revoke_after_the_last_read_cannot_acquire_a_lease` 仍在；line 271
  仍断言 `store.active_lease(RESOURCE, now=time.time()) is None`（no lease）。
- `tests/test_owner_commit_fence_adapter.py:48`
  `test_the_adapter_refuses_an_owner_backed_positive_without_commit_fence`
  仍断言 `AuthorityError("BLOCKED_OWNER_COMMIT_SEAM")`。
- `tests/test_owner_policy_binding.py` 的 revoked/non-bool、future
  `issued_at`、BLOCKED_ATOMICITY、point-in-time audit 等负测均保留
  （27P/1S 中运行）。
- 广义 9-file 集（`test_owner_policy_binding`、`test_owner_commit_fence`、
  `test_owner_policy_reduced_gate`、`test_owner_commit_fence_adapter`、
  `test_v1_grant_epoch_authority`、`test_v1_grant_os_principal_schema`、
  `test_serveros_lease_authority`、`test_fleet_owner_grant`、
  `test_fleet_owner_grant_template`）在 **#369 与 #371 均为
  97P/23S/23 subtests**；计数逐项相同，**没有断言减少**。

### 5.3 117P/1S/23 的说明

作者 receipt 为 `117 passed, 1 skipped, 23 subtests`。独立环境同一 9-file 集
为 `97 passed, 23 skipped, 23 subtests`：其中 **22 个 skip** 是
`tests/test_serveros_lease_authority.py:56-57` 的
`SERVEROS_LEASE_AUTHORITY_REF`/`fc935900…` #272 source 不在本 checkout；
另有 1 个 two-uid skip。**23 subtests 与作者 receipt 一致**；#369 与 #371
的 97P/23S/23 完全相同。这是 `SOURCE_NOT_PRESENT` 的环境限制，不是 #371 的
FAIL；117P/1S 未在本文环境中独立复现，不能冒充已复现。

---

## 6. 证据分类

| 级别 | 结果 |
|---|---|
| `LOCAL` | Linux pytest：#369 28P/1S；#371 27P/1S + adapter 1P；广义 9-file 两版 97P/23S/23；probe 分类测试 skip/error 正确 |
| `SYNTHETIC_WIN32` | #369 3 ERROR fcntl rc1；#371 3 SKIP/0 ERROR + adapter COLLECTED rc0 |
| `REAL_WINDOWS_NOT_RUN` | FJPCOS 真实 pytest 未由本 QA 执行；不能把 SYNTHETIC_WIN32 或 Linux 结果说成真实 Windows 通过 |
| `SOURCE_NOT_PRESENT` | 22 个 #272 source 测试 skip；23 subtests 仍匹配；#369/#371 计数一致 |

### 日志

```text
logs/s1-371-369-three.log       28P/1S
logs/s1-371-371-three.log       27P/1S
logs/s1-371-adapter.log         1P
logs/s1-371-red-369.log         3 ERROR fcntl, rc1
logs/s1-371-green-371.log       3 SKIP/0 ERROR + COLLECTED, rc0
logs/s1-371-369-broad.log       97P/23S/23
logs/s1-371-371-broad.log       97P/23S/23
logs/s1-371-probe-masking.log   skip rc0; RuntimeError rc1; ImportError rc1
```

---

## 7. 结论与建议

- **#371 在 test-only 收集修复范围内：PASS（LOCAL + SYNTHETIC_WIN32）。**
  RED 在 #369 exact 复现，GREEN 在 #371 exact 成立；gate 在 daemon/fcntl
  import 之前；probe 不会把其他错误伪装成 skip；adapter 跨平台可收集且在
  Linux 真实 pytest 通过；原授权负测/no-lease/revoke-after-read/
  COMMIT_FENCED adapter 断言未减少。
- **REAL_WINDOWS_NOT_RUN。** FJPCOS 必须运行真实 pytest：三文件应
  SKIP、adapter 应 PASS；在该结果回来前不能签 Windows/V1。
- **P2 限制：** `os.name` 可被进程内 synthetic proxy 替换（probe 本身即如此），
  probe 的 os proxy 保持 POSIX `os.path` 且只屏蔽 `fcntl`，`COLLECTED` 只是
  module import；这些都是 SYNTHETIC_WIN32 的边界，不是产品漏洞，也不能替代
  真实 Windows。
- **总体：`QA_PARTIAL` / `NOT_RELEASE`；无 P0/P1；不签 QA_SIGNED。**
