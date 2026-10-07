# 给Tokyo Claude Code的实施交接

> 2026-10-07：当前执行入口已转到 [原生工作环境 V2 计划](plans/plan-01-native-work-environment.md)。本文件保留为既有子系统与历史执行参考，不再作为另一条并行总计划；权限和接口约束按 V2 的兼容迁移说明保留。

这是后续用户明确委托实施时使用的短提示，不是本次已经启动的工作。完整顺序/验收只维护在[CC-EXECUTION-PLAN](CC-EXECUTION-PLAN.md)，带时间的现场事实在[Implementation Baseline](IMPLEMENTATION-BASELINE-20260925.md)。

## 可交给CC的任务正文

请按本仓最新已接受的`docs/CC-EXECUTION-PLAN.md`，将ServerOS目标架构实现为可持续使用的工作环境，完成获准范围内的安装、真实消费者验收、有限日常任务启用及目录收口。不要只完成设计、写代码或打包就停止。

先核最新GitHub/本机源码、架构PR #6及Ability PR #4的接受状态、当前installed release和保护中的并行工作。现用`dbc9144`/`8ce6fe7`告警修复必须保留；canonical checkout较旧，不能直接拿它构建。若这些依赖已进main，复用实际祖先，不重复套补丁。每次实施从正确基线新建自有worktree。

一次解析现有可信授权、真实测试接收对象、source范围、费用/外发预算、安装/重启窗口及清理边界。缺不可替代决定时集中询问；普通实现、接线、迁移适配和局部测试在已接受范围内持续推进，不反复问“继续吗”。本任务不授权LiteLLM生产切流、共享服务任意重启、客户联系、复制凭据或删除他人/历史资产。

先过P0：系统盘曾99%，重新测容量/inode/quota；构建、依赖、缓存和测试输出放`/data`。不得为了推进安装直接清系统盘或停止历史测试daemon。保护现有SRV-0011、LiteLLM reasoning、MarketOps、Support Bot、浏览器profile和数据库/卷。

按P0–P12的实际依赖推进，优先修会让所有普通任务卡住的旧指令/装载入口；复用原有writer、Work Engine、Agent Control、发布器、Quay和Provider。把producer到receiver的接力做实，尤其新任务不能因没有历史产出而拒绝启动，child返回不靠用户转贴。

保持Native Agent主脑。简单任务原生直接完成，复杂/高风险才研究、独立review、委派或E2E。Skill精确触发、按需读reference，不为目录完整造能力，不把每次write变成一个Run。普通局部编译/测试问题自己修，独立真实产品unexpected FAIL按现行边界留证并停该产品自动修改。

P9构建只写隔离候选，用完整Ability SHA与可重现内容；P11先验证适用用户Journey。获准P12后分别证明Node Runtime、fresh Claude Plugin、fresh Codex MCP/Plugin加载正确，不以marketplace路径或`current`链接代替真实运行。

安装必须有consistent state backup、版本/配置回退和明确目标unit；promote的daemon-reload不是restart，`--no-reload`不是dry-run。定时任务只启用已有真实receiver的有限routine，替换现有sender时防双发；旧daily-task仅workbench render，不是完整工作循环。

目录收口先切consumer再决定retire，检查refs、owner、lease、open files、唯一证据与retention。无法证明可删则保留并解释，不以“零残留”当完成；缺真实接线也不能包装为正常保留。Shared Memory按真实许可采用，不把OpenViking数据库放Git或公网上暴露。

每个可审查切片提交并保checkpoint：当前基线、已满足/未满足结果、真实child/effect、下一动作和证据locator。直到required结果完成；遇预算/认证/权限边界给精确PARTIAL与恢复条件，其他独立范围继续。没有真实scheduler不得声称离开会话后仍会继续。

最终报告给版本矩阵、21职责/五循环的实际场景证据、启用的routine及安全停用、目录前后与保留例外、回退验证、全部剩余必需项。禁止把测试数、拒绝案例、fixture或一张卡片当全产品LIVE。
