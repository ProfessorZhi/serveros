# Fleet V1 里程碑主动播报（#417）

本目录是 #417 的**最小离线工作包**：把「可信源事件 → 里程碑判定 → 可选 Agent
简报 → 现有 outbound provider」做成一个薄适配。它不是第五套 Fleet 子系统，
也不是第二套长期事件总线。

## 边界

- **设备侧只产出事实**：CloudCLI `run_get`、GitHub Actions `workflow_run`、
  已被签收的 phase 里程碑。归一化函数不发送、不读凭据。
- **东京是唯一出口**：`MilestoneNotifier` 只接收调用方注入的
  `HumanControlProvider`（生产上是 `runtime/direct_lark_notify.py` +
  `runtime/message_transport.py`）。本模块不读收件人环境变量、不执行
  `lark-cli`、不开 socket、不持 token。
- **只有回执账本，没有事件总线**：SQLite 表只存稳定 delivery key、状态
  （`IN_FLIGHT` / `DELIVERED` / `DELIVERY_UNKNOWN`）与安全 intent；没有订阅者、
  队列、轮询线程或全天 LLM 推理。投递前先原子 `claim`，`DELIVERED` 是终态，
  晚到的 UNKNOWN 不能把它覆盖回未投递。
- **不导入 #407 的在途实现**（`fleet_mcp/events.py`、`run_watcher.py`、
  `runtime/fleet_event_delivery.py`）。只复用 main 上已有的
  `runtime/notification-contract.yaml` 严重度轴（INFO / ATTENTION / ACTION）、
  `runtime/notifications.py#capability_of` 与现有 outbound provider。

### 为什么不冒充 §10 事件

合同把 `turn.completed` / `session.waiting_input` / `approval.required` 等
明确声明为 `DECLARED_ABSENT`（“INFO 本身不值得一条外发消息”）。里程碑触发与
告警/事件列表在 #417 里本来就是两条轴；强行复用事件名会让仓库的 producer
扫描多出一个“新生产者”，也会把成功塞进报警语义。因此本模块**只使用合同的
严重度词**，消息由本模块按 ServerOS 事实撰写，再交给同一个 provider。

## 数据流

```
CloudCLI run_get ─┐
GitHub workflow_run ├─ normalize ──> decide ──> compose ──> capability_of
signed PHASE_ACCEPTED ┘   (facts)     (outcome)  (deterministic headline      │
                                                 + optional summarizer)       │
                                                                    injected provider.notify
                                                                              │
                                                                    receipt ledger
```

`normalize` 的**顶层信封**是闭合的：`prompt` / `transcript` / `token` 这类顶层
字段直接拒绝。CloudCLI 的嵌套 `run` 是源端逐字结果，只读取 `status` / `runId` /
`bootId` / `note` 以及「是否存在 assistant message」；其余字段（包括 `run.prompt`）
被忽略，**从不复制其 content**，因此也不会进入消息或账本。

## 判定表

| 输入 | 判定 | 外发？ |
|---|---|---|
| CloudCLI `completed` + 可信 native output + boot 匹配 + 必需 gate 全过 + `milestone` | `VERIFIED_GREEN` / `RUN_COMPLETED` | 是，一次，INFO |
| CloudCLI `completed` 但 native output 缺失/note 说无可读消息，或 boot 不匹配 | `UNKNOWN` | 是，ACTION；**绝不报 success** |
| CloudCLI `failed` / `cancelled` | `RED` | 是，ATTENTION |
| CI `success` + 必需 gate 全过 + `milestone` | `VERIFIED_GREEN` / `CI_PASSED` | 是，一次，INFO；**不是 RELEASE/PHASE_ACCEPTED** |
| CI `action_required` | `RED` | 是，ACTION |
| CI `failure` / `timed_out` / `startup_failure` / `cancelled` | `RED` | 是，ATTENTION |
| CI `neutral` / `skipped`，或 phase 未签收 | `YELLOW` | 否，仅卡片/摘要 |
| 普通绿色（`milestone=false`） | `ORDINARY_GREEN` | 否，仅卡片 |
| 非终态（running / queued / in_progress） | `SILENT` | 否 |
| `source_trusted=false`（任何源） | `SILENT` | 否；不可信输入不能触发外发 |

`RED` / `UNKNOWN` 的确定性模板**先于** summarizer 生成；summarizer 抛错或返回
非字符串都只丢掉附加句，模板仍然可发。summarizer 由调用方注入，本模块只在
**首次投递**时调用它一次；重放/重复 webhook 在调用模型之前就命中账本。
summarizer 自身的超时与并发边界由调用方负责（本模块不并发调用它）。

消息首行是判定事实（如 `VERIFIED_GREEN · CI_PASSED · ci:Tests:… · device:…`
或 `ACTION REQUIRED · CI_ACTION_REQUIRED · …`），其后是确定性的
`Kind / Status / Severity / Gates / Evidence / Summary / Next` 块。成功是
INFO，不是红色报警；`YELLOW` 从不外发。

## 幂等与 UNKNOWN

- delivery key 由事实派生：`milestone:<source>:<device>:<kind>:<key>:<outcome>`；
  同一 milestone、同一 outcome → 同一 key。投递前用主键 `INSERT` 原子 `claim`；
  同进程重复观察、重复 webhook、进程重启后重放，都读到既有行：
  `DELIVERED` → `DUPLICATE`，`IN_FLIGHT` / `DELIVERY_UNKNOWN` → `UNKNOWN_REPLAY`，
  不再调用 provider。
- provider 返回 `delivered: true` 但**没有 `remote_id`**、返回没有显式
  `delivered: true`、provider 抛错、或 capability 为 `UNSUPPORTED` → 账本记
  `DELIVERY_UNKNOWN`，**绝不记 `DELIVERED`**。
- 进程在 claim 之后崩溃会留下 `IN_FLIGHT`；下一次观察把它当成
  `UNKNOWN_REPLAY`，不自动重发。对 `IN_FLIGHT` / `DELIVERY_UNKNOWN` 的显式
  `reconcile(key)` 只在 transport 自己的 `IDEMPOTENCY_WINDOW_SECONDS` 内、用
  **同一个 key** 再问一次；窗口外拒绝，因为重复会变成第二条消息。拿到
  `remote_id` 才升级为 `DELIVERED`，且 `DELIVERED` 不会被降级。
- **纠正不是重复**：同一 milestone 从 `UNKNOWN` 变成终态属于新事实（outcome
  词不同，key 也不同），会作为一次纠正投递；同一 outcome 的再次观察才是
  `DUPLICATE`。两种情况都有测试。

## 唯一未接的生产接口

```python
class SourceRead(Protocol):
    def read(self, source: str, ref: str) -> Mapping[str, Any]: ...
```

本 PR 交付 `FixtureSource`（离线 JSON）与三个 payload 归一化器；**没有**交付
需要认证的 CloudCLI `run_get` 客户端或 GitHub Actions 客户端。这是下一个、也是
唯一的接线点。测试证明的是**离线端到端**，不是真实设备/真实飞书端到端。

## 运行测试

```bash
python3 -m unittest -v tests.test_fleet_v1_milestone_notify
```

测试默认只用 `FakeLark`；另有一项 monkeypatch 真实
`direct_lark_notify.DirectLarkHumanNotifyProvider` 的集成测试。两者都无网络、
无 CLI、无凭据、无真实飞书消息。

## 证据分级

- **CODE**：本目录。
- **UNIT**：`tests/test_fleet_v1_milestone_notify.py`（31 项，含一项驱动真实
  `direct_lark_notify` provider 的 monkeypatch 集成测试）。
- **HOSTED_CI**：PR 的 `Tests` 工作流。
- **REAL_FEISHU_DELIVERY**：**未声称**。只有真实 provider receipt + Owner
  批准的上线测试才能说“用户已收到消息”；本包不碰飞书应用凭据或 owner 收件人。

## 与 #409 的关系

本能力只覆盖 #417 明确放宽的「窄带外里程碑投递」；更宽的自主 manager 续跑、
抢占、飞书入站决策权与 V2 Agent 层级仍未实现。
