# V13 monitor lane · 只读审计交付（M1-monitoring-v13）

本目录是 Issue #170 / Draft PR #233 / #239 监控车道的**只读审计交付**，
不是生产监控组件，也不是 LIVE 报告。

## 边界（本目录成立的前提）

- 基线：`feat/v13-wave2-monitoring-audit-20261016` head `c345f6b`
  （stacked on #233 `f76bc09` → #196）。
- **不部署、不接 timer、不发 Feishu、不登录账号、不监听端口、不启动/重启服务。**
- **不读生产 state、凭据、聊天内容；不调用真实 Core 路由。**
- 本目录只新增审计文档与独立测试，**没有修改 `runtime/**`**。
- 没有真实发送收据，因此这里没有任何 `LIVE REPORT`、`DELIVERED` 或
  `V1_CONNECTED` 的生产声明。

## 内容

| 路径 | 作用 |
|---|---|
| `MONITOR-SCHEMA-GAP-MATRIX.md` | Core 读数 / 设备条目 / 报告收据 / 样本账本的 schema 与缺口矩阵 |
| `tests/check_monitor_contract.py` | 无副作用合成负测：契约必须成立的部分 + 已确认缺口的 expectedFailure |

## 运行

```bash
python3 integrations/fleet-v13-monitor/tests/check_monitor_contract.py
```

或：

```bash
python3 -m unittest discover -s integrations/fleet-v13-monitor/tests -p 'check_*.py'
```

**为什么文件名不是 `test_*.py`：** `tests/test_collection_scope.py` 明确禁止
`tests/` 之外存在会被 pytest 按名字收集的 `test_*.py` / `*_test.py`。
本车道的独立测试因此命名为 `check_*.py`，只按显式路径运行，不进入主套件
census（`scripts/verify.py` 只枚举 `tests/test_*.py`）。

## 预期结果

- 通过项：现行代码确实保持的 fail-closed 契约。
- `expectedFailure`：本文件记录、已在源码/探针中确认的缺口（`V13-G*`）；
  缺口修复后它们会显示为 unexpected success，届时应改成真断言。
- **不把 expectedFailure 计成“已修复”**，也不把本目录的存在计成生产验收。
