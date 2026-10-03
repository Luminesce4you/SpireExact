# holdout-01 发布范围

本包的版本锚点是 `frozen-i054a`，对应 iteration-055 的 `holdout-01` 第一阶段。Python 路线规划核心与 C# 宿主来自该冻结源码。公开构建/启动/动作重放入口、依赖定位、README 和导航为此次源码交付新增，不改变研究策略。C# 身份保护增加可移植构建契约检查，具体差异见下文。后续 iteration-057 及之后的新策略或主树 infra 改动不继承本包的 17/20 结论。

公开构建保留原有完整 DLL 白名单和原来的 block 补偿修正；跨目录生成的新 DLL 只有在 `CorePowerSupport` 整类及嵌套类型的规范化 CIL 与已审阅基线一致时才可通过身份前置检查。宿主直接校验加载的 DLL，固定契约摘要不能用本地 JSON 改写。游戏与 RitsuLib 依赖按固定 SHA 检查。该发布适配支持从固定源码在不同目录重编译，不是新的搜索优化，也不表示新二进制复现了 17/20。

## 历史研究证据

- IRONCLAD，Ascension 10，all unlocks，mode1 全信息，fresh start；目标是整局胜利 0/1。
- 显式配置为 focus + `--root-policies pick,elo --focus-cluster-cap 2 --final-gate-plan open`，有序派发，`solver_seed=271828`。
- 20 个新种子各跑一次，30 分钟安全上限；17 个获胜候选经独立新进程重放确认。实际 worker 为 5–7 个。
- 这是一个固定预算面板的汇总，没有对照臂；单次运行不能估计单种子可靠性。3 个没解出的种子不是无解证明。
- 历史结果、版本/依赖指纹、17 条完整获胜动作和证书见 [results.md](../release/holdout-01/results.md) 与 [summary.json](../release/holdout-01/summary.json)。原始完整作业、每步状态、worker 日志及冻结二进制未纳入源码发行。

## 新源码包的验证

本次准备仅验证依赖定位、构建和源码交付，不运行新的整局、战斗探针、种子面板或策略统计实验。构建报告区分依赖检查、编译成功与运行身份准备；它不能替代当前构建的胜利重放证据。

已在 Windows、Python 3.12.14、.NET SDK 9.0.318 下完成四项目构建和静态身份检查，构建日志均为 0 warning / 0 error。`CorePowerSupport` 整类规范化 CIL 与已审阅基线一致，改动 CIL 指令、上游补丁源码或依赖字节均被拒绝。公开记录见 [BUILD_VALIDATION.json](../release/BUILD_VALIDATION.json) 与 [SOLVER_CONTRACT_AUDIT.json](SOLVER_CONTRACT_AUDIT.json)。发布宿主的完整二进制指纹因源码构建适配而改变，不是历史 85% 运行中的宿主；该检查没有运行宿主 `Configure`、原生游戏请求或新的路线重放。

| 层次 | 说明 |
| --- | --- |
| 包内容 | 公开源码、测试、构建工具、许可证与历史 holdout 证据 |
| 构建 | 使用普通 Python/.NET/Git 与用户安装的游戏/Workshop 依赖；具体结果由本包构建报告记录 |
| 运行身份 | 必须通过 game/solver/harness 二进制保护；编译成功本身不等于通过 |
| 历史重放 | holdout-01 的原生离线 TestMode 证书属于原始身份；重编译后的本机身份需重新重放 |
| 正常游戏 | 正常 Godot 游戏等价尚未验证 |
| 平台与兼容 | 本次源码准备以 Windows x64 为范围；Linux/macOS、未来游戏或 Workshop 更新没有获得运行认证 |
| 看板 | 附 SpireBoard 源码；本发布版看板启动新任务尚未独立端到端验收，CLI 是主要入口 |

`UNKNOWN [0,1]` 表示本预算下未知。`VERIFIED_WIN_IN_NATIVE_HOST [1,1]` 只说明当前离线规则模型中的布尔目标已由独立重放关闭；不表示最优分数、最少战损、无未来信息游玩或正常游戏等价。

## 公开内容与本地内容

保留项目源码、回归测试、构建补丁、固定上游信息、MIT 许可证和第三方署名。包内研究工具供阅读与后续研究；依赖未附历史实验数据的工具不能被当作开箱运行入口。

公开发行从本地构建目录另行导出，剔除所有依赖、编译产物、缓存与运行输出。三个只适用于作者私有资料的历史工具 `tools/diagnose_solver_pcore.py`、`tools/profile_solver_panel.py`、`tools/publish_run_report.py` 也未打包；标准 setup、单种子启动与动作重放入口均保留。[CLEANUP_REPORT.json](../release/CLEANUP_REPORT.json)记录精简范围，原件继续在本地保存。

发行不含游戏/Workshop 程序集与素材、反编译游戏源码、SDK、作者机器缓存、私人聊天、完整运行目录或预编译专有依赖。用户通过自己的合法安装准备本机依赖；这些内容与构建生成的 `vendor/`、`runtime/`、`.tools/`、`outputs/` 由 Git 忽略。

完整冻结源码与原始运行记录继续由作者本地保留，精简公开交付不删除研究档案。发布的身份清单与历史结果不能通过修改 SHA、关闭保护或把当前主树数据改名来刷新。
