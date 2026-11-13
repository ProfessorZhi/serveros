# Rust 评估：serverosd / core-lite 要不要从 C++ 换成 Rust，Fleet 哪些部分该用 Rust

- **日期：** 2026-11-13
- **决定（Owner）：** 新的性能敏感组件**默认用 Rust**。现有组件是否改写，按本文的实测和顺序逐个决定。
- **本文是什么：** 一次可复现的对照测量和据此得出的改写顺序。不是改写计划的全部，也不代表任何设备已安装 Rust 组件。

## 1. 结论

1. **C++ → Rust（core-lite 采集器 / serverosd）没有可测的性能收益。** 两者都是原生程序，一次采集 2–3 ms、2–4 MB，
   都远小于采集间隔（Linux 30 s、Windows 60 s）。改写的理由只能是**维护性**（内存安全、一套代码同时编译 Linux 和
   Windows、一套工具链），不是速度。所以它**不排在前面**。
2. **真正的瓶颈不在语言，在结构：** Hub 发现一次派发的中位延迟是 **13 s（P95 29 s）**，因为 Hub 是单线程串行轮询，
   每轮还要经同一条 SSH 隧道读各台设备。换语言不会自动消掉轮询；要改成**设备主动推送 + 并行异步读**。
   这个新结构正适合用 Rust（tokio）写——这是 Rust 的第一个落点。
3. **Python 的固定成本在“每分钟启动一次”和“每台设备常驻”的地方才值得换：** 一次解释器启动 15 ms / 10 MB；
   节点连接器只 import 就 24 MB、120 ms，每台设备常驻一个。Windows 上每分钟还要拉起 wscript → PowerShell → 两个
   exe 的链（PowerShell 本身启动重，且正是 11-13 那次 7 棵孤儿进程树、11 GB commit 的载体）。

## 2. 测量（可复现）

环境：云端 Linux 容器（4 vCPU），2026-11-13，main `30e43ef`。同一台机器、同一时刻、每项 300 次取平均；
内存是 GNU `time -f %M` 的峰值 RSS。

| 项目 | 每次耗时 | 峰值 RSS | 说明 |
|---|---|---|---|
| C++ `serveros-node-resource-snapshot --interval-ms 0 --compact` | 3.1 ms | 4.3 MB | 现役采集器，完整 schema |
| Rust 原型（同样的内核文件，两次采样，statvfs） | 2.0 ms | 2.2 MB | `native/rust/core-lite-proto/`，**输出字段比 C++ 少**，所以这是“同量级”，不是“快 35%” |
| 同样读取的 Python 版本 | 24 ms | 12.2 MB | 其中 15 ms / 10.6 MB 是解释器本身 |
| `python3 -c pass` | 15 ms | 10.6 MB | 每启动一次 Python 的底价 |
| C++ 采集 + `core-lite-status` 生成文档 | 7.5 ms | — | 一次完整发布（不含 shell/PowerShell 外壳） |
| import `fleet_mcp.node.connector` | 120 ms | 23.9 MB | 节点连接器，每台设备常驻 |
| import hub-watch + `fleet_mcp.fleet` | 220 ms | 28.4 MB | Tokyo Hub（运行后实际更高） |

带 500 ms 采样间隔时，C++ 与 Rust 的墙钟都等于间隔（0.50 s），CPU 都记为 0.00 s。

**Fleet 链路实测（fleet-journal 11 月的 69 个 run，从各页时间线算）：**

| 环节 | n | P50 | P95 | 最大 |
|---|---|---|---|---|
| 派发 → Hub 开始盯 | 66 | 13 s | 25 s | 31 s |
| 派发 → 第一次读到状态 | 66 | 14 s | 29 s | 38 s |
| 设备做完 → Hub 取回结果 | 52 | 1 s | 3 s | 17 s |
| 取回结果 → 卡片处理 | 44 | 2 s | 11 s | 17 s |
| 派发 → 取回结果（端到端，含任务本身） | 52 | 100 s | 2098 s | 2514 s |

“设备做完”本身也是轮询发现的，真实完成到被发现之间的等待在时间线里看不到；设备推送完成事件后才能量出来。

复现命令：

```bash
cmake -S native/node-core -B "$B" -DCMAKE_BUILD_TYPE=Release && cmake --build "$B" -j8
cargo build --release --manifest-path native/rust/core-lite-proto/Cargo.toml
/usr/bin/time -f "%M KB %e s" "$B/serveros-node-resource-snapshot" --interval-ms 0 --compact >/dev/null
/usr/bin/time -f "%M KB %e s" native/rust/core-lite-proto/target/release/core-lite-proto --interval-ms 0 >/dev/null
```

链路统计：`python3 integrations/fleet-hub-watch/fleet_hub_watch/perf.py <fleet-journal 目录>`。

## 3. Rust 落点与顺序

按“实测收益 × 出问题的频率”排序。每一项都要先在目标设备上取基线、改完再对照，结果写回本文。

| 顺序 | 组件 | 现在 | 为什么先做 | Rust 版做什么 |
|---|---|---|---|---|
| 1 | **设备 → Hub 的事件推送** | Hub 每轮串行轮询（P50 13 s 才发现派发） | 链路里最大、最常见的等待 | 节点侧一个小的 Rust 推送器：run 开始/完成、读数变化直接推到 Hub；Hub 侧用 tokio 并行接收。轮询降为兜底 |
| 2 | **Windows 发布链** | 每分钟 wscript → PowerShell → 两个 exe | PowerShell 冷启动重；是 11-11 读数停更、11-13 孤儿进程树的同一类载体 | 一个 Rust exe 完成“采集 → 生成文档 → 原子写入 → 网络探测”，自带超时和单实例锁，计划任务直接调它 |
| 3 | **节点连接器** | Python 常驻，import 即 24 MB | 每台设备都常驻；签名 HTTP、并发、超时都是 Rust 擅长的 | Rust（tokio + rustls）实现同一协议，先与 Python 版并行跑、对账，再切换 |
| 4 | **Key Center 同步** | Python + git + ssh，Windows 还要套 PowerShell | 已用 #627 修了无界等待；下一步是去掉多层外壳 | Rust 一体化：有界 git/ssh、单实例、只记名字不记值 |
| 5 | **core-lite 采集器 / serverosd（C++）** | C++，已经是原生性能 | 性能无收益；只为一套代码跨平台与内存安全 | 等 1–4 稳定后再评估；与 C++ 并行跑、逐字节对照 `node-resource-snapshot.v1` 后才替换 |

**不改的：** Hub 的卡片排版、日志、管家问答这些低频逻辑留在 Python——它们一天跑几百次，瓶颈在网络与飞书，不在解释器。

## 4. 验收方式（每一项都一样）

1. 目标设备上的改写前基线：延迟 P50/P95、CPU、峰值 RSS、进程数、空闲开销。
2. Rust 版与原版**并行运行**，输出逐项对照一致（协议、文档 schema、退出码）。
3. 故障注入：网络挂起、对端不回、上一轮未结束——不叠进程、不无限等、状态如实。
4. 改写后同条件复测，数字写回本文第 2 节；没有收益就不切换。
