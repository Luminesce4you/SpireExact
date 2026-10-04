# i082 架构说明与冻结源码复核

复核日期：2026-10-04。依据是上游 `I082_ARCHITECTURE.md` 的 16 节原稿，以及只读的 `frozen-i082` 源码。冻结源码版本：`e1b10f6f10f1c1d27f5f176f8b4e73ce8aa07fc2a9ab94b9eaf2d9cd10bd7c74`。

以下列出原稿中需要修正或补充条件的具体位置。原稿行号指上游文件，不指经过排版、脱敏和修订的公开副本。[公开架构说明](I082_ARCHITECTURE.md) 和 README 概览采用源码对应的行为。

## 描述与代码的差异

| 原稿位置 | 原稿描述 | 冻结代码对应的行为 | 代码依据 |
|---|---|---|---|
| 第 1、2、5、14 节，行 36、40、61、129、217、235、493–526 | 将 7 workers、1,000,000 评估、3570 秒搜索、3600 秒 Job、99 层、12,000 动作及 1024/128 MiB 档案写得像 `--feature-profile i082` 的全部默认值。 | `i082` 修改的是功能表和部分 planner 参数。直接调用 planner 时，默认仍为 24 评估、600 秒搜索、90 秒任务、2000 动作、80 动作/2 层 lookahead、`dispatch=stream`、`solver_seed=0`、512/64 MiB 档案；workers 默认自动探测。原稿完整参数是启动器叠加的 **60 分钟配置**。公开入口明确叠加 30 分钟、7 workers、有序派发等运行设置，并写入 manifest。 | [final_defaults.py](../spire_exact/planning/final_defaults.py#L33)，[planner 参数](../spire_exact/planning/__main__.py#L25)，[run_seed.py](../tools/run_seed.py#L218)，[资源探测](../spire_exact/planning/resources.py#L102) |
| 第 10 节，行 373 | 第一、二幕首领的 `auto` 等于“取最好”。 | `auto` 仅在生命会直接带入第二首领的最终幕 F1 解析为 `best`；其他场合解析为 `first_win`，遇获胜成员就停止。第一、二幕首领不满足代码对最终幕连续双首领的判定条件。 | [BeamAdvisor.cs](../native/SpireNativeHost/BeamAdvisor.cs#L438)，[选择解析](../native/SpireNativeHost/BeamAdvisor.cs#L454)，[停止条件](../native/SpireNativeHost/BeamAdvisor.cs#L545) |
| 第 10 节，行 374 | 最终幕两个首领在 `open` 下都“全跑” E45/E68/E135/E270。 | `open` 去掉首成员失败后的升级门槛。最终幕 F1 采用 `best`，通常执行完整成员列表；最终幕 F2 采用 `first_win`，可以在较早成员获胜时停止。F2 全部失败时才尝试完四个成员。 | [gates.py](../spire_exact/planning/gates.py#L65)，[BeamAdvisor.cs](../native/SpireNativeHost/BeamAdvisor.cs#L454)，[成员循环](../native/SpireNativeHost/BeamAdvisor.cs#L545) |
| 第 8 节，行 309、328 | 同幕同死亡层、凡有 F1 入场的轨迹，都参与 F2 就绪度的联合聚焦重排。 | 所有实际 F1 入场可登记信号，但精英 `source.f1_entry` 只在 **实际死于 F1** 时设置。联合聚焦只重排这些 F1 失败精英；通过 F1 后死于 F2 的精英不因为这项规则换位。 | [focus.py 入场登记](../spire_exact/planning/focus.py#L172)，[精英字段](../spire_exact/planning/focus.py#L237)，[联合排序](../spire_exact/planning/focus.py#L431) |
| 第 8 节，行 315 | 偏离代价为“回退层数 + 2 × 已试次数 − 2 × 关口模型值”。 | 完整代价还包含选项 penalty：`use_potion` 为 6，其他为 0；模型值先截到 `[-4, 4]`。候选的字典序还以较晚决策索引、原选项位置等字段处理平局。 | [focus.py 药水 penalty](../spire_exact/planning/focus.py#L223)，[候选代价](../spire_exact/planning/focus.py#L486) |
| 第 12 节，行 465 | 第三幕火堆有 SMITH 备选就提议锻造。 | 还要求入场生命达到 `low_hp_percent`，i082 默认是 **至少 50%**，且 SMITH 不是源轨迹已选择的动作。火堆分组按坐标、幕/层、牌组/升级、遗物、药水和生命等字段做启发式提案去重，不声称完整游戏状态相等；缺少分组键时仍可按完整请求生成提案。 | [preparation.py 分组键](../spire_exact/planning/preparation.py#L55)，[锻造触发](../spire_exact/planning/preparation.py#L363) |
| 第 5 节，行 218、236 | 派发窗口和 `allocation_serial` “只数普通评估”。 | 代码仅排除 `f2_readiness_probe`。选牌对照表的 `paired_card_probe` 等其他合成工作仍计入 allocation serial 和派发窗口，因此“只有非合成工作计入预算”会误导读者。 | [search.py](../spire_exact/planning/search.py#L429)，[派发预算](../spire_exact/planning/search.py#L459) |
| 第 2、5、16 节，行 125、234、568 | 同代码/种子/solver_seed/计数预算必走同一条路径，所有触发条件都不用秒数。 | 有序模式保证 **非就绪度任务** 按序交回；F2 就绪度采样允许完成即收，之后会更新联合模型/聚焦信号。内存准入重派还有 10 秒等待，worker 与搜索都有时间保险，资源丢失可触发减半节点重派。这些机制依赖实际完成、时间与资源状态，不能从源码推出无条件逐路径相同。相同 solver_seed、确定性取样和有序普通吸收是可复现路径的基础；比较时也要保持资源及信号吸收条件，并查看账本。 | [search.py 收集](../spire_exact/planning/search.py#L507)，[信号更新](../spire_exact/planning/search.py#L839)，[内存重派](../spire_exact/planning/search.py#L825)，[派发时间](../spire_exact/planning/search.py#L1070)，[pool.py](../spire_exact/planning/pool.py#L184) |
| 第 11.1 节，行 437 | 每 20 个原生完成“攒一次额度”，可读成累计额度。 | 信用是布尔 token，上限一个；每逢 20 的倍数设置 `credit=True`，不会积攒多组采样形成突发派发。缓存命中与合成工作不增加这个原生完成计数。 | [f2_readiness.py](../spire_exact/planning/f2_readiness.py#L358) |

## 部署边界与当前前端

原稿第 2、4、14、15 节还混入了作者工作机的部署说明，这些应与冻结搜索核心分开理解：

- **启动入口须显式固定功能配置。** 冻结 `tools/run_seed.py` 的 `--feature-profile` 默认为空；其旧 `--profile focus` 参数会先显式传入部分旧设置。只有显式指定 `--feature-profile i082` 时，`feature_argv()` 才把完整 i082 表放在它们之后覆盖。公开 CLI 和前端入口均明确渲染 i082 表，见 [run_seed.py](../tools/run_seed.py#L91) 和 [公开启动器](../tools/run_release_source.py)。
- **SpireBoard 支持操作求解。** 原稿行 132、203 的“只读网页”只适合描述结果面板。最新前端已有新建、暂停、继续、退出接口，见 `dashboard/server.py` 的 `/api/jobs` 与控制路由，以及 `dashboard/jobs.py` 的 `launch`/`control`。冻结 `frozen-i082` 中没有 `dashboard/`；公开包使用最新前端，并在准备后绑定公开源码和宿主身份。
- **当前入口为 i082。** 已部署入口的配置指向 i082；公开源码入口也明确传入 `--feature-profile i082`。原机可选 1–180 分钟属于其已授权部署配置；公开包入口可选 1–45 分钟，默认 30 分钟，项目作者建议大部分种子选择 45 分钟，参见 [构建与启动](BUILD.md) 和 [前端说明](../dashboard/README.md)。
- **目录与 CPU 号是部署选择。** 原稿的制品磁盘、目录联接、端口及固定 CPU 编号不是 frozen 核心规定。公开文档采用用户提供的输出根和自动核对的同类 CPU。原机最新前端索引也同时包含普通 iteration manifest 和 frozen 工作区内的 iteration manifest，不只一种路径。
- **Job 计时也支持暂停排除。** `tools/limited_cli.py` 的实际超时条件使用 `active_counter()`，有暂停账本时排除暂停时间；它同时记录原始墙钟和活跃时间。`wall_limit_seconds` 是保留的字段名，不能把带暂停作业的实际停止时刻等同于原始墙钟到 1800 或 3600 秒，见 [limited_cli.py](../tools/limited_cli.py#L85)。

## 已核对的范围

16 节均按其涉及的模块阅读并追踪关键分支：入口默认表、资源探测、主循环/派发/收集/吸收、聚焦及谱系回退、真实关口和联合模型、档位合并、关口成员选择与重试、F2 两段状态机、配对选牌表、F1 赢法复用、路线/锻造提案、前缀恢复、结果存档与独立胜利重放。原稿描述的三层职责、原生执行、合成结果隔离、完整字节身份以及胜利证书主链对应现有代码。

本次为源码与文档复核；没有启动整局、策略或探针实验，没有修改冻结工作区。构建、前端初始化与发布包的工程校验由各自的公开验证记录给出。
