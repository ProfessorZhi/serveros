# Fleet V1 里程碑的真实 SourceRead 输入（#417 / #418）

本目录填的是 #423 留在代码里的**唯一一处未接缝**：

```python
class SourceRead(Protocol):
    def read(self, source: str, ref: str) -> Mapping[str, Any]: ...
```

#423 交付了「归一化 → 判定 → 外发 → 回执账本」的全部离线实现，只有
`FixtureSource`（读 JSON 文件）。本目录交付这条缝的**生产侧只读适配器**：
把一次**已受权**的 CloudCLI `run_get` 和一次 GitHub Actions 查询，变成
#423 的 `normalize()` 本来就能吃的 payload。除此之外它不做任何事。

## 边界：这不是什么

- **不是第二套事件总线。** 没有订阅者、队列、线程、定时器或常驻循环。`read()`
  是一个纯拉取函数，由**已经拥有 tick 的那个东西**调用。
- **不是第二个 CloudCLI 客户端。** 受权 transport 是注入的；本目录不拼 tool
  调用、不持 token、不决定 device/session。
- **不读凭据、不读环境变量、不开 socket、不写文件。**
  `tests/test_fleet_v1_milestone_sources.py::ReadOnlySurfaceTest` 用 AST 钉住：
  模块的 import 面里没有 `os`/`socket`/`urllib`/`http`/`requests`/`subprocess`/
  `pathlib`/`shutil`/`tempfile`，也没有 `open`/`eval`/`exec` 调用。
- **不是 LIVE。** 见「证据分级」。

## 两个适配器

### `CloudCliRunSource`（`read("cloudcli", runId)`）

`ref` 是 **Owner ACK 自己提交的 `runId`**，不是 `deliveryId`。

这不是风格问题，是 #416 的实测结论：一个 queued `session_send` 拿到的
`deliveryId` 会在稍后回 `RUN_NOT_FOUND`/never_issued，而同一次发送的 `runId`
读得通。所以 `RunBinding.run_id` 是**必填**，且 `RunBinding` 里根本没有
`delivery_id` 字段——只有 deliveryId 的 ACK 会被拒
（`RUN_NOT_ACKNOWLEDGED`），而不是被猜成一个 run。

`bootId` 来自绑定（ACK 侧记录的 boot），不是来自 run 自己的回答。run 返回的
`bootId` 与绑定不一致时，适配器**不修不拒**，原样把两者都交出去，让 #423 的
分类器判 `RUN_COMPLETED_WITHOUT_TRUSTED_BOOT` → `UNKNOWN`。同理
`completed` + `lastAssistantMessage: null` + 设备自己的
「no readable assistant message」note，一律落 `UNKNOWN`，绝不升成成功。

### `GitHubActionsSource`（`read("github_actions", "owner/repo#runId")`）

只读**已经发生**的 workflow run：`status` / `conclusion` / `head_sha`。

gate 证据**不是调用方声明的，是算出来的**：只有当某个 job 的 `conclusion`
是 `success`、且该 job 自己的 `head_sha` **等于** workflow 的 `head_sha` 时，
它的名字才算一个通过的 gate。所以

- 没有注入 jobs reader → 没有任何 gate 证据 → 需要 gate 的里程碑落 `UNKNOWN`；
- 另一个 commit 上的绿色 job → `SHA_MISMATCH` 拒绝，不会被算进这次 run；
- `head_sha` 缺失或不是 40/64 位十六进制 → `BAD_SHA` 拒绝（短 sha 不是「弱一点的 sha」，它不是一个 sha）。

## 生产接线点（本目录不持有）

```python
# CloudCLI：一次受权的 native run_get，返回设备逐字结果
def read_run(*, run_id: str, timeout_seconds: float) -> Mapping[str, Any]: ...

# GitHub：GET /repos/{owner}/{repo}/actions/runs/{id}
def read_workflow(*, owner: str, repo: str, run_id: str,
                  timeout_seconds: float) -> Mapping[str, Any]: ...

# GitHub：GET /repos/{owner}/{repo}/actions/runs/{id}/jobs
def read_jobs(*, owner: str, repo: str, run_id: str,
              timeout_seconds: float) -> Any: ...
```

生产实现应复用既有实现，而不是新写客户端：

- CloudCLI：`fleet_mcp/providers/cloudcli.py#get_run`（#407 分支）已经带
  `connect_timeout`/`read_timeout` 与 Owner grant 校验。适配器只把它包成一个
  带 `run_id` + `timeout_seconds` 的 callable。
- GitHub：用既有 owner PAT / `gh` 授权读取。**适配器永远看不到 token**：
  签名里就没有它的位置。

`timeout_seconds` 是**双层**的：注入的 transport 用它设 socket 超时；适配器
再用注入的 monotonic 时钟做一次 deadline 校验，超时的答案被拒
（`SOURCE_TIMEOUT`），不会因为「读得慢」而被当成事实。

## 拒绝表：任何拒绝都只能落 UNKNOWN

`SourceRefused` 带一个短码，`as_dict()` 里明写 `settle_as: "UNKNOWN"`。

| code | 触发 | 是否读上游 |
|---|---|---|
| `RUN_NOT_FOUND` | 上游明说这个 run 已过期/不存在 | 是 |
| `PERMISSION_REFUSED` | `PermissionError`，或 `PERMISSION_DENIED`/`AUTH_REQUIRED`/`SCOPE_DENIED` | 是 |
| `SOURCE_TIMEOUT` | 上游抛 `TimeoutError`，或答案超过 deadline | 是 |
| `PROVIDER_REFUSED` | 上游抛了本边界不认识的 code（如伪造 `RUN_COMPLETED`） | 是 |
| `UNKNOWN_RUN` | ref 不在已 ACK 的绑定表里 | **否** |
| `RUN_NOT_ACKNOWLEDGED` | 构造只带 deliveryId 的绑定 | 否 |
| `SESSION_MISMATCH` / `RUN_IDENTITY_MISMATCH` | run_get 回答的是别的会话/别的 run | 是 |
| `BAD_SHA` / `SHA_MISMATCH` | sha 缺失/畸形，或 job 属于另一个 commit | 视情况 |
| `MALFORMED_SOURCE_PAYLOAD` | 上游返回的不是对象、缺 status、超 64 KiB | 是 |

上游的**异常文本从不进入 exception**（可能夹带凭据），只保留异常类型名；
异常里只允许出现 `[A-Za-z_][A-Za-z0-9_]{0,40}` 形状的 token。

## 数据裁剪

只保留 #423 的 `normalize()` 真正读的字段，其余全部丢弃：

| 上游 | 保留 | 丢弃 |
|---|---|---|
| `run_get` | `runId` `status` `sessionId` `bootId` `note` `lastAssistantMessage.content`（只留存在标记） | `prompt` `transcript` `deliveryId` `activityNote` `startedAt` `explanation` 等 |
| `run_get.lastAssistantMessage` | 只留 `content: "[present]"` | `id` `sessionId` `provider` `kind` `role` `timestamp` `forkAnchorId` |
| `workflow_run` | `id` `name` `status` `conclusion` `head_sha` | `html_url` `actor` `head_commit` `jobs_url` `logs_url` 等 |

`lastAssistantMessage.content` 必须留下，因为 #423 的分类器正是靠「它是不是
非空字符串」判有没有可读输出；但它的**内容换成标记 `PRESENT`**，助手的原话
留在设备上。测试会递归走遍整个 payload，断言 prompt/transcript/token 三条
哨兵串都找不到。

## 生命周期：复用既有 tick，不写 supervisor

- `main` 上已有 `runtime/daemon.py` 的 tick 循环；#407 分支上另有
  `fleet_mcp/run_watcher.py#RunWatcher.tick`（每 tick 取至多 100 个 due watch）。
- 本目录提供的是**被调用函数**：没有 `start()`、没有线程、没有 backoff。
- **单一缺口**：`main` 上还没有任何循环同时持有「watch 生命周期」和「本次
  PR 的适配器」。这个循环在 #407 的未合并分支里。方案是一处改动，不新增线程：
  在 `RunWatcher.tick` 里，对每个 due watch 调一次
  `CloudCliRunSource.read("cloudcli", watch["run_id"])`，把 payload 交给
  `MilestoneNotifier.observe_payload`；GitHub 侧在同一 tick 里按
  「本 tick 新结束的 workflow run」各读一次。**在没有那次接线之前，不声称
  V1 端到端。**

## 运行测试

```bash
python -m unittest -v tests.test_fleet_v1_milestone_sources
```

30 项，全部离线：`FakeRunRead` / `FakeActionsRead` / `FakeJobsRead` 代替受权
transport，`FakeLark` 代替 outbound provider。无设备、无 GitHub API、无飞书。

## 证据分级

- **CODE**：本目录。
- **UNIT**：`tests/test_fleet_v1_milestone_sources.py`（30 项，含一项把
  CloudCLI 适配器 → #423 `normalize` → `MilestoneNotifier` → 回执账本 串起来的
  重复观察测试）。
- **HOSTED_CI**：PR 的 `Tests` 工作流。
- **NOT_LIVE**：**没有**调用真实 CloudCLI、真实 GitHub API 或真实飞书；
  **没有**行使任何 OAuth/PAT 授权；**没有**部署持续观察。谁要说「线上在观察了」，
  必须先有 HOSTED_CI + 一次真实 tick 的日志 + 一条真实 provider 回执。

## 与 #417 / #418 的关系

#417/#418 要求「GPT 派单后退出，仍由独立进程观察 Run 与 CI 并通知」。本目录
只补齐「观察什么、怎么读」这一段；「谁按时调用它」#423 的账本不负责，#407 的
watcher 负责，而 #407 尚未合并。
