> 历史宏观组件说明：保留早期分析。当前默认值、测试数、长尾算法、命令与发布证据以 [I085.md](I085.md) / [I085_TAIL.md](I085_TAIL.md) 为准；本文件中的当时状态不是本轮验证。

# i085 macro candidate：让宏观搜索保留真正不同的选择

**状态：可进行本地 native 对照的研究候选，不是已经证明优于 i082 的生产版本。**

基线是 `codex/i082-release` 的提交 `4c5823acd1a68acb797069fb8f7522a6ad857bbf`。本轮重点是整局层的候选保留、分支展开与计算分配；没有修改 CombatSolver、native 战斗预算、F1/F2 模型目标或胜利验证契约。实际执行的 Python 测试、静态比较、合成实验与环境限制见 [results.md](../release/i085/results.md)。

## 1. 独立诊断：先区分能够证明的问题和没有测到的问题

### 1.1 i082 的真正优势

i082 已经具备可以复用的规划基础，而不是一组孤立的选牌规则：完整动作前缀的 trie、完整请求缓存、最长前缀检查点、常驻 native worker、根据真实失败回退的 lineage、不同根策略的延续、独立的 synthetic 管道，以及从初始状态再次执行的胜利证明链。新增 macro 搜索没有理由重写这些基础。

它还不是“只看牌名强弱”：`StrategicStateEvaluator.cs`、宿主 metadata/DynamicVars/reflection/IL 路径已经从游戏模型读取信息，`GateModels` 也用本 seed 的真实首领结果更新决策排序。问题不是完全没有游戏语义，而是**已有语义、保留池和调度器如何共同决定下一笔计算花在哪里**。

### 1.2 确认存在的宏观弱点

| 代码位置 | 实际行为 | 本轮结论 |
|---|---|---|
| `focus.py:FocusScheduler.add` | i082 在读取候选决策邻域之前，只因终局 `utility` 与已有来源相同就返回 | 同分不代表同邻域；这是可构造反例的候选保留偏差 |
| `indexed_strategy.py:IndexedStrategicScheduler.add` | 宽菜单只排固定等距子集；其他选项只增加 deferred 计数，没有恢复队列 | “延后”不等于未来会被 explorer 尝试；旧文档的普遍覆盖表述过强 |
| `search.py:solve` 派发循环 | 先取 urgent，再考虑 repair 和普通 scheduler | 3:1 focus/explore 只是普通宏观调用的比例，不是全部评估或 native seconds 的比例 |
| `preparation.py:NativeMap.path / observe` | 金币/低血阈值触发有限路线；路径按精英数量、跳数排序 | 只能产生很窄的一类多步提案，不能代表所有发展方向 |
| `focus.py:_candidate` | 回退楼层距离与同菜单已尝试数构成主要代价 | 新的晚期精英来源不断补充近处偏离时，早期重构仍可能等很久；lineage backoff 只是部分缓解 |
| `archive.py:DiverseFrontier` 与 `search.py` 使用处 | frontier 被保存和展示，但主派发不从这里选候选 | “多样性前沿里有它”不等于“它得到实际搜索预算” |

第一个反例很简单：两条路线在同一首领处以同样生命伤害进度死亡，但一条经过某个商店、另一条经过另一组选牌。这两条路线的终局标量可能完全相同，前面的可改动动作却不同。先按标量合并，会丢失其中一条的 focus 资格。i085 不把这件事升级成状态等价问题，只修正 focus 来源的保留。

宽菜单问题也不是假设：当合法替代项有 30、127、511 项，固定 `site_cap=12` 的旧 explorer 单独运行只展开 12 项。恢复队列的夹具能最终展开全部这些替代项。这里证明的是**机制的覆盖差别**，不是游戏胜率差别。

### 1.3 不能据现有材料宣布的结论

没有完整的、可归因的 i082 端到端原始运行语料，无法诚实给出“计算的多少百分比在战斗”“主要失败在 Act 几”“macro 比 combat 更限制胜率”这样的实测结论。代码能证明一笔 rollout 包含前缀处理、native 规则执行、战斗搜索和结果传输；不能仅凭代码给出各项占比。

公开的 `release/i082/existing-run-progress.json` 是 seed `524130501` 的进行中快照：896 次评估、约 1134.38 秒、最深观察楼层 48、无证书。它不是完整 60 分钟结果，不能算一个最终失败；其中的楼层事件也不足以恢复每场首领及每种工作耗费。`holdout-01` 的 17/20 属于 `historical-frozen-i054a`，不是 i082，更不是 i085。

因此本轮选择的是“存在源码反例、可单独关闭、可测量的 macro 改动”，而不是声称已经测出了全系统最大的瓶颈。

## 2. 实际数据流和算力流

公开入口使用完整 i082 配置、普通战斗 10k 节点、7 worker、99 层/12,000 动作的宏观 horizon。一次宏观 evaluation 通常是“已执行完整前缀，在一个决策点换选项，再运行到死亡/胜利/预算边界”，不是一个廉价的单步价值查询。

1. 根 rollout 的 `native/pick/elo` 产生实际轨迹；派生请求继承来源策略族，并在允许的生成请求派发时取当前先验档位。
2. `Evaluator` 以完整请求身份查缓存，再找完整前缀检查点；常驻 worker 执行真实规则。没有命中时才产生新 native 工作。
3. 普通结果按提交序吸收。真实结果进入 gate 模型、frontier、scheduler、lineage；根据来源可产生战斗重试、准备路线、F1 赢法复用等工作。synthetic 结果走独立分支。
4. `GateModels` 的值确实影响 focus 选项排序和 native rollout 档位；拟合后的联合 F1/F2 模型也确实会接管相应关口信号。它们不是纯遥测。反过来，frontier 的多样性快照本身不决定派发。
5. 获胜候选交给 `verify_win`：新进程，从初始状态，不带 advisor、checkpoint 或结果缓存，核对原生终局、动作证据与身份，只有契约通过才生成 verified 结果。

有序窗口 56 与 7 worker 会使“提交时用的模型”落后于尚未吸收的结果，这是可疑的成本来源，但没有测到其收益损失。进一步检查 `NativePool.run` 后，否决了更强的说法：**独立 replay 并不是固定排在全部 56 个 executor 请求之后**；它直接等待可用 worker，与其他工作竞争同一资源。窗口大小、队列等待、头部阻塞都应在本地测量，而不是先宣布窗口 14 更好。

路线消费者使用 `research_consumer` 的隔离进程路径：不使用缓存或检查点，不重新抽取先验。完整前缀 replay、进程启动与战斗费用可能很高，所以多步路线不能无约束塞进 urgent。

## 3. 游戏研究如何影响设计，而不是变成手写攻略

### 3.1 事实来源与版本边界

本仓库构建锁和 `docs/A10_RULES.md` 对应固定的游戏 DLL/版本。其历史静态审计描述 A10 累积规则、最终幕两个首领、F1→F2 仍在同一幕中，不能凭空套用幕间恢复。这个审计不是本轮重新执行的 native 测试。

外部资料检查使用了官方补丁/通讯入口、Spire Codex 的开发者 API 文档，以及搜索算法论文。检索中的二手补丁页存在版本、日期或引用不一致；官方具体补丁正文和 live API 数据没有全部成功取得。因此本轮**没有依据某条二手的卡牌改动重写 evaluator，也没有声称确认了 2026-10-05 的最新游戏二进制**。

Spire Codex 开发者文档区分 main/beta 通道，提供版本、diff、卡牌/遗物及统计/批量数据入口。这支持可更新数据管线的方向，但“文档写有接口”不是“本轮成功下载并验证了最新统计”。本轮没有替换 `policy_data.py`，对照实验必须使用同一 native identity 与同一源数据。更新游戏后，重新提取 metadata/语义和按版本下载统计，应建立一个新的实验块，不应混入旧块。

### 3.2 对 macro 的启发

地图是有承诺成本的多步选择。只改变当前一条边，之后立刻回到同一 rollout policy，未必能穿过“单步看似差、整段更合适”的路径组合。Act 1/2 的不同商店、事件、战斗和休息序列会改变后续可用选择；所以试验多步到首领的 travel 提案是合理的。但这并不推出“多精英好”或“少精英好”。两者都应由实际 continuation 评价。

进入最终双首领前，资源不能简化成“F1 赢即可”。满血 synthetic F2 与真实 F1→F2 continuation 在 HP、药水、遗物计数和消费状态上不相同。i082 联合模型的加牌支借用原基础 F1 结果、同表样本相关，都是需要本地检验的 surrogate 假设。此轮没有足够 native 数据重写这些模型，保留它们作为固定控制条件。

人类攻略通常在未知未来下估计收益，而 mode1 能用已知 seed 的未来验证具体候选。因此新路线只根据 native 提供的拓扑构造提案，把选牌/奖励/商店实际内容交回真实执行；不写“某张牌很强”“某角色必须走某路”。这个 portfolio 也**不是已经完整求解未来全部奖励的 full-information map oracle**。

## 4. 研究方向、取舍与被否决的方案

| 方向 | 判断与处理 |
|---|---|
| 去掉同分拒绝，保留不同决策邻域 | 有可复现反例；实现为 i085 默认 macro 开关 |
| 宽菜单逐渐恢复 | 源码确实无恢复队列；实现并用 30/127/511 替代项验证 |
| 限制 urgent 连续挤占 | 主循环可构造饥饿夹具；采用简单 admission 计数，不虚构学习收益 |
| Act 1/2 多步路径多样化 | 实现有界原型、默认 shadow、另设 native-on 比较组；未证明收益 |
| 自动把 focus/explore 改为 UCB/PUCT | 暂缓：作用臂非平稳、反馈延迟且高度相关，原生费用未知，胜利样本极稀疏；新复杂控制器缺乏有效的信用分配基础 |
| 更小窗口默认化 | 暂缓：可能缩短反馈延迟，也可能减少吞吐；提供 window14 消融，不默认改变 |
| 把路线特征、HP 或牌组签名做 transposition/dominance | 否决：遗漏 RNG/计数器/顺序，不能作为完整状态等价或不可行证明 |
| 关闭全部 F2 probes | 暂缓：成本可能高，但没有 measured dispatch/value-of-information 对照；本轮不把想象当证据 |
| 增大所有战斗预算 | 不选：破坏 macro 对照归因，也未证明战斗预算是首要限制 |
| 直接换成纯 MCTS 或 beam over Acts | 不选：需要新的中间状态价值和边界复用设计，无法由这轮小样本验证；不为了版本号重写系统 |
| 根重启 policy-seed 自动提供多样性 | 不假定：已有代码注释/历史观察指出可能重复；现成 root/restart jitter 作为单独比较臂，不擅自打开 |
| synthetic toy 更深代表真实胜率更高 | 否决：本轮 toy 中 i085 改变了分配，但没有提高最深楼层；保留这个负结果 |

算法论文提供的是研究问题而不是可直接搬用的保证。Policy-Guided Heuristic Search 强调搜索努力与解路径长度不是同一目标；当前 tier prior 并不是满足其定理前提的归一化路径策略。Go-Explore 的 return-then-explore 思路启发保留并返回已发现轨迹，但在这里应使用已有完整前缀/检查点，而不是把粗特征格子变成 exact identity。子目标引导搜索是后续方向；本轮只有文献摘要层面的调查，没有把它伪装成训练完成的模型。

## 5. i085 实现

### 5.1 `macro_plateau`：保留不同的宏观邻域

旧 profile 保留原来的 utility 去重。i085 才比较保留来源的决策邻域：每个决策索引及其合法替代前缀的完整 trie 节点集合。同分而邻域不同，可以成为 focus 来源；同邻域、同分或更差的来源不重复占位。若同邻域获得更好的真实结果，新来源替换旧来源。

pool=48、elites=8、现有牌组族上限、utility 排名、模型选项排序都保留。它没有使 focus 变成无界档案，也没有保证同分来源永不被淘汰。邻域键只是 focus 保留规则，不进入 game-state/cache/proof identity。普通 explorer 在 focus 保留判断之前仍吸收真实分支。

### 5.2 `macro_widening`：让 deferred 有一个真实的后续入口

初始仍展开 `site_cap` 个等距选项；剩余选项作为紧凑的 `CompactBranch` 放入 FIFO deferred 队列。每次 explorer 机会晋升一个；被真实执行或被 focus 提交的分支从 deferred 中移除，过期队列项忽略。lineage 暂停路径保持原有可逆分配语义。

这是简单的逐步展开，不是 UCT progressive widening 的理论实现。有限静态菜单、持续 explorer 机会下有明确的恢复行为；持续增长的搜索树、有限预算、已有过滤和模型停顿下不承诺遍历全部分支。保存更多分支会增加 Python 内存与探索费用。**该队列和已有 trie 没有新增总 RSS 上限**；不能把路线队列的 MiB 参数误读成整个 solver 内存界。

### 5.3 `macro_fair`：先保证主搜索仍有机会

默认连续 4 个 auxiliary 普通提案后，尝试优先取一个未被 lineage 阻塞的 core macro 分支。没有可用 core 分支时回到原次序，不丢弃 urgent 项。异步根轮次保持原顺序。

计数对象是 proposed ordinary admissions，不是已接受 evaluation、更不是 native seconds。额外 F2 readiness 的原采样路径不在这个小控制器的频率保证之内。不同任务的耗时可以差很多，因此只能说限制了这类队列的连续插队，不能说“至少 20% CPU 给 macro”。现有 focus/explore 比例和 repair 规则继续工作。

### 5.4 `macro_routes`：Act 1/2 有界多步 travel 实验

每条实际轨迹、每个早期 Act 只取第一个通过契约检查且有至少两个合法第一步的 map source。沿原生节点/边做有限简单路径搜索：跨第一步轮转，第一步内部 DFS，默认最多 256 个完整路径、4096 次部分路径展开。候选允许一般有环图输入，但提案路径不重复节点；这只是候选限制，不是排除循环可行解的证明。

从枚举集合中挑最多 3 条路线，相对于已观察路径及已选同组路线做 farthest-first 多样化。距离等权使用原生房间类型序列、房间类型数量差、经过节点差；不把某种房间写成“收益”或“风险”的固定数值。可解释但仍有主观结构权重、词典序和截断偏差，不宣称得到最有战略意义的 3 条路径。

路线使用既有 `spire-map-route-plan/v1` 与 `MapRouteReplay`，从真实来源完整前缀进入，继承原 `policy_seed/policy_prior/advisor/research_progress`。不强制新增 shop policy；商店经过后的行为仍由现有策略决定。每步合法性、来源原生状态/身份和上下文由现有 native consumer 检查。

| 模式 | 行为 |
|---|---|
| `off` | 不做新的路线构造 |
| `shadow`（i085 默认） | 构造并记录候选、结构与计算时间；不提交该 portfolio 的 native 路线 |
| `on` | 候选进入独立 round-robin 队列，遵守 lineage 阻塞，默认每 8 个普通提案至多安排一个 |

这里的 shadow **不是并行运行完整 i082 和 i085 两个调度器，也不是已有固定 seed 的反事实胜率估计**。只能观察“会生成哪些提案、代价多少、native-on 是否值得试”。shadow 本身有 Python 费用，所以提供 `i085-no-shadow` 比较组。

pending serialized payload 和近期 seen-key 分别使用默认 16 MiB 限额。它们不是 Python RSS 限额；字典、对象和既有 preparation 全局去重仍有额外成本。淘汰/超大来源保留 unresolved 语义，并在计数中暴露。只读前 2 Act，不改变 Act 3 的最终准备机制；没有 source 数据或契约不匹配时不猜测。

## 6. 默认配置、版本兼容与证据身份

新 profile 是 `--feature-profile i085`，继承 i082，新增开启 `macro_plateau/macro_widening/macro_fair`，路线为 shadow。公共默认仍为 i082。四个旧 profile 的原有字段解析结果和 i082 启动 argv 在独立旧源码进程中比较；18 个确定性 toy 路径、1,440 个请求逐字节相同。native C#/csproj、原证明/规范化文件和锁文件按字节比较不变。这不是所有 native 情况下的运行时间或输出路径等价证明。

唯一有意共享的测量变化是：成功 replay 契约之后，写入 `verification-timing.json`，区分候选产生和完成独立验证的时间。它不修改 replay 请求或判胜契约，但确实添加 post-success IO；不声称旧 profile 全部墙钟耗时逐位复现。新增关闭参数也会出现在配置快照。

`release/i085/defaults.json` 冻结实际解析结果。参数 4/8/3/256/4096/16 MiB 都是可消融的研究配置，**没有测得最优值**。没有改写历史 `release/i082`、holdout 或原 `SOURCE_MANIFEST.sha256`；原文件只描述原版身份，不描述 candidate。candidate 的源码哈希、Git 提交、差异与新证据另列。

## 7. 正确性影响

本轮不改 `mode1.check_winning_replay`、`is_winning_candidate`、原生终局信号、identity 检查、checkpoint 恢复语义或 CombatSolver。所有新分支仍需真实 native 执行，发现胜利仍需原流程独立重放。

synthetic 结果不能进入新路线来源；空的 `map_route_plan` 也被视为 consumer 请求，不递归产生新组合路线。描述符不进入 exact identity；路线 source 的 native counters/完整状态不同，不能因为牌组或路径描述相似就共享实际来源。DEAD/beam failure/超时/资源失败从未升级为 UNSAT。缓存命中不重新记 native 费用。

关键回归覆盖了：同分不同邻域、同邻域更好结果替换、deferred 回收、lineage 阻塞、source 身份/上下文、循环地图、Act 3 排除、shadow 无派发、源策略继承、隔离 consumer 无缓存、urgent 洪泛时主搜索恢复机会，以及已发现的一次缓存分支缩进回归（在最终候选前已修复）。

## 8. 本地 native 验证：按顺序执行

### 8.1 使用新目录，保留旧证据

解压完整 candidate 源码到新目录，或在拥有基线的 Git 仓库应用交付的 patch / bundle。不要覆盖正在运行的目录，不要把旧 certificate 复制到新输出。远端此前研究分支只包含早期 workflow/log；本次完整算法交付以源码包、补丁与本地提交身份为准。

PowerShell，在 candidate 根目录执行：

```powershell
python tools/setup_source.py --game-dir "D:\你的游戏目录" --ritsu-dir "D:\你的Workshop\3747602295"
python tools/setup_source.py --check-only
python tools/i085_validate.py --candidate-only --out outputs/i085-local-python
```

使用自己的合法 STS2 安装、完整 RitsuLib、Python 3.11+ 与 .NET 9 SDK。原构建锁仍适用；不要为了让 check-only 通过而跳过身份检查。若安装版本与锁不符，先按原 BUILD 流程准备一致依赖，或将版本迁移作为单独实验，不在 AB 中途更换。此处列的是待本机执行的命令，**不是本轮已经完成的构建**。

需要完整回归时：

```powershell
python tools/i085_validate.py --out outputs/i085-local-full-python
```

本次 Linux 无依赖源码环境中的两项基线错误没有被隐藏或强行改成通过：前端旧测试期待 180 分钟而公开上限为 45 分钟；vendor 未安装导致 solver-patch 类初始化失败。Windows 安装 vendor 后后一项可能消失；若出现新失败必须排查。完整测试返回非零就仍是非绿，不把“已知”改成“通过”。

### 8.2 确认解析值，先做短时接线检查

```powershell
python tools/run_release_source.py --seed 101 --out outputs/i082-plan --feature-profile i082 --minutes 1 --dry-run
python tools/run_release_source.py --seed 101 --out outputs/i085-plan --feature-profile i085 --minutes 1 --dry-run

python tools/benchmark_i085.py plan --out outputs/i085-smoke --seeds 101 --solver-seeds 271828 --arms i082,i085,i085-routes --minutes 1 --workers 7
python tools/benchmark_i085.py run --out outputs/i085-smoke
python tools/benchmark_i085.py report --out outputs/i085-smoke
```

plan/report 不执行 native；只有 run 执行。`run` 在 Windows 串行启动各 arm；每个作业仍由原 Windows Job 施加 8 个同类逻辑 CPU、14 GiB workload、2 GiB 系统保留和时间上限。worker 请求不能满足时拒绝静默缩放。短时未赢只检查接线、资源和契约，不是效力结论。

plan 保存绝对路径与源码哈希，**在将要运行的 Windows 目录重新生成**，不要直接运行交付中的 Linux dry-plan。修改源码后重新 plan。中断、失败记录不覆盖；runner 只跳过已有 status 文件，不自动重跑被中断的条件。异常留下锁时，先确认原进程树已退出，再使用新 experiment 目录；不要同时运行两个 AB 作业。

### 8.3 公平主比较与消融

`release/i085/panel.json` 包含按固定 SHA-256 规则生成、未根据本轮结果选择的 8 个公开开发 seed，避开了仓库公开 holdout seed。不能保证它们与用户所有私人研究 seed 不重合，**也不是封存 holdout**。

```powershell
$panel = Get-Content release/i085/panel.json -Raw | ConvertFrom-Json
$seeds = $panel.seeds -join ','
$solverSeeds = $panel.solver_seeds -join ','
python tools/benchmark_i085.py plan --out outputs/i085-ab30 --seeds $seeds --solver-seeds $solverSeeds --arms i082,i085 --minutes 30 --workers 7
python tools/benchmark_i085.py run --out outputs/i085-ab30
python tools/benchmark_i085.py report --out outputs/i085-ab30
```

固定同 seed、solver_seed、DLL、数据、机器资源与墙钟 cap；arm 顺序按 block 循环换位，避免总让一种配置先运行。单个 seed 的多 solver_seed 不是独立游戏样本，报告按游戏 seed 聚类给区间。需要路线实效时新建同面板的 `i082,i085,i085-routes` 实验；需要研究机制贡献时分别用 `i085-no-plateau`、`i085-no-widening`、`i085-no-fairness`、`i085-no-shadow`。窗口与根策略另有 `i085-window14`、`i085-root-jitter`，不要一次打开所有变化后混称一个原因。

默认对照在**同一个 candidate checkout** 中运行旧 i082 配置，使共有测量、环境和宿主一致。独立旧代码的有限兼容性检查已完成；若需要逐 native 请求比较原 4c checkout，请另建 reference 工作目录与完整身份记录，不混合新旧源码结果冒充同一实验块。

## 9. 报告的胜利、时间和费用口径

`comparison.json/.md` 自动生成，但 JSON 包含更完整的证据。report 重新检查保存的候选、独立 replay 请求/结果、二进制身份、完整动作、expected evidence 和原 `check_winning_replay` 证书一致性。单有 VERIFIED_WIN 字样或 certificate/route 文件不够。**这次检查只是已保存证据的一致性审计，不是在报告阶段新执行了 native，也不是防恶意伪造的外部证明。** 测试中的人造契约夹具明确不算 native 胜利。

首胜时间来自 replay 成功后的 `verification-timing.json`，不以候选时间代替。计时起点是 coordinator wall origin；外部构建/setup、所有 wrapper 启动时间不都包含其中。Windows Job 超时/失败后得到的真实证明可保留 proof 意义，但不记为该预算内成功。原任务的严格 outer-job 生效由资源报告检查，benchmark runner 另记录总 wrapper_seconds。

楼层/Act 首达时间记录的是**结果可被观察到的时间**，不是 rollout 内部未经采集的真实跨幕瞬间。F1/F2、不同入场、模型及路线遥测来自当前作业，不用历史胜利或 synthetic 样本增加真实 pass。

native seconds、combat nodes、beam/prefix 阶段、前缀动作与检查点跳过数按 kind 分组；缺测单列 unknown，部分 probe 成本保留 known subset，不用 0 冒充完整费用。cache-hit 的再次评估费用为 0。成功验证本身的总 native 工作、在途被取消任务或崩溃前未返回的工作可能不完整，不能把账本总和当成全作业已知总费用。

比较还核对 manifest/effective settings、seed/solver_seed、fresh start、host identity、实际 workers/dop、Job 上限、记录的 CPU 分区以及运行前后源码哈希。缺结果、部分结果、身份混用、验证不一致、验证时间缺失都会明确列为 omitted；不悄悄写成一次普通 defeat。报告的可审计配对统计不是覆盖所有中断作业的 intention-to-treat 胜率，必须同时检查遗漏率和失败原因，防止只看到幸存作业。

restricted-time 取成功验证时间，不解出取共享 cap；candidate−i082 为负才倾向 candidate。少量 seed 的 bootstrap 区间仅供描述，不能凭一个正差异批准生产。shadow 没有对应未执行路线的胜败标签，也不能给因果结论。

## 10. 当前证据与后续晋级条件

本轮完成源码修改、独立旧/新解析及请求夹具比较、新增 Python 测试、全量 Python 回归、合成覆盖/微成本实验、实际命令 dry-run、自动 native AB 与证据报告工具。native build、native rollout、真实通关及胜率比较均未执行。

一个重要负结果是：toy campaign 的搜索分配发生变化，最深楼层没有提高。它阻止了“排得更公平就一定更会赢”的过度推断。多步路线仅测了合成图构造耗时，未包含真实 source 序列化、启动、前缀 replay、战斗；不能据此宣布 negligible overhead。

建议的晋级条件不是某个主观 feature 清单，而是：同资源下配对 first-verified-win 或真实 gate conversion 有稳定改进；无额外验证失败；更丰富的分支确实进入了真实 rollout；新增 pending 内存、probe/route 耗时和漏报比例可接受。负收益时应关闭相应开关，保留得到的证据。i085 不是不可逆替换，`--feature-profile i082` 始终可回到对照。

尚未回答：宽菜单恢复实际触发频率、同分多样性在有限 pool 中的收益、auxiliary streak 对 native seconds 的映射、Act 1/2 组合路线的真实得失、窗口反馈延迟、synthetic F2 信号相对真实 continuation 的校准，以及跨角色 native 接线。核心数据结构没有牌名绑定，但沿用的 research consumer 契约仍限制 IRONCLAD/A10；不以结构通用性冒充跨角色 native 测试。

## 11. 源码索引与文献

- `spire_exact/planning/{focus,indexed_strategy,macro_i085,search}.py`：保留、展开、限插队、组合路线、主循环。
- `spire_exact/planning/{preparation,final_defaults,__main__}.py`：契约复用、profile、CLI。
- `tools/{run_release_source,run_seed,benchmark_i085,i085_validate,check_i085_compat,i085_synthetic_study}.py`：启动、对照、验证、可复现实验。
- `tests/test_i085_macro.py` 与 `tests/test_i085_benchmark.py`：机制与报告边界测试。
- 既有源码审查覆盖 `lineage/gatemodel/f2_joint_model/f2_readiness/paired_card_probes/f1_winners/archive/pool/resources/strategy/gates`，以及 native `BeamAdvisor/StrategicStateEvaluator/MapRouteReplay/CampaignReplay/NativeRouteGraph` 和构建/证明契约。

文献与公开资料（查阅日 2026-10-05；论文结论只按实际取得的摘要范围引用）：

1. Orseau & Lelis, *Policy-Guided Heuristic Search with Guarantees* (2021): https://arxiv.org/abs/2103.11505 。搜索努力与解路径成本的区别；未在本项目声称其定理保证。
2. Ecoffet et al., *First return, then explore* (2021): https://arxiv.org/abs/2004.12919 。返回已发现状态后探索的启发；此处保留完整 native 前缀边界。
3. Tuero, Buro & Lelis, *Subgoal-Guided Policy Heuristic Search with Learned Subgoals* (2025): https://arxiv.org/abs/2506.07255 。后续子目标学习方向，未在本候选训练或复现。
4. Spire Codex, *Developer API*: https://spire-codex.com/developers 。main/beta、版本、diff、数据接口文档；本轮未导入新数据集。
5. Mega Crit 官方消息入口：https://megacrit.com/news/ 与 Steam app 2868840 官方新闻。具体补丁原文获取不完整，不据二手版本表迁移模型。

所有建议的游戏机制解释、模型错位和路线价值应区分“已有固定 DLL 审计”“社区资料”“当前源码事实”“研究推断”。本候选只把前两种中可验证的输入当事实，其余交由本地实验。
