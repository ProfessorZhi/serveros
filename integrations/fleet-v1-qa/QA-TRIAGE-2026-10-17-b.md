# Fleet V1 独立 QA — Snapshot B（2026-10-17 晚）

- **角色**：独立 QA lane（campaign `ProfessorZhi/serveros#244` / integration `#250`）。非 S2/F1/G1 作者，非 I1 integrator。
- **状态**：`QA_PARTIAL`。**没有** QA PASS，**没有** signature，**没有** `V1_COMPLETE`。
- **与 Snapshot A 的关系**：`QA-TRIAGE-2026-10-17.md` 是上一快照的 source-of-evidence，不因本次更新变成 PASS。本文件只记录新 pin 上的独立复跑。
- **边界**：仍只改 `integrations/fleet-v1-qa/**`；未改 lane 源、未改 I1 目录、未接触生产/供应商/凭据/网络。

## 1. 快照（live head 已逐一核对）

快照时间 `2026-10-17T23:19+08:00`；`git fetch --all --prune` exit 0。

| 对象 | 本快照精确 head | live head | 相对 Snapshot A |
|---|---|---|---|
| F1 #243 | `4a5d0a06f2452424972d66399b7c2b60dc8d03a1` | 一致 | 新（`494f452` 之后 1 commit，修 guard） |
| S2 #252 | `f2b89e1275a0fdc0b460be0f8c1b82369c75686f` | 一致 | 新（`dac3088` 之后 1 commit，修 lookup-absence） |
| G1 #255 | `619cf637cbdba3020f989cee3d9f72b7f2b25ce5` | 一致 | **未变**；B3/B4 只对本 SHA |
| I1 #254 | `3e23ef24f161b8f1f6197dcf8feafc08e889be98` | 一致 | 新（刷新 pins、加入 G1） |
| S1 #241 | `d0754edfd65a490213a40b3033a70a43b0a4a187` | 一致 | 未变 |
| work-engine #13 | `40b062209de4a989a84b04ea57b6cc8657e1be1e` | 一致 | 未变 |

**新发现的 pin 漂移（只记录，不改 I1 文件）**：I1 `3e23ef2` 的 manifest 仍 pin **S2=`dac3088`** 与 **F1=`494f452`**，二者都落后当前 live head 一个 commit；本次被审的两个修复均不在 manifest pin 内。G1 `619cf63` 已加入 candidates，`stacks_on=243`（按分支关系），但 F1 的 pin 本身也是旧的。

`qa` 块：`status=PENDING`、`independent_of_integrator=true`、`signature=null`。本 lane 只提供部分 receipt，不填 signature，不改该文件。

## 2. 环境（GREEN 判定）

- load `11.26 / 12.42 / 13.40`（128 vCPU）；cpu PSI some avg10 `0.18`；io some/full avg10 `0.37`；memory PSI `0.00`。
- MemAvailable 170 GiB；**swap 已用约 30.0 GiB / 32 GiB（≈89.5%）**，与 Snapshot A 相同量级；强制 `MemorySwapMax=0`。
- 无 `pytest` / `serverosctl test` 进程。资源满足单条受限命令条件。

## 3. 唯一一条受限命令与结果

```sh
systemd-run --user --scope \
  -p MemoryMax=1G -p MemoryHigh=768M -p CPUQuota=25% -p MemorySwapMax=0 \
  -- bash -c 'ulimit -v 1048576; ...'
```

### 3.1 F1 `4a5d0a`：guard **红→绿，独立复跑成立**

把 `4a5d0a` 的 tree `git archive` 到证据目录，跑**完整** `tests/test_no_node_paths_in_product_data.py`（不是只跑单个类）：

```sh
PYTHONPATH=<tree>/runtime:<tree> python3 tests/test_no_node_paths_in_product_data.py -v
```

结果：

```text
Ran 10 tests in 14.102s
OK
f1_guard_exit=0
```

`TheProductDataNamesNoNodeTests.test_product_data_names_a_node_only_where_it_is_declared` 从上一快照的 `{'fleet_mcp/deploy/release_plan.py': 1}` 变为 `{}`。修法是把活值改成 `Path("~/runtime-assets/serveros-fleet-mcp/ingress").expanduser()`，扫描器不再把该字符串视为节点绝对路径。**P0-3 在 `4a5d0a` 上 CLOSED（LOCAL）**；这不是全量套件绿，也不代表其他 lane 的测试通过。

### 3.2 S2 `f2b89e`：一项修复成立，三项 P0/P2 仍开放

探针源码：`probe/qa_probe_f2b.py`；18/18 复现，`probe_exit=0`。

| 检查 | 实测 | 判定 |
|---|---|---|
| S7a `NOT_FOUND` | `REFUSE_UNKNOWN` | **lookup-absence 修复成立（LOCAL）** |
| S7b `MISSING` | `REFUSE_UNKNOWN` | 同上 |
| S7c `ABSENT` | `REFUSE_UNKNOWN` | 同上 |
| S7d `NOT_APPLIED` 无 `source` | `REFUSE_UNKNOWN` | **provenance 必填成立（LOCAL）** |
| S7e `NOT_APPLIED` 带 `source` | `SETTLE_NOT_APPLIED` | 语义正确，但 `source` 只是未校验字符串 |
| S3 SETTLED 后晚到回复 | `REFUSE_NOT_UNKNOWN` | kernel 不变量仍成立 |
| S0/S1/S6 | router 记录仍无 `principal`；跨 principal + 自报 `source` 仍 `SETTLE_APPLIED`；daemon `agent.dispatch.record` 仍接受调用方 `phase`/`principal` 且从不 reconcile | **P0-2 仍开放** |
| S2 | `current_writer={}` 仍与空 reconciler 相等 → `SETTLE_APPLIED` | **P2 仍开放** |
| S4/S5 | `intent_digest` 仍不含 `session_ref`/`agent_runtime`，router 调用点也未传 | **P0-1 仍开放** |

**新增的 source-level 推论（未单独执行，标 SOURCE）**：`source` 只要求非空字符串，不查生产者登记。对无 `principal`、无 `work_item` 的 router 形状记录，一个自报 `source` 的 `NOT_APPLIED` 会得到 `SETTLE_NOT_APPLIED` 并把 attempt 退回 `CALL_NOT_SENT`，从而**放开重试**——这是比 `APPLIED` 方向更危险的伪造（可能产生第二次效果）。与 S1 的跨 principal 缺口同根。

### 3.3 G1 `619cf63`（未变 SHA，B3/B4 只对本 SHA）

| 检查 | 实测 | 判定 |
|---|---|---|
| B1/B2 `health`/`policy` 键缺失 | `UNKNOWN`/`UNKNOWN`，`effective=None` | 既有负面不变量成立 |
| B3/B4 键存在但值为 `null` | `READY`/`ALLOWED`，`effective="provider:alpha"` | **P1 仍开放**（present-but-null 绕过 UNKNOWN） |

G1 在本次快照期间没有新 head；**没有**把 B3/B4 的结论外推到未来的 G1 commit。若 G1 更新，需要新快照重跑。

## 4. 逐 claim 的证据级别

| Claim | Level | 依据 |
|---|---|---|
| F1 guard `4a5d0a` 红→绿（10 tests） | **LOCAL** | 命令 + exit 0 + `Ran 10 tests ... OK` |
| S2 lookup-absence 不再证明 `NOT_APPLIED` | **LOCAL** | S7a–S7c（精确 SHA 上执行 kernel） |
| S2 observation 必须带 `source` | **LOCAL** | S7d |
| S2 同 key 换 target/runtime 仍为 REPLAY | SOURCE + LOCAL 探针 | S4/S5 |
| S2 跨 principal / 自报 source 仍可 SETTLE_APPLIED | SOURCE + LOCAL 探针 | S0/S1/S6 |
| S2 空 writer 记录相等 | LOCAL 探针 | S2 |
| S2 晚到回复不覆盖 SETTLED | LOCAL 探针（通过） | S3 |
| G1 present-but-null 绕过 UNKNOWN | LOCAL 探针（`619cf63`） | B3/B4 |
| I1 manifest pin 落后 live head | SOURCE | manifest + `git ls-remote` |

**没有任何 REAL_DEVICE 或 CLIENT_E2E 证据**；没有运行全量套件、T1–T12 LOCAL 场景、Fleet MCP discovery 或 client E2E。

## 5. 状态变化

| ID | Snapshot A | Snapshot B | 说明 |
|---|---|---|---|
| P0-1 S2 digest 漏 target/runtime | OPEN | **OPEN** | `f2b89e` 未触 `intent_digest` |
| P0-2 S2 信任判决未接生产/可自证 | OPEN | **OPEN** | `f2b89e` 只加 `source` 与非同义词收紧；daemon 写入口未改 |
| P0-3 F1 guard 失败 | OPEN | **CLOSED (LOCAL)** | `4a5d0a` 10/10 OK |
| P1 G1 present-but-null | OPEN | **OPEN** | SHA 未变 |
| P2 S2 空 writer 相等 | OPEN | **OPEN** | 未改 |
| P1 I1 pin 漂移 | OPEN | **OPEN（新形态）** | manifest 自己仍 pin 旧 S2/F1 |

**Next owners**：P0-1/P0-2/P2 → S2 `f2b89e` 之后；P1 G1 → G1；pin 漂移 → I1（下一次 composition 前重新 pin 到 live head）。

## 6. 证据

| 内容 | 路径 | sha256 |
|---|---|---|
| 本次受限命令日志（F1 guard + probe） | `/data/home/vince/artifacts/skills/automated-testing/v1-qa-20261017/run-2.log` | `4b3d397245f5ccde6ce618e94f7f3d9238011f41c7a3ad9c8a077e81f9cc448e` |
| 本次探针源码 | `/data/home/vince/artifacts/skills/automated-testing/v1-qa-20261017/qa_probe_f2b.py` | `fc6d0b34690f28e800eca0924fac4ca95e19124261b7c9b51c4fdfeeaac654ca` |
| Snapshot A 证据 | `QA-TRIAGE-2026-10-17.md` §6 | 保持不变 |

**最终判定：`QA_PARTIAL`。** P0-1、P0-2 与 G1 P1 未关闭前，不签 QA PASS，不进入 RC composition。
