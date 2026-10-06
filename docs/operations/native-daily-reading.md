# 有限的日常阅读与工程续跑

`recipe run --recipe perception` 现在可以调用原生 Claude 阅读已有的固定版本来源。
采集仍由原 Source Provider 负责；没有材料变更时不需要调用模型。

在实例的 `state/recipes/model-binding.yaml` 中显式绑定：

```yaml
transport: native-claude-reader
wall_seconds: 120
max_cost_usd: 1.0
```

项目的 `context/sources.yaml` 可以声明已有卡片与已采集来源：

```yaml
sources:
  - id: project-work-doc
    kind: understanding
    card: information/understanding/projects/example.md
    manifest: feishu/chats/people/example/oc_example/links/work-doc/manifest.json
    grant_ref: grant:example-reading
```

`grant_ref` 可省略，此时只准备待对齐内容。Web 外置快照还需显式的
`source_asset_roots: [<absolute-asset-root>]`。项目入口复用已有的 recipe：

```text
serverosctl recipe run --recipe perception --scope project:example
  --period-start <ISO-with-offset> --period-end <ISO-with-offset>
  --budget 2 --run-dir <absolute-owned-artifact-directory> --json
```

Node/Item scope 可用 `--sources <absolute-yaml>` 指定绑定。跨时段复用同一个
阅读产物目录，已产生的 reading 和 invocation receipt 可复用；有歧义的调用不盲目重试。
模型仅收到所选 packet，关闭工具、插件、hooks、MCP 和其他自定义上下文。
文档引用由模型选行，控制器复制原始字节；IM 使用已读位置。超出输入预算明确拒绝，
不静默截断来源。写入前重新核对来源与卡片版本，使用已有 semantic writer。

`alignment.json` 从各 reading 的 resolution 生成，保留需要人决定的内容。
它不接受需求、不生成 Work Item，也不等于语义接受记录。接受前仍需版本核验。
一个事实的写入 receipt 完成，不表示同批提案已获接受。

Focus 从 Work Engine 投影中的唯一 READY Activity 读取动作，从当前 Item card
计算 scope digest，再关联唯一有效 grant。多个可执行 Activity 或多个 grant 不自动择一。
scope 变化会被原有准入政策拒绝；旧投影的时间不会被重新标成当前时间。

有工程权限的 Parent 可调用 `continuation.execute(key, argv)`。镜像 digest、专属
worktree、内存/CPU/PID 上限来自已批准 grant；执行容器无网络、无宿主秘密与 Docker socket，
只挂载该 worktree 与当次产物目录。普通命令不能改变镜像或挂载。退出、超时、输出上限、
幂等重试和容器终止都有独立 receipt，命令退出不等于 Item 验收。

长期 grant 使用 Provider 对完整授权范围的签名，在签发时验证人类证据新鲜度；
后续仍检查有效期、撤销、预算和 epoch。旧的短期上下文不会自动升级为长期授权。
Provider trust binding 与真实的人类批准事件必须单独接好，源码支持不能记为 LIVE。
