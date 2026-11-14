# 日循环接缝现场验证 · 2026-09-22

本报告区分已选择的架构、实际执行的组件验证和仍未接通的完整业务流程。不是全系统 READY 证明。原始输出只保存在本用户的受管证据目录，不包含在本提交里。

## 范围与基线

- 工作时区：Asia/Singapore；以下 UTC 现场回执对应当地 2026-09-22。
- ServerOS 源码修改基线：defb134405b620348983f86cf493788ae9f32831。
- Ability 设计基线：c782ef5a440f479606494b750ac365b6ed29cd5b。
- 编辑发生在本轮独立检出；没有在 canonical checkout 或并发 records/LiteLLM 工作树写入。
- 原始证据根：`$ARTIFACTS_ROOT/skills/serveros-daily-cycle/20260922-live-verification/`。
- 没有安装定时器、升级 Provider、启动长期日循环、发消息给同事或执行生产命令。

## 1. 原生 GPT 深度审阅：现场通过，边界明确

测得 Codex CLI 0.153.4，现有登录可用于非交互调用。当前用户日常默认模型/推理配置并非本次高强度 reviewer 的配置；本次显式请求 gpt-6-astra / high。

首先用不含业务原始数据的日循环设计问题完成实际调用，返回有意义的 CHANGES_REQUIRED，涉及来源信任、可撤销授权、WIP 容量、输入版本、失败恢复及预算。后续用独立 Session 审阅本轮代码，发现并修正：

1. 缺 author_session 时不能声称与 reviewer 独立。
2. 较早回合的完成不能为后续未结束回合背书；最终文件必须等于该完成回合的输出。
3. 模型给出 list/dict/null 等非法 verdict 必须拒绝，不能 TypeError 崩溃。

主侧另加来源 revision 空值反例：缺失的 observed revision 不能与 null claimed revision 相等并允许旧认识召回。

实际过程中保留了一次 exit 0 但只有 thread/turn started、没有完成/最终正文的回执。它被归为 INCOMPLETE，不算通过；没有以重复调用抹掉原失败。有限调用器也取消了会影响 native 工具其他文件的全局文件大小限制，改为只限制捕获输出。

最终独立静态审阅：**APPROVE**，绑定最终源码内容 digest（见下）和本次设计/计划；可信适配器验证了真实 completed turn 与最终正文匹配。审阅者没有运行测试，代码测试证据单独列出。它不是对整套部署、授权系统或 OS 沙箱的认证。

- final source digest: `82288f5cb206130f8a64d66d20e680f3ab7deabd919c9415eed003122616e2e3`
- design digest: `f2e68f9b3ba56e0a5a0953ebe180a42892c1149108f8b8bd11952cf153d7ee94`
- final reviewer Session: `01a0c532-7dd7-7102-856a-5dd39aaf08ea`
- observed_at: `2026-09-21T18:20:24.383098+00:00`
- receipt: `release-native-review-receipt.json`

## 2. Quay：实际安装、实际测试、失败不晋级均已核对

真实安装的插件元数据为 0.11.0，关联 revision 7b907317de76ec0149f5c8f9d715a306ee4671d9；插件内 bin/quay 实际执行报告 0.11.0-dev。登录 PATH 找不到 quay 不代表没有安装，旧 archived 源码 checkout 也不代表当前安装。

在独立测试 store 使用当前原生 CLI 配置与内置 native Provider，未触碰已有队列或 worker：

- VERIFY-001：执行第一版策略单元验证，真实 acceptance 与 complete 事件。
- VERIFY-002：通过 Quay gate 调用实际 Chrome 的独立新 context；真实点击、DOM 断言、前后两张截图并关闭自建 context，PASS。
- VERIFY-003/004：代码审阅修订后按新输入重验；最终 VERIFY-004 的 acceptance/complete 均通过。
- VERIFY-005：刻意让 acceptance 返回 7，complete 返回失败，回读任务仍为 ready，未自动修复或晋级。这是通过的负面合同实验，不是失败的业务需求。

**本轮 Quay 运行的是有意义的验证任务，不是已委派两个业务需求给 LLM worker。** 当前默认 worker 配置屏蔽 Playwright MCP；没有移除此隔离。浏览器验证走独立、明确的测试入口，不据此宣称普通 worker 可以拿用户登录态。

证据：`quay-release-run.txt`、`quay-release-events.txt`、`quay-negative-run.txt`、`quay-negative-readback.txt`、`quay-browser-events.txt`。

## 3. 浏览器：真实引擎与截图通过，登录/可见性未验

当前 MCP 0.0.81；CDP 报告 Chrome 153.0.8010.12。验证只建立新的隔离 context，在本地合成页面完成真实浏览器操作，保留 browser-before.png、browser-after.png 与 browser-receipt.json。

没有读取/操作已有用户页面、cookies 或登录状态，没有关闭共享浏览器。现有 MCP 配置含 headless 选项，当前调用环境也没有 DISPLAY 证据，所以 **headed_verified=false / real_user_login_verified=false**。这条 smoke 验证不能代替业务要求的有登录态、可见窗口 E2E。

## 4. Windows：外部受权通道与 checkpoint 回读通过

Windows 的 Remote Desktop Commander 与 Python 3.12.10 实际工作。在本轮自建 artifact 目录写入无业务内容的 checkpoint，再读取并计算 hash；目标根正确，内容一致。

checkpoint SHA256：77bb7a7f71fd3fd5cc892cf8a4b013104e27738fc56beee2c78800383ea843fa。

这证明当前外部通道能在目标端处理 checkpoint，不证明 Tokyo 的夜间 worker 已能自动调用该通道，也不证明真实业务写入权完成交接。本次上下文中的 Windows ssh.exe 连 -V/-G 与连接探针都返回 255 且缺少诊断；没有归因为 DNS/认证，也没有尝试绕过企业控制或修改网络。

状态：外部传输与目标根 probe 通过；无人值守控制绑定、真实业务租约交接仍未验证。普通 Tokyo 任务不依赖此路径。

## 5. OpenViking：采用接口已明确，现场服务未绑定

检查本端已知命令、本人进程/服务、约定配置位置与 OPENVIKING_CLI_CONFIG_FILE 后，未发现可用的已绑定 endpoint。未扫描全网，也不宣称其他机器不存在服务。

采用官方独立 SDK/HTTP Provider，不重写 Memory 平台。接口依官方文档：health/ready 是基础服务探针；受限身份执行 search/find、需要时读取 content；配置须限定 namespace、认证与数据范围。成功响应、资源处理完成、索引可召回是不同状态，部署时逐项验证。

本轮没有部署服务、配置 embedding/密钥或读取真实 OpenViking memory。已有 fixture 不计 LIVE。日循环通过 records/checkpoint/原生检索降级继续，旧认识在本地来源版本过滤层就失效。

官方参考：[OpenViking API](https://docs.openviking.ai/en/api/01-overview)。

## 6. 云文档：实际创建/修改/回读通过，CAS 未证明

在用户自己的已有“临时”目录创建一份仅含合成文字的测试文档，未改业务文档、权限或共享。

- create 返回 revision 1，但正文写完后 fetch 实际是 revision 3。
- 用 revision 1 尝试局部更新返回失败且正文保持不变；不能据此推导所有并发写均安全。
- fresh fetch 得到 revision 3 后局部更新成功；update 返回 revision 3，而再 fetch 实际为 revision 4，正文正确。

由此实证：**写前与写后必须 fetch，不能将 create/update 的 revision 直接当最终正文版本。**

计划中的 stale-write 负面实验被执行安全检查拦截，没有执行；因此没有真实证明服务器端 compare-and-swap 或抵抗人工并发修改。没有换工具绕过。当前安全策略是共享正文自动覆盖保持禁用，生成候选/独立版本并保留回执；需要原地更新时先验证实际条件写与冲突处理。

该合成文档仍留在“临时”目录作实验记录，未触碰任何业务文档。证据：cloud-create.json、cloud-baseline-readback.json、cloud-valid-write.json、cloud-after-valid.json。

## 7. 生产切流：仍只接受真实 Runbook，不猜命令

查阅已授权源代码与现有设计，窄范围搜索既有工作会话并读取相关文档候选 metadata，没有取得足以确认当前实际 traffic authority 的 Runbook。更深文档检索请求未执行，不能把它说成已读或断言 Runbook 不存在。

因此当前目标的切换命令/入口仍是 RUNBOOK_UNRESOLVED。未连接生产执行变更，未猜 Nginx/Docker/端口。保留人执行切流/回滚，并只阻断相关生产动作；研究、代码验证和报告无需等待该绑定。

## 8. Support 知识：实际导出通过，Bot 与人工送达未验

复用现有 litellm-support/scripts/build_agent_kb.py，在本轮独立 artifact 目录导出，未修改知识源和 Bot 业务代码。

实际产出：23 条员工条目，25 个文件；导出器的 3 项自检通过。operator/candidates 整目录及单条不符合 audience/status 的内容被排除，处理了跨层引用。

这是 **导出机制** 通过，不是所有知识语义已经重新审核；历史 verified 标签与正文警告、字符替换式检查等风险仍不能用 exit 0 抹掉。

产品选择：Bot 使用经批准的员工快照（版本/哈希/受众），不使用 raw/operator 源；无结论时交已绑定个人 owner 的现有队列，得到实际回执才声称已转人工。当前 Bot 消息入口、旧 matcher 到新知识格式的消费适配、owner 投递/回调闭环没有现场通过。不得把知识包生成写成 Bot 已上线。

## 9. 代码与测试范围

本轮新增：

- intake/daily_cycle.py：fresh board、最大两项焦点/未结束工作、当前 scope grant、独立 review 绑定与来源失效。
- runtime/review_receipt.py：只归一化完整可信 native 审阅过程，拒绝缺完成回合、陈旧文件和畸形模型 JSON。
- runtime/bounded_invocation.py：POSIX 的有界调用/私有输出/同输入幂等回执，不是沙箱、不是业务状态、不证明全进程树停止。
- tests/test_daily_cycle.py：**47 项针对性测试通过**，包含多项负面与真实子进程案例。子测试反例不另虚增测试数。

最终代码通过原生 Python suite 和 Quay 的真实 acceptance 路径。自有测试 store 中的失败晋级反例行为符合预期。没有运行所有产品测试，也没有声称实际业务 E2E 已覆盖。

## 10. 完整每日业务循环仍未启用

原有 daily service 只渲染工作台，不负责本次完整链路。旧 intake/workchain-contract.yaml 还明确记录了来源写入/认识 writer 未接通与人工 today selection；本轮没有把这些历史 live=false 字段改为 true 来假装实现。

新增 helper 有真正测试和 native 调用消费者，但尚未接入全业务的来源 writer、Work Engine 原子准入/预算、真实执行器与发布回执。首次启用要以一条获授权的真实目标完成端到端演练；不能只安装 timer 就把它叫无人值守工作系统。

当前已落实的价值是：日循环角色和边界闭合；GPT 原生审阅可调用；Quay/隔离浏览器/员工知识导出/Windows checkpoint 接缝有实测；关键准入与回执错误有代码和反例防护。剩余现场能力按步骤精确降级，不设置一个虚假的全局 READY。

原生自动化参考：[Codex non-interactive](https://developers.openai.com/codex/noninteractive)。本轮没有自动操纵 ChatGPT GUI。
