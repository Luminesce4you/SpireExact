# SpireBoard：i100 本机前端

SpireBoard 提供 i100 新作业提交、实时进展、关口模型、胜利路线与作业控制。前端和命令行使用相同的 i100 profile，普通 Python、.NET SDK 和用户自己的游戏安装即可准备运行。

## 构建、准备、启动

在源码根目录按 [BUILD.md](../docs/BUILD.md) 完成依赖准备：

```powershell
python tools/setup_source.py `
  --game-dir "<游戏根目录或 data_sts2_windows_x86_64 目录>" `
  --ritsu-dir "<Workshop/3747602295 目录>"

python tools/setup_source.py --check-only
python tools/prepare_dashboard.py
powershell -NoProfile -File dashboard/start.ps1 -Python python -Port 8765
```

用真实路径替换占位符。支持 Windows x64、Python 3.11+、.NET 9 SDK、Git、STS2 v0.111.0 与完整 RitsuLib item 3747602295。SDK 不在 PATH 时，在 setup 命令追加 `--dotnet "<dotnet.exe 的绝对路径>"`。

准备工具校验本机 setup、源码和宿主身份，并执行零动作宿主初始化，检查可暂停的 Evaluate 计时适配。报告保存到 `outputs/dashboard-prepare/<timestamp>/report.json`，成功后写入 `.tools/dashboard-ready.json`；新作业使用当前包内 i100 实现。源码或编译产物改变后重新构建并准备，运行中保持作业使用的源码与产物不变。

启动器读取 setup 保存的 SDK 路径，启动 Python 后端并打开 [本机页面](http://127.0.0.1:8765/)。追加 `-NoBrowser` 可只启动服务；用 `-Port` 选择其他端口。也可在当前终端运行：

```powershell
python dashboard/server.py --port 8765
```

然后自行打开对应浏览器地址。后端日志位于 `dashboard/runtime/`。如果端口由另一份项目的服务使用，选择空闲端口并打开对应地址。

## 新建 i100 求解

点击右上角 **新建求解**，输入种子、选择时间上限并提交。大部分种子建议选择 45 分钟。

- 默认 30 分钟，支持 1–45 分钟。
- IRONCLAD / Ascension 10 / all unlocks / fresh start / mode1 全信息。
- 完整 `--feature-profile i100`，设置来自 `final_defaults.py`。
- 7 个 worker、有序派发窗口 56；标准资源为 8 个同类型逻辑 CPU、14 GiB Job 工作负载与 2 GiB 预留。
- 资源准入必须满足请求的 worker 数，不满足时显示失败原因并保留记录。
- 用户新作业独立登记，预算下的 UNKNOWN 保留为未知结果。

本机就绪检查或作业身份不匹配时拒绝启动。浏览器只提交种子与时间，不接受 shell 命令或任意工作区。后端使用同源请求和每次服务的 session token 核对操作。

新作业保存在 `outputs/spireboard/interactive/<run-id>/`。prepare 生成本机 `storage-policy.json` 与 `experiments/iteration-manual` 目录联接，供看板索引本地作业；这些配置和目录随本机生成，发行不附作者的运行目录。

## 暂停、继续与退出

当前作业页显示 **暂停求解 / 继续求解 / 退出求解**：

| 控制 | 行为 |
| --- | --- |
| 暂停求解 | 冻结受控协调器、计时监控与原生 worker，保留内存；全部活成员暂停后才显示完成 |
| 继续求解 | 恢复同一组进程，接着当前搜索状态运行 |
| 退出求解 | 结束所属 Windows Job，保留已有结果、日志与证据 |

暂停时间不计主动求解预算与已支持的原生 Evaluate 截止时间。PID 创建身份和具名 Windows Job 用于绑定实际作业，身份不可确认时禁用操作。排队中或已结束的作业按当前状态显示可用控制。

暂停是存活进程的状态保留，进程退出或机器重启后不能用“继续求解”恢复。前端使用 Evaluate 计划；不兼容暂停计时的 Coordinator 模式会在交互请求执行前被拒绝。退出不把未完成求解写成获胜，也不删除已有研究记录。

## 看板与路线

**搜索进展** 显示当前种子的评估、到达层数、节点与资源变化。读取完整评估账本补全历史曲线，实时结果文件可以只保留最近记录。Windows 文件通知和 SSE 推送已有结果更新。

**关口模型** 读取所选作业保存的模型快照，显示入场数、拟合数据、结果和系数。前端不重新拟合模型；系数用于搜索分配，已观察的通过比例与校准胜率分别解释。最终幕 F1 与 F2 保持独立，合成探针单独显示。

**胜利轨迹** 提供跟打与证据核查。跟打按幕、层和阶段列出动作、记录中的手牌/目标位置与前后状态；证据核查保留原始动作、合法选项与 JSON。只有完整证书、独立重放和身份检查通过的路线才显示验证徽标。原始历史文件保持原身份。

跟打进度与“与游戏不一致”笔记保存在浏览器 localStorage。手牌与目标位置按离线 TestMode 记录的顺序列出；旧宿主缺少的选牌观测在页面中标明。

名称来自 `route_zh_data.js` 保存的中文 Wiki 快照，未知名称保留 native ID。刷新来源的维护命令为 `python tools/build_route_zh.py --refresh`。原动作、证书和导出的 JSON 不修改。

## 版本与历史

i100 的设计、判定规则与评估见 [I100.md](../docs/I100.md)；运行结果的 `search_metrics.gate_clinic` 记录每个 (stem, 首领关口) 的判定与依据。i082 的机制说明见 [I082.md](../docs/I082.md)。历史 holdout-01 的 17/20 属于 `frozen-i054a`，其完整动作可以在本机构建上重放并生成新证书。

[源码根入口](../README.md) · [构建与运行](../docs/BUILD.md) · [发布范围与方法](../docs/RELEASE_SCOPE.md)

