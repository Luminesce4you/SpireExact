# SpireExact — holdout-01

**输入一个 Slay the Spire 2 种子，搜索完整通关路线。** SpireExact 把地图、选牌、商店、休息和战斗放在同一次规划中，由游戏原生程序集执行每一步。获胜候选在独立新进程中从开局复核，输出完整动作路线与验证证书。

项目研究的是全信息求解：规划可以利用种子所确定的未来，持续探索同一个种子的不同路线，当前研究预算为每局半小时。Python 负责整局规划，C# 原生宿主执行游戏规则，固定版本的 CombatSolver 提供战斗候选。[项目背景与架构](docs/PROJECT_BACKGROUND.md)

## holdout-01 结果

本发布以 **`frozen-i054a`** 和 `holdout-01` 为研究基线。20 个新种子各运行一次，**17 个在 30 分钟内找到完整路线并通过独立新进程重放**，观察到的解出比例为 85%，Wilson 95% 区间为 64–95%。获胜候选时刻中位数为 461 秒，最长为 1,397 秒。

| 实验设置 | 取值 |
| --- | --- |
| 角色与难度 | IRONCLAD / Ascension 10 |
| 初始条件 | all unlocks / fresh start / mode1 全信息 |
| 配置 | focus + A + B + open，`solver_seed=271828` |
| 预算 | 每种子一次，30 分钟墙钟安全上限 |
| 实际 worker | 5–7 个，逐次设置随结果保存 |
| 执行方式 | v0.111.0 游戏程序集，离线 TestMode |

[完整实验结果](release/holdout-01/results.md) · [机器可读记录](release/holdout-01/summary.json) · [版本与方法](docs/RELEASE_SCOPE.md)

## 构建

准备 Windows x64、Python 3.11+、.NET 9 SDK、Git，以及自己安装的 STS2 **v0.111.0** 和 RitsuLib Workshop item **3747602295**。首次构建从公开仓库获取固定版本 CombatSolver，并通过 NuGet 获取构建依赖。

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

用实际路径替换 `<...>`。SDK 使用独立安装路径时，追加 `--dotnet "<dotnet.exe 的绝对路径>"`。构建器校验本机依赖，准备包内 runtime 布局，获取固定上游并应用 harness 补丁，构建宿主、solver、harness 和静态契约检查器。[详细构建说明](docs/BUILD.md)

本次已完成 Windows / Python 3.12.14 / .NET SDK 9.0.318 下的四项目源码构建，均为 0 warning / 0 error，静态身份校验通过。[构建报告](release/BUILD_VALIDATION.json)记录本次发布构建，[契约审阅记录](docs/SOLVER_CONTRACT_AUDIT.json)记录可移植身份检查。历史执行证据对应 `frozen-i054a`；发布源码的构建与执行身份分别保存在记录中。

## 搜索一个种子

先预览启动参数，再启动单种子作业：

```powershell
python tools/run_holdout_source.py `
  --seed 2098492050 --out "outputs/my-a10-plan" `
  --minutes 30 --solver-seed 271828 --workers 7 --dry-run

python tools/run_holdout_source.py `
  --seed 2098492050 --out "outputs/my-a10-run" `
  --minutes 30 --solver-seed 271828 --workers 7 --detach
```

`--dry-run` 打印完整请求与命令。`--detach` 通过 Windows WMI 启动作业并返回 PID，日志保存在输出目录同级；去掉该开关可在当前终端等待结束。

这个入口显式采用 **focus + A+B+open**，对应 `--root-policies pick,elo --focus-cluster-cap 2 --final-gate-plan open`，有序派发窗口为 56。标准作业分配 8 个同类型逻辑 CPU、14 GiB Job 工作负载和 2 GiB 预留，请求 7 个 worker，实际数量按可用内存确定。一次运行一个种子，使用新的输出目录。

`result.json` 保存当前搜索结果。获胜时输出 `VERIFIED_WIN_IN_NATIVE_HOST`、完整动作与新进程复核证书；`UNKNOWN` 保存预算结束时的搜索进展。每次本机运行都有独立的配置、二进制身份与结果记录。

## 重放获胜路线

仓库附有 holdout-01 的 **17 条完整获胜动作路线与历史证书**。在自己的构建上从开局重放一条路线：

```powershell
python tools/replay_holdout_source.py `
  --route "release/holdout-01/routes/seed-2059734609/winning-route.json" `
  --out "outputs/replay-holdout-2059734609"
```

重放器用当前宿主在两个独立新进程中执行动作，核对通过后生成当前二进制身份下的新证书。追加 `--dry-run` 可先检查路线结构与摘要。源码交付的构建记录和历史路线的执行证据都可在 [发布范围与方法](docs/RELEASE_SCOPE.md)中追溯。

## 源码导航

| 路径 | 内容 |
| --- | --- |
| `spire_exact/` | Python CLI、整局搜索、关口模型、调度与进程池 |
| `native/SpireNativeHost/` | C# 游戏原生规则宿主 |
| `tools/setup_source.py` | 依赖准备与源码构建 |
| `tools/run_holdout_source.py` | holdout-01 配置的单种子启动器 |
| `tools/replay_holdout_source.py` | 完整动作重放与新证书生成 |
| `tools/`、`tests/` | 研究工具与源码回归测试 |
| `release/holdout-01/` | 20 个种子的结果、配置、完整获胜动作与证书 |
| `docs/` | 背景、构建、规则来源、实现与实验方法 |
| `dashboard/` | 补充的 SpireBoard 看板源码；本发布的运行入口为 CLI |

发行内容包含源码、文档、许可证和研究证据，本机依赖与构建产物由 setup 生成。[源码清单与 SHA-256](SOURCE_MANIFEST.sha256)和[发布精简记录](release/CLEANUP_REPORT.json)列出公开文件与导出范围。

项目采用 [MIT 许可证](LICENSE)。上游、社区数据与中文映射来源见 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)，CombatSolver 固定提交见 [upstream.lock.json](upstream.lock.json)。
