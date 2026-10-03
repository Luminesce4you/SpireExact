# 从公开源码构建 holdout-01

所有命令在源码根目录运行。支持的准备与构建平台为 Windows x64；包内路径由工具相对源码目录计算，无需作者的工作区、D 盘、Codex 或私有 NuGet 缓存。

## 1. 准备环境

| 依赖 | 要求 |
| --- | --- |
| Python | 3.11 或更高，`python` 可用；核心无第三方 Python 包依赖 |
| .NET | 9 SDK，`dotnet --version` 应输出 `9.*`；仅安装 runtime 不够 |
| Git | 可执行 `git`，首次构建可以访问固定上游仓库 |
| 游戏 | 自己安装的 Windows STS2 v0.111.0，含 `data_sts2_windows_x86_64` |
| Workshop | 自己安装的 RitsuLib item 3747602295 |
| 网络 | 首次获取 CombatSolver 固定提交与 NuGet 包 |

已登记的游戏 `sts2.dll` SHA-256：

```text
0861bfa1df347538d932f22d580e75420f08082792eb914e53b4882764acdbe9
```

游戏版本或依赖指纹不同应停止并报告。不要把新游戏版本的程序集换到旧路径上后沿用 holdout-01 的证据，也不要为通过检查修改身份白名单。

## 2. 准备依赖并构建

PowerShell：

```powershell
python tools/setup_source.py `
  --game-dir "<游戏根目录或 data_sts2_windows_x86_64 目录>" `
  --ritsu-dir "<Workshop/3747602295 目录>"
```

如果 .NET 9 SDK 不在 PATH 中：

```powershell
python tools/setup_source.py `
  --game-dir "<游戏根目录或 data_sts2_windows_x86_64 目录>" `
  --ritsu-dir "<Workshop/3747602295 目录>" `
  --dotnet "<dotnet.exe 的绝对路径>"
```

脚本把需要的用户依赖准备到源码包的私有 runtime 布局，获取 [upstream.lock.json](../upstream.lock.json) 固定的 CombatSolver，应用 `tools/install_native_probe.py` 维护的本地 harness 补丁，再构建 C# 宿主、solver、harness 与二进制契约检查工具。它仅修改源码包中的文件，不写真实游戏的 `mods` 或玩家存档，不执行宿主、战斗搜索或探针。

配置写到 `.tools/source-setup.json`；每次准备的报告写到 `outputs/source-setup/<timestamp>/report.json`。运行以下命令检查既有准备和产物，检查模式不重新构建或联网：

```powershell
python tools/setup_source.py --check-only
```

`compiled=true` 只表示编译成功。必须同时检查 `runtime_ready` 与身份检查结果；`runtime_ready` 是构建与身份前置检查的结果，不表示宿主或整局已经执行成功。二进制保护拒绝重编译身份时不能宣称可用。日志与报告保留具体失败原因。

公开包对跨目录重编译增加了可移植身份检查：保留原有六个完整 DLL 的白名单；新 DLL 则必须匹配已审阅 `CorePowerSupport` 整类及嵌套类型的规范化 CIL 契约。宿主重新读取实际 DLL 自行计算，固定契约摘要不能由本地 JSON 授权覆盖。游戏与 RitsuLib 依赖也按固定 SHA 检查。该适配支持从固定源码在不同目录重编译，不能替代新构建的运行与重放验证。

本交付已使用 Python 3.12.14 与 .NET SDK 9.0.318，在独立源码包目录通过四项目构建（0 warning / 0 error）和 `--check-only`。原有已审阅 DLL 与新编译 DLL 的整类规范化契约一致；对改动一条 CIL 指令、改动上游补丁源码和改动依赖文件的负向检查均拒绝。以上均为源码/依赖/编译产物与静态契约检查，没有执行原生游戏请求。新宿主的完整二进制指纹不同于历史 holdout 身份，旧证书不自动认证该构建。详情见 [BUILD_VALIDATION.json](../release/BUILD_VALIDATION.json) 与 [SOLVER_CONTRACT_AUDIT.json](SOLVER_CONTRACT_AUDIT.json)。

## 3. 源码测试与运行前检查

```powershell
python tools/run_tests.py
python -m spire_exact --help
```

源码测试不会产生新的 holdout 胜率。后续运行需 8 个同类型逻辑 CPU，按 Windows CPU Set Information 识别，14 GiB Job 工作负载加 2 GiB 预留；请求 7 个 worker，但冻结核心可能因可用内存下调实际数量，应读取运行 manifest 和资源报告。不同 worker 数、核心类型或机器上的时刻不严格可比。

使用公开启动器预览完整参数：

```powershell
python tools/run_holdout_source.py `
  --seed 2098492050 --out "outputs/my-a10-plan" `
  --minutes 30 --solver-seed 271828 --workers 7 --dry-run
```

`--dry-run` 只列请求与命令，不执行原生宿主或求解。正常启动去掉 `--dry-run`，并使用新的输出目录：

```powershell
python tools/run_holdout_source.py `
  --seed 2098492050 --out "outputs/my-a10-run" `
  --minutes 30 --solver-seed 271828 --workers 7 --detach
```

此公开启动器从 `.tools/source-setup.json` 读取已构建的 SDK/游戏依赖；构建身份检查失败时拒绝启动。`--seed` 和 `--out` 必填；`--minutes` 默认 30，范围 1–30；`--solver-seed` 默认 271828；`--workers` 默认 7，范围 1–7。`--detach` 通过 Windows WMI 在当前终端之外启动作业并返回 PID，日志位于输出目录同级；去掉该开关可在当前终端等待。输出需要是尚未使用的新目录。手动降低 worker 数会改变机器条件，应记录实际配置。

启动器以 Windows Job 强制限制 CPU、内存与墙钟，固定 IRONCLAD/A10/all unlocks/fresh start 和 focus + A+B+open；显式研究开关为 `--root-policies pick,elo --focus-cluster-cap 2 --final-gate-plan open`，有序派发窗口 56。内部节点与评估预算保持冻结配置，墙钟仅为终止上限。运行产物与资源报告位于所选输出目录或同级目录，最终以本次 `result.json`、证书和 manifest 为准。

## 4. 重放与证据

本包保存的是完整动作路线与历史证书，没有附每一步的状态快照。用公开入口检查一条路线：

```powershell
python tools/replay_holdout_source.py `
  --route "release/holdout-01/routes/seed-2059734609/winning-route.json" `
  --out "outputs/replay-holdout-2059734609" --dry-run
```

`--dry-run` 仅校验 context/trace 结构和摘要，不检查本机 setup，不启动宿主，不写运行输出。构建与身份检查成功后，去掉该开关开始实际重放：

```powershell
python tools/replay_holdout_source.py `
  --route "release/holdout-01/routes/seed-2059734609/winning-route.json" `
  --out "outputs/replay-holdout-2059734609" `
  --seconds 180 --worker-memory-mib 1000
```

重放器从 setup 配置读取本机 SDK 和依赖，对当前宿主从初始状态用两个独立新进程执行完整动作。它不调用 advisor，不加载旧检查点/缓存，不继承历史证书；通过后生成当前二进制身份下的新证书。读取 `report.json` 的 `status` 与 `whole_run_verified`，并确认已生成新的 `certificate.json`。`--seconds` 是每次重放的安全上限，`--out` 必须是新目录。此交付过程中只检查了工具与路线结构，没有执行该原生重放。

若有自己保存的完整 `candidate.json`（含逐决策证据）与对应 `request.json`，可使用原始 `tools/replay_p5_route.py`。重建的 DLL 会产生新的身份；旧检查点应被拒绝，不能改 SHA 绕过保护。

若构建时用 `--dotnet` 提供了未加入 PATH 的 SDK，先在当前 PowerShell 里启用已保存的 SDK：

```powershell
$sourceConfig = Get-Content -LiteralPath ".tools/source-setup.json" -Raw | ConvertFrom-Json
$sourceSdkDir = Split-Path -Parent $sourceConfig.dotnet
$env:DOTNET_ROOT = $sourceSdkDir
$env:PATH = $sourceSdkDir + [IO.Path]::PathSeparator + $env:PATH
```

```powershell
python tools/replay_p5_route.py `
  --game-dir "runtime/steamapps/common/Slay the Spire 2/data_sts2_windows_x86_64" `
  --candidate "<含 trace 与 decision_evidence 的 candidate.json>" `
  --request "<该 candidate 对应的 request.json>" `
  --out "outputs/my-independent-replay"
```

原始工具的候选必须含所需的完整证据；包内 `winning-route.json` 或历史证书不能作为它的 `--candidate`。执行重放命令会真实启动离线原生宿主；重放报告与新证书才说明当前身份下的胜利重放。

## 5. 故障处理

| 问题 | 处理 |
| --- | --- |
| 找不到 `python`、`git` 或 .NET 9 SDK | 安装相应依赖并重开终端；明确提供 `--dotnet` |
| 找不到游戏或 RitsuLib DLL | 检查安装目录、Workshop item 与脚本报告；提供真实依赖路径 |
| 游戏/依赖 SHA 不匹配 | 当前安装与基线不同；停止运行，保留报告 |
| 上游或 NuGet 下载失败 | 检查网络/代理，再重跑构建；不需要作者的缓存 |
| 编译失败 | 查看 `outputs/source-setup/` 报告与 native build 日志 |
| 编译成功但身份保护未通过 | 保留报告；不要关闭保护或伪造旧 SHA；见 [发布范围](RELEASE_SCOPE.md) |
| CPU 或内存不满足运行协议 | 释放资源或换满足协议的机器；构建成功不代表能够运行标准配置 |
| 输出目录已经存在 | 使用新的目录名，不复写历史运行 |

公开发行目录已剔除 `vendor/`、`runtime/`、`.tools/`、`outputs/`、全部 `bin/obj`、缓存与本机配置；它们会在用户自行 setup/构建/运行时重新生成，并由 `.gitignore` 排除。发布内容与本地保留内容的区别见 [CLEANUP_REPORT.json](../release/CLEANUP_REPORT.json)。`SOURCE_MANIFEST.sha256` 记录发行文件的 SHA-256；构建结果与前置身份检查见 [BUILD_VALIDATION.json](../release/BUILD_VALIDATION.json)，两个报告均不表示新增原生胜利。
