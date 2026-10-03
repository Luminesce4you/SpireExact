# A10 原生规则审计

日期：2026-09-30。协议：A10-seed-v2。范围：用户实际安装的 v0.111.0 DLL 与本项目离线副本，二者 SHA-256 均为 `0861bfa1df347538d932f22d580e75420f08082792eb914e53b4882764acdbe9`。

以下结论来自该 DLL 的反编译，源码保存在 `experiments/iteration-021/game-source/`。这不是正常 Godot 场景等价证明，也不替代 M0 要求的 100 场 A10 战斗动态审计。

## 等级与具体修正

`MegaCrit.Sts2.Core.Entities.Ascension/AscensionManager.cs` 的 `HasLevel` 使用 `_level >= (int)level`，所以 A10 累积启用 1–10 级；该版本的 `maxAscensionAllowed` 是 10，不能沿用旧任务的 A20 假设。

| 级别 | 原生枚举 | 已核实行为及来源 |
|---|---|---|
| 1 | SwarmingElites | `MapPointTypeCounts.NumOfElites` 从 `round(5 × 1)` 变为 `round(5 × 1.6)`，即地图生成的目标数量 5 → 8；不能直接当作一条路线必经的精英数。 |
| 2 | WearyTraveler | `AncientEventModel.BeforeEventStarted` 按缺失生命的 80% 治疗，而非全部补满。Neow 分支先把当前生命设为 0，因此该修正也影响开局恢复。最终取整和效果由原生 `CreatureCmd.Heal` 执行。 |
| 3 | Poverty | `AscensionHelper.PovertyAscensionGoldMultiplier = 0.75`。`EncounterModel` 默认战斗金币区间由普通 10–20、精英 35–45、首领 100，变为整型转换后的 7–15、26–33、75。`OneOffSynchronizer.DoTreasureRoomRewards` 的原生 42–52 金币抽样也乘 0.75 再转整型。遭遇覆盖和遗物额外效果仍以对应原生实现为准。 |
| 4 | TightBelt | `AscensionManager.ApplyEffectsTo` 调用 `SubtractFromMaxPotionCount(1)`，减少一个药水槽。 |
| 5 | AscendersBane | 同方法创建并加入一张原生 `AscendersBane`，记录 `FloorAddedToDeck = 1`。 |
| 6 | Inflation | `MerchantCardRemovalEntry` 首次移除基础价 75 → 100，每次递增 25 → 50。不能把这个数值泛化为所有商店商品统一涨价。 |
| 7 | Scarcity | 卡牌稀有度基础权重和增长见下表；`CardFactory.UpgradedCardOddScaling` 从 0.25 变为 0.125。 |
| 8 | ToughEnemies | 怪物各自定义生命修正，不是统一倍率。例如 `Aeonglass.MinInitialHp` 为 512 → 535；`Axebot` 基础生命区间 70–78 → 76–86，随后仍加其原生复活生命加成。 |
| 9 | DeadlyEnemies | 怪物各自定义攻击及其他行为修正。例如 `Aeonglass.EbbDamage` 为 22 → 26，`IncreasingIntensityBaseStrength` 为 3 → 4；`Axebot.BootUpBlock` 为 10 → 15。不能只统一乘伤害倍率。 |
| 10 | DoubleBoss | 最后一幕生成第二个不同的首领。选择发生于新局房间生成，使用游戏 `Rng.UpFront`。 |

Scarcity 的原始参数来自 `MegaCrit.Sts2.Core.Odds/CardRarityOdds.cs`；它们不是忽略历史后的最终独立概率：

| 参数 | A0 | A10 |
|---|---:|---:|
| 普通常见基础权重 | 0.600 | 0.615 |
| 普通稀有基础权重 | 0.030 | 0.0149 |
| 精英常见基础权重 | 0.500 | 0.549 |
| 精英稀有基础权重 | 0.100 | 0.050 |
| 商店常见基础权重 | 0.540 | 0.585 |
| 商店稀有基础权重 | 0.090 | 0.045 |
| 非稀有后增长量 | 0.010 | 0.005 |

原生稀有度历史偏移初始为 -0.05，抽到稀有后重置，非稀有后递增且最多 0.4；首领奖励走单独的原生规则。分析与缓存必须保留这些状态。

## 首领选择与生命衔接

- `MegaCrit.Sts2.Core.Runs/RunManager.cs::GenerateRooms` 在新局调用每幕 `GenerateRooms`。最后一幕的第二首领从 `AllBossEncounters` 中排除第一个首领后，用 `State.Rng.UpFront.NextItem` 选择。
- `MegaCrit.Sts2.Core.Map/StandardActMap.cs` 建立 `BossMapPoint → SecondBossMapPoint` 的地图边。因此最后一幕第一首领后仍是本幕下一房间，不应直接结束整局。
- `MegaCrit.Sts2.Core.Rooms/RoomSet.cs` 根据已访问首领次数返回第二个首领；原生存档保留 `SecondBossId`。
- 常规的 80% 缺血恢复属于 AncientEvent 入场逻辑。两场最终首领之间没有幕转换触发的这次通用恢复；正常战后遗物等效果仍应执行。双首领衔接还需要原生动态轨迹检查，不能仅凭此静态审计认证通关。

## 离线与上游范围

固定上游提交为 `4d2c55069f2cf9f8c621631594857b412cf9660f`。已读 `vendor/CombatSolver/tools/OfflineSearchHarness/ModRuntime.cs`：`ApplyFixedBudgetSettings` 将 `OnlineStatisticsEnabled` 设为 false，随后建立 unattended 离线会话。`RunStatistics` 的上传条件同时要求在线统计启用且不是 unattended；`CombatShowcaseCollector` 也检查在线统计开关。尚需在 M0 原生运行中记录这些开关的实际值，静态关闭证据不冒充网络抓包证明。

## TestSubject 多形态 HP（2026-10-01 补核）

对本文件固定指纹的实际 sts2.dll 单类型反编译，保存于
`experiments/iteration-036/TestSubject.native.cs`。`FirstFormHp`、`SecondFormHp`、
`ThirdFormHp`（81–85 行）在 ToughEnemies 生效时分别为 111、212、313；
未生效时为 100、200、300。`RespawnMove`（260 行附近）按复活次数进入第二、
第三形态，`Revive`（332 行附近）再调用原生多人 HP 缩放。不能把第二形态的
剩余 HP 与第一形态初始 HP 相减，作为完整战斗进展。

实际 A10 单人轨迹 `eval-2851-macro_deck` 观察到 111 -> 0 -> 212 -> 118。
它已击倒第一形态、削减第二形态 94 HP，而不是未造成伤害。与原生形态基数
合计 636 比较，已观察到的 205 HP 削减约为 32.23%；没有整局胜利。
通用统计代码采用观测到的生命阶段，不写死这组怪物数值；其 205 / 323 只覆盖
已经观察到的两个形态，必须与完整三形态比例区分。治疗、消失、未观测形态
保留不确定性，任何比例都不能代替原生胜利和独立完整重放。

## 待完成

- 两个 DEV 种子的确定性重复执行。
- 100 场 A10 战斗的续接匹配、不支持机制与双首领衔接统计。
- 独立新进程完整重放、原生 `OnEnded(true)`、无 advisor / 缓存 / 检查点的胜利检查。
