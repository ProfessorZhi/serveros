# Fleet V1 owner/runtime 验收记录 — 2026-10-22

> **这篇回答什么：** 针对 SRV-0011 候选，哪些已经通过源码审阅与隔离执行，哪些只是源码就绪，哪些在真实设备上被拒绝或仍未验证。本文**不**声称生产 App 已更新。
>
> **状态：** 候选验收证据，2026-10-22。文中的数字（34 个 Fleet 工具、3102 个测试、通过项与耗时）是该候选当天的值，不回改；引用前先核对日期与对应 commit。
>
> **结论先读：**
> - 工具面为 34 个 Fleet 工具（10 个手写 + 22 个原生镜像 + 2 个描述符）；另有打过补丁的 CloudCLI 提供 23 个原生工具。
> - CC 与 Codex 作为执行器，在同一批 send 下各自跑完一个隔离任务，ACK 分别为 0.090 / 0.162 秒；连接被替换后执行器继续运行。
> - 各条源码线都有对应 scoped review 与独占的 Evidence Run。
>
> **仍然不成立的：** Windows 的 LIVE 执行被自动审批拒绝，SCM `StartServiceW` 效果与现场更新未验证；生产 Fleet、常驻服务、全局客户端与网络传输均未改变；没有上传、没有 OAuth 或 ChatGPT 发布。
>
> **怎么读：** 「V1 behavior」是目标行为；「Accepted source and execution」是逐项证据表；「Full-build acceptance and live boundaries」是最终构建门与仍然成立的边界。正文保留英文原文，以免改写证据表述。

## V1 behavior

A commander can be a local Claude Code/Codex agent or, after local acceptance,
ChatGPT. Executors are the native Claude Code/Codex sessions on enrolled
devices. Fleet supplies identity, routing, grants and durable delivery; native
sessions retain their own provider state and execution abilities.

The commander gathers information and communicates with people and other
models, then dispatches complete long tasks in parallel where dependencies
permit. It retains the original session/run/queue references, returns after
ACK, and reconciles those references after disconnection. A DAG describes
work; it does not schedule itself. A continuing agent must consume dependency
edges. Unknown delivery is fenced rather than retried as a new task.

For an explicitly selected account/device and named project roots, owner-max
allows real project discovery/creation and session discovery/read/create/send
without adding a grant for each new project. New native sessions inherit the
selected family's maximum execution mode. Other accounts, unselected device
namespaces and explicit incompatible model rules retain their boundaries.

The candidate publishes 34 Fleet tools: 10 authored, 22 pinned native mirrors
and two descriptors. The separate patched CloudCLI advertises 23 native tools.
Eight shared Skills teach the commander the actual tool and recovery workflow.
Native Core/serverosd recovery must observe fresh facts, respect maintenance
and active agents, bound recovery attempts, and prove a real recovery effect.

## Accepted source and execution

| Area | Evidence and scope |
| --- | --- |
| Fleet owner/control | Core PR391, through 9603f47. Independent behavior review at 6a23d1d: 12 targeted cases pass. Later commits correct success fixtures. Full runtime contains the owned entry and reuses the existing AgentControlStore; the standalone Fleet surface cannot impersonate that durability. |
| Maximum native permissions | Targeted read-only metadata from the two exact acceptance-native conversations proves CC bypassPermissions, and Codex danger-full-access/never. CC is the executor family, not a claim that its configured model vendor is Anthropic. |
| Real concurrent executors | CC and Codex each execute one isolated own-project task after the two sends are issued together. ACKs return in 0.090/0.162 seconds. Both output files are correct. The commander MCP connection is replaced while executors continue. |
| Codex terminal correction | Original e90fbfe Codex task actually finishes but its receipt is UNKNOWN; this negative evidence is retained. CloudCLI c6be626 recognizes typed provider completion without inventing an OS exit code. One new distinct read-only task ACKs in 0.125 seconds and completes with success=true, exitCode=null and provider-turn-completed. |
| Restart and replay | Fresh Node processes reconstruct the new completed run and exact final snapshot from the same SQLite receipt owner. Original CC completed and original Codex UNKNOWN records remain unchanged. Accepted intent/key replay returns the same run ID with IDEMPOTENT_REPLAY while model dispatch is disabled. Root Evidence641 records this isolated acceptance. |
| Windows source | PR387 at 3eeb84c: official hosted Windows build, six suites/477 checks, extracted static ZIP startup, and a real current-user ScheduledTask recovery effect. Independent source/hosted evidence review passes. Actual SCM StartServiceW effect and user-device LIVE acceptance are not covered. |
| Private state ancestry | a4d74b8 source, integrated as 7edfce8/2a8c295/d7a6c14. Hosted native full tree: 33 suites/2844 checks pass. The current-user private boundary permits the measured state-root layout while retaining no-follow identity checks. Independent review passes. |
| Codex commander Skill | One native-owner invocation discovers eight Skills, reads orchestration Skill with exact source hash, makes three read-only Fleet calls and returns a long-task DAG. Read output is 7492 characters / 7504 UTF-8 bytes. Old read-only bwrap failures remain recorded. |
| Linux recovery source | PR390 through bc1ad5c plus root correction 2bc869a, independently reviewed. C++ and Python effects retain/revalidate the actual lease namespace; the daemon guards its real actuator; private state ancestors work while immediate writer parents remain nonshared. Each Core tick releases its lease. |
| Combined Fleet regression | Exact integration 75f0855: all 96 Fleet test files, 3102 tests and 3522 subtests pass in 196.07 seconds under 2 GiB, no swap, one CPU. Later CI input and documentation changes do not alter these product/client source bytes. Evidence646. |
| CC commander Skill | A real model selects fleet-commander-orchestration, makes three Fleet reads and returns a parallel DAG. It reports four enrolled devices with three live routes, as returned by tools. One invocation; CLI reports six internal turns, exceeding the three-turn test target. No writes were requested. |

The native model evidence is under root Run0046's exclusive
`v1-owner-runtime-20261021/integration-20261022/native-c6be626/` namespace;
CloudCLI policy metadata and the six-patch packet are in Run0050's exclusive
`cloudcli-run-readback-20261022/` namespace. Windows/private-state and commander
Skill evidence retain their separate Runs and source pins. Existing historical
Run finalization files are not overwritten.

## Full-build acceptance and live boundaries

All source lanes above have scoped review. The final build gate is the exact
candidate's official Linux workflow: all 34 native suites and both entire
Python files (`test_daemon.py` and `test_native_core_facts.py`). Logs, JUnit,
source SHA and the actual root-input contract are retained. The three existing
pilot-specific daemon skips remain visible; the hosted runner does not have
this user's installed/running pilot service.

The first 2cfb0ad run passed 34 native suites and 56 facts tests but failed five
of the 148 daemon cases: the runner had not bound its four semantic filesystem
roots. It was not recorded as a combined PASS. The CI-only input driver at
3ab74c5 supplies real private DATA/workspace/artifacts/toolchains/state paths
through the existing environment seam. The unchanged five cases then pass;
no product code, old assertions, catalogue authority or extra skips are changed.
A missing global ability catalogue remains NOT_MATERIALIZED. The final workflow
still runs both whole files rather than extrapolating those five cases.

Client source 3ebd5f7 closes the four residuals at 169e50b: owner-max Skill
semantics, output-root ownership, correlated successful initialization and
bounded stdio/notification observation with complete writes. Original ten
review probes and 42 independent focused cases pass. The real commander Skill
model evidence uses the earlier identical orchestration Skill bytes; it is not
mislabelled as a rerun of every model on the final client head.

The commander evidence has explicit limits. CC's CLI reported six internal
turns against a three-turn target: the budget was exceeded, even though the
logical stages were three. Some Codex redacted model-log lines were cut at
20000 characters and are not complete JSON; their call identities can be
counted, with summaries as additional evidence. The exact Skill read bytes and
final answer are retained. Requested native-owner flags are not misrepresented
as policy fields absent from the event stream. Native executor effective policy
has its separate exact-conversation metadata evidence.

The complete Python owned-runtime build uses the real pinned ability revision
c3fa07205bdd714e82c82e3dfc8df358c316e0c8. The first isolated ZIP was extracted,
started outside the checkout, promoted twice and rolled back using the actual
release.py entry; config and owner-ledger hashes remained unchanged. It carries
392 payload files including runtime/bin/Fleet and three ServerOS bootstrap
Skills. It does **not** contain C++ NativeCore, and is not an App upload ZIP.
Final delivery records the refreshed source/package hashes, separate native
artifacts, and update/rollback receipts. Build --check verifies expected content;
release.py's own gate verifies structure/startup and records measured hashes,
without pretending to authenticate an externally modified artifact.

CloudCLI c6be626 is a separate six-patch packet with independent source review
and root native warm/cold/replay acceptance. It is not represented as an
upstream merge or a production installation. Local CC/Codex development
adapters and the eight shared Fleet Skills remain distinct from the three
ServerOS bootstrap Skills and from the existing App's presentation metadata.

User Windows LIVE execution was rejected by automatic approval review:
execution required approval while policy was never. Hosted testing does not
substitute for that device. Native SCM StartServiceW effect and onsite update
remain unverified. No alternate route bypasses the rejection.

The actual existing App's Creator backend ID is not resolved by its plugin://
URI. Same-ID production upload, OAuth and ChatGPT rollout have not occurred;
GPT follows local acceptance as requested. Synthetic update checks are not
reported as an uploaded release. Production Fleet, resident services, global
clients and network transports remain unchanged. Reverse tunnels remain the
observed transport; this candidate does not migrate the network to Tailscale.
