# i085 continuation 文档迁移

当前可运行延续/长尾实现为 [I085_TAIL.md](I085_TAIL.md) 中的 `TailFocusScheduler`，核心是确切前缀菜单、持久服务、在途费用和分层重根。总体入口见 [I085.md](I085.md)。

上一轮摘要描述的 `continuation_search.py` / `continuation_value.py` 二次核 prototype 没有保存在本次可恢复的完整源码包或远端分支中。保留的文档和测试摘要已转存至 `release/i085/evidence/tail/previous-continuation-summary/`，不能作为当前构建或测试的证据。当前不提供那些旧命令，也没有声称原 95 项验证属于这次源码。

本次重新实现和重新测试了可实际应用的候选，默认 tail 为 shadow；`--tail-mode on` 才真正控制。完整源码与新的 108 项测试、构造实验、Git bundle 一并封存，避免只留下进度说明。
