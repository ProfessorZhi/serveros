# Fleet V1 独立 QA — Snapshot F（2026-10-18）

- **角色**：独立 QA（campaign `ProfessorZhi/serveros#244`，P1 本地 Claude Code Fleet developer plugin）。非作者，未登录、未安装到 live profile、未调用模型或 provider。
- **状态**：`QA_PARTIAL`。本次只证明**离线打包**；`AUTHENTICATED initialize/tools/list/tools/call` 仍 `BLOCKED_BY_OWNER_OAUTH`。
- **对象**：owner Draft #230 精确 head `a934beece13af429808f04c1bd5b887e9c19ae91`（live head 已核对一致）。
- **边界**：只改 `integrations/fleet-v1-qa/**`；未改插件源、5 个 canonical Skills、S1/S2/G1、Work Engine、I1、main。
- **敏感材料纪律**：**没有**读取、打印、搜索、复制或检查任何受保护的 raw receipt、真实 process argv/env、凭据、浏览器/session profile、token 或 transcript。本报告的输入只有 SOURCE 与完全合成的测试值。

## 1. Snapshot 与 preflight

UTC **`2026-10-17T22:28:22Z`**（local `2026-10-18T06:28:22+08:00`）。

- load `13.09 / 13.78 / 13.71`（128 vCPU）；cpu PSI some avg10 `1.30`（full `0.00`）；io some `0.00`；**memory PSI `0.00`**。
- MemAvailable 169 GiB；swap 已用约 30.0 GiB / 32 GiB（≈89.4%）；强制 `MemorySwapMax=0`。
- 无 `pytest`/测试锁；其他用户的 quay driver 不计入本 lane 的锁。判定 **GREEN**。
- 只读树：`git archive a934bee → QA_TREE`（677 files）。

## 2. 唯一一条受限命令

```sh
systemd-run --user --scope \
  -p MemoryMax=1G -p MemoryHigh=768M -p CPUQuota=25% -p MemorySwapMax=0 \
  -- bash -c 'ulimit -v 1048576;
              QA_TREE=<archive> QA_SCRATCH=<scratch>
              python3 qa_probe_f.py'
```

- 用**两次独立离线构建**（同一源、同一合成 endpoint）验证字节与哈希确定性；endpoint 为 `https://fleet-qa.invalid/mcp`（`.invalid`，不解析、不联网）。
- **command exit = 0**：**70 项 PASS，0 FAIL / 0 SECURITY_FAIL**。
- `run-6.log` sha256 `aa7dab5c0ba0c4c0840109374cbdc5831c1756f986209794aa11d9983d94c0df`；探针 `qa_probe_f.py` sha256 `e697468f1f53dece26d783ea6e131bbc8e71472d0308d687533ab4b75d44544c`。
- `run-6.log` 129 KB，因为逐文件 PASS 行带出了生成的 SKILL.md 全文；内容全是合成构建产物，不含敏感值。

## 3. 已证（SOURCE / LOCAL，offline）

### 3.1 一个源，5 个 Operator Skills，字节同一

- first-install 共享源恰好 5 个：`fleet-create-with-grants`、`fleet-device-and-session-discovery`、`fleet-evidence-review`、`fleet-reuse-idle-session`、`fleet-safety-boundaries`。
- 生成的 5 个 `skills/<name>/SKILL.md` 与共享源**逐字节相等**（`read_bytes()` 比较，不是计数或文本相等）。
- front-matter `name` 与目录名一致（builder 在写入前强制；不一致会 `Refused`）。

### 3.2 manifest 与本地 Claude Code binding

生成 `.claude-plugin/plugin.json`：

```json
{"$schema":"https://code.claude.com/schemas/plugin.json",
 "name":"serveros-fleet-dev","displayName":"ServerOS Fleet (development)",
 "version":"0.0.1","description":"DEVELOPMENT ONLY …",
 "author":{"name":"ProfessorZhi"},"license":"MIT",
 "homepage":"https://github.com/ProfessorZhi/serveros",
 "repository":"https://github.com/ProfessorZhi/serveros",
 "keywords":["serveros","fleet","mcp","development"]}
```

生成 `.mcp.json`：

```json
{"mcpServers":{"serveros-fleet":{"type":"http",
  "url":"https://fleet-qa.invalid/mcp","oauth":{"scopes":"fleet:read"}}}}
```

- 只含文档化顶层键；**没有** `icon`、`permissions`、`hooks`、`settings` 或 `dependencies`。
- binding 是 `type: http`（无 `type` 会被读成 stdio）；只有一个 server；默认 scope 是 **read-only `fleet:read`**。
- 没有 `icon`/图片/ZIP/`.mcpb`；输出是 `DEVELOPMENT_ONLY` 目录。

### 3.3 确定性

两次构建的 `member_sha256` 映射完全相同，且 8 个成员逐字节相同：

```text
.claude-plugin/plugin.json                          9d5fbfd5494bc91fb8caea7266e327623de3db776c4319cf2c76c65f85b53491
.mcp.json                                           d252f8a17a0d00ba823f2dbe7486bbff119814dfc2ec4989fb0ace47d80b5f6e
DEVELOPMENT_ONLY.md                                 dd67f0014d8fcc895b07e215cf75b4c359f52305223796bb86d54e763b7273f5
skills/fleet-create-with-grants/SKILL.md            b0a68f5463528ddf3eea3c37dd1de866621f9c911d458cf9be155e4132174a46
skills/fleet-device-and-session-discovery/SKILL.md  62283dd6484a26489db2f5cb4e1a74c8f0bb13a4388f7043bae2f4037a06b664
skills/fleet-evidence-review/SKILL.md               b4ab1d93513f7a10894e253fc74422f158508e9cb5bc61fbd71f85eada2ed67d
skills/fleet-reuse-idle-session/SKILL.md            7b74361db7a60586cbba871ede985893f07c256668dde0366f499a6b4af52b1e
skills/fleet-safety-boundaries/SKILL.md             a61b5f3ca9cf0bf6fa89b2bef1d82ff8686a116ad6f1819571e4548d1e9b6a76
```

这些是**合成 endpoint 下**的哈希；不是 release pin，也不是 first-install 的 `content_sha256`（后者因缺品牌资产仍 BLOCKED）。

### 3.4 无敏感/身份/路径/绕过内容

对 8 个生成文件逐一扫描（合成值；未读取任何真实 receipt）：

- 无 `client_secret`/`access_token`/`refresh_token`/`Bearer <credential>`/`api_key=`/`password=`/私钥/GitHub token/Slack token。
- 无 `/home/`、`/data/home/`、`/opt/applications/`、`/Users/`、`/tmp/`、`/scratch/` 或 Windows 用户路径；无本机 scratch 根路径。
- 无 `os.environ`、`sys.argv`、`$HOME`、`$USER`。
- manifest/.mcp.json 中无 `bypassPermissions`、`dangerously-skip-permissions`、`permissions`、`allowedTools` 键。

### 3.5 拒绝路径（均未写入）

| 输入 | 结果 |
|---|---|
| 空 scope | `Refused`（至少一个） |
| 不在六 scope 内（如 `fleet:root`） | `Refused` |
| `http://` endpoint | `Refused` |
| endpoint 不以 `/mcp` 结尾 | `Refused` |
| endpoint 带 userinfo | `Refused` |
| `--out` 在 skills 源内 | `Refused`，源目录下未创建任何东西 |
| 向已存在的 out 第二次生成 | `Refused`，第一次产物未被改写 |
| 支持的写 scope `fleet:send` | **被接受为请求**（见 §4） |

`skills_source` 在结构化记录中是相对仓库路径（`fleet-v1-first-install/plugin/skills`）；记录中没有 `out`/`argv`/`env`/`cwd`/`user` 字段。

## 4. 观察（不升级为缺陷）

1. **CLI stdout 带绝对 out 路径（P2）**：`main()` 打印 `out=<绝对路径>`（源码 `build_cc_plugin.py`）。结构化 `generate()` 记录不含 `out`；但若某个 wrapper 把 stdout 直接存成 receipt，就会把本机路径写进 receipt。**我没有读取任何 receipt**；这是源码观察，未包含任何值。
2. **支持写 scope 是有意的操作者选择**：`check_scopes` 允许六 scope 中任意非空子集，默认 `fleet:read`；`fleet:send` 可通过参数请求。插件包本身**不能**在 tool-call 层阻止 send——真正的授权在服务端 scope/admission。没有 no-scope/unsupported-scope 请求能通过。
3. **字节同一性依赖文本模式**：builder 用 `read_text`/`write_text`，在 LF 源上逐字节相等；若源以 CRLF checkout（Windows/autocrlf），会归一化。本次只测了本仓 LF 源；跨平台 checkout 未测（`NOT_RUN`，P2）。
4. **manifest 对真实客户端的有效性未独立复跑**：本探针不做 `claude plugin validate`、不加载 live profile（那会调用客户端）。README 记录的 `Validation passed` 是 author 证据，不是本 lane 的。**SOURCE only**。

## 5. 明确的能力边界

| 项 | 本 lane 结论 |
|---|---|
| offline 构建 / 字节同一性 / manifest 形状 / scope 拒绝 / 确定性 | **LOCAL（合成）** |
| offline plugin load / inventory（`claude --plugin-dir`、`plugin details`） | **NOT_RUN**（不调用客户端、不装 live profile） |
| 未认证 `initialize` / `tools/list` | README 记录 401 `invalid_token`；本 lane **未复跑**（无网络/无登录） |
| `AUTHENTICATED initialize/tools/list/tools/call` | **BLOCKED_BY_OWNER_OAUTH**；需要 owner consent 与交互式 OAuth |
| `tools/call` 的 scope 授权 | **NOT_RUN**；服务端权威，插件包不拥有该判决 |
| 真实模型调用 / provider send / 凭据 | **未发生** |

插件加载成功登记一个 MCP server 名称**不等于** tools/list；本 lane 不把包加载说成已发现工具。

## 6. 证据级别

| Claim | Level |
|---|---|
| 5 Skills 字节同一、manifest/binding 形状、scope 拒绝、确定性 | **LOCAL（offline synthetic）** |
| 无 secret/path/env/argv/bypass 内容 | **LOCAL（synthetic scan）** |
| 真实客户端 manifest 校验 / 插件加载 / inventory | **NOT_RUN** |
| authenticated initialize/tools/list/tools/call | **BLOCKED_BY_OWNER_OAUTH** |

**最终判定：`QA_PARTIAL`。** 离线打包面在本轮独立复跑中全绿；authenticated 客户端与真实授权仍不可达，不签 PASS、不进入 release。
