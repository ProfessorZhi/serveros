# workbench_sources — the AUTHORED half of the workbench.
#
# WHY THIS FILE IS SEPARATE FROM `workbench.py`
# --------------------------------------------
# Everything in `workbench/` is either MEASURED or DECIDED, and the two must not
# look alike. Measured content is rendered by `scripts/workbench.py` from a
# command that was actually run, so a reader can re-run it. Decided content is
# this file: a person wrote it, and no command produces it.
#
# The split exists so that a wrong decision cannot hide inside generated output.
# A decision rendered by a generator reads like a measurement, and the first
# thing anybody does with a measurement is believe it.
#
# WHAT THIS FILE IS NOT
# ---------------------
# Not a state store and not a source of truth for anything a verb can answer. If
# a claim here is also answerable by running something, it does not belong here;
# it belongs in `STATE_MATRIX` below, where the generator RUNS the command and
# prints what happened rather than what somebody remembered.
#
# The `command` on each state row is the whole point of that matrix: a row saying
# 已实现 is a row whose command exited 0 at the moment this file was rendered, and
# a row whose command now fails renders as FAILING without anybody editing prose.

from pathlib import Path

# The repository this module SHIPS inside, as the renderer sees it: the checkout
# when rendering from one, the revision when rendering from an installed
# artifact. Repo-internal sample paths are derived from it rather than written
# out, because this module is a member of the distribution and a distribution
# must not carry the path of the working copy it happened to be built in — see
# the `serveros` pin below, which did exactly that until a release criterion
# grepped the shipped tree for it.
ROOT = Path(__file__).resolve().parent.parent

# ── STATE_MATRIX ──────────────────────────────────────────────────────────────
#
# One row per capability a person would want to know the standing of. `command`
# is argv, run by the generator; `expect` is the exit code that makes the claim
# true. A row is never rendered from `claim` alone — the claim is shown BESIDE
# the observed exit code, so a stale claim is visible as a disagreement.
#
# `argv[0] == "serverosctl"` is replaced by this repository's `bin/serverosctl`.
# Any other argv[0] runs as given, relative to the repository root.
STATE_MATRIX = [
    # §5-B: the node's own runtime truth.
    dict(id="runtime", claim="serverosd 在跑，并且报告它最后一次看的时间",
         command=["serverosctl", "status", "--json"], expect=0),
    dict(id="device", claim="这台设备的 CPU/内存/load/GPU/磁盘事实",
         command=["serverosctl", "device", "show", "--json"], expect=0),
    dict(id="resource", claim="按 role 分的资源压力（根盘 vs 数据盘）",
         command=["serverosctl", "resource", "summary", "--json"], expect=0),
    dict(id="process", claim="进程按 group/session/container/role 归属",
         command=["serverosctl", "process", "top", "--json"], expect=0),
    dict(id="container", claim="容器归属，证据留在结论旁边",
         command=["serverosctl", "container", "attribution", "--json"], expect=0),
    dict(id="anomaly", claim="异常码 + 未评估项（未评估不是阴性）",
         command=["serverosctl", "anomaly", "list", "--json"], expect=0),
    dict(id="fs", claim="文件系统根分类（只报告，不移动）",
         command=["serverosctl", "fs", "hygiene", "--json"], expect=0),
    dict(id="tunnel", claim="隧道与结构性盲区（hidepid=invisible）",
         command=["serverosctl", "tunnel", "list", "--json"], expect=0),

    # §5-A: the ability plane.
    dict(id="ability", claim="本节点的能力投影，以及它有多新鲜",
         command=["serverosctl", "ability", "list", "--json"], expect=0),
    dict(id="ability-tree", claim="从分类元数据派生的能力树（不看文件位置）",
         command=["serverosctl", "ability", "tree", "--json"], expect=0),
    dict(id="taxonomy", claim="声明的导航 DAG，投影成树",
         command=["serverosctl", "taxonomy", "tree", "--json"], expect=0),
    # Two rows, because there are two answers and the difference between them is
    # the whole point of the verb.
    #
    # Neither is `expect=0`: this verb's exit codes are the kernel's own, and
    # `EXIT_COULD_NOT_ANSWER` is what it returns for UNKNOWN. The claim is
    # therefore not "it is available" — asserting that here would be asserting a
    # verdict about this node's catalogue inside a document about whether the
    # verb is reachable. The claim is "it answers, and it names what it could not
    # see", which `contains` checks.
    #
    # The first row supplies no catalogue and no provider set. It used to be the
    # only row and it used to come back `UNAVAILABLE · PROVIDER_NOT_INSTALLED`,
    # green, while `inputs_seen` was `[]` and the sentence on `detail` was
    # "runtime-management is not in the pinned catalogue" — a finding about a
    # catalogue nobody had handed over. Two rows now hold that apart: no
    # catalogue is a GAP and says so; a catalogue that was read and missed is a
    # FINDING, and the second row is the one that reads one.
    dict(id="ability-resolve", claim="八输入一个未给：UNKNOWN，且把八个输入全部具名列出",
         command=["serverosctl", "ability", "resolve",
                  "--request", "abilities/requests/resolve-example.json",
                  "--json"], expect=2, contains="inputs_missing"),
    # `--catalogue` rather than `--ability-repo`: the latter needs an absolute
    # checkout path, this row is re-run by `workbench check` on machines that do
    # not have this one's layout, and `rel_home` rewrites the displayed path to
    # `~` without the shell ever expanding it. The example catalogue is a couple
    # of lines in the repo, so this row exercises input 1 and stays portable.
    dict(id="ability-resolve-catalogue",
         claim="给了 catalogue 之后：它读到了它，然后只指出剩下没给的",
         command=["serverosctl", "ability", "resolve",
                  "--request", "abilities/requests/resolve-example.json",
                  "--catalogue", "abilities/requests/catalogue-example.txt",
                  "--json"], expect=2, contains="inputs_missing"),

    # §5-B: the registration loop.
    dict(id="registration-discover", claim="观察到却没有注册的东西，具名列出",
         command=["serverosctl", "registration", "discover", "--json"], expect=0),
    dict(id="registration-resolve", claim="每条注册对照实测：OBSERVED/ABSENT/STALE/UNREPORTED",
         command=["serverosctl", "registration", "resolve", "--json"], expect=0),

    # §5-C: the three cards.
    dict(id="card-status", claim="状态卡：一个模型，Feishu JSON，一个预览",
         command=["serverosctl", "card", "status", "--json"], expect=0),
    dict(id="card-alert", claim="告警卡：最多三条事件，各带未容纳计数",
         command=["serverosctl", "card", "alert", "--json"], expect=0),
    dict(id="card-recovery", claim="恢复卡：只讲被通知过、且已恢复的",
         command=["serverosctl", "card", "recovery", "--json"], expect=0),
    dict(id="alert-list", claim="告警账本：持久化的是同一个问题，不是每分钟一条",
         command=["serverosctl", "alert", "list", "--json"], expect=0),

    # §5-D: the chain from information to a delivered report.
    dict(id="requirement-board", claim="跨项目需求看板",
         command=["serverosctl", "requirement", "board", "--json"], expect=0),
    dict(id="work-board", claim="个人看板：§6 的分节与顺序",
         command=["serverosctl", "work", "board", "--json"], expect=0),
    dict(id="work-coverage", claim="覆盖页：磁盘、三个飞书面、关注源，各带分母",
         command=["serverosctl", "work", "coverage", "--project", "serveros", "--json"],
         expect=0),
    dict(id="work-skeleton", claim="文档骨架：每一节为什么在那里",
         command=["serverosctl", "work", "skeleton", "brief", "--json"], expect=0),
    dict(id="workspace", claim="放置与治理：一个东西属于哪里",
         command=["serverosctl", "workspace", "overview", "--json"], expect=0),
]

# ── DECISIONS ─────────────────────────────────────────────────────────────────
#
# What was chosen, what was rejected, and what would change the answer. `state`
# is CLOSED or OPEN; the OPEN ones are what `REPORTS.md` collects into the
# Decision Packet, because a decision that is still open is the only thing an
# outside reader can actually help with.
DECISIONS = [
    dict(
        id="D1", state="CLOSED",
        chose="复用已有的 `work_delivery_slice` 与 work-engine，不新建工作引擎或调度平台",
        rejected="为本轮另写一条工作链",
        why="主链不是从零开始：已经有一条跑通过完整交付路由的 slice。再写一条会产生"
            "两个关于「一个 Item 怎么走完」的权威，而这类双权威正是这个仓库一直在删的东西。",
    ),
    dict(
        id="D2", state="CLOSED",
        chose="卡片通用逻辑留在运行时模块，节点/对象/目标由配置给出",
        rejected="把 renderer 留在 ops 里，每个节点一份",
        why="状态/告警/恢复三种卡共用一个确定性模型；按节点复制会让「同一条事实在不同"
            "节点上长得不一样」，而手机上的那张卡才是人真正读的那张。",
    ),
    dict(
        id="D3", state="CLOSED",
        chose="Work Engine ID 真实冲突时不硬建 Item，改用具名 `foundation-skeleton` 工作区",
        rejected="直接创建 SRV Item，接受一次静默重号",
        why="当前 store 只认识 `tokyo/TKY`，而 records 里还留着历史 `SRV-0001...`。直接创建"
            "会静默重新分配同一个 ID，让两个不同对象共用一个名字——这类损坏在后来的每一次"
            "引用里都会继续。",
    ),
    dict(
        id="D4", state="CLOSED",
        chose="Quay 适配写在 Provider 版本自己的语义上（`GOAL-NNN`/`AC-NNN` + criterion），"
              "不在 ServerOS 侧写死 `PHASE-NNN`",
        rejected="把设计文档里固定的 `PHASE-NNN` 当作全世界的约定",
        why="被固定的 archived 版本概念里有 goal store 却没有可调用的 goal CLI；当前 develop"
            "有。ID schema 是 Provider 版本的事情，ServerOS 需要知道的是「Goal / criteria /"
            "Task / execution / result」这五件事的语义。",
    ),
    dict(
        id="D5", state="CLOSED",
        chose="复用 `scripts/release.py` 的 promote/rollback，不手搓 RELEASE.json",
        rejected="为工作包 F 写一个自己的发布步骤",
        why="那条路已经有不可变 revisions/、current/previous 指针、渲染后再交换、验证通过"
            "才激活。手搓一份会得到一个只有本轮验证过的第二实现。",
    ),
    dict(
        id="D6", state="CLOSED",
        chose="工作台提交到 `serveros` 仓的 `workbench/`，不在 `records` 仓提交",
        rejected="把工作台写进 records 的发布面",
        why="`records` 是另一个 actor 的工作树，测量时有 36 条未提交条目。在白名单 commit 的"
            "范围内，向一个别人正在写的工作树提交是不可接受的；而工作台是**产物**，把它和"
            "产出它的 revision 放在同一个仓里才是可复算的。records 的发布位置在 DEPLOYMENT 里"
            "作为待决项列出，不在这里替它决定。",
    ),
    dict(
        id="D7", state="CLOSED",
        chose="负控保留在产物里（Quay 探针任务、加深两级的 workspace 副本）",
        rejected="清理掉探针，只留干净结果",
        why="负控是「这条判据真的会红」的唯一证据。删掉它们，剩下的全绿就退回成一句主张。",
    ),
    dict(
        id="D8", state="CLOSED",
        chose="生成器进产物，渲染出的文档不进：`runtime/workbench.py` 随 `runtime/` 一起发行，"
            "`workbench/` 留在源码仓作为被审阅的快照",
        rejected="把 15 份渲染好的 .md 也作为 composition 成员发行",
        why="这个判断在本轮改过一次，改的原因是读产物：最初的写法是「先按不进实现，列为待决」，"
            "但真正去写 `bundle.yaml` 时看出两者不是同一类东西。生成器是代码，属于发行；"
            "文档是**某一个节点某一时刻的读数**——这台机器的 state 根、覆盖分母、pin 所在路径。"
            "发行不是节点，把它装进产物等于把一台机器的读数塞进每一份安装。另有两个具体后果："
            "产物目录会被下一次更新整体替换（正是 `bundle.yaml` 给 MCP adapter 设 "
            "PYTHONDONTWRITEBYTECODE 的同一个理由），写进去的东西会被它本要服务的那件事销毁；"
            "而且产物里的 `runtime/` 是 `release.py` 按 hash 验证过的 staged revision，改写会让 "
            "RELEASE.json 描述一棵已经不存在的树。所以 `workbench render` 在安装里拒绝默认输出"
            "目录并按名字说明原因，要求显式 `--out`；`check` 在目录不存在时返回 2 说不曾比较过"
            "任何东西，而不是报 15 份文档缺失。",
    ),
    dict(
        id="D9", state="OPEN",
        chose="（待定）`records/<project>/` 与 `records/projects/<project>/` 两套布局是否合一",
        rejected="（待定）",
        why="这个分歧在本轮被观察到（同一份东西两种写法都出现过）。合一需要改别的 actor 正在"
            "写的工作树，超出本轮白名单范围。",
    ),
]

# ── DEPLOYMENT ────────────────────────────────────────────────────────────────
#
# §8's minimal change list. NOTHING here has been executed. Each row is what
# would change, what it touches, how a person would know it worked, and how to
# put it back — so that the approval is a decision about a named change and not
# about a paragraph.
#
# `state` is deliberately one value only. This file has no way to say "done",
# because nothing in this round installs anything: the one promotion performed
# this round went to an isolated runtime root under the run's own artifact
# directory, and it is reported as such in RELEASE-MANIFEST.md.
DEPLOYMENT = [
    dict(
        id="P1", target="把 serverosd 指到新 revision（promote + restart）",
        effect="线上 `current` 指针移动，serverosd 重启；节点观察中断一个 tick 级别的时间",
        before="`scripts/release.py status --runtime-root /data/home/vince/runtime-assets/hwz-server-os`"
               " 记下 current 与 tree_hash；`serverosctl status --json` 记下 pid 与 ticks",
        after="`release.py status` 显示新 revision 且 agreement AGREE；"
              "`serverosctl status` 的 ticks 继续增长、tick_failure_count 未上升",
        rollback="`scripts/release.py rollback --runtime-root /data/home/vince/runtime-assets/hwz-server-os`"
                 "（previous 指针已在 promote 时写下），再 restart",
    ),
    dict(
        id="P2", target="覆盖现用的 CC 插件 / Hook",
        effect="当前会话与后续会话加载的 skill 集合变化；正在跑的其他会话可能读不到原有 skill",
        before="记录当前插件目录的 revision 与 tree_hash；记录 `.claude/settings.json` 的 hook 段",
        after="新会话里 skill 列表包含本轮新增项；旧 skill 的入口仍可解析",
        rollback="把插件目录指回记录下的 revision；hook 段按记录恢复",
    ),
    dict(
        id="P3", target="切换生产卡片 timer（tokyo-status-card / tokyo-alert-digest）",
        effect="真实投递到飞书；收件人看到的卡片内容与频率变化",
        before="记录两个 timer 的 enabled/active 状态与上一次投递时间",
        after="卡片出现在目标会话，且内容与本仓 `workbench/STATUS-PREVIEW.md` 同源可比",
        rollback="`systemctl --user disable --now` 回原 timer，并恢复原 unit 文件",
    ),
    dict(
        id="P4", target="启用每日定时任务（日报/看板生成）",
        effect="每天固定时刻产生一次写入；本轮只交付默认 disabled 的模板",
        before="确认模板 unit 的 ExecStart 指向隔离安装而不是源码树",
        after="首次 fire 后检查产物落点与 exit code，而不是只看 timer 是 active",
        rollback="`systemctl --user disable` 并删除已生成的 unit",
    ),
    dict(
        id="P5", target="真实飞书投递与客户端回读",
        effect="对外发送。内容一旦发出即可能被缓存或索引，删除不等于撤回",
        before="用 `workbench/*-PREVIEW.md` 的离线渲染逐字确认要发的内容",
        after="在飞书客户端回读，确认与预览逐字一致；不一致则按 P3 回退",
        rollback="无法撤回已投递内容；回退的是后续投递（停 timer）",
    ),
    dict(
        id="P6", target="`records` 发布位置写入工作台",
        effect="向另一个 actor 正在写的工作树写入；测量时有 36 条未提交条目",
        before="重新取一次 records 的 git 状态快照（不得复用旧快照）",
        after="只以显式路径提交，`git status` 里其他人的条目数量不变",
        rollback="`git revert` 该条提交",
    ),
]

# ── PINS ──────────────────────────────────────────────────────────────────────
#
# What this release was built from. `repo` is the clone the reachability check
# runs in, so it is a fact about THIS node — which is why it is authored here and
# not derived: there is no rule that says where a clone lives.
#
# EXCEPT THE FIRST ONE, AND IT WAS WRONG. The `serveros` row named this round's
# build worktree by absolute path — in a module that SHIPS INSIDE THE ARTIFACT.
# The effect is small and the shape is not: an installed revision carried a
# sentence saying where the source it was built from could be found, on a machine
# where that path is the build machine's. It is the same defect the composition
# comments name twice over (`ExecStart` pointed at a per-Item worktree; the
# capability catalogue read from a dirty checkout): a DISTRIBUTION naming a
# movable working copy. `ROOT` is the correct answer for the revision's own
# repository because in a checkout it IS the repository and in an artifact it is
# the revision — which is the identity this row is about. Found by writing the
# release criterion that greps the shipped tree for exactly this path.
#
# `release_ref` IS A DECLARATION, NOT THE REACHABILITY CHECK, AND THE SPLIT IS A
# REPAIR. This field used to be called `branch` and it was the ONLY input to the
# manifest's reachability column — so that column answered "does the branch I
# typed exist on the remote", and printed the answer as "this pin is reachable".
# §6 asks for a reachability check because a manifest can name a revision nobody
# can fetch and still read as a release; what it did not ask for is a check that
# a hand-typed NAME can satisfy on its own.
#
# Both directions went wrong in one round. Three of the four repositories below
# had their pin on the remote's own default branch the entire time and read as
# `远端不存在`, because the name in this field had never been created there. Then
# the name was created on all four remotes — and on three of them the tip was
# already that remote's HEAD, so nothing was published and the page went green.
#
# The column now asks the remote for its ADVERTISED REFS and decides ancestry
# against those (`runtime/workbench.py:_remote_reachable`); a name in this file
# cannot manufacture or destroy that verdict. `release_ref` is what it always
# should have been: which ref this release says it publishes at, rendered in its
# own column beside the reachability answer rather than standing in for it.
#
# A pin with no `release_ref` and `publishes=False` is one this node holds
# locally and does not claim to publish. A pin with neither renders `未指定`,
# because an undeclared publication point is a finding and a dash would render
# the absence of a finding as though it were one.
#
# `kind` NAMES THE CLASS, AND THE CLASS IS THE POINT. This list used to hold
# five rows and every one of them was `code` or a work package — the
# `work-engine`, `hwz-ops` and adapter classes were absent from a document whose
# acceptance sentence reads「固定所有代码/能力/策略/**adapter** revision」. A
# five-row table where every row is green does not look like a table with three
# classes missing from it; that is what made the omission durable rather than
# merely wrong. The column was added so that a reader can see what the table
# SHOULD contain, not only what it does.
#
# EACH `kind` IS A SEPARATE THING THAT CAN GO STALE ON ITS OWN. The ability
# source, the ops repo and the adapter each carry their own revision and each
# can drift while the others hold still. A manifest that pins only the code is
# one that reports "the release is pinned" during exactly the period an
# unpinned ability source is being built into it.
PINS = [
    dict(name="serveros", kind="code",
         repo=str(ROOT),
         revision="60da36f3", release_ref="serveros/system-v1-20260920"),
    dict(name="ability-management", kind="ability",
         repo="/data/home/vince/workspace/worktrees/ability-management/SRV-0011",
         revision="fbd0b1a7", release_ref="serveros/system-v1-20260920"),
    dict(name="work-engine", kind="code",
         repo="/data/home/vince/workspace/worktrees/work-engine/SRV-0011",
         revision="65e9b56c", release_ref="serveros/system-v1-20260920"),
    dict(name="hwz-ops", kind="ops",
         repo="/data/home/vince/workspace/worktrees/hwz-ops/SRV-0011",
         revision="790bca27", release_ref="serveros/system-v1-20260920"),
    # The adapter is BUILT from `plugin/`, so its revision is a serveros
    # revision — and it is still its own row, because the artifact that lands on
    # a node is the composed plugin, not the checkout. `composition` names which
    # one: this row is the identity the installed plugin must match, and the
    # gap between it and what is installed is a fact for ENVIRONMENT to report,
    # not a reason to leave the row out.
    dict(name="Claude/Codex adapter", kind="adapter",
         repo=str(ROOT),
         revision="60da36f3", release_ref="serveros/system-v1-20260920"),
    # `publishes=False` rather than an absent field: a toolchain this node holds
    # and does not claim to publish is a DECISION, and it renders as one. The
    # difference matters because `release_ref` absent WITH this flag is 本节点
    # 持有，不声称发布, and absent WITHOUT it is 未指定 — a gap. Collapsing the
    # two would let a row that simply forgot to declare a publication point
    # render exactly like a row that decided not to have one.
    dict(name="quay（工作包 E 固定的版本）", kind="toolchain",
         repo="/data/home/vince/workspace/worktrees/quay/foundation-skeleton",
         revision="ddc4fb40f", publishes=False),
    # This row used to name `worktrees/quay/foundation-skeleton-develop`, which
    # no longer exists — so the pin printed `**repo 不可读**` forever while the
    # revision it names was sitting in the sibling clone all along. Both fixed
    # versions live in ONE repository; two rows pointing at it with two
    # revisions is the accurate shape, and it is what the reachability check
    # actually needs.
    dict(name="quay（本轮 Goal 闭环用的版本）", kind="toolchain",
         repo="/data/home/vince/workspace/worktrees/quay/foundation-skeleton",
         revision="8bb184ef3", publishes=False),
    dict(name="design package（本轮固定版本）", kind="design",
         repo=str(ROOT),
         revision="c01d2a0d", release_ref="docs/final-architecture-20260920"),
    dict(name="design package（本轮从它继续）", kind="design",
         repo=str(ROOT),
         revision="e1f7de4d5", release_ref="docs/foundation-skeleton-20260920"),
]

# ── DOC_KINDS ─────────────────────────────────────────────────────────────────
#
# WHICH HALF OF THIS SET CAN BE CHECKED, AND WHY THE OTHER HALF CANNOT.
#
# This list exists because the first version of `workbench check` compared
# every document against a fresh render and went RED on four of them — twice in
# a row, seconds apart, on a correct renderer. The four were STATUS-PREVIEW,
# ALERT-PREVIEW, RECOVERY-PREVIEW and ENVIRONMENT, and the diff was load
# average, available memory, and alert/recovery counts. Those documents are
# MEASUREMENTS OF A RUNNING SHARED MACHINE. Re-measuring does not reproduce
# them; it produces a new reading, and demanding equality would have produced a
# check that flaps forever and gets switched off — which is worse than no check,
# because it trains the reader to ignore the one time it is right.
#
# So each document declares which of two things it is:
#
#   claim   — its content is produced by commands' VERDICTS (exit codes,
#             required substrings, authored decisions, revision pins). A fresh
#             render must reproduce it. `check` compares it in full.
#   reading — its content is a quantity of this node at one moment. A fresh
#             render must NOT be expected to equal it; what must hold is that
#             every command it claims to read from still answers. `check`
#             verifies THAT and compares no numbers.
#
# A reading is not a weaker document — it is the one a person actually reads.
# It is only weaker as a RE-CHECKABLE ARTIFACT, and saying so is the difference
# between a page whose numbers are known to be a moment and a page whose numbers
# look like facts. The header of every rendered document states which it is, in
# the reader's language, right under the title.
#
# The list is authored rather than derived. Nothing in the renderer can tell
# whether a number came from a live host or from an authored table, and a
# heuristic that guessed would be this file inventing a standard again.
DOC_KINDS = {
    "START-HERE": ("claim",
                   "状态矩阵是 23 条命令的判定，不是读数。渲染时会重跑它们并比对同一份判定块"),
    "ENVIRONMENT": ("reading",
                    "load、内存、进程数取自本机当前快照"),
    "STATUS-PREVIEW": ("reading", "CPU/内存/磁盘占用是本机当前读数"),
    "ALERT-PREVIEW": ("reading", "告警条数与账本游标随每次采集变化"),
    "RECOVERY-PREVIEW": ("reading", "恢复计数随每次采集变化"),
    "COVERAGE": ("reading", "目录/文档计数随别人写入而变"),
    "UNDERSTANDING-DELTA": ("reading", "认识文档的计数与 sources 覆盖随写入而变"),
    "BOARD": ("reading",
              "在飞条数与状态取自各项目的 board 文档；该文档取出多旧，页面上就写多旧"),
    "TODAY": ("reading", "今日重点是当前状态的一次选择"),
    "ITEM-CHAIN": ("reading", "单项链接读的是当前 Item 状态"),
    "DECISIONS": ("claim", "作者输入，不随机器变化"),
    "REPORTS": ("reading", "引用的是运行产物目录里当时的日报文件"),
    "QUAY-RESULTS": ("reading", "Goal/AC/Task 与 GateEvent 账本按渲染时刻读取"),
    "RELEASE-MANIFEST": ("claim",
                         "pin 与 revision 是定值；promote 之后这一页应当变红，直到重新渲染"),
    "DEPLOYMENT": ("claim", "作者输入，没执行过就不能变"),
}

# ── SAMPLES ───────────────────────────────────────────────────────────────────
#
# What exists as a worked example. §7 is explicit that a fixture passing means
# THAT offline path passes and not that any connection works, so each row says
# what it demonstrates.
SAMPLES = [
    dict(path="/data/home/vince/artifacts/skills/serveros/RUN-20260920-foundation-skeleton-f/"
              "release/hwz-server-os/serveros-linux-work/current",
         what="隔离安装：一个 runtime root 下不可变的 revision 加 current/previous 指针",
         how="由 `scripts/release.py promote` 落的，不是手工拷的；入口从 `/tmp` 走通"),
    dict(path="/data/home/vince/artifacts/skills/serveros/RUN-20260920-foundation-skeleton-f/"
              "install/build.log",
         what="构建日志：5 个 pack 的 pin 状态与产物落点",
         how="`scripts/build.py --write` 的原始输出"),
    dict(path="/data/home/vince/artifacts/skills/serveros/RUN-20260920-foundation-skeleton-f/"
              "install/check.log",
         what="确定性构建检查：同一输入两次构建逐字节一致",
         how="`scripts/build.py --check`，两次 tree_hash 相同"),
    dict(path="/data/home/vince/artifacts/skills/serveros/RUN-20260920-foundation-skeleton-f/"
              "quay/ws",
         what="Quay Goal/AC/Task 记录与 GateEvent 账本（本轮真实执行）",
         how="`quay goal write` / `quay goal gate` / `quay task` 在隔离 workspace 里跑出来的"),
    dict(path="/data/home/vince/artifacts/skills/serveros/RUN-20260920-foundation-skeleton-f/"
              "quay/probe-deeper/a/b/ws2",
         what="负控：同一份 config 放深两级的 workspace",
         how="复现 provider `path` 的深度依赖（`spawn node ENOENT`）"),
    dict(path="/data/home/vince/artifacts/skills/serveros/RUN-20260920-foundation-skeleton-e/"
              "negative",
         what="工作包 E 的三个负控：陈旧 revision / 子任务缺席 / complete 事件被删",
         how="每个负控单独 exit 1 并给出具名原因；未做手脚的副本 exit 0"),
    dict(path=str(ROOT / "runtime" / "card-scenarios.yaml"),
         what="六类卡片场景（正常/部分未知/单异常/观测失败/混合/仅恢复）",
         how="测试与预览读同一份输入，所以审阅的卡片就是测试跑过的卡片"),
    dict(path=str(ROOT / "runtime" / "workbench_sources.py"),
         what="工作台里不是由命令产生的**那一半**：状态矩阵的主张、九条取舍、待部署清单、pin",
         how="这一页与它背后的文档由 `serverosctl workbench render` 生成；作者输入在这里，"
             "所以「这条主张是谁写的」有答案，而不是藏在生成器里。**本文件集自身不作为样例列出**，"
             "因为 `render` 第一次写它时它还不存在，把自己作为存在性样例会让首次 "
             "`render` 之后的 `check` 必红一次"),
]

# ── LIVE_UNVERIFIED ───────────────────────────────────────────────────────────
#
# Code exists; the live path has never been walked. This is the column a reader
# should look at hardest, and every entry names WHY it is unverified rather than
# that it merely is.
LIVE_UNVERIFIED = [
    dict(what="真实飞书投递",
         why="`card` 渲染 payload 但不持有 transport；发送需要一个收件人，而收件人由人指定。"
             "本轮投递次数为 0。"),
    dict(what="飞书客户端回读",
         why="没有发送就没有回读。布局验证目前只到「同一份 payload 的离线渲染」。"),
    dict(what="CC 插件 / Hook 在真实会话中加载",
         why="覆盖现用插件是 §8 的 P2，需要单独批准；本轮没有动过任何会话实际加载的东西。"),
    dict(what="serverosd 指向新 revision（线上 promote/restart）",
         why="本轮只在隔离 runtime root 上跑通了 promote/rollback。线上指针未动，见 P1。"),
    dict(what="三个飞书空间的 live 抓取",
         why="覆盖页读的是离线契约与本地测量；没有任何适配器声称支持 live 抓取。"),
]

# ── NOT_DELIVERED ─────────────────────────────────────────────────────────────
#
# Named, not omitted. §9's rule is that a report with no such section reads as a
# report about a finished thing, and this round is not one.
NOT_DELIVERED = [
    dict(what="认识变化（delta）的产生者",
         why="认识层有卡片与 Sources 溯源，但没有东西把「今天知道了什么」读成一次变化。"
             "`UNDERSTANDING-DELTA.md` 因此是 PARTIAL，并且写明缺的是哪一个输入。"),
    dict(what="serveros 项目的关注源清单",
         why="`work coverage` 报 `sources_state = UNPRODUCED`：读取器在，但 "
             "`records/projects/serveros/context/sources.yaml` 没有被写出来。"),
    dict(what="每日定时任务的已启用实例",
         why="只交付默认 disabled 的模板，并且本轮不启用它 —— 启用是 §8 的 P4。"),
    dict(what="board 的优先级（P0/P1）",
         why="`requirement board` 报 `priority.state = UNPRODUCED`：这套 fabric 里没有"
             "任何 priority/severity 字段，看板的优先级分节因此全是空的。这是一个缺失的"
             "**产生者**，不是一个读错的值。"),
    dict(what="`today.yaml` 的一次真实选择",
         why="四个 ACTIVE 的 Item 在飞，而今日重点没有被记下来过。看板列出所有在做的事，"
             "与「今天做这两件」是两回事。"),
]

