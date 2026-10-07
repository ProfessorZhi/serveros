# CC 当前实施入口

2026-10-08。继续 SRV-0011 已接受的 ServerOS 工作环境目标，保留现有源码、版本与安全边界。旧 P0–P12 计划和长 ledger 是历史/专题资料，不整本加载，不据其旧现场结论停工。

1. 先读 [当前执行计划](plans/plan-01-native-work-environment.md)，只核当前包所需的事实。
2. 按包展开 [总架构](architecture/NATIVE-WORK-ENVIRONMENT.md)、[21 项 Ability 要求](architecture/ABILITY-REQUIREMENTS.md)、[目录收敛合同](architecture/DIRECTORY-CONVERGENCE.md)。
3. 用户委托夜间持续实施时，读取 [夜间执行委托](CC-OVERNIGHT-MANDATE.md)。该文件不是签名 grant，原生会话已有授权与无人续跑权限分开。

完成一个 PR/Run 后继续下一项已授权工程。普通工程不重复申请权限；真实外部条件只阻塞对应分支。使用隔离 worktree，保护 WIP、活动资源与未批准的历史删除；不扩生产权限，不伪造批准。

当前优先修复 evidence 对不存在 Run 的引用校验，再推进决定到 Work、本机接任、交付与目录消费者。已经交付的 reader、产品树、采集与看板生产者不要重做。每项结果带实际版本、消费者和范围；没有证据不声明完成。
