"""Render the public i082 overview as a standalone SVG using Python's standard library.

The editable Mermaid counterpart is i082-architecture.mmd.
Run from any directory: python docs/assets/render_i082_architecture.py
"""
from pathlib import Path
import xml.etree.ElementTree as ET

SVG = "http://www.w3.org/2000/svg"
ET.register_namespace("", SVG)

def el(parent, tag, attrs=None, text=None):
    child = ET.SubElement(parent, "{"+SVG+"}"+tag, {k: str(v) for k,v in (attrs or {}).items()})
    if text is not None:
        child.text = text
    return child

def render(target):
    root = ET.Element("{"+SVG+"}svg", {"viewBox":"0 0 1200 945","width":"1200","height":"945",
                                      "role":"img","aria-labelledby":"title desc"})
    el(root, "title", {"id":"title"}, "SpireExact i082 Beta 架构概览")
    el(root, "desc", {"id":"desc"}, "SpireBoard 与 CLI 经资源边界进入 Python 整局搜索。常驻原生 worker 使用战斗候选和游戏 DLL执行，合成探针只提供排序信号，获胜候选独立重放后生成证书。")
    style = el(root, "style")
    style.text = """
    text{font-family:"Segoe UI","Microsoft YaHei",sans-serif}
    .title{font-size:28px;font-weight:700;fill:#0f172a}
    .sub{font-size:15px;fill:#64748b}
    .layer{font-size:14px;font-weight:700;letter-spacing:1px}
    .node-title{font-size:19px;font-weight:650}
    .node-sub{font-size:15px}
    .edge-label{font-size:13px;font-weight:500;fill:#64748b}
    """
    defs = el(root,"defs")
    for name,color in [("slate","#64748b"),("purple","#9333ea"),("green","#15803d")]:
        marker = el(defs,"marker",{"id":name,"viewBox":"0 0 10 10","refX":"9","refY":"5",
                                  "markerWidth":"7","markerHeight":"7","orient":"auto-start-reverse"})
        el(marker,"path",{"d":"M 0 0 L 10 5 L 0 10 z","fill":color})
    el(root,"rect",{"width":"1200","height":"945","rx":"20","fill":"#ffffff"})
    el(root,"text",{"x":"48","y":"53","class":"title"}, "一个种子，贯穿整局的搜索")
    el(root,"text",{"x":"48","y":"80","class":"sub"}, "SpireExact · full-information search · native replay")
    el(root,"rect",{"x":"935","y":"30","width":"217","height":"38","rx":"19","fill":"#eff6ff"})
    el(root,"text",{"x":"1043.5","y":"55","text-anchor":"middle","font-size":"16","font-weight":"600","fill":"#1d4ed8"}, "i082 Beta · 0.3.1b1")
    def layer(x,y,w,h,label,color):
        el(root,"rect",{"x":x,"y":y,"width":w,"height":h,"rx":"20","fill":color,"stroke":"#e2e8f0"})
        el(root,"text",{"x":x+22,"y":y+27,"class":"layer","fill":"#475569"},label)
    layer(36,326,1128,185,"02  /  PYTHON · 整局规划","#f8fafc")
    layer(36,548,1128,180,"03  /  .NET · 原生执行","#fffbeb")
    el(root,"text",{"x":"58","y":"113","class":"layer","fill":"#2563eb"},"01  /  入口与资源")
    el(root,"text",{"x":"58","y":"794","class":"layer","fill":"#15803d"},"04  /  独立验证")
    colors={
        "entry":("#dbeafe","#2563eb","#172554"),
        "boundary":("#f1f5f9","#64748b","#0f172a"),
        "planner":("#ccfbf1","#0f766e","#134e4a"),
        "signal":("#f3e8ff","#9333ea","#581c87"),
        "runtime":("#fef3c7","#d97706","#78350f"),
        "proof":("#dcfce7","#15803d","#14532d"),
        "store":("#ffffff","#94a3b8","#0f172a"),
    }
    def node(x,y,w,h,title,lines,kind):
        fill,stroke,ink=colors[kind]
        el(root,"rect",{"x":x,"y":y,"width":w,"height":h,"rx":"14","fill":fill,"stroke":stroke,"stroke-width":"1.5"})
        el(root,"text",{"x":x+w/2,"y":y+30,"text-anchor":"middle","class":"node-title","fill":ink},title)
        for i,line in enumerate(lines):
            el(root,"text",{"x":x+w/2,"y":y+53+i*21,"text-anchor":"middle","class":"node-sub","fill":ink},line)
    def edge(d,*,color="slate",both=False,dash=False,label=None,x=0,y=0):
        attrs={"d":d,"fill":"none","stroke":{"slate":"#64748b","purple":"#9333ea","green":"#15803d"}[color],
               "stroke-width":"1.6","marker-end":"url(#"+color+")","stroke-linejoin":"round"}
        if both: attrs["marker-start"]="url(#"+color+")"
        if dash: attrs["stroke-dasharray"]="5 5"
        el(root,"path",attrs)
        if label: el(root,"text",{"x":x,"y":y,"class":"edge-label","text-anchor":"middle"},label)
    # Nodes follow the same eleven components as the Mermaid overview.
    node(240,104,300,78,"SpireBoard",["新建 · 暂停 · 继续 · 退出"],"entry")
    node(660,104,300,78,"命令行",["单种子 · 默认 30 分钟"],"entry")
    node(390,228,420,72,"Windows Job · 资源边界",["8 同型逻辑 CPU · 7 worker · 14 + 2 GiB"],"boundary")
    node(75,383,270,94,"前缀与结果档案",["完整字节身份","检查点 · 请求缓存"],"store")
    node(420,383,360,94,"宏观搜索主循环",["聚焦 · 探索 · 关口重试","有序吸收 · 评估账本"],"planner")
    node(855,383,270,94,"模型与合成探针",["F1 / F2 就绪度","成对选牌 · 只提供排序信号"],"signal")
    node(75,600,270,94,"CombatSolver",["束搜索战斗候选","F1 best / F2 first_win"],"runtime")
    node(420,600,360,94,"7 个常驻 worker",["合法菜单 · 整局 rollout","预测候选 → 原生执行"],"runtime")
    node(855,600,270,94,"游戏 DLL",["用户安装的 v0.111.0","离线 TestMode"],"runtime")
    node(420,790,360,90,"独立新进程重放",["从开局逐动作执行","无 advisor / 检查点 / 缓存"],"proof")
    node(855,790,270,90,"结果与证书",["路线 · 身份 · 工作量","SpireBoard 实时展示"],"store")
    edge("M390 182 L505 228")
    edge("M810 182 L695 228")
    edge("M600 300 V383")
    edge("M345 430 H420",both=True)
    edge("M855 430 H780",color="purple",dash=True)
    edge("M600 477 V600",both=True,label="JSON 请求 / 真实执行结果",x=732,y=539)
    edge("M345 647 H420",both=True)
    edge("M780 647 H855",both=True)
    edge("M728 477 V523 H1150 V756 H600 V790",label="获胜候选",x=1062,y=748)
    edge("M780 835 H855",color="green")
    el(root,"text",{"x":"600","y":"911","text-anchor":"middle","class":"sub"},
       "真实执行进入路线与证据；synthetic 信号只影响算力分配。")
    target.parent.mkdir(parents=True,exist_ok=True)
    ET.ElementTree(root).write(target,encoding="utf-8",xml_declaration=True)
    print(target.name)

if __name__=="__main__":
    render(Path(__file__).resolve().with_name("i082-architecture.svg"))

