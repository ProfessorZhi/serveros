# Fleet 插件更新手册（私人 App-backed overlay）

面向操作者：日常改 Skill / 头像素材 → 构建 overlay ZIP → 在 **ChatGPT Plugin Creator 里对已存在的插件做受保护更新** → 读回校验 / 必要时回滚。

**本文件不授权任何线上操作。** 它描述流程；执行更新、创建、回滚、卸载的永远是人，在 Creator 里点按。

---

## 0. 两条路径不是一回事（**先读这节**）

Fleet 插件有两条互斥的分发路径，规则**相反**。混淆它们会做出一个两边都不合规的 ZIP。

| | **私有 App-backed（本目录的 overlay）** | **公开目录提交（public submission）** |
|---|---|---|
| 载体 | 对**已存在**插件做同 ID 更新 | 新提交或更新同一个 listing |
| `apps` / `.app.json` | **必须保留**——它就是 App 绑定 | **不允许**：含 app 引用或 lifecycle hooks 的 ZIP "cannot currently be submitted" |
| `.codex-plugin/plugin.json` | 保留（compatibility 清单） | 非必需；如用，只放 `plugin.json` 在里面 |
| MCP 地址 | 由 App 绑定决定 | 在**你的 MCP 配置里声明 server URL**，并在 dashboard 完成 setup |
| 凭据 | 不入 ZIP | 同样不入；manifest 拒 `clientSecret`，ZIP metadata 拒 `test_credentials` / `reviewer_instructions`，公开提交拒非空 server `headers` |
| 本仓库的构建器 | `build_overlay.py`（本目录） | `integrations/chatgpt-fleet-next/build_zip.py` |

**出处**：`developers.openai.com/plugins/build/plugins` 与 `.../deploy/submission`（2026-10-15 读）。公开路径的禁止项是原文引用。

### 必须区分的两类证据

| 说法 | 证据强度 |
|---|---|
| 公开提交的 ZIP 不允许 app 引用 / hooks | **官方文档原文** |
| 元数据、素材、bundled skills 的改动需要**重新上传 ZIP** | **官方文档原文** |
| hosted MCP 工具的改动**不需要**新 ZIP（由 MCP scans 单独处理，且每日扫描） | **官方文档原文** |
| 换 ZIP 即可更新**已安装的 App-backed 插件的头像/展示** | **UNKNOWN** —— 官方公开文档**没有覆盖**私有 Creator 的 App-backed 更新规则 |
| 私有 Creator 的 update 要求"保留 App 绑定、MCP 配置、name、presentation" | **操作者陈述**，非我读到的官方文档。**不要**拿公开文档去证明它 |

**因此**：本手册不承诺"换个 ZIP 就能改插件头像"。头像/展示能否在该路径下更新，必须由**真人在 Creator 里实测**后在 Creator-side 记录；在那之前一律标 **UNVERIFIED**。若 Creator 拒绝头像/展示更新，那是 **FAIL / UNSUPPORTED**——**不要**通过绕过校验让它"通过"；走 dashboard 有支持时的路径。

---

## 0.1 Skill/版本更新与头像/展示更新是两条边界

这一节只处理 **App-backed 插件的 Skill / 版本 / payload 更新**。它不处理头像、
`displayName` 或其它 presentation 字段；那些属于 **App/Creator owner**，本仓库
的 overlay ZIP 不能证明自己可以改它们。在 Creator 侧真人读回之前，头像与展示
更新一律是 **UNVERIFIED**。不要用“重新打个 ZIP”去绕过这个边界，也不要把
`displayName` 当成 overlay 可以顺带修的字段。

同 ID 更新必须有可执行的本地检查，而不是只看人眼：

```sh
# 1) 构建新 overlay；identity 字段由 baseline 钉住
python3 integrations/fleet-app-overlay/build_overlay.py \
    --source <source-tree> \
    --baseline integrations/fleet-app-overlay/baseline.json \
    --out <dist>

# 2) 同 plugin_id、版本前进、旧 Skill 保留、archive hash 一致
python3 integrations/fleet-app-overlay/package_update_check.py plan \
    --previous <previous.release.json> \
    --next <next.release.json> \
    --out <plan.json>

# 3) 真人/Creator readback 与 plan 对齐；这是 claim，不是本机观察
python3 integrations/fleet-app-overlay/package_update_check.py readback \
    --plan <plan.json> --observed <readback.json> --out <readback-result.json>

# 4) 只确认 previous archive 是合法回滚目标，不执行回滚
python3 integrations/fleet-app-overlay/package_update_check.py rollback \
    --previous <previous.release.json> \
    --current <next.release.json> \
    --out <rollback.json>
```

没有 Creator 调用时，`verify_readback` 接受
`OPERATOR_SUPPLIED_RECEIPT` 或 `SYNTHETIC_LOCAL_REHEARSAL`，并始终写
`production_verified: false`；标成 `LIVE` 的 receipt 会被拒绝，因为本工具没有
观察它。回滚检查只产出一个 target，不创建 release、不删除当前 release、不
调用 Creator。

本地演练（两个合成版本，无网络、无安装）：

```sh
python3 integrations/fleet-app-overlay/package_update_check.py rehearse \
    --source tests/data/fleet-overlay-fixture \
    --baseline integrations/fleet-app-overlay/baseline.json \
    --out <rehearsal-dir>
```

`rehearse` 的 `--out` 必须是**新建的空目录**（或尚不存在），且从该路径到根的
所有已存在父路径都不得是 symlink；输出不得与 source 重叠。源树和共享 Skills
会在任何 copy/write 之前检查 symlink、禁入成员和边界；工具不会 `rmtree` 调用者
已有目录。拒绝时外部 sentinel 不变，也不读取 symlink 目标内容。

`plan` 不只比较 plugin id/name/version/Skills。它比较完整的受保护 manifest
（root 与 `.codex-plugin` 重复字段）、`mcp.json`/`.mcp.json` 的结构及其引用
文件，并拒绝非 Skill 成员的增删或字节漂移。App-backed 更新只允许版本、
Skills/其 supporting files、keywords、onboarding selection 和 local-executor
requirement；MCP/后端/tool 行为变化属于 server deployment，不是 overlay 更新。

它只证明本地 identity/受保护配置/readback/rollback 规则成立；它不是一次真实
Creator 更新，也不证明私有 Creator 接受同 ID 更新或回滚。真实更新、回读和回滚
仍由真人在 Creator 里执行，并按 §5 记录结果。

---

## 1. 什么都不用动的情况

**后端变化不需要新 ZIP。** 新增设备、新增 runtime、换处理器、扩容实例，全在服务端完成；ChatGPT 侧固定 **Plugin ID / App ID / MCP HTTPS 地址**。

但要说清边界，别把它讲成"之后再也不用管"：

| 变化 | 需要什么 |
|---|---|
| 后端设备 / runtime 增减、处理器更换、扩实例 | **不需要**插件重装，也不需要新 ZIP |
| hosted MCP 工具本身变化 | **不需要**新 ZIP（MCP scans 单独处理；也可在 MCPs → Issues → Rescan 手动请求） |
| **工具 schema 或 scope 变化** | 可能需要客户端 `tools/list` **刷新**，以及 **OAuth 再授权** |
| Skill / 素材 / 元数据 | **需要**新的 overlay ZIP + 一次 Creator 更新 |

**绝不向用户承诺"连刷新都不需要"。**

---

## 2. 日常更新流程

```
1) 改源                 operator 的 overlay 源树（不入库）：skills/、assets/、plugin.json 的 version
2) 构建                 python3 integrations/fleet-app-overlay/build_overlay.py \
                          --source <源树> --baseline integrations/fleet-app-overlay/baseline.json --out dist
3) 复核                 看 distribution 标记、sha256、以及两个 icon slot 的校验结论
4) 人工审 ZIP diff      由人看这次 overlay 到底改了什么
5) Creator 里更新       打开已存在的插件 → "Upload plugin to make changes" → 同 plugin ID
                        （用预期的 release id 做 CAS 保护；**绝不以 create_plugin 作为正常更新路径**）
6) 读回校验             plugin_id 未变；新 release id；skills 延续（旧的还在、新的出现）；
                        素材读回；必要时 diff 成员清单
7) 回滚                 需要时回到**上一个 Release**
```

### 关于版本

`baseline.json` 的 `version` 是**下限**，overlay 必须严格高于它。构建器拒绝不前进的版本——一个版本不动的新包，无法与它要替换的那次发布区分。

⚠️ `baseline.json` 里 `version` 目前是 `"0.0.0"`，因为**当前线上发布版本尚未从 Creator 读回**。首次真实构建前请把它设为**你正要离开的那个版本**，否则一个重复线上版本的 overlay 会被接受。

### 关于身份（不会被改动的部分）

构建器把 **plugin id、`name`、`displayName`、`apps` 引用**钉在 `baseline.json` 上，源树里任何一个变了都会被拒。理由：改这些不是"更新这个插件"，而是**另一个插件占据它的发布位**。

`baseline.json` 里 `null` 表示**未钉住**（不知道真实值）。**不要**为了让它"看起来完整"而填一个猜的值——那会让检查以错误的理由通过。

### 关于图标

槽位来自 manifest 的 `extensions.com.openai`：**`composerIcon`** 与 **`logo`**（官方文档里**没有** `icon` 这个键），素材放 `./assets/`，路径以 `./` 开头。

本仓库自己的校验规则（**不是**平台要求——官方文档没有给出任何格式/尺寸/体积限制）：

| 规则 | 值 | 说明 |
|---|---|---|
| 格式 | PNG / JPEG / WebP / SVG | |
| 最小尺寸 | ≥ 48×48 | |
| 最大体积 | ≤ 5 MiB | |
| `composerIcon` 必须方形 | 是 | 它被画进方形槽位 |
| `logo` 可为非方形 | 是 | 字标常常靠长宽比表达 |

- 槽位**已声明但无文件** → `NEEDS_ASSET`（**不是**失败；这是"还没提供素材"的正常中间态）。
- 槽位**有文件但不可用**（格式/尺寸/体积/方形不符）→ **REFUSED，构建失败**。
- 报告里 `renders_as` 恒为 `null`：**校验了文件，不等于声称客户端会这样渲染**。别把 SVG 通过校验读成"客户端一定会显示"。

**当前状态：`NEEDS_ASSET`。** 本仓库**没有**任何合规的品牌像素素材——fixture 里的方块是合成占位（stdlib zlib 生成），**不是**品牌图形，`assets/README.md` 已注明。等操作者提供真实头像文件后替换源素材、一键重打 ZIP。

---

## 3. 这个工具的边界（它不做什么）

`build_overlay.py`：

- **不创建插件**（没有 create 路径）；`creates_plugin: false`、`writes: []` 写在发布记录里。
- **不联网**：不联系 Creator、不做 OAuth、不更新任何东西。
- **不读凭据**：它只拒绝凭据形状的**文件名**，从不打开凭据文件。
- **不改 OAuth、MCP App 绑定或后台 tunnel** —— ZIP 本身也改不了这些。
- 拒绝把 `.git`、`.env`、`__pycache__`、`dist/`、`tests/`、`*.pem/.key/.pyc`、`token.json` 之类打进包。
- 每个产出的 ZIP 都带 `distribution: PRIVATE_APP_BACKED_OVERLAY_ONLY` 与一条 warning —— 因为**它带着 app 引用，公开提交会被拒**。这个标记就是用来在磁盘上回答"这个包能公开上传吗"。

---

## 4. 首次安装（不在本工具范围内）

首次安装用**已有的正式 Fleet 名称与 Plugin ID**，以及**受支持的 MCP App 绑定方式**。
**除非确认它确实无法承载，否则不要新建插件**——那属于人的决策，不是自动化步骤。

参考标识（**仅本地离线合同用途**，不代表任何线上操作授权）：

- 私有 App-backed 插件 id：`plugin_asdk_app_6ace0c9c0dfc81918ed5f0fac18182b6`
- 该插件当前 release id：`PluginRelease_14a7d9a68c348191987a7383fc45435a`
- `.codex-plugin/plugin.json` 的 `interface.displayName`：`ServerOS Fleet`

**展示名保持 `ServerOS Fleet` / `Fleet`。** 内部源码包可以叫 `fleet-next`，但**正式 UI 永不出现 "Fleet Next"**，也不新建 "Fleet Next" 生产插件。

`PR #185` 的 ZIP Lab（plugin id `plugins_6acf305a11a88191856c02e10242f8c6`）证明了**同一 plugin id 从 0.1.0 原地更新到 0.1.1**。那是**该流程**的实测，**不**是本 overlay 的证据：它没有绑定真实 MCP，也没有携带 app 引用。

---

## 5. 尚需真人实测的清单

| # | 事项 | 状态 |
|---|---|---|
| 1 | 私有 Creator 是否允许更新 App-backed 插件的头像 / presentation | **UNVERIFIED** |
| 2 | 一次真实 overlay 更新后，`plugin_id` 不变 / 新 release id / skills 延续 | **UNVERIFIED**（待真人执行） |
| 3 | 素材在上传后被客户端实际渲染成什么 | **UNVERIFIED** |
| 4 | 回滚到一个旧 Release 的实际行为 | **UNVERIFIED** |
| 5 | `baseline.json` 的 `version` 与 `name` 的真实值 | **待操作者从 Creator 读回后填入** |
