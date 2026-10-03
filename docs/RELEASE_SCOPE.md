# holdout-01 发布范围与验证记录

本次源码发布以 `frozen-i054a` 为版本锚点，对应 iteration-055 的 `holdout-01` 第一阶段。Python 路线规划核心与 C# 宿主来自该冻结源码，构建、启动、完整动作重放、依赖定位及导航入口为本次交付新增。17/20 的研究结果归属于该冻结基线；后续策略与主树 infra 改动按各自版本记录。

## 研究设置与历史结果

- IRONCLAD / Ascension 10 / all unlocks / mode1 全信息 / fresh start，目标为整局胜利 0/1。
- 配置为 focus + `--root-policies pick,elo --focus-cluster-cap 2 --final-gate-plan open`，有序派发，`solver_seed=271828`。
- 20 个新种子各运行一次，30 分钟安全上限；17 个获胜候选经过独立新进程重放确认，实际 worker 为 5–7 个。
- 研究设计为固定预算、单配置的种子面板。结果汇总记录这 20 次运行；同种子成功概率和配置比较属于独立的研究设计。其余 3 个种子保留为该预算下的 `UNKNOWN`。
- [results.md](../release/holdout-01/results.md) 与 [summary.json](../release/holdout-01/summary.json) 保存结果、版本与依赖指纹、17 条完整获胜动作及历史证书。完整作业、逐步状态、worker 日志和冻结二进制继续保存在本地研究档案中。

## 公开源码的构建适配

发布宿主保留原有完整 DLL 白名单和 block 补偿修正，并加入跨目录构建的固定契约检查。`CorePowerSupport` 整类及嵌套类型的规范化 CIL 需与已审阅基线一致，宿主直接校验实际加载的 DLL；契约摘要固定在源码中。游戏与 RitsuLib 依赖使用固定 SHA 校验。

这项适配服务于不同目录中的固定源码重编译。历史搜索结果对应原始冻结二进制，本次源码构建对应新的二进制指纹，两组身份分别记录；规划策略沿用 holdout-01 基线。

## 本次交付验证

已在 Windows / Python 3.12.14 / .NET SDK 9.0.318 下完成宿主、CombatSolver、harness 和静态契约检查器的四项目源码构建，均为 0 warning / 0 error，静态身份检查通过。

`CorePowerSupport` 整类规范化 CIL 与已审阅基线一致；CIL 指令、上游补丁源码及依赖字节的变更检查均按保护规则拒绝。详情见 [BUILD_VALIDATION.json](../release/BUILD_VALIDATION.json) 与 [SOLVER_CONTRACT_AUDIT.json](SOLVER_CONTRACT_AUDIT.json)。

本次交付验证覆盖依赖定位、编译与静态身份校验。宿主 `Configure`、原生游戏请求和当前构建的胜利路线重放仍待运行验证；本次新增整局搜索、战斗探针与种子面板实验数为 0。历史原生执行与胜利证据由 holdout-01 记录提供。

| 层次 | 当前范围与状态 |
| --- | --- |
| 发行内容 | 源码、测试、构建工具、许可证及精选 holdout 证据 |
| 构建环境 | Windows x64，普通 Python/.NET/Git，用户安装的游戏与 Workshop 依赖 |
| 身份保护 | game/solver/harness 字节校验与固定 CIL 契约；源码构建及静态校验已完成 |
| 历史执行 | 原始身份下的离线 TestMode 执行与独立新进程重放，见 holdout-01 证书 |
| 当前构建重放 | 提供双新进程动作重放入口，通过后生成本机构建身份下的新证书；运行验证待完成 |
| 正常游戏等价 | 正常 Godot 游戏等价待验证 |
| 其他平台与版本 | Linux/macOS、后续游戏与 Workshop 版本待验证 |
| SpireBoard | 附看板源码；主要运行入口为 CLI，看板启动任务的端到端验收待完成 |

`UNKNOWN [0,1]` 表示该预算下结果未知。`VERIFIED_WIN_IN_NATIVE_HOST [1,1]` 表示离线规则模型中的整局胜利由独立重放确认。该布尔目标与最优分数、最少战损、无未来信息游玩和正常游戏等价分别定义、分别验证。

## 公开目录与本地档案

公开目录保留项目源码、回归测试、构建补丁、固定上游信息、MIT 许可证和第三方署名。setup、单种子启动和完整动作重放是本次交付的运行入口；其他研究工具随源码提供，按工具所需数据与环境使用。

依赖、编译产物、缓存和运行输出由本机 setup 与作业生成。用户提供自己的游戏与 Workshop 安装；游戏程序集与素材、反编译游戏源码、SDK、作者缓存、私人聊天及完整运行目录保留在各自本地环境。`vendor/`、`runtime/`、`.tools/` 和 `outputs/` 纳入 Git 忽略规则。

三个依赖作者私有资料的历史工具 `tools/diagnose_solver_pcore.py`、`tools/profile_solver_panel.py` 和 `tools/publish_run_report.py` 继续本地保留。公开导出范围见 [CLEANUP_REPORT.json](../release/CLEANUP_REPORT.json)。

完整冻结源码与原始运行档案继续保存。公开身份清单引用各次实际构建与执行记录，版本更新随新的构建和重放证据生成。
