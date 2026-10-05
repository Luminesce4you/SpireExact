# i085 continuation：面向确定性全信息求解的宏观搜索

状态：**已实现并通过无 DLL 测试的 research candidate；没有 native 效果证明。**
研究基线为 `4c5823acd1a68acb797069fb8f7522a6ad857bbf`。本次在先前 i085 macro candidate 上继续迭代，目标是改善长预算下从局部修补转向跨幕重构的能力，而不是只降低模型门槛。代码见 `continuation_search.py`、`continuation_value.py`；实际证据见 `release/i085/results.md`。

## 1. F2 的“24 行”究竟意味着什么

源码中存在不同的等待条件，不能混成一个样本门槛：

| 机制 | 原条件 | 实际含义 |
|---|---|---|
| 每个真实 gate 的 `GateModels` | minimum=24，refresh=16 | 本关口的入场行达到门槛后拟合；不是 24 个独立随机实验 |
| F1/F2 `F2JointModel` | minimum=24，refresh=16 | 合成 F2 表配齐后才有联合行，拟合目标是 F1 分数加满血 F2 均值 |
| F2 readiness | 一段 5 个合法样本；全败再申请另一段 | 5/10 的完整性契约，与回归的 24 行不是同一个维度 |
| readiness cadence | every=20 | 按已完成的非合成、非缓存原生 evaluation 计，不按秒 |
| ordinary ordered dispatch | 公开设置 window=56，workers=7 | 提交时先验可能在许多尚未吸收的结果之前确定 |

联合表还可能含借用原 F1 结果的加牌行和共享样本。**24 行既可能等待过久，也可能仍只有很少有效变化方向；不能把 24 读作 24 组独立 F2 证据。** 原模型记录了 sample group，但其岭回归没有按这些组重新赋独立样本权重。

用户提供的“部分离群 seed 从 F1 通过到 F2 通过通常还要 30 分钟以上”是重要的尾部现象，但目前没有随附这些完整原始日志。它不足以判断因果：可能是等待拟合，也可能模型早已拟合却一直选择错误的早期构筑或晚期修补。不要把该描述变成整体均值或本轮测得的分布。

新加 `--gate-model-minimum` / `--gate-model-refresh`，原默认仍为 **24/16**，提供 **8/4** 的单独消融。所有旧 profile 默认不变。新算法不依赖这个门槛才开始利用真实 F2 延续。

## 2. 数学目标：存在一条好延续，不是平均延续较好

在固定上下文、完整 native 状态和 RNG 状态下，把转移记为 T(s,a)。全信息 Boolean 目标是：

```
V*(s) = max_a V*(T(s,a))
```

不是“随机走法的平均胜率”。同一状态下做了许多差的搜索尝试，不应抵消一条已经找到的优良延续。但是所有未穷尽失败仍 UNKNOWN；这里的 max 不能把 heuristic damage 变成胜利证明。

本实现使用完整规范化动作前缀命名结点 p，并保留已观察的真实延续集 C(p)。宏观调度的观测摘要为：

```
B(p) = max_{c in C(p)} achievement(c)
```

achievement 按是否原生候选胜利、真实已过 gate 数、Act、floor、同次致命战斗已造成伤害排序。它仍是 allocation surrogate；只有原来的独立 root replay 能把全局下界置为 1。

每条真实轨迹返回时，扫描它经过的所有真实战略菜单，将该延续回传给其**确切的祖先前缀**。新档案不依赖该轨迹进入原来的 48 条 focus pool。因此，一条暂时较差但能保留新早期组合的路线，不会仅因 terminal utility 较低而失去所有宏观重构机会。

这利用了全信息条件下可以重复执行、比较具体未来的优势。没有用社区平均胜率代替当前 seed 的实际延续，也没有把满血、改 RNG 的合成 F2 当作这条真实路线的未来。

### 并没有做的事情

没有把粗牌组特征合并成 native transposition；没有声称实现完整 Bellman 动态规划或求出 V*；没有保存所有可能后继；没有加入不可行证明。已有 checkpoint/cache、原生菜单合法性、重放链路继续工作。动作前缀不同但物理状态等价的路线仍可能重复计算——这是正确性优先保留下来的限制。

## 3. 时间扩展：按服务费用在不同回退尺度间分配

### 3.1 为什么只加长时间不一定有用

原 focus 把死亡点附近的修改排在前面，新出现的晚期精英又带来便宜的近处修改。总时间增加时，算力可能持续投入相似的晚期邻域。按 gate 入场数触发的 backoff 只能部分纠正这种现象。

新档案将每个决策放入 `(Act, b)` 单元：

```
r = max(0, 已观察延续终点 floor - 决策 floor)
b = bit_length(r)
```

这是对数距离分层：半径 0、1、2–3、4–7、8–15……各有持续机会，而不是按距离线性延后所有早期候选。尺度只是分配，绝不用于状态等价或剪枝。

### 3.2 服务账本和在途预约

对于队列、尺度单元或具体决策，记录：

```
C = 已测 native wall seconds + 缺测时明确标注的估计 + 在途预约
```

提交前先预约近期 64 个正 native 耗时的中位数；首次使用 1 秒作为显式初值。完成后以 host 报告的实际费用结算，cache hit 是零新增 native 费用；缺少指标保留 estimate 和 unknown count，而不伪装成零。native wall seconds 的求和是 worker 工作量，不是并行整局 elapsed 或纯 CPU 时间。

默认两个 core macro 通道按费用争取相同服务：原 focus/explorer，以及新 continuation。新通道内部让每个活跃 Act 具有相同总份额，再在该 Act 的活跃距离层均分：选择最小的 `C(cell) * active_bands(act)`。

单元内部选择 `(C(site)+estimated_job_cost)/w(site)` 最小的决策。w 使用 B(p) 导出的 1–8 有界权重，偏向已经实际走得更远的延续，但不会把其他保留结点的权重压为零。已花费用不会因为出现新 elite/source label 而重置。真实祖先延续变好时可以更新距离层，原单元历史费用仍保留。

这是**费用服务控制，不是 UCB 胜率估计**。计入 pending 很重要：一口气派发 56 个请求时，不能让每个请求都把前面尚未完成的 55 个当作免费。

### 3.3 可以推导的性质与不能承诺的性质

在固定、有限、持续可用的候选集合，正且有界任务费用、有限估计误差、有界非零权重等条件下，最低累计服务规则不会让一个候选永久保持零服务：反复选择其他候选会不断提高它们的服务量。对数分层让尺度数量随 horizon 对数增长，而不是让最早决策永远被新的晚期半径零任务压住。

**这些条件不是当前整个动态 solver 都满足的全局假设。** 档案容量、lineage 暂停、持续新增分支、任务中断、辅助队列、昂贵未知任务都会影响有限预算结果。本实现没有无条件公平性、UCT regret、完整性、原 baseline 首胜时间常数倍或指数加速保证。尤其两个通道共享真实结果与 trie，不是两个互不影响的完整 solver 的并行模拟。

core macro 费用分配不覆盖 urgent、修复、synthetic probes 或独立验证的全部费用；之前的 `macro_fair` 仍只限制辅助连续 proposal 数。报告明确区分这两种预算。

## 4. 不等 24 行的真实延续模型：先实现，再否决默认控制

### 4.1 标签必须是实际 F1→F2 continuation

新 `RealContinuationModel` 只接受有 native campaign metadata 的真实 F1 入场及已知结局：

```
F1 失败                   y = 0.25 × F1 伤害 / 已观察最大生命总量
F1 通过、F2 失败          y = 0.50 + 0.25 × F2 伤害 / 已观察最大生命总量
F2 通过                  y = 1
```

范围保证 F2 reach 高于任何 F1-only loss；不再奖励 F1 战后 HP 本身。F2 已进入但预算中断没有失败标签。任何 synthetic、cache、UNKNOWN、timeout/resource failure 都不增加训练证据。F2 pass 仍不等于 independent verified win。

相同完整 F1 入口的重试只保留最佳已观察结局。每个 exact entry 保留完整 observation guard；相同前缀却观测不同则拒收，而不是猜测等价。默认最多 96 个统计入口，按阶段多数类中的最旧行淘汰，避免 F1-only 洪泛完全挤走罕见的 F2 证据。淘汰只是统计样本容量，不删除真实游戏分支。

### 4.2 相关性、组合结构和连续收缩

使用已有 native 卡牌 ID、升级、遗物、药水和 HP 资源特征，不写牌名强弱规则。计数先 log1p，再归一化。相同特征向量的多个真实入口，在回归中取组均值、组权重 1，并记录组内目标方差。**组是统计投影，绝不是 exact state。** 不同特征组仍可能相关，不能声称彻底解决有效样本量估计。

使用正半定二次核：

```
k(x,z) = 2 × <u(x),u(z)> + <u(x),u(z)>²
H = I - 11ᵀ/n
alpha = (H K H + lambda I)^(-1) H y     lambda = 0.25
```

二次项可表达 item–item、item–resource 交互。中心化避免同一个常见卡牌/共同 HP 被无意义的全局均值赋功；Cholesky 求解带正则系统。不同组只有一个时不产生对比信号，两个不同组即可拟合，不另设 24 行跳变。

对替代选择的前后投影计算 d、预测增益 delta，并按它在已观测特征方向上的支撑度 q 衰减：

```
d = centered(k(x_after,X) - k(x_before,X))
q = clip(dᵀ (H K H + lambda I)^(-1) d / ||phi_after-phi_before||², 0, 1)
trusted_delta = delta × sqrt(q) / (1 + within_group_target_variance)
```

q 是几何支撑程度，不是正确概率或校准置信区间。新的方向、相关样本、未建模 native 状态仍可能让预测错。数值异常直接 abstain。实际控制开启时，与原模型的有界标准化分数做连续残差混合，不让任意微小增益字典序压倒原排序。

### 4.3 选择替换不是凭空加牌

F1 入口已经包含原先选过的奖励。比较“换成另一张”之前，先尝试撤销原选择的特征，再应用合法替代项；不能把它误写成在已有结果上额外加牌。无法确定的变换、删除升级牌、未知 rest/shop effects 会 abstain。该运输仍是近似：后续选牌、消耗、升级和条件效应可能改变，所以永远要回到 native 实际执行。

新模型只给 continuation 通道的选项排序，不修改 worker 的整条 rollout policy、不注入旧联合表。这限制了它的即时影响，也使消融和 synthetic 边界清楚。

### 4.4 实验为什么使它默认 shadow

30 个可复现的构造实验（5 类世界 × 2 种目标选项 × 3 种 core scheduler），每次最多 120 个 oracle evaluation：

| 构造问题 | 原 focus-core | reroot 无新模型 | reroot 模型控制 |
|---|---:|---:|---:|
| 早期两个互补选择 | 113 / 未找到 | 92 / 94 | 104 / 未找到 |
| 晚期两个互补选择 | 17 / 9 | 17 / 11 | 32 / 11 |
| 单个早期选择 | 11 / 38 | 2 / 15 | 2 / 15 |
| 单个晚期选择 | 2 / 4 | 3 / 5 | 3 / 5 |
| 预算内未解控制 | 均未找到 | 均未找到 | 均未找到 |

表中数字是 **toy goal 首次出现的 evaluation 数，不是游戏胜率或 native 时间**。完整事件、明确标注的模拟 oracle 费用和真实 Python 用时保存于证据中。

结果同时支持跨幕服务可以穿过一种局部陷阱，又表明重新回归真实 F2 伤害也可能仍追逐错误 surrogate；新模型不是因为公式更复杂就应启用。因此 i085 **默认新宏观通道 on，但新回归只 shadow 计算**。`--continuation-model-control` 为独立实验开关，`--no-continuation-model` 可去掉 shadow 的 CPU 费用。这个负结果不是最终游戏结论，也不应通过只展示“早期单选”来掩盖晚期代价。

## 5. 拟合时间与 F1→F2 长尾的可观测性

新增 gate telemetry 记录：首个实际入场、首个实际 pass、真实 gate 首次拟合时间和行数、joint 首次拟合时间和行数、新模型首次非零信号，以及 F1-pass→F2-pass 的结果可观察时间差。

已进入但中断的 F2 可以记 reach，不能记 pass 或训练负样本。计时来自 coordinator 收到结果，不是假装知道 rollout 内部的跨 gate 时刻。

`benchmark_i085.py report` 新增 `time_scaling`：

- verified-win 在 5/15/30/60/120/180 分钟的长作业前缀曲线；只用成功 independent replay 后的计时。
- 每个预算同时报告 declared panel、auditable、omitted 和明确的成功下限口径；不把缺结果直接混为普通失败，也不只隐藏掉它们。
- F1→F2 cohort 同时保留最终成功和右删失者，给描述性 Kaplan–Meier 曲线；同秒事件先于删失处理。
- 首次实际 F2 pass 与首次 verified win 分开，拟合时点和新信号时点并列。

长作业在 t 分钟时的前缀不等于以 t 分钟预算从头启动的作业：任务 timeout、取消、剩余预算会影响搜索。需要真正不同预算的 time scaling 时，分别创建 15/30/60/120 分钟实验块，并各自比较同一 cap 的 arms。Kaplan–Meier 在这里是描述性的；自适应停止可能使删失具有信息性，不给虚假的 iid 置信结论。

## 6. 本地 native 实验命令

先在新的 Windows checkout 中按原 `docs/BUILD.md` 安装固定 native 依赖并执行 `setup_source.py --check-only`。公共 UI/普通 `--minutes` 仍 1–45；只有明确的 `--research-minutes` 支持 1–180，不悄悄改变原公开运行契约。

```powershell
python tools/i085_validate.py --candidate-only --out outputs/i085-python-local
python tools/run_release_source.py --seed 101 --out outputs/i085-dry120 --feature-profile i085 --research-minutes 120 --dry-run

# 最小接线检查；不是效力 benchmark
python tools/benchmark_i085.py plan --out outputs/i085-smoke --seeds 101 --arms i082,i085-core,i085 --minutes 1
python tools/benchmark_i085.py run --out outputs/i085-smoke
python tools/benchmark_i085.py report --out outputs/i085-smoke

# 隔离“只是门槛降低”与“宏观结构变化”的主比较
$panel = Get-Content release/i085/panel.json -Raw | ConvertFrom-Json
$seeds = $panel.seeds -join ','
$solverSeeds = $panel.solver_seeds -join ','
python tools/benchmark_i085.py plan --out outputs/i085-ab120 --seeds $seeds --solver-seeds $solverSeeds --arms i082,i082-low-threshold,i085-core,i085 --minutes 120 --workers 7
python tools/benchmark_i085.py run --out outputs/i085-ab120
python tools/benchmark_i085.py report --out outputs/i085-ab120
```

进一步的 `i085-contextual` 只打开新模型控制；`i085-no-continuation-model` 关闭它的 shadow 开销；`i085-continuation-shadow` 让新宏观通道也只记建议；`i085-window14` 单独考察反馈延迟。实验工具给所有 arms 加相同 gate telemetry，不是只测候选。

所有工作串行运行、各 arm 使用同一资源/身份/seed，公共 launcher 保留 Windows Job 约束和完整 manifest。prepare/run/report 不混同；生成 plan 不执行 native。不要在 AB 过程中更换游戏版本、重新抓统计或改 DLL。

## 7. 复杂度、风险与进一步的大改方向

新档案默认 1024 个菜单、16 MiB 序列化 payload；这不是总 RSS 界。每次选择扫描该有界档案；每个菜单评分前 8+sqrt(launches+1) 个 pending 选项，逐渐展开。96 行模型中心化/Cholesky 最坏 O(n³)，没有假装免费；遥测记录吸收/选择 Python seconds。共享旧 trie、旧 explorer 和旧探针的其他内存仍按原方案管理。

大改中研究过但本轮未部署的方向：

**全 native 决策边界图与跨幕动态规划。** 可以将 rollout 拆为逐战略决策、用完整 native identity 构成状态图，缓存精确转移，做更强的重根与子目标组合。但需要验证截断、replay/checkpoint 恢复、状态字节充分性和启动费用；未经 native 实验就把粗特征当 DAG 会损坏正确性。本候选先在完整前缀树上提供可实际比较的服务和回传层。

**root-LTS / PHS。** 作者论文把优化搜索努力而非路径长度、策略概率和重根子问题结合；本实现受到这些问题定义启发，但没有满足归一化 policy / rerooter 和单步成本等全部条件，不引用其指数加速界作为本项目保证。

**真实 F1 赢法的多后继联合规划。** F1-winning plans 产生 HP、药水、遗物计数、RNG 等不同 F2 状态；直接用后续可达性比较它们，比选最高 HP 更接近目标。既有 F1WinnerReuse 已提供部分候选。本轮没有改变 native 计划保留与预算，因为需要验证后继恢复及真实费用；这些成本会影响 macro 和 tactical 的最佳边界。

**自适应成功 hazard / bandit。** 将回报写成成功事件/秒比回归伤害更贴合首胜目标，但稀疏、非平稳、强相关、延迟反馈意味着普通 UCB 的 iid 保证不可直接套。这里选择可审计的服务账本与单独 shadow，而不是宣称拟合了可靠 hazard。

真正尚待回答的是：在用户的长尾 seed 上，是否减少了真实 F2 转化时间、是否提升同 cap verified win、哪些阶段损失了吞吐。当前只有代码、无 DLL 验证和构造实验，native 答案保持未知。

## 8. 资料及证据边界

源码为上述固定仓库基线和当前 candidate；游戏版本不在本轮自动迁移。Spire Codex API/官方消息的研究没有被用于硬编码新的卡牌强弱。

论文查阅为作者公开摘要及其版本记录，不声称本项目复现全文实验：

1. Orseau & Lelis, *Policy-Guided Heuristic Search with Guarantees*, 2021. https://arxiv.org/abs/2103.11505
2. Orseau, Hutter & Lelis, *Levin Tree Search with Context Models*, 2023, revised 2024. https://arxiv.org/abs/2305.16945
3. Orseau, Hutter & Lelis, *Exponential Speedups by Rerooting Levin Tree Search*, 2024, revised 2025. https://arxiv.org/abs/2412.05196

上述方程中服务规则、目标区间、核和收缩是本 candidate 的设计，不是这些论文已证明适用于 Slay the Spire 2 的结论。
