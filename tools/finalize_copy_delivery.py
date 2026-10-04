"""Record completion only after actual native/fate audits pass."""
from pathlib import Path
import hashlib
import sys

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from spire_exact.planning.io import read_json,write_json


def main():
    artifact=Path(read_json(ROOT/'storage-policy.json')['artifact_roots'][0])/'iteration-052'
    state=read_json(artifact/'final-07b/status.json');native=read_json(artifact/'final-07b/native-equivalence.json')
    summary=read_json(ROOT/'experiments/iteration-052/copy-summary.json');tests=read_json(artifact/'python-tests-delivery.json')
    if state['state']!='completed'or not native['passed']or not summary['accounting_passed']or not tests['successful']:raise ValueError('Completion evidence missing')
    variant_path=artifact/'copy-diagnostics-07/variant.json';variant=read_json(variant_path)
    variant.update(native_verified=True,native_verification_scope='18 retained Low/10000 node entries, original/off/writes/costs full exported game bytes and node equivalence; separate fate/write closure. Not production promotion.',promoted=False)
    write_json(variant_path,variant)
    audit=ROOT/'experiments/iteration-052/block-contract-audit/report.json'
    source=audit.parent/'copy-diagnostics-07.cs';digest=hashlib.sha256(source.read_bytes()).hexdigest()
    if digest!='668b53a6135392e7a4865661d203df9ba8d70723580fe0abbc36a48bf1e2b831':raise ValueError('Protected contract changed')
    write_json(audit,{'protected_class_sha256':digest,'solver_sha256':variant['solver_sha256'],'source_pin':variant['upstream_commit'],
        'game_sha256':variant['game_sha256'],'matches_pinned_class':True,'native_verified':True,'scope':variant['native_verification_scope']})
    record={'a0':'complete for declared component scope','b0':'measurement and bounded opportunity delivered','native_requests_final':72,
        'distinct_entries':18,'native_equivalence':True,'accounting_passed':True,'python_tests':tests['passed'],
        'source_version':read_json(ROOT/'experiments/frozen-i052-copy07/freeze.json')['source_version'],
        'diagnostic_solver_sha256':variant['solver_sha256'],'report':str(ROOT/'experiments/iteration-052/results.md'),
        'limitations':['P7 sampled-time denominator and E-to-P transfer, not measured production speedup','total U_lazy not exactly isolated; whole-Fork conservative upper bound','no B1/B2/B3 or default/search strategy changes']}
    write_json(ROOT/'experiments/iteration-052/completion.json',record)
    status='''# iteration-052 当前状态

2026-10-02：用户要求的 A0 / B0 组件诊断已交付，见 results.md / copy-summary.json / completion.json。A0 导出 40 个计数并用对象身份得到互斥去向；B0 完成实际写入、规模关系、三份保留 P7 剖面的上下文分解及两个 U 的估计 / 保守范围。总 U_lazy 尚不能精确隔离，不将宽上限当作现实提速，也没有据此推广优化。

最终 final-07b：18 个入口 × 原始 DLL / off / writes / costs，共 72 次实际原生执行，完整导出游戏字节、动作、节点、请求输入、时钟裕度、离线禁用状态全部通过；18 条 writes 搜索的转移 / 节点 / 分叉分别闭合，未归因转移为 0，含选牌续用。540 项 Python 测试通过。最终 DLL 已实际编译并原生核对，不是源码或启动状态。

分叉后卡包装 100% 有真实链接赋值，Power 16.28% 写过（含分叉内链接），不能把前者的初始化写入排除后叫从未写过。已识别的未写入 Power 复制占旧 P7 搜索采样估计 3.96% / 6.10% / 9.94%；unexpanded Fork 的成本迁移估计 18.01%–23.76%。总 Fork 上限 23.39%–29.38%，两个 U 不可相加。分配尺度拟合 R² 0.960；时间拟合 R² 0.014，不能声称时间线性。具体限制见报告。

采集只使用 1 个 E worker；P 核剖面来自保留数据，未新增整局作业。B1 / B2 / B3 未做；A1 / A2 不属于本次 A0 B0 范围；默认预算、策略和关口计划未调整。全部失败、覆盖修正、内存准入拒绝及重跑数据均保留。WMI 控制器已 completed，没有本批次残留求解作业。
'''
    (ROOT/'experiments/iteration-052/status.md').write_text(status,encoding='utf-8')
    progress=f'''

## 2026-10-02：iteration-052，A0 / B0 组件诊断交付

用户要求继续完成 A0 / B0。最终 frozen-i052-copy07，源版本 {record['source_version']}，诊断 solver SHA-256 {variant['solver_sha256']}。假设：展开数低估真实分支复制量；先测去向与写入，再决定是否做惰性复制。改动为默认关闭计数与项目自有诊断构建，未改变搜索策略、节点预算默认或关口计划。7 次构建准备、6 次编译成功、5 个版本进入原生调用；全部失败与覆盖修正保留。

协议：三种子 10101010 / 1741222413 / 564940356，18 个保留入口，Low / DOP 1 / 10000 节点 / gate none / 600000 ms 保险预算，1 个 E worker、RSS 1536 MiB、实际 GC 堆 1152 MiB；DEV 仅用于诊断，不选 A 的规则。final-07b 原始 / off / writes / costs 共 72 次原生执行，完整游戏导出字节、动作、节点、请求输入、时钟、离线全部一致，三种分叉路径的转移 / 子节点 / 物理分叉分别闭合。此前 A0 输出哨兵 72 请求和 v05 一致性 72 请求也通过；v05 的 8405 次选牌续用归因缺口已由 v07 实际重跑修正。540 / 540 Python 测试通过。

结果：79810 个展开、377842 次转移、379810 次物理分叉；置换表淘汰占转移 3.6%–9.5%，未触发 A7 门槛。10631784 个卡包装全部有分叉内链接写入；3049514 个 Power 中 496589 个写过（16.28%）。三份旧 P7 warm profile 的 Fork 份额 23.39%–29.38%；未写入 Power 成本迁移估计 3.96% / 6.10% / 9.94%，U_undo 成本分层迁移估计 18.01%–23.76%。这是采样口径与 E 比例迁移，非实际优化收益；总 U_lazy 只能保留宽上限，不能从它证明 B1 的 10% 停止门槛。修正分配拟合 R² 0.960；时间 R² 0.014，不支持时间线性。

决定：A0 / B0 的组件计量与有覆盖声明的机会范围交付；不启动 B1 / B2 / B3，不作默认推广。下一候选是未写入 Power 克隆，仍须独立纯提速原型及 P7 验收。组件实验没有首次整局胜利时间、KM 或整局显著性检验。细节、CSV、JSON 与失败尝试在 experiments/iteration-052/results.md 及 iteration-052-runs/final-07b；保留报告的限定，不宣称 Godot 等价或生产关口比例。
'''
    with(ROOT/'PROGRESS.md').open('a',encoding='utf-8')as stream:stream.write(progress)
    print({'completed':True,'a0':record['a0'],'b0':record['b0'],'native_verified':True})


if __name__=='__main__':main()
