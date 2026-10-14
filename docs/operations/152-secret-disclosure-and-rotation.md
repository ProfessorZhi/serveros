# Fleet 秘密暴露面与轮换手册（Issue #152）

Issue #152 是「扩大 Fleet 权限之前的安全门」。它要求先回答两个问题：

1. ServerOS / Fleet 里有哪些输出路径会把敏感字段送到**进程 argv、systemd unit
   Description、stdout、audit、Agent transcript**；
2. 已经发生的那次暴露（CloudCLI Provider 模型凭据、JWT signing key、browser MCP
   token 出现在内部 Agent 日志里）根因在哪里。

这份文档是第 2 问的答案，以及**需要人工批准**的轮换清单。它不是一份「已经修好了」
的声明：本仓库内能修的部分已经修好并有测试（见第一节），不在本仓库内的部分只能给出
受控方案（见第二节），任何轮换都必须由你批准后单独执行（见第三节）。

**本文档不含任何真实凭据、其摘要或可复原片段。** 提到的全部是名字与锚点路径的形状。

---

## 一、本仓库内已修好的部分：一处根因，两条出口

### 根因

`fleet_mcp/mcp_http.py` 在把远端响应变成错误信息时，把**远端原文**直接拼进了 detail：

| 位置 | 原来的写法 |
|---|---|
| HTTPError 分支 | `f"{self.label} returned HTTP {status} for {method}: {raw[:200]}"` |
| JSON-RPC error 分支 | `f"{self.label} refused {method}: {err.get('code')} {err.get('message')}"` |
| 非 JSON 响应 | `f"provider returned non-JSON: {raw[:200]!r}"` |
| 无法解析的 SSE 帧 | `f"unparseable SSE frame from {raw[:200]!r}"` |
| `isError` 的 tool result | `f"{text[:300] or 'provider reported an error'}"` |

`raw[:200]` 看起来像一道闸，其实不是：它限制的是「一段未知字符串里有多少被转发出去」，
不是「要不要转发」。200 个字符足够装下一行 `Authorization`、一个 key，或一份配置转储
的第一行。而 `cloudcli_endpoint` 对面是一个**代理到模型 Provider 的网关** ——
上游 SDK 拿错 key 报错时，最典型的做法就是把那个 key 原样抄进错误消息里。

### 两条出口（这才是它为什么严重）

```
                                    ┌─→ ProviderRefusal.detail
mcp_http.py ── 远端原文 ────────────┤        │
                                    │        ├─→ providers/cloudcli.py::_translate
                                    │        │      → refuse(BAD_REQUEST, exc.detail)
                                    │        │      → tools.py#_error
                                    │        │      → structuredContent  ← ① Agent transcript
                                    │        │
                                    │        └─→ providers/cloudcli.py::health()
                                    │               → ProviderHealth.detail
                                    │               → fleet.py#status()["providers"][*]["detail"]
                                    │               → ② fleet_status 返回值（同样是 transcript）
```

① 和 ② 是两条不同的出口，共用一个根因。在 ① 处修一遍、在 ② 处再修一遍的做法会漏掉
第三条出口（未来任何一个读 `ProviderHealth.detail` 的地方）。**改在入口**：远端原文
根本不进入 `detail`。

### 改了什么

* **新增 `fleet_mcp/disclosure.py`** —— 出口 allowlist。两条规则：
  * **远端文本只被测量，不被转发。** `remote_summary()` 接收原始 body / error message，
    只用它们的**长度**和 body 的**粗分类**（`json` / `event-stream` / `html` /
    `plain-text` / `empty` / `binary`），并在需要时报告 HTTP 状态码与 JSON-RPC code。
    它自己写出一句本地描述。媒体类型字符串也不转发 —— 那是远端输入，被分类而不是被回显。
  * **audit 的字段名与值形状都是封闭的。** `project_record()` 按名字放行，**按名字选出一
    个类型，再由类型决定这个值能不能留下**：计数（非负整数）、布尔、时间戳（本模块自己写
    的那个格式）、小写控制码、引用（`device|provider|native` 那种）、OAuth scope（每个
    token 必须是 `namespace:action`）、URI origin（**只留 `scheme://host[:port]`，丢掉
    path / query / fragment / userinfo**）、摘要（SHA-256 前 12 位）、以及**一律不留**。
    列表按下标逐个套同一个类型；嵌套对象直接拒（audit 字段里的一个 dict 是一份文档，
    不是一条事实）。类型表在 `disclosure.KIND_BY_FIELD`，`SAFE_AUDIT_FIELDS` 由它派生 ——
    两份清单会漂移，一份不会。
  * **自由文本不是一种类型。** `detail`、`reason`、`client_name` 一律置为
    `[omitted:fleet-mcp]`，**不看长度、不看内容**。只按名字放行、再按「够短就是事实」放行
    的做法有一个洞：`ccp_` 加十六字节 hex 是 37 个字符，一行 `Authorization` 更短，
    都远在 200 字符的门槛之内 —— 而 `detail` 恰恰是调用方自己填的自由文本，往错误消息里
    粘一个 token 就把它放进了 audit。
  * **`idempotency_key` 存摘要而不是值。** 这个值对端有一个调用方、内容不透明，没有任何
    模式能区分「会话 id」和「token」；而 audit 需要它只是为了证明两条记录带的是同一个 key
    —— 摘要能做到，原值做不到。`redirect_uri` 同理只留 origin：query 里坐的是授权码或
    access token，userinfo 按定义就是放凭据的地方。
  * **已知残留（写出来，不藏起来）：** `ref` 类型的字段仍然可以装下一个**裸** token，因为
    `[A-Za-z0-9._:|-]+` 正是会话引用和 token 共同的长相。这些字段恰好是本 surface **自己
    的响应里已经返回**的那些（transcript 先拿到 `session_ref` 和 `run_id`，audit 后拿到），
    所以 audit 不是它们最后一道防线；再收窄只会让日志说不出「哪一次 run」，换不来一个它
    并不独有的保证。
* **`fleet_mcp/mcp_http.py`** —— 上表五处全部改走 `remote_summary()`。文件内不再存在
  对远端 body 的切片（有测试断言 `"raw[" not in source`）。
* **`fleet_mcp/audit.py`** —— `record()` 写入前投影，`tail()` 读出时**再投影一次**。
  第二遍覆盖的是「构建本文件的不是这个版本」的情况：旧版本写的、或被人手工改过的行。
  被丢弃的字段**计数并写到 stderr**（unit 会收进 journal），不静默；计数同时作为
  `withheld` 留在记录里，重读不会重复计数。
* **`fleet_mcp/privacy.py`** —— 只加了一段文档说明它的定位。**正则脱敏保留**，因为
  session transcript 是自由文本，allowlist 在那里会变成「读出来的会话内容少了一段」，
  那是另一种谎。它现在只服务这一种输入，其余出口走 allowlist。

### 对外可见的行为变化（两处）

1. `fleet_status` 里 `providers[*].detail` 在网关异常时的**文本形状变了**：以前是一段远端
   摘录，现在是「哪个网关、对哪个方法、HTTP 几、body 多少字节、什么类型、原文被扣下」。
   排障时需要看原文的话，去产生它的那台设备上看 —— 那份正文属于那台设备的主人，
   而不属于任何能读到 Agent transcript 的人。**这一点请让使用者知道。**
2. **audit 里几个字段的值形状变了**（`fleet_status` 的 `recent_audit` 一并受影响）：

   | 字段 | 以前 | 现在 |
   |---|---|---|
   | `detail`、`reason`、`client_name` | 原样（< 200 字符即可） | `[omitted:fleet-mcp]`，并计入 `withheld` |
   | `redirect_uri`、`redirect_uris` | 原样，含 query | 只剩 `scheme://host[:port]` |
   | `idempotency_key` | 原样 | `sha256:<12 位>`，同 key 仍可对齐、原值不可得 |

   这三处都是**有意选择「不泄漏」而不是「保留原样」**：`detail` 在 `mcp.unauthorized` 上
   就是调用方填的自由文本，`redirect_uri` 的 query 在授权流程里坐的就是凭据。
   代价是明确的：**audit 不再直接告诉你「鉴权为什么失败」**。那条原因仍然存在，只是分成
   了两处：发请求的那个调用方在 401 响应体里照旧拿到 `error_description`（值是它自己触发
   的），而 unit 的 journal 里有 `fleet-mcp: audit withheld N field(s) on <event>` 这一行，
   告诉你**某个事件上丢了几个字段**——journal 只说丢了，不说内容，因为说出内容就是把值放回
   刚拦住它的地方。audit 的读者是**另一个凭据**，不是发起人，这就是两者的区别。
   其余字段（`event`、`device_ref`、`session_ref`、`run_id`、`principal`、`mode`、计数……）
   与合法测试用例**逐字不变**。

---

## 二、不在本仓库内的部分：已发生的那次暴露

### 结论

观测到的那次暴露（CloudCLI Provider model credentials、JWT signing key、browser MCP
token 进入内部 Agent 日志）**根因不在 `fleet_mcp`**，因此本 PR 不声称修复了它。
依据：

* ServerOS 侧的秘密卫生本来就是「锚点而非值」：`fleet_mcp/config.py#describe()`
  只输出 `credential_anchor_present` 布尔值和路径；`fleet_mcp/node/install.sh` 与
  `install.ps1` 只打印 **sha256 前 12 位指纹**，脚本头部注释写明「never printed,
  never passed as an argument」；`deploy/*.service` 与 `node/systemd/**/*.service.in`
  的 `Description=` / `Environment=` / `ExecStart=` 里没有任何凭据（本 PR 加了
  source-level 测试逐条断言这一点，见下）。也就是说：argv / unit / 安装脚本这三条
  路径在本仓库内是干净的。
* 那三类值（上游模型 key、JWT signing secret、browser MCP token）**都不由 ServerOS
  持有**，它们在 CloudCLI 自己的配置面上，由 CloudCLI 的运维/检查命令读取。
* 因此最可能的具体路径是：一次**配置检查**把整份配置打印出来（`cat` 配置、`env`、
  `ps e`、或一个「把设置原样输出以便确认」的脚本），而输出进了 Agent 会话。

### 受控修复方案（在 CloudCLI 侧或运维侧执行，不由本 PR 假装完成）

1. **投影，不转储。** 任何用于诊断的读取，只输出**已经点名**的非敏感字段
   （endpoint、bind、port、路径、布尔开关、计数）。不要输出整个配置对象再指望脱敏。
   脱敏是 fail-open 的：它从「全发」开始，减掉它认得出来的；allowlist 是 fail-closed 的：
   它从「不发」开始，放行被点名的。
2. **不要 `cat` 一份配置、不要 `env`、不要 `ps e`。** 要看某个值有没有配，看
   「配了没有」（布尔/长度/指纹），不看它是什么。
3. **指纹而不是值。** 两端确认同一个秘密时比 sha256 的前几位 —— `install.sh` /
   `install.ps1` 已经是这个做法，把它作为 CloudCLI 侧的标准。
4. **`EnvironmentFile` 也是文件。** 本仓库 `runtime/serveros-evening-alignment.service`
   引用了 `%h/.config/serveros/evening-alignment.env`；那个文件的内容不在本仓库里，
   它的**权限**就是它的控制手段。凡是「把凭据放进 env 文件」的做法，都要按凭据对待。
5. **诊断输出与 Agent 会话分离。** 需要看原文时，在产生它的设备上、在一个你本来就信任的
   shell 里看；不要经过一个会被写进 transcript 的通道。

### 本 PR 已经落的源码级闸门

`tests/test_fleet_secret_hygiene.py` 会把仓库当文本读，断言：

* 所有 in-repo 单元的 `Description=` / `Environment=` / `ExecStart=` / `ExecStartPre=` /
  `WorkingDirectory=` 里**没有**任何凭据形状（用 `privacy` 的模式当「无聊检测」，命中即失败）；
* 单元里 `Environment=` 的**变量名**不含 TOKEN / SECRET / PASSWORD / APIKEY / CREDENTIAL；
* `EnvironmentFile=` 必须是绝对路径（`/`、`%h/`、`%t/`）—— 它命名一个文件，不携带值；
* Fleet 单元 `ExecStart=` 里的占位符 ⊆ 一份**逐条复核过**的清单（都会解析成路径 / 主机 /
  端口 / runtime 根）；
* `install.sh` 里读秘密文件的命令替换只有 `wc -c`（长度）、`sha256sum`（指纹）、
  `dirname`（目录）这一组，也就是说**没有任何一处会把文件内容读进变量**；
  `install.ps1` 里 `$secret` 只出现在「生成」和「写文件」两处；
* 定时任务的 `-Argument` 只由脚本路径与绑定路径构成，**不含**凭据；
* 两个安装器打印的秘密相关名字只有路径与长度，指纹是唯一描述秘密本身的东西。

---

## 三、需要你单独批准轮换的凭据（**本 PR 一律不轮换**）

下面每一项都按「轮到什么后果、什么条件下可以回」写。**没有任何一项已被执行**，
也不要因为本 PR 合并就自动执行。

| # | 凭据 | 存放位置（形状） | 轮换的影响 | 回滚条件 |
|---|---|---|---|---|
| 1 | CloudCLI **provider model credentials**（上游模型 API key） | CloudCLI 自己的配置面，按设备 | 该设备上所有走这个 provider 的模型调用开始失败，直到新 key 配好；**正在运行的 run 不会因此中断**，下一次调用才会报错 | 只有在你确认旧 key **没有被写进任何会保留的地方**时才可回滚（回填旧 key）；否则只能前滚。回滚前先确认「谁读过、读到了哪里」 |
| 2 | CloudCLI **browser-use MCP token** | CloudCLI 侧 | 该 token 对应的浏览器自动化会话失去权限，需要重新签发并更新使用方 | 旧 token 未被读到过 → 可直接换回；被读到过 → 不回滚 |
| 3 | **CloudCLI JWT signing secret** | CloudCLI 侧 | **所有当前有效的 CloudCLI 会话立即失效**，使用者必须重新登录；需要安排维护窗口，不要在有人正跑 Agent 时做 | 回滚 = 恢复旧 secret，但那会把已暴露的值重新放回线上；因此**回滚条件应写成「仅在确认从未离开过本机时」**，否则接受一次重新登录的窗口，前滚 |
| 4 | **Fleet node connector secret**（每设备一份，`install.sh` / `install.ps1` 生成） | 设备本地 `…/ServerOS/state/fleet-node/secret`，锚点在 binding 里 | 该设备与 Fleet 主机之间的签名请求全部被拒（`DEVICE_MISMATCH` / 验签失败），直到两端持有同一个值。**注意重跑安装脚本不会覆盖已有 secret** —— 这是刻意的 | 重新生成后按 `install.sh` 的指纹比对流程两端对齐；旧值可保留一段时间以便回滚 |
| 5 | **Fleet owner PAT / OAuth client 凭据** | 本面 OAuth store（`default_store_root`） | ChatGPT connector 需要重新授权；期间 Fleet 的 HTTP 调用会被 `-32001` 拒绝 | 旧的 client 可用 `--revoke-client` 撤销；重新注册即前滚 |

**逐个轮换，不要一次做完。** 每换一项，先确认上一项已经恢复正常工作（第 1 项看一次真实
模型调用，第 3 项看一次重新登录，第 4 项看一次 `fleet_status` 的该设备为 reachable）。

---

## 四、完成判定

按 #152 的要求，**下列条件全部满足之前，本项不得标记为完成**：

- [ ] 你批准并执行了第三节中需要轮换的项（至少第 1–3 项，因为它们在已发生的暴露里）；
- [ ] 执行后**验证过新的 model call 与 Session 仍然正常**（这是「不是假装修好」的唯一证据）；
- [ ] CloudCLI 侧/运维侧的诊断输出改成投影（第二节第 1、2 条）；
- [ ] 本 PR 已评审合并，并**部署**到实际服务这些设备的那份 runtime（本 PR 只提交代码，
      不部署、不合并、不改 Tokyo / FJ 现有配置）。

---

## 五、对现有会话的影响

**现在：没有。** 本 PR 只提交代码，不重启、不部署、不合并。`fleet_mcp` 的改动只有在
Fleet MCP surface 被重新加载之后才生效，因此当前运行的 CC 会话、正在跑的 run、
PTY 里的进程都不受影响。

**部署之后：** 变更局限在错误路径的输出文本与 audit 的字段投影上；工具面（名字、顺序、
schema）、路由表、scope、授权开关**均未改动**。唯一需要提前告知使用者的是 `fleet_status`
里 `providers[*].detail` 在网关异常时的文本形状变化（见第一节末）。
