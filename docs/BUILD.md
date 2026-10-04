# 从公开源码构建与运行 i082

在源码根目录运行本页命令。支持的构建与启动平台为 Windows x64；工具按源码目录计算包内路径，用户提供自己的游戏与 Workshop 安装。

## 1. 环境

| 依赖 | 要求 |
| --- | --- |
| Python | 3.11+，`python` 可用，规划核心无第三方 Python 包依赖 |
| .NET | 9 SDK，仅安装 runtime 不足以编译 |
| Git | `git` 可用，首次准备能获取固定上游提交 |
| 游戏 | Windows STS2 v0.111.0，完整 `data_sts2_windows_x86_64` 目录 |
| Workshop | 完整 RitsuLib item 3747602295，含 `compat/0.111.0`、`shared` 与 references props |
| 网络 | 首次获取 CombatSolver 与公共 NuGet 构建依赖 |

支持的 `sts2.dll` SHA-256：

```text
0861bfa1df347538d932f22d580e75420f08082792eb914e53b4882764acdbe9
```

游戏、RitsuLib 与固定上游分别按版本和字节指纹校验。版本不同的安装需要单独适配和验证，检查失败时保留报告。

## 2. 构建

```powershell
python tools/setup_source.py `
  --game-dir "<游戏根目录或 data_sts2_windows_x86_64 目录>" `
  --ritsu-dir "<Workshop/3747602295 目录>"
```

SDK 不在 PATH 时：

```powershell
python tools/setup_source.py `
  --game-dir "<游戏根目录或 data_sts2_windows_x86_64 目录>" `
  --ritsu-dir "<Workshop/3747602295 目录>" `
  --dotnet "<dotnet.exe 的绝对路径>"
```

setup 将用户依赖复制到包内 runtime 布局，通过 [upstream.lock.json](../upstream.lock.json) 获取固定 CombatSolver，应用公开 harness 补丁并编译宿主、solver、harness 与静态契约检查工具。`CopyModOnBuild=false`；准备和构建只修改源码包目录，不安装 mod 或写入玩家存档，也不执行游戏求解。

配置保存到 `.tools/source-setup.json`，日志与报告保存到 `outputs/source-setup/<timestamp>/`。随后检查既有准备与产物：

```powershell
python tools/setup_source.py --check-only
```

检查模式不重新构建或联网。查看报告中的 `compiled`、`runtime_ready` 和身份检查结果：它们表示编译、依赖及身份前置检查，实际宿主执行和完整胜利路线另有运行记录。

可移植身份适配保留完整 DLL 白名单，并检查固定 `CorePowerSupport` 整类及嵌套类型的规范化 CIL 契约；宿主直接校验实际加载的 DLL。契约摘要固定在源码中，本地 JSON 不授权覆盖。游戏与 RitsuLib 也按固定 SHA 校验。编译目录改变会产生新的二进制身份，历史证书保留原始身份。

本交付四项目构建均为 0 warning / 0 error，静态身份前置检查通过，未执行游戏请求、探针、搜索或胜利重放。[BUILD_VALIDATION.json](../release/BUILD_VALIDATION.json)记录实际构建与检查范围。

作者建议大部分种子选择 45 分钟；这项推荐不作为测得的胜率结论。前端与 CLI 默认 30 分钟，支持 1–45 分钟。

## 3. 前端准备与启动

完成 setup 与 `--check-only` 后，在同一源码根目录运行：

```powershell
python tools/prepare_dashboard.py
powershell -NoProfile -File dashboard/start.ps1 -Python python -Port 8765
```

准备工具先执行 setup 身份检查，然后执行零动作宿主初始化，核对可暂停 Evaluate 计时适配与零动作结果。报告保存到 `outputs/dashboard-prepare/<timestamp>/report.json`，成功后写入 `.tools/dashboard-ready.json`，前端新作业使用这个经过本机检查的 i082 源码入口。它不执行战斗、探针或整局搜索。源码或宿主改变后需要重新构建并准备；运行中保持该作业使用的源码与产物不变。

启动器从 setup 读取普通 .NET SDK 路径，启动 Python 后端并打开 [本机 SpireBoard](http://127.0.0.1:8765/)。`-NoBrowser` 只启动服务；`-Port` 可选择其他端口，浏览器地址使用对应端口。也可在当前终端运行 `python dashboard/server.py --port 8765` 并自行打开页面。

点击 **新建求解** 输入种子，默认 30 分钟，支持 1–45 分钟。新作业完整采用 i082 profile、7 worker、有序派发窗口 56。资源不足以满足请求时拒绝启动，状态与错误保留在作业记录。产物位于 `outputs/spireboard/interactive/<run-id>/`；prepare 生成本机存储配置与用于看板索引的 `experiments/iteration-manual` 目录联接，这些都是本地运行产物。

运行页的 **暂停求解** 冻结受控进程树并保留内存，暂停时间不计主动求解预算；**继续求解** 恢复相同进程；**退出求解** 结束受控作业并保留已有文件。这是进程存活期间的暂停，退出或重启机器后不能继续该暂停状态。[前端使用与证据说明](../dashboard/README.md)

## 4. 命令行干跑预览

```powershell
python tools/run_release_source.py `
  --seed 101 --out "outputs/my-i082-plan" `
  --feature-profile i082 --minutes 30 --solver-seed 271828 --workers 7 --dry-run
```

预览完整命令和 i082 设置，不执行原生宿主或搜索。配置来自 `final_defaults.py`，包含完整 i082 机制，详见 [I082.md](I082.md)。

## 5. 命令行单种子作业

```powershell
python tools/run_release_source.py `
  --seed 101 --out "outputs/my-i082-run" `
  --feature-profile i082 --minutes 30 --solver-seed 271828 --workers 7 --detach
```

`--seed`、`--out` 必填；默认 profile 为 `i082`、30 分钟（支持 1–45）、`solver_seed=271828`、7 worker（支持 1–7）。输出目录必须尚未使用。`--detach` 返回 PID，日志在输出目录同级；省略该开关则在当前终端等待结束。

启动器读取 setup 保存的 SDK 和依赖，身份前置检查失败时拒绝启动。标准配置使用 Windows Job 限制 8 个同类型逻辑 CPU、14 GiB 工作负载内存，并预留 2 GiB。默认预算 30 分钟，可选 45 分钟；接入暂停账本时超时按排除暂停的主动时间判断。有序派发窗口为 56，资源准入必须满足请求的 worker 数，否则拒绝启动。可显式指定较小的 `--workers`，设置记录在 manifest 与资源报告中。一次运行一个种子；不同机器、worker 数与核心类型的墙钟结果需结合资源记录比较。

作业固定 IRONCLAD / A10 / all unlocks / fresh start / mode1。配置、身份和结果随输出保存；读取本次 `result.json`、manifest 和重放证书。`UNKNOWN` 是当前预算下未知，胜利以独立新进程的真实原生终局与证书为准。

## 6. 测试与动作重放

```powershell
python tools/run_tests.py
python tools/run_release_source.py --help
```

iteration-082 原冻结已有完整源码回归 845/845 通过；本地重新运行产生自己的报告，不生成整局效果结论。

包内历史 holdout 路线可以在当前构建上检查并重放：

```powershell
python tools/replay_holdout_source.py `
  --route "release/holdout-01/routes/seed-2059734609/winning-route.json" `
  --out "outputs/replay-holdout-2059734609" --dry-run

python tools/replay_holdout_source.py `
  --route "release/holdout-01/routes/seed-2059734609/winning-route.json" `
  --out "outputs/replay-holdout-2059734609" `
  --seconds 180 --worker-memory-mib 1000
```

重放干跑检查 context/trace 结构与摘要；实际重放用当前宿主从初始状态执行完整动作，使用两个独立新进程，不使用 advisor、旧检查点或缓存。通过后生成当前身份下的新 `certificate.json`；读取 `report.json` 中的 `status` 和 `whole_run_verified`。每次使用新输出目录。

## 故障处理

| 问题 | 处理 |
| --- | --- |
| 找不到 Python、Git 或 .NET SDK | 安装依赖并重开终端，或明确提供 `--dotnet` |
| 找不到游戏或 RitsuLib | 检查完整安装和 `compat/0.111.0`，提供实际目录 |
| 游戏或依赖 SHA 不匹配 | 保存报告，使用支持版本；新版本需要独立适配 |
| 上游或 NuGet 获取失败 | 检查网络/代理，再执行 setup |
| 编译或身份检查失败 | 查看 `outputs/source-setup/` 报告和编译日志 |
| 前端提示入口未准备或身份已改变 | 执行 `python tools/prepare_dashboard.py`，按报告解决前置检查失败 |
| 网页无法打开或端口被其他服务占用 | 查看 `dashboard/runtime/` 服务日志，使用 `-Port` 选择空闲端口 |
| CPU/内存不满足标准作业 | 释放资源，读取准入报告与实际 worker 数 |
| 输出目录已存在 | 选择新的目录名，保留历史运行 |

公开发行排除游戏/Workshop DLL、SDK、vendor 检出、构建产物与缓存；setup 和作业在用户本机生成这些内容。构建身份、历史执行身份和验证范围见 [RELEASE_SCOPE.md](RELEASE_SCOPE.md)。

