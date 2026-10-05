# i085 tail：改善长尾的研究、实现与实验

**结论：值得做的是保留已知未来并重新分配搜索努力，而不是盲目缩短重启间隔。新的 tail 实际控制保持实验性；默认 shadow。没有 STS2 native 效果声明。**

## 1. 首先确定要缩短什么尾部

固定游戏 seed 后，不同 solver_seed/搜索安排的首次 verified-win 时间记为 T。另一个分布是在不同游戏 seed 上混合 T。两者必须分开：少数 seed 本身困难，也能让总体曲线有很长的右尾，却不代表对同一 seed 不断独立重启就有效。目前没有足够匹配的 i082/A10 重复试验，不能拟合幂律、宣称已经识别 heavy-tail 指数，或把用户报告的 30 分钟间隔当成完整数据集。

项目真正目标是同预算下找到一条可重放路线。采用三个描述指标：

```
S(t) = P(T > t)
R(B) = E[min(T,B)] = integral_0^B S(t) dt
L(B) = integral_(B/2)^B S(t) dt
```

R 是受限首次成功时间，L 衡量预算后半段仍未解决的面积；成功率/验证失败/漏报率同时报告。p90/p95 只有曲线真的穿过对应概率时才输出，否则为 unknown，而不是把 time cap 填成 p95。没有支持到 B 的删失样本不外推 RMST。

另记录全局首次 F1 pass 到全局首次 F2 pass 的可观察间隔，右删失的作业仍保留。二者可能来自不同谱系，所以这不是同一场 F1 后连续打 F2 的战斗时长。阶段时点来自 coordinator 收到结果，不是未被采集的 rollout 内部时刻。自适应停止可能导致信息性删失，Kaplan–Meier 仅作描述，不能假定样本 iid。

## 2. 为什么没有直接加入强制 Luby 重启

经典 Las Vegas 重启假定独立运行。若每次在 tau 截断、失败后支付 c 的重启成本，单次成功概率 F(tau)，更新方程给出：

```
E_restart(tau) = [ E[min(T,tau)] + c*(1-F(tau)) ] / F(tau)
```

F=0 时不会完成。若有密度 f、hazard h=f/S，固定截断目标的导数符号由以下式子决定：

```
sign(E_restart'(tau)) = sign(S(tau)*F(tau) - f(tau)*(m(tau)+c))
m(tau) = E[min(T,tau)]
```

因此仅知道“尾部很长”不够：还要知道独立重启成功概率、已投入后的条件收益和重启损失。构造分布 10% 在 1 单位完成、90% 在 100 完成，无重启均值 90.1；截断 1 且无开销为 10；失败后开销 20 时反而为 190。固定相同路径、需要 100 的确定性过程，每次在 10 重启永远失败。这些数字是方程的有限分布测试，不是游戏样本。

i082 中换 policy_seed 并不必然改变宏观选择。full-information solver 还拥有已完成前缀和 checkpoint，强制根重启会丢失这些资产。故本轮没有套用 Luby 最优性定理，也没有取消长任务、清空缓存或重置已知前缀来制造“独立尝试”。保留原 root/restart jitter 为独立比较臂，不擅自视为成功率提升。

## 3. 本轮实现：持久前缀上的多尺度服务

### 3.1 真实延续立即可用，不等拟合门槛

`TailFocusScheduler` 继承原 FocusScheduler。它另存一个有界的真实战略菜单档案，以完整规范化动作 trie 的 parent 节点命名。每个结点包含实际菜单和完整 observation guard、合法替代 child、来源与最好已观察成就。成就按原生候选、已过 gate、Act、floor 排序；**不因致命首领伤害从 80% 增至 81% 就重置服务量**。

可以把保留摘要写为：

```
W(p) = max { achieved(c) : c 是实际执行并经过完整前缀 p 的延续 }
```

当前实现的 W 用于保留最佳来源和回退尺度，不是学习的通关概率，也不是 native V*。这不是完整 Bellman 动态规划：不同前缀但等价状态仍不合并，未知后继仍需实际执行，档案有上限。其价值是“这个具体前缀确实产生过这个未来”可以立即进入重根候选，不必进入旧 48 条 focus pool，也不必先凑足 24 行回归。

### 3.2 新候选不能拥有无限次零费用优先权

旧的累计费用最小规则若让新来源从零加入，会被持续到来的新来源打败。固定一个旧候选、每轮新建并在机会后退出一个零费用候选的 60 轮反例：旧规则给旧候选 0 次，本实现给它 60 次。该反例只证明持久旧工作没有被“零费用新生流”冲掉；不证明所有瞬时到达流都得到公平服务。

`ServiceClock` 对每个层级维护非递减虚拟 floor。新/重醒 flow 接入不早于当前活跃 flow 的最小服务标签；相同标签按稳定出生次序而非反复重建的 label 排序：

```
start_i = max(saved_tag_i, virtual_floor)
choose i with smallest (start_i, stable_birth_i)
tag_i += estimated_job_seconds
on completion: correct by actual_seconds - estimated_job_seconds
```

虚拟标签是调度量，不等于已测 native 秒数。校正不能把 floor 倒退。源码分别报告 known cost、unknown estimate 和 pending reservation。冷启动估计 1 秒，之后取最近 64 个正 native wall 秒数的中位数；这些是研究配置，不是测得最优值。

### 3.3 层级分配不是把“所有晚期来源”当同等独立机会

实际控制按以下层级选择：

```
旧 focus/explorer 通道  /  新 tail 通道
                            -> Act
                            -> 回退尺度 bit_length(max(0, end_floor-decision_floor))
                            -> 进入该 Act 前的战略动作 stem cohort
                            -> 确切前缀菜单
                            -> 合法替代选项
```

各层采用最低虚拟服务标签。回退尺度对应 0、1、2–3、4–7、8–15……；早期重构不再只因距离远就排在所有晚期小修改之后。族由进入当前 Act 前的战略动作序列形成，战斗细节别名可共享服务；**族绝不替代实际前缀身份**。Act 1 来源往往共享一个根族，这是保守的并发限制，不是学到的独立性分类器。

每个新 tail 菜单最多一笔在途工作、同族默认最多两笔；旧通道、urgent、synthetic 和资源重派不受这个局部上限的全局保证。菜单每隔一次选择不读 gate 模型标量，以稳定 seed 派生的散列作平局顺序；其他次数保留原模型排序。这样避免同一个回归完全控制新通道，但 **native 后续 rollout policy 仍是原 policy/prior**，不能称为独立策略 ensemble。

当前档案最多 1024 个菜单和 16 MiB 序列化 payload，优先淘汰 exhausted，再在较拥挤 Act/尺度中淘汰已服务多的来源。否则按 FIFO 淘汰又会把所有早期根冲掉。对象开销、原 trie、explorer、probe/archive 不在这个 MiB 界内；没有给整个进程新增硬 RSS 证明。

### 3.4 在途和重派都必须入账

任务提交前预约费用，结果回来后结算，不能让窗口内 56 笔工作都以“前面还没完成，所以免费”争抢一个来源。缓存命中新增 native 费用为零；缺测保留估计并增加 unknown，绝不填零。账本汇总的是 worker native wall work，不是并行作业 elapsed 或 CPU seconds。

资源失败的 requeue 是另一笔真实尝试。新票据复制 group，不修改已记录原票据；重复结果不能双结算，合法重派却不能漏结算。Evaluator 没接受的提案退预约；重派拒绝不会把之前已提交身份误删。以上均有直接测试及主循环 fake-host 测试。被取消且未吸收的尾部工作仍列 pending/unknown，不能把已结算总和当作全作业完整花费。

### 3.5 F1 后减少承诺到旧模型上的排队工作

只有 native campaign metadata 确认最后一幕有双首领、且已知通过该幕 F1 后，tail-on 将 ordinary ordered dispatch window 限至 workers × 2（7 worker 时 14）。不取消既有请求、不改变结果提交序吸收，旧队列自然排空；未知 metadata 不猜测幕号。

这同时是收益与风险：更快利用新 F2 信息，可能牺牲吞吐。`i085-tail-wide` 设 rounds=8 隔离窗口作用，`i085-tail-no-cap` 放宽族并发。这个改动不是将 native 战斗节点预算缩短，也没有证明通常窗口 14 更好。

## 4. 能证明什么、不能证明什么

固定有限且持续活跃的流、正而有界服务费用、有限误差和非零服务机会下，最低累计服务不会让一个保留流永久零服务：其他流反复取走服务，其标签会上升。虚拟接入 floor 修复了新来源反复零费用接入的一个反例。

这不是整个 solver 的无条件公平性/完备性：流不断增长、菜单被容量淘汰、lineage 暂停、未知任务耗时、共享 trie、辅助队列和并行取消均影响结果。两通道共享结果，不是两个完整 solver 各占一半资源的独立模拟，因此没有“至少 baseline 的 1/2 效率”或常数倍首胜时间保证。

论文中的 root-LTS 有归一化策略、rerooter 和搜索步成本条件。本实现借用“返回已见子问题并分配努力”的思路，不实现该论文全部条件，也不引用其指数加速定理作为 STS2 性能保证。

## 5. F2 拟合门槛：新增的源码结论

真实 gate 的普通 `add` 路径首次拟合也同时要求 `entries >= minimum` 和 `dirty >= refresh`。joint 的首次拟合只检查 minimum，refresh 限制已有拟合后的更新。真实 gate 显式 `refit()` 另有强制路径，主循环的最终保存会使用它，但这不能使已经结束的搜索更早受益。

直接实例测试采用无生命分母重标定、每次新增一行的夹具：

| 配置 | 8 行时真实 gate | 8 行时 joint |
|---|---|---|
| minimum=8, refresh=16 | 未拟合；16 行后拟合 | 已拟合 |
| minimum=8, refresh=4 | 已拟合 | 已拟合 |

实际 dirty 可因重标定变化，不能普遍把 refresh=16 读成“恰好还等 16 个入场”。默认仍为 24/16；`i082-low-threshold` 同时改成 8/4，而不是只改 minimum 后误报消融。

此外，F2 readiness 的 5/10 合法样本完整性、every=20 的采样节奏、联合行等待完整表、共享样本和借用 F1 标签、dispatch 延迟是不同问题。低门槛不会修复模型目标错位，更不会创造独立信息。新增 first-fit 时点与 F1/F2 首达时点并列，才能回答长尾 seed 到底“尚未拟合”还是“早就拟合但方向不对”。

本轮没有恢复上一轮丢失的二次核实现，也没有把它默认化。历史摘要仅存档，当前不存在 `continuation-model-control` 命令。未经 native 数据支持，不再加另一套回归掩盖分配问题。

## 6. 实验中否决了什么

曾实现连续下游 macro 修改的 Luby 尺寸 lease，试图让早期改动紧接着得到配套探索。160 个原构造实验没有稳定改善目标互补情形，还拖慢其他情形，因此移出运行路径。确切五个原型文件、当时 diff 和原始结果保留在 `evidence/tail/rejected/`。它们与最终源码不是一个版本，复现说明明确区分算法复现与完整历史 checkout 字节复现。

最终重新运行两个各 120 次的面板：5 类构造世界 × 4 个完整目标组合 × 3 个核心调度器 × 2 种窗口。一个固定 120 evaluations；另一个固定 **30 模拟时间单位**，evaluation guard=400，1 或 7 个模拟 worker。所有返回都是明确标记的假数据，toy goal 独立于 native status；没有游戏 DLL、native 胜利或 replay 证书。

以下为窗口 14、7 个模拟 worker、共同时间 cap=30 的受限时间均值；括号是 toy goal 成功数/4。失败按同 cap 计，越低越好：

| 世界 | i082 focus core | 原 i085 macro core | 新 tail on |
|---|---:|---:|---:|
| 早期两项互补 | 19.13 (4/4) | 27.15 (1/4) | 17.20 (4/4) |
| 晚期单项 | 3.60 (4/4) | 3.60 (4/4) | 4.20 (4/4) |
| 晚期两项互补 | 17.33 (3/4) | 7.78 (4/4) | 9.53 (4/4) |
| 昂贵晚期诱饵 | 28.70 (1/4) | 30.00 (0/4) | 29.08 (1/4) |
| 预算内未解控制 | 30.00 (0/4) | 30.00 (0/4) | 30.00 (0/4) |

精确未舍入数和窗口 1 的全部结果见原始 JSON，不只展示有利的早期案例。eval cap 面板的尾部排序也与 time cap 不同，不能以 evaluations 代替实际时间。模拟 Python 用时另记录；模型化 task cost 不是测得的 native 工作量。

**这些结果不支持默认开启 tail 控制。** 它修复了机制问题且在一种组合世界有效，但会在晚期简单世界付出成本；原宏观候选本身也不单调优于 i082。最终 i085 默认 shadow，明确的 `i085-tail` 臂才控制。shadow 只比较当前状态下的结构建议，不模拟整场反事实，且仍有 Python/内存开销；用 i085-core 测该开销。

## 7. 本地 Windows 机械验证

在新 checkout/解压目录中使用合法本机游戏、固定 Workshop/native 依赖；先按原 BUILD 配置。不要覆盖运行中的目录，不在同一 AB 块中更换 DLL/统计/硬件资源。

```powershell
python tools/setup_source.py --game-dir "D:\你的游戏目录" --ritsu-dir "D:\你的Workshop\3747602295"
python tools/setup_source.py --check-only
python tools/i085_validate.py --candidate-only --out outputs/i085-tail-python
python tools/run_release_source.py --seed 101 --out outputs/i085-tail-dry --feature-profile i085 --research-minutes 120 --dry-run --extra --tail-mode on

# 只检查接线。plan/report 不执行 native；run 才执行。
python tools/benchmark_i085.py plan --out outputs/i085-tail-smoke --seeds 101 --solver-seeds 271828 --arms i082,i085-core,i085,i085-tail --minutes 1 --workers 7
python tools/benchmark_i085.py run --out outputs/i085-tail-smoke
python tools/benchmark_i085.py report --out outputs/i085-tail-smoke
```

公共 UI 和普通 launcher `--minutes` 仍 1–45。明确 `--research-minutes` 才支持 1–180；benchmark 自己的 minutes 会走该研究入口。所有 arm 统一 gate timing、资源约束、source/native identities，不能靠放宽内存或 worker 数给候选额外算力。

主比较：

```powershell
$panel = Get-Content release/i085/tail-panel.json -Raw | ConvertFrom-Json
$seeds = $panel.seeds -join ','
$solverSeeds = $panel.solver_seeds -join ','
python tools/benchmark_i085.py plan --out outputs/i085-tail120 --seeds $seeds --solver-seeds $solverSeeds --arms i082,i082-low-threshold,i085-core,i085,i085-tail --minutes 120 --workers 7
python tools/benchmark_i085.py run --out outputs/i085-tail120
python tools/benchmark_i085.py report --out outputs/i085-tail120
```

这是公开开发面板，不是 holdout，也不是声明它未在用户私有工作中出现。跨游戏 seed 的混合与同一游戏 seed 的重复分别报告，solver_seed 在 arms 间严格配对。先检查作业数/总预算，按批次运行；不要多个 AB block 同时竞争机器。使用新输出目录，source 改动后重新 plan；包内 Linux dry-plan 只作证据，不直接在 Windows run。

独立 time scaling 应分别创建 30/60/120 分钟的新块。一个 120 分钟作业的 30 分钟前缀不等于重新启动的 30 分钟作业，因为 deadline、task timeout 与取消会改变路径。窗口/并发消融分别用 `i085-tail-wide` 和 `i085-tail-no-cap`，不要一次全改后混称单因果结果。

报告审计已保存的原 replay 证据，不在 report 阶段新执行 native。单有 VERIFIED 字样或证书文件不够；身份/完整动作/expected evidence/旧 replay contract 要一起核对。缺失、崩溃、资源失败、过预算验证和早停单列 omitted，不偷偷作普通败场，也不能只隐藏它们而宣布高胜率。

## 8. 晋级条件与仍开放的问题

实际晋级需要同 cap 的 verified-win 受限时间、晚半段 survivor area 或真实 F1→F2 转化改善，并且验证失败/遗漏/峰值内存/总 native work 没有不合理恶化。尾部分位数未观察到足够成功时保持 unknown。只有少数案例成功不能拟合稳定 hazard；固定回归 minimum 也不是独立样本量。

进一步的大改优先项仍是经过 native 验证的“战略边界精确转移缓存”和真实 F1 多赢家后继比较。前者要证明状态字节充分、前缀/检查点恢复正确，后者要测每个 HP/药水/遗物计数/RNG 后继的真实 F2 成本。没有这些证据，不把粗牌组当 transposition，也不删掉原 host 的来源核对。

剩余 compute 问题包括：core 控制没有覆盖所有 probes/urgent 工作；phase 内层模型仍共享；新并发 cap 可能削弱短时吞吐；有序窗口减少可能令 worker 等待；菜单档案以外 trie 仍持续增长。本轮没有宣称已经解决所有这些问题。

## 9. 主要资料与实际阅读范围

以下使用作者/出版社摘要与版本记录，未声称复现全文定理或实验：

1. Luby, Sinclair, Zuckerman (1993), *Optimal speedup of Las Vegas algorithms*, Information Processing Letters 47(4), 173–180, DOI 10.1016/0020-0190(93)90029-9。独立运行是重启问题的重要前提。
2. Scaman (2023), *Black-box Acceleration of Las Vegas Algorithms and Algorithmic Reverse Jensen's Inequalities*, https://arxiv.org/abs/2304.11017 。黑盒重启分布理论不能直接当作本项目保障。
3. Orseau, Hutter, Lelis (2024/2025), *Exponential Speedups by Rerooting Levin Tree Search*, https://arxiv.org/abs/2412.05196 。软子问题/重根努力分配的研究参照。
4. Tuero, Buro, Orseau, Lelis (2026), *Structure-Induced Information for Rerooting Levin Tree Search*, https://arxiv.org/abs/2605.30664 。已查阅 2026 年作者公开摘要；提出结构/启发式/hybrid rerooter。这里没有训练该论文模型，也没有宣称该论文涵盖 STS2。

本轮以固定仓库游戏版本为研究上下文，没有根据二手补丁或攻略迁移卡牌数据。所有新增核心机制无牌名强弱表；结构一般性不等于完成跨角色 native 接线，原研究 consumer 约束仍保留。
