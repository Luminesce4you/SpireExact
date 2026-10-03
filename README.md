# SpireExact — holdout-01 source release

SpireExact 是 Slay the Spire 2 的全信息路线搜索工具。这份 Windows 源码包固定到 **`frozen-i054a`**，面向 IRONCLAD / Ascension 10 / all unlocks / fresh start，允许根据种子利用未来信息。Python 协调路线搜索，C# 宿主在用户提供的游戏程序集上执行规则，CombatSolver 提供战斗候选。

`holdout-01` 的 20 个新种子各运行一次，17 个在 30 分钟内找到路线并通过独立新进程重放。实际 worker 为 5–7 个；没有对照臂。**17/20 是该冻结版本、配置与机器条件下的历史汇总，不是任意种子的成功概率。** 未解出的 3 个种子仅表示本次预算内未解出。[完整结果](release/holdout-01/results.md) · [机器可读汇总](release/holdout-01/summary.json) · [发布范围](docs/RELEASE_SCOPE.md)

## 构建

需要 Windows x64、Python 3.11+、.NET 9 SDK、Git，以及自己安装的 STS2 **v0.111.0** 与 RitsuLib Workshop item **3747602295**。首次构建需要联网获取固定版本 CombatSolver 和 NuGet 包。此包不附游戏 DLL、素材、Workshop 文件、SDK 或作者机器缓存。

在解压目录或 Git clone 根目录打开 PowerShell：

```powershell
python --version
dotnet --version
git --version

python tools/setup_source.py `
  --game-dir "<你的游戏根目录或 data_sts2_windows_x86_64 目录>" `
  --ritsu-dir "<你的 Workshop/3747602295 目录>"

python tools/setup_source.py --check-only
```

用实际路径替换 `<...>`；.NET SDK 没有加入 PATH 时可追加 `--dotnet "<dotnet.exe 的绝对路径>"`。构建器检查依赖身份，拉取固定上游并应用包内补丁，构建宿主、solver 和 harness。它不启动游戏或求解、不写玩家存档，也不把模组安装进游戏。构建详情、故障处理和产物位置见 [BUILD.md](docs/BUILD.md)。

本包已在 Windows、Python 3.12.14、.NET SDK 9.0.318 下实际构建宿主、solver、harness 和静态契约检查器，四个项目均为 0 warning / 0 error，构建与身份前置检查通过。[构建报告](release/BUILD_VALIDATION.json)与[契约审阅记录](docs/SOLVER_CONTRACT_AUDIT.json)保留了验证边界。发布宿主增加了可移植构建的身份检查，二进制指纹与历史 holdout 宿主不同；没有执行新的原生游戏请求、求解或探针，17/20 仍只属于原始冻结运行。

## 运行配置与结果

构建与身份检查成功后，先查看完整启动命令，再运行一个种子：

```powershell
python tools/run_holdout_source.py `
  --seed 2098492050 --out "outputs/my-a10-plan" `
  --minutes 30 --solver-seed 271828 --workers 7 --dry-run

python tools/run_holdout_source.py `
  --seed 2098492050 --out "outputs/my-a10-run" `
  --minutes 30 --solver-seed 271828 --workers 7 --detach
```

`--dry-run` 只打印请求与命令；`--detach` 通过 Windows WMI 在当前启动终端之外创建作业并返回 PID，日志保存在输出目录同级。去掉 `--detach` 可在当前终端等待作业结束。公开启动器固定使用历史 **focus + A + B + open** 配置，其中 A+B+open 对应 `--root-policies pick,elo --focus-cluster-cap 2 --final-gate-plan open`；`solver_seed=271828`，有序派发窗口 56。源码中的 `focus` profile 本身不自动开启这些研究开关。启动器读取 setup 保存的本机 SDK/依赖配置，使用 Windows Job 限定 8 个同类型逻辑 CPU、14 GiB 工作负载和 2 GiB 预留。实际 worker 可能按可用内存下调。

一次运行一个种子，30 分钟为整局墙钟安全上限。示例使用历史面板中的种子；在自己机器上重跑属于新运行，不计入历史 17/20。完整配置与检查范围见 [BUILD.md](docs/BUILD.md)。

新运行必须使用新的输出目录。查看 `result.json`；只有 `VERIFIED_WIN_IN_NATIVE_HOST` 和对应的新进程重放证书支持当前离线模型中的胜利。`UNKNOWN`、死亡、超时、资源上限或适配失败均不证明种子无解。包中证书是历史记录，重新构建后须在当前二进制上重放才能形成新的证据。[运行与证据边界](docs/RELEASE_SCOPE.md)

包内已附 17 条完整获胜动作路线。构建成功后，可在本机从初始状态独立重放其中一条，生成当前二进制身份下的新证书：

```powershell
python tools/replay_holdout_source.py `
  --route "release/holdout-01/routes/seed-2059734609/winning-route.json" `
  --out "outputs/replay-holdout-2059734609"
```

追加 `--dry-run` 只检查路线结构和摘要，不启动宿主。此源码交付过程中没有执行新的求解或路线重放；上面的命令供用户在自己提供的依赖上运行。

## 源码导航

| 路径 | 用途 |
| --- | --- |
| `spire_exact/` | Python CLI、路线搜索、关口模型、调度与进程池；固定 i054a 核心 |
| `native/SpireNativeHost/` | C# 原生规则宿主 |
| `tools/setup_source.py` | 公开依赖准备与构建入口 |
| `tools/run_holdout_source.py` | 单种子公开启动器，固定 holdout-01 的显式研究配置 |
| `tools/replay_holdout_source.py` | 在当前本机依赖上独立重放已附完整动作，生成新证书 |
| `tools/` | 构建、重放、研究辅助工具；部分历史工具需要作者保留的实验数据 |
| `tests/` | 源码回归测试 |
| `release/holdout-01/` | 固定基线、20 个种子的结果与精选动作/证书 |
| `docs/` | 构建说明、规则来源、实现与证据边界 |
| `dashboard/` | 可选 SpireBoard 看板源码；首版以 CLI 为交付入口 |

后续主树的策略实验与性能改动不进入这一冻结核心。看板展示和启动新任务尚无本发布版的独立端到端验收。

发行目录仅包含源码、文档、许可证与已公开的研究证据；`SOURCE_MANIFEST.sha256` 记录发行文件的 SHA-256。本地构建生成的 `runtime/`、`vendor/`、`.tools/`、`outputs/`、`bin/obj` 和缓存均未打包。三个绑定作者私有数据的旧工具已从发行中剔除，具体范围见 [发布精简记录](release/CLEANUP_REPORT.json)。按上述 setup 命令可在自己的目录重新准备依赖和构建产物。

项目许可证见 [LICENSE](LICENSE)，上游与数据署名见 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)，CombatSolver 固定提交见 [upstream.lock.json](upstream.lock.json)。本发布版交付检查以 Windows 源码构建为范围；正常 Godot 游戏等价、Linux/macOS 运行、未来游戏版本兼容没有因此得到验证。

公开源码文件清单与 SHA-256 见 [SOURCE_MANIFEST.sha256](SOURCE_MANIFEST.sha256)。
