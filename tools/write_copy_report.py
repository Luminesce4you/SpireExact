"""Render the measured A0/B0 tables from retained native records."""
import csv
import hashlib
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from spire_exact.planning.io import read_json,write_json


def main():
    folder=ROOT/'experiments/iteration-052';d=read_json(folder/'copy-summary.json')
    artifact=Path(read_json(ROOT/'storage-policy.json')['artifact_roots'][0])/'iteration-052'
    native=artifact/'final-07b';state=read_json(native/'status.json');equivalence=read_json(native/'native-equivalence.json')
    if state['state']!='completed'or not equivalence['passed']or not d['accounting_passed']:raise ValueError('Cannot publish incomplete diagnostics')
    pct=lambda x:f'{100*x:.2f}%'
    integer=lambda x:f'{x:,}'
    lines=['# iteration-052：A0 / B0 组件诊断结果','',
        '2026-10-02，Asia/Taipei。A0 的默认关闭计数输出与互斥去向表已完成；B0 的对象复制 / 真实写入、规模关系与 P7 成本范围已完成。没有实现复制优化，没有调整搜索预算、束宽、默认配置或关口计划。',
        '', '## 证据与验收','',
        '三个种子 10101010 / 1741222413 / 564940356，每个 Monster / Elite / Boss 各 2 个保留入口，共 18 个不同入口。DEV 种子只用于诊断接口与 B0，不进入 A1 调参。最终冻结 frozen-i052-copy07，原始 DLL / 诊断关闭 / writes / costs 四臂共 72 次原生执行；全部完整游戏导出字节、动作、节点、请求输入、保险时钟裕度与离线统计关闭核对通过。此前 A0 输出开关的 72 请求哨兵及 final-05 的 72 请求一致性核对也已完成。重复执行不是更多独立入口。',
        '',
        '所有新原生组件采集为 1 个 E 核 worker，DOP 1、Low、各房间 10000 节点、gate none、600000 ms 保险上限，RSS 1536 MiB、实际 GCHeapHardLimit 1207959552 字节（1152 MiB），Job 14 GiB。P 核成本权重仅来自 iteration-042 三份保留的无插桩 P7 warm profile；没有占用 Claude 当前 P 核作业，没有新增整局运行。首领预算低于生产，以下比例不能说成生产关口计划的比例。',
        '',
        '固定游戏 SHA-256：`0861bfa1df347538d932f22d580e75420f08082792eb914e53b4882764acdbe9`；上游 pin：`4d2c55069f2cf9f8c621631594857b412cf9660f`。最终诊断 DLL：`8c95672f6c591519fd0282d17ff193a28720d3e602e1efa7e193a90c2ea72810`。CorePowerSupport 全类反编译与原 pin 逐字节相同，SHA-256 `668b53a6135392e7a4865661d203df9ba8d70723580fe0abbc36a48bf1e2b831`，白名单只登记已审计的精确二进制。',
        '',f"最终写入臂包含 {d['searches']} 条搜索；每条转移 / 子节点 / 物理分叉分别闭合，未归因转移为 0。540 / 540 Python 测试通过，原生报告单列。这是离线原生 DLL TestMode 组件证据，未作 Godot 等价或整局获胜声明。",'',
        '## A0：每个展开节点产生的转移去向','',
        'WorkMetrics 从 30 个字段增至 40 个，补齐交接列出的 10 个计数。原始计数会重叠，以下去向来自诊断对象身份追踪，不把它们直接相加。去向优先顺序是实际展开、明确淘汰原因、未淘汰终局、预算 / 其它剩余；因此死局被层级释放时列入层级释放，终局列指未被前列规则淘汰的终局。根展开、辅助回放与未成节点的转移另列。', '',
        '| 房间 | 展开节点 | 转移 / 节点 | TT 淘汰 | 支配 | 候选截断 | 层级释放 | 展开 | 终局 | 预算 / 其它 | 辅助 / 未成节点 |',
        '|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|']
    for room,r in d['rooms'].items():
        f=r['transition_fates_per_expanded'];tt=f.get('transposition_admission',0)+f.get('transposition_expansion',0)
        vals=[tt,f.get('dominance',0),f.get('candidate_cut',0),f.get('layer_retention',0),f.get('expanded',0),f.get('terminal',0),f.get('frontier_budget_or_other',0),f.get('auxiliary_or_pre_node',0)]
        lines.append(f"| {room} | {integer(r['expanded'])} | {r['transitions_per_expanded']:.4f} | "+' | '.join(f'{v:.4f}'for v in vals)+' |')
    lines+=['','单位均为原始 TransitionCount 的转移 / 展开节点，行内去向之和等于转移 / 节点。物理 Fork、SearchNode 与 TransitionCount 是三个不同集合；不能用 `1 − 1 / 4.73` 直接得出束丢弃率。逐搜索的三份互斥分区及原始数见 copy-summary.json / search-records.csv。','',
        '## B0：复制之后是否真的写入','',
        '按实际成功赋值 / mutation 记录，包括同值 stfld；不使用终值比较作为从未写入证据。Power / DynamicVar / 卡包装用 IL post-stfld，6 个开放泛型 Power 写入方法用固定源码 hook，Forkable 集合 first-COW 与牌堆成功列表修改也用源码 hook。普通、执行续用、选牌续用三种 simulator 分叉均覆盖。', '',
        '“包含分叉内链接”的写入从对象复制注册完成后开始，到搜索结束；“返回后写入”单列为子集。对象构造和复制自身的字段初始化在注册前，不作分支使用。不可把分叉内的牌堆 / observer 链接赋值排除后称其从未写过。', '',
        '| 对象 | 复制个数 | 写过：含分叉链接 | 写过比例 | 分叉返回后写过 |',
        '|---|---:|---:|---:|---:|']
    for kind,n in d['total']['copies'].items():
        w=d['total']['writes_including_fork'][kind];post=d['total']['writes'][kind]
        lines.append(f'| {kind} | {integer(n)} | {integer(w)} | {pct(w/n)} | {integer(post)} |')
    lines+=['',f"Power 引用子图覆盖不确定项：`{d['power_reference_coverage_uncertainty']}`；未覆盖泛型写入方法：`{d['unsupported_writer_methods']}`。覆盖缺口不能作为未写入。不同去向的复制 / 写入原始数与时钟在 JSON 中保留。",'',
        '## B0：P7 调用上下文与机会范围','',
        '| 种子 | Fork / 搜索采样 | Power 复制 / 搜索 | U_lazy：已识别 Power 估计 | U_undo：成本分层迁移估计 | 两项总上限 |',
        '|---|---:|---:|---:|---:|---:|']
    for p in d['p7_sampled_opportunity']:
        seed=next(s for s in ('10101010','1741222413','564940356')if s in p['profile'])
        lo,hi=p['U_lazy_identified_power_estimate_interval']
        power_text=pct(lo)if abs(hi-lo)<1e-10 else f'{pct(lo)}–{pct(hi)}'
        lines.append(f"| {seed} | {pct(p['fork_share'])} | {pct(p['power_share'])} | {power_text} | {pct(p['U_undo_corrected_time_transfer_estimate'])} | ≤ {pct(p['fork_share'])} |")
    lines+=['',
        'U_lazy 的总可识别范围为 0 到表中全部 Fork 份额；其中 Power 列是从原 P7 Power 调用子树成本乘上同种子写入诊断的时间加权未写入比例得到的估计，显式扣除不确定对象图。Card wrapper 已有真实链接写入，不能直接列为“从未写入”；部分 wrapper / collection / history / RNG 调用被 JIT 内联，原采样不能精确拆开。总上限是把全部 Fork 都免费时的保守限额，不是可实现提速。',
        '',
        'U_undo 估计使用成本模式扣除已观察簿记后，各去向实际 Fork 时间的比例，再乘原 P7 Fork 份额；不采用所有 Fork 等成本假设。E 到 P、18 入口到旧 21 入口的迁移是估计，没有置信界。两个 U 有重叠，不能相加。以上采用原交接文档的 P7 SolveCore 采样时间口径，是搜索墙钟成本的代理；没有把插桩 E 核耗时写成 P7 生产速度。', '',
        'CloneModelForSimulation 的上下文进一步区分 Fork、外部 MutablePreview 与外部其它 / 内联。完整分解见 fork-profile-*-final.json；没有把全部 outside_fork 当作首次写入，也没有把缺失的 wrapper 栈名写成零成本。', '',
        '## B0：单次分叉与规模','',
        '成本模式保留各 (copied cards, copied powers) 桶的次数、时间和当前线程分配量。它关闭写入 IL 插桩 / 引用图扫描，同时另减去直接观测的 Copied() 簿记时间与分配，并在计时前创建诊断 Record。修正后仍有 timer / Harmony / JIT / GC 干扰；这是尺度诊断，不是生产计时。下面是按 fork 数加权的描述性平面，无因果或 iid 显著性声明。', '',
        '| 指标 | 每张复制牌增量 | 每个 Power 增量 | 加权 R² |',
        '|---|---:|---:|---:|']
    for label,key in [('修正分配（byte）','bytes_excluding_bookkeeping'),('修正时间（µs）','microseconds_excluding_bookkeeping')]:
        f=d['size_fits'][key]
        if f['identifiable']:lines.append(f"| {label} | {f['per_card']:.2f} | {f['per_power']:.2f} | {f['weighted_R2']:.4f} |")
        else:lines.append(f'| {label} | 不可识别 | 不可识别 | — |')
    lines+=['', '分配量与牌 / Power 数的描述性关系很强；时间拟合 R² 仅约 0.014，且牌数斜率略负，不能据此认定时间呈线性或多牌更快。时间受状态复杂度、JIT / GC 与少量异常长样本影响。牌数指本次真实复制的注册卡包装与额外卡，不一定等于入场牌组张数；Power 数同样指实际复制实例。牌型、Power 类型、状态规模和 GC 未受控，不能把回归斜率当作每多一张牌的必然成本。size-relation.csv 保留全部桶。', '',
        '## 决定与边界','',
        'A0 / B0 的组件采集与有覆盖声明的计量交付完成。没有单靠旧 79% 推断启动 undo。B1 的 < 10% 停止条件尚不能由总 U_lazy 的保守上限证明；这也不等于证明 B1 能获得 ≥ 10% 实际提速。卡包装全部已有分叉内链接写入，不支持直接共享包装的简单方案；优先候选是未写入 Power 的克隆，仍需单独的纯提速原型与实际 P7 验收。没有启动 B1 / B2 / B3，B1 尚未做时不能判其后的剩余 U_undo 门槛。A1 / A2 不属于本次“完成 A0 B0”的范围。', '',
        '三类房间的置换表淘汰占全部转移约 3.6%–9.5%，未触发 A7 的 30% 考虑门槛，本集没有依据开始动作交换性优化。这不是所有种子上的上限。Power 引用图 {} / 泛型遗漏 [] 只说明这批实际入口观察到的形状没有报告缺口，不能替代其它原生模型或未运行机制的审计。', '',
        '7 次诊断构建准备尝试，6 次实际编译成功；5 个版本进入过原生调用。失败保留：v01 泛型 IL 导入失败；v02 首次白名单拒绝（审计后登记）；v03 固定源码锚点准备失败；v04 动态变量 namespace 覆盖遗漏；v05 选牌续用转移归因缺口；v06 已编译但未作原生验收；v07 是本报告最终覆盖版。两次成本请求内存准入拒绝仅补失败请求。控制器相对路径错误未启动原生；跨冻结工作区的比较被请求路径严格校验拒绝，改在同一新冻结工作区实际重跑原始 DLL 臂，未放宽比较规则。', '',
        '原始执行、完整输出、编译日志与状态：iteration-052-runs/final-07b；严格核对：native-equivalence.json；分区检查：accounting-checks.json；机器汇总：copy-summary.json；Python 报告：iteration-052-runs/python-tests-delivery.json。所有失败数据仍保留。']
    (folder/'results.md').write_text('\n'.join(lines)+'\n',encoding='utf-8')
    records=d['search_records']
    with(folder/'search-records.csv').open('w',newline='',encoding='utf-8-sig')as stream:
        writer=csv.writer(stream);writer.writerow(['seed','path','search','room','expanded','transitions','forks','children','node_fates','transition_fates'])
        for r in records:writer.writerow([r[k]for k in ('seed','path','search','room','expanded','transitions','forks','children','node_fates','transition_fates')])
    with(folder/'size-relation.csv').open('w',newline='',encoding='utf-8-sig')as stream:
        fields=list(d['size_relation'][0]);writer=csv.DictWriter(stream,fieldnames=fields);writer.writeheader();writer.writerows(d['size_relation'])
    print({'report':str(folder/'results.md'),'searches':d['searches'],'native_equivalence':True})


if __name__=='__main__':main()
