# i100 发布范围与验证方法

本源码 **0.4.0b1** 发布 **i100**：关口诊所按"进入该幕前的决策序列（stem）× 首领关口"判断卡点是根不对还是深度不够，并据此分配回退与局部加深；SpireBoard、单种子启动器和规划器命令行默认使用 i100。发布内容包括规划与宿主源码、完整前端、公开依赖准备与源码编译、命令行启动、完整动作重放入口和精选证据。

## 版本身份

| 对象 | 身份及用途 |
| --- | --- |
| i100 | profile `i100`，取值来自 `spire_exact/planning/final_defaults.py`；解析结果与源码身份见 [defaults.json](../release/i100/defaults.json) |
| 公开入口 | SpireBoard（`tools/prepare_dashboard.py` 绑定）、`tools/run_release_source.py`、`python -m spire_exact.planning` 均默认 i100 |
| 可选旧版本 | `--feature-profile i082`（`frozen-i082` 配置，原冻结 `source_version` `e1b10f6f10f1c1d27f5f176f8b4e73ce8aa07fc2a9ab94b9eaf2d9cd10bd7c74`）、`--feature-profile i085` |
| 历史 holdout-01 | `frozen-i054a`，20 次 30 分钟结果与 17 条获胜路线 |
| 用户重新编译 | 依赖、宿主、solver / harness 的二进制指纹随 setup 与运行记录保存；胜利证书在当前二进制下由重放生成 |

## 验证层次

| 层次 | 内容 |
| --- | --- |
| 源码回归 | 全量 Python 测试与前端 JS 检查；记录见 [tests.json](../release/i100/evidence/tests.json) |
| 兼容性 | legacy / i075-final / i081 / i082 显式选择时解析值逐项不变，i082 启动参数不变，18 条夹具路径 / 1,440 个请求一致；原生差异只有战斗边界修复的两个文件（[compatibility.json](../release/i100/evidence/compatibility.json)） |
| 离线评估 | 合成战役世界驱动真实规划主循环，代码冻结后在 80 个留出世界 × 2 个 solver seed 上比较 i100、i054a、i082、i085 与消融（[results.md](../release/i100/results.md)） |
| 源码编译与静态身份 | 用户本机运行 `tools/setup_source.py`，编译宿主、solver、harness 与静态契约检查器，并核对依赖指纹与固定 CIL 契约 |
| 前端准备 | `tools/prepare_dashboard.py` 绑定当前源码与宿主，执行零动作初始化，验证暂停计时前置条件 |
| 原生运行 | 每个作业保存设置、资源、诊所判定与评估账本 |
| 胜利确认 | 新进程从初始状态完整重放，无 advisor / 检查点 / 缓存，观察原生 `OnEnded(true)` 后生成证书 |

原生分层面板（i054a 未解 / 慢解 / 快解三层，另含开场选牌回归种子）与配对基准命令见 [I100.md 第 7 节](I100.md#7-本机-windows-原生验证)。

## 发行内容

发行保留规划 / 宿主源码、完整前端、回归测试、公开构建与身份适配、固定上游信息、MIT 许可证、第三方署名和精选证据。游戏与 Workshop DLL、SDK、第三方检出、缓存和运行输出由用户在本机准备。SpireBoard 运行页支持作业暂停、继续与退出；暂停保存存活进程的内存，暂停时间不计入求解预算。[前端说明](../dashboard/README.md)

## 历史版本

i082 的实现结果、测试与部署记录保留在 [release/i082](../release/i082/)，机制说明见 [I082.md](I082.md) 与 [I082_ARCHITECTURE.md](I082_ARCHITECTURE.md)；i085 的宏观与长尾研究见 [I085.md](I085.md) 与 [release/i085](../release/i085/)。
