# 从源码构建与运行 i100-r

本页命令都在源码根目录运行。支持的平台是 Windows x64；游戏和 Workshop 文件由你自己的安装提供。

## 1. 环境

| 依赖 | 要求 |
| --- | --- |
| Python | **3.12+**（setup、前端和规划器都会检查），规划核心没有第三方 Python 包依赖 |
| .NET | 9 SDK（只装 runtime 不能编译） |
| Git | 首次准备时获取固定版本的 CombatSolver |
| 游戏 | Windows 版 STS2 **v0.111.0**，完整的 `data_sts2_windows_x86_64` 目录 |
| Workshop | 完整的 RitsuLib item **3747602295** |
| 网络 | 首次获取 CombatSolver 和公共 NuGet 包 |
| 硬件 | 标准作业使用 8 个同类型逻辑 CPU、14 GiB 工作集加 2 GiB 预留，共需约 16 GiB 可用内存 |

支持的 `sts2.dll` SHA-256：

```text
0861bfa1df347538d932f22d580e75420f08082792eb914e53b4882764acdbe9
```

## 2. 构建

```powershell
python tools/setup_source.py `
  --game-dir "<游戏根目录或 data_sts2_windows_x86_64 目录>" `
  --ritsu-dir "<Workshop/3747602295 目录>"
```

SDK 不在 PATH 上时追加 `--dotnet "<dotnet.exe 的绝对路径>"`。

setup 把你的游戏和 RitsuLib 文件复制到包内的 `runtime/` 布局，按 [upstream.lock.json](../upstream.lock.json) 获取固定提交的 CombatSolver，应用 harness 补丁，然后编译原生宿主、CombatSolver、harness 和静态契约检查器。构建时 `CopyModOnBuild=false`，不会往游戏目录安装模组，也不会写入存档。配置保存在 `.tools/source-setup.json`，日志和报告在 `outputs/source-setup/<时间戳>/`。

检查已有构建（不联网、不重新编译、不运行原生代码）：

```powershell
python tools/setup_source.py --check-only
```

游戏、RitsuLib 和上游提交都按字节指纹校验。宿主对新编译的 CombatSolver 做完整的 CIL 契约检查（`AdvisorBlockCompensation.cs`、`SolverContractFingerprint.cs`），契约摘要固定在源码里。你在本机编译会得到自己的二进制身份，历史证书保留原来的身份。

## 3. SpireBoard 前端

```powershell
python tools/prepare_local_entry.py --artifact-root "<存放运行输出的目录>"
powershell -NoProfile -File dashboard/start.ps1 -PythonExe python -Port 8765
```

`prepare_local_entry.py` 写入本机存储策略和种子登记表，并把当前源码冻结为 `experiments/frozen-i100r`。前端的每个新作业都在这个快照里运行，之后修改源码不影响它。修改源码后如果想让前端使用新代码，删除 `experiments/frozen-i100r` 再运行一次准备命令。

启动器会检查 Python 版本，读取 setup 记录的 SDK，启动后端，然后打开 [本机页面](http://127.0.0.1:8765/)。`-NoBrowser` 只启动服务；`-Port` 换端口。端口已被其他服务或另一份 SpireBoard 占用时，启动器会拒绝复用。

点 **新建求解**，输入种子和时间上限（默认 60 分钟，可选 1–180 分钟）。作业固定 IRONCLAD / A10 / all unlocks / fresh start / mode1，参数与 iteration-105 A 臂相同：7 个 worker、有序派发窗口 56、solver seed 271828、CombatSolver 4d2c550、不开插件。已有作业占用资源时新作业会排队，排队时间不计入预算。运行页提供 **退出求解**。暂停要求作业通过暂停计时兼容验证，i054a 的 `escalate` 关口计划不在其中，所以 i100-r 作业的暂停按钮保持不可用。

## 4. 命令行

```powershell
python tools/run_seed.py --seed 101 --minutes 60 `
  --iteration my-runs --name seed-101 --workspace experiments/frozen-i100r --detach `
  --extra --root-policies pick,elo --focus-cluster-cap 2 --final-gate-plan open --worker-memory-policy shared
```

这条命令与前端作业的参数相同。`--detach` 启动后立即返回；去掉它则在当前终端等待结束。运行目录在 `experiments/<iteration>-runs/<name>/`（链接到 artifact root），每次换一个新的 `--name`。

规划器本身是 `python -m spire_exact.planning`（`--help` 列出全部参数），`--config i054a-shm` 从 `configs/i054a-shm.toml` 展开同一组参数，后面的参数可以覆盖其中的单项。它不自己创建 Windows Job，而共享内存策略要求外层 Job 的内存上限，所以日常使用请走前端或 `run_seed.py`。

结果读 `result.json`：`VERIFIED_WIN_IN_NATIVE_HOST` 表示候选通过了独立原生重放；`UNKNOWN` 表示预算内没有找到胜利。预算到点时，进行中的长战斗任务可能让作业由安全上限结束（`exit_code` 124），结果保留最后一次保存的状态。

## 5. 测试与等价检查

```powershell
python tools/run_tests.py
```

有自己录制的运行记录时，可以用新编译的宿主逐请求对拍：

```powershell
python tools/speed_calibration.py --other-host --worker-memory-policy hard --worker-mib 1536 `
  --reference "<录制的运行目录>" --out "outputs/host-equivalence"
```

`all_identical: true` 表示新宿主对每个录制请求给出的决策完全相同。`tools/replay_golden.py` 用同样的方式检查整个搜索的请求序列和最终报告。

## 故障处理

| 问题 | 处理 |
| --- | --- |
| 找不到 Python 3.12、Git 或 .NET SDK | 安装后重开终端，或用 `--dotnet`、`-PythonExe` 指定 |
| 游戏或 RitsuLib 校验失败 | 确认游戏为 v0.111.0、Workshop item 完整；其他版本需要单独适配 |
| CombatSolver 或 NuGet 获取失败 | 检查网络和代理后重跑 setup |
| 编译或身份检查失败 | 查看 `outputs/source-setup/` 下的报告与编译日志 |
| 前端提示“已验证的求解版本不可用” | 运行 `python tools/prepare_local_entry.py` |
| 本机 HTTP 请求被代理拦截 | 为 `127.0.0.1` 设置 `NO_PROXY` |
| 网页打不开或端口被占用 | 查看 `dashboard/runtime/` 下的服务日志，换 `-Port` |
| CPU 或内存不够标准作业 | 释放资源；准入失败时作业不会降级运行 |
