"""Render the i100 architecture overview and search flow as standalone SVGs.

Standard library only. Editable Mermaid counterparts: i100-architecture.mmd and
i100-search-flow.mmd. Run from any directory:

    python docs/assets/render_i100_diagrams.py
"""
from pathlib import Path
import xml.etree.ElementTree as ET

SVG = "http://www.w3.org/2000/svg"
ET.register_namespace("", SVG)
VERSION = "i100 · 0.4.0b1"
# kind: (header band, accent/stroke, ink)
PALETTE = {
    "entry": ("#dbeafe", "#2563eb", "#1e3a8a"),
    "boundary": ("#f1f5f9", "#64748b", "#0f172a"),
    "store": ("#f1f5f9", "#64748b", "#0f172a"),
    "planner": ("#ccfbf1", "#0d9488", "#134e4a"),
    "clinic": ("#ffe4e6", "#e11d48", "#881337"),
    "signal": ("#ede9fe", "#7c3aed", "#4c1d95"),
    "runtime": ("#fef3c7", "#d97706", "#78350f"),
    "proof": ("#dcfce7", "#16a34a", "#14532d"),
}
LINES = {"slate": "#94a3b8", "rose": "#e11d48", "violet": "#7c3aed", "green": "#16a34a", "teal": "#0d9488"}
LABEL_INK = {"slate": "#475569", "rose": "#be123c", "violet": "#6d28d9", "green": "#15803d", "teal": "#0f766e"}


def el(parent, tag, attrs=None, text=None):
    child = ET.SubElement(parent, "{" + SVG + "}" + tag, {k: str(v) for k, v in (attrs or {}).items()})
    if text is not None:
        child.text = text
    return child


def text_width(text, size):
    """Rough advance width: CJK and full-width symbols are about one em."""
    return sum(size if ord(ch) > 0x2E7F else size * 0.56 for ch in text)


class Canvas:
    def __init__(self, width, height, title, desc):
        self.width, self.height = width, height
        self.root = ET.Element("{" + SVG + "}svg", {"viewBox": f"0 0 {width} {height}", "width": str(width),
                                                   "height": str(height), "role": "img",
                                                   "aria-labelledby": "title desc"})
        el(self.root, "title", {"id": "title"}, title)
        el(self.root, "desc", {"id": "desc"}, desc)
        style = el(self.root, "style")
        style.text = """
    text{font-family:"Segoe UI","Microsoft YaHei","PingFang SC","Noto Sans CJK SC",sans-serif}
    .title{font-size:30px;font-weight:700;fill:#0f172a}
    .sub{font-size:15px;fill:#64748b}
    .tab{font-size:13px;font-weight:700;fill:#ffffff;letter-spacing:.5px}
    .card-title{font-size:17px;font-weight:700}
    .card-line{font-size:14px;fill:#334155}
    .pill{font-size:12.5px;font-weight:600}
    .legend{font-size:13px;fill:#475569}
    .step{font-size:13px;font-weight:700;fill:#ffffff}
    """
        defs = el(self.root, "defs")
        shadow = el(defs, "filter", {"id": "shadow", "x": "-10%", "y": "-10%", "width": "120%", "height": "140%"})
        el(shadow, "feDropShadow", {"dx": "0", "dy": "3", "stdDeviation": "5", "flood-color": "#0f172a",
                                    "flood-opacity": "0.09"})
        backdrop = el(defs, "linearGradient", {"id": "backdrop", "x1": "0", "y1": "0", "x2": "0", "y2": "1"})
        el(backdrop, "stop", {"offset": "0", "stop-color": "#f8fafc"})
        el(backdrop, "stop", {"offset": "1", "stop-color": "#eef2f7"})
        for name, color in LINES.items():
            marker = el(defs, "marker", {"id": "arrow-" + name, "viewBox": "0 0 10 10", "refX": "8.5", "refY": "5",
                                         "markerWidth": "6.5", "markerHeight": "6.5", "orient": "auto-start-reverse"})
            el(marker, "path", {"d": "M 0 0.8 L 9 5 L 0 9.2 z", "fill": color})
        el(self.root, "rect", {"width": width, "height": height, "rx": "24", "fill": "url(#backdrop)"})
        self.labels = []

    def header(self, title, subtitle):
        el(self.root, "text", {"x": "48", "y": "58", "class": "title"}, title)
        el(self.root, "text", {"x": "48", "y": "86", "class": "sub"}, subtitle)
        w = text_width(VERSION, 15) + 40
        x = self.width - 48 - w
        el(self.root, "rect", {"x": x, "y": "34", "width": w, "height": "36", "rx": "18", "fill": "#1d4ed8"})
        el(self.root, "text", {"x": x + w / 2, "y": "57", "text-anchor": "middle", "font-size": "15",
                               "font-weight": "700", "fill": "#ffffff"}, VERSION)

    def layer(self, x, y, w, h, tab, kind):
        band, accent, _ = PALETTE[kind]
        el(self.root, "rect", {"x": x, "y": y, "width": w, "height": h, "rx": "22", "fill": "#ffffff",
                               "fill-opacity": "0.55", "stroke": accent, "stroke-opacity": "0.25",
                               "stroke-width": "1.2"})
        tw = text_width(tab, 13) + 28
        el(self.root, "rect", {"x": x + 24, "y": y - 13, "width": tw, "height": "26", "rx": "13", "fill": accent})
        el(self.root, "text", {"x": x + 24 + tw / 2, "y": y + 4.5, "text-anchor": "middle", "class": "tab"}, tab)

    def card(self, x, y, w, h, title, lines, kind, *, step=None, icon=None, band_height=40):
        band, accent, ink = PALETTE[kind]
        r = 14
        group = el(self.root, "g", {"filter": "url(#shadow)"})
        el(group, "rect", {"x": x, "y": y, "width": w, "height": h, "rx": r, "fill": "#ffffff"})
        el(self.root, "path", {"d": f"M{x},{y + band_height} V{y + r} A{r},{r} 0 0 1 {x + r},{y} H{x + w - r} "
                                    f"A{r},{r} 0 0 1 {x + w},{y + r} V{y + band_height} Z", "fill": band})
        el(self.root, "rect", {"x": x, "y": y, "width": w, "height": h, "rx": r, "fill": "none",
                               "stroke": accent, "stroke-opacity": "0.55", "stroke-width": "1.3"})
        label = (icon + " " if icon else "") + title
        if step:
            el(self.root, "circle", {"cx": x + 26, "cy": y + band_height / 2, "r": "12", "fill": accent})
            el(self.root, "text", {"x": x + 26, "y": y + band_height / 2 + 4.5, "text-anchor": "middle",
                                   "class": "step"}, step)
        el(self.root, "text", {"x": x + w / 2, "y": y + band_height / 2 + 6, "text-anchor": "middle",
                               "class": "card-title", "fill": ink}, label)
        for i, line in enumerate(lines):
            el(self.root, "text", {"x": x + w / 2, "y": y + band_height + 25 + i * 21, "text-anchor": "middle",
                                   "class": "card-line"}, line)

    def hexagon(self, cx, cy, w, h, title, lines, kind):
        band, accent, ink = PALETTE[kind]
        e = 26
        points = (f"{cx - w / 2 + e},{cy - h / 2} {cx + w / 2 - e},{cy - h / 2} {cx + w / 2},{cy} "
                  f"{cx + w / 2 - e},{cy + h / 2} {cx - w / 2 + e},{cy + h / 2} {cx - w / 2},{cy}")
        group = el(self.root, "g", {"filter": "url(#shadow)"})
        el(group, "polygon", {"points": points, "fill": band, "stroke": accent, "stroke-width": "1.4",
                              "stroke-linejoin": "round"})
        top = cy - (len(lines) * 21) / 2 + 1
        el(self.root, "text", {"x": cx, "y": top, "text-anchor": "middle", "class": "card-title", "fill": ink}, title)
        for i, line in enumerate(lines):
            el(self.root, "text", {"x": cx, "y": top + 23 + i * 20, "text-anchor": "middle", "class": "card-line"}, line)

    def edge(self, d, *, color="slate", both=False, dash=False, label=None, at=None, arrow=True):
        attrs = {"d": d, "fill": "none", "stroke": LINES[color], "stroke-width": "1.9",
                 "stroke-linejoin": "round", "stroke-linecap": "round"}
        if arrow:
            attrs["marker-end"] = "url(#arrow-" + color + ")"
        if both:
            attrs["marker-start"] = "url(#arrow-" + color + ")"
        if dash:
            attrs["stroke-dasharray"] = "6 6"
        el(self.root, "path", attrs)
        if label:
            self.labels.append((label, at, color))

    def pill(self, text, x, y, color="slate"):
        w = text_width(text, 12.5) + 20
        el(self.root, "rect", {"x": x - w / 2, "y": y - 12, "width": w, "height": "24", "rx": "12",
                               "fill": "#ffffff", "stroke": LINES[color], "stroke-opacity": "0.6"})
        el(self.root, "text", {"x": x, "y": y + 4.5, "text-anchor": "middle", "class": "pill",
                               "fill": LABEL_INK[color]}, text)

    def legend(self, y, items, note=None):
        widths = [46 + text_width(text, 13) + 26 for text, _, _ in items]
        x = (self.width - sum(widths)) / 2
        for (text, color, dash), w in zip(items, widths):
            attrs = {"d": f"M{x} {y} H{x + 34}", "stroke": LINES[color], "stroke-width": "2.2", "fill": "none",
                     "stroke-linecap": "round"}
            if dash:
                attrs["stroke-dasharray"] = "6 5"
            el(self.root, "path", attrs)
            el(self.root, "text", {"x": x + 44, "y": y + 4.5, "class": "legend"}, text)
            x += w
        if note:
            el(self.root, "text", {"x": self.width / 2, "y": y + 32, "text-anchor": "middle", "class": "sub"}, note)

    def write(self, target):
        for text, (x, y), color in self.labels:      # labels last: they sit above every line
            self.pill(text, x, y, color)
        target.parent.mkdir(parents=True, exist_ok=True)
        ET.ElementTree(self.root).write(target, encoding="utf-8", xml_declaration=True)
        print(target.name)


def architecture(target):
    c = Canvas(1200, 1175, "SpireExact i100 架构概览",
               "SpireBoard 与命令行经资源边界进入 Python 整局搜索。关口诊所判断卡住的首领关口是根不对还是深度不够，"
               "决定回上一幕换选项还是在关口附近加深；派发与调速把请求交给常驻原生 worker，CombatSolver 规划战斗，"
               "游戏 DLL 执行规则；获胜候选在独立新进程中重放后生成证书。")
    c.header("一个种子，贯穿整局的搜索", "SpireExact · 全信息整局搜索 · 关口诊所 · 原生重放验证")
    c.layer(36, 122, 1128, 262, "01 · 入口与资源", "entry")
    c.card(220, 150, 320, 86, "SpireBoard", ["新建 · 暂停 · 继续 · 退出"], "entry", icon="▣")
    c.card(660, 150, 320, 86, "命令行", ["单种子 · 1–45 分钟 · 默认 i100"], "entry", icon="›_")
    c.card(380, 274, 440, 86, "Windows Job · 资源边界", ["7 个 worker · 14 GiB 工作集 + 2 GiB 预留"], "boundary")
    c.layer(36, 418, 1128, 344, "02 · Python 整局规划", "planner")
    c.card(66, 454, 320, 108, "前缀与结果档案", ["完整字节前缀树", "检查点 · 请求缓存"], "store")
    c.card(440, 454, 320, 108, "宏观搜索主循环", ["有序吸收 · 精英与谱系", "聚焦 · 探索 · 根策略"], "planner")
    c.card(814, 454, 320, 108, "关口诊所", ["stem × 首领关口", "根不对 / 深度不够 / 待定"], "clinic", icon="✚")
    c.card(66, 616, 320, 108, "关口模型与重试", ["每个首领关口的结果模型", "DEPTH 重试优先 · 份额 ≤ 35%"], "planner")
    c.card(440, 616, 320, 108, "派发与调速", ["核心 → 辅助 → 重试 → 提案", "辅助份额 10% · 争夺后 30%"], "signal")
    c.card(814, 616, 320, 108, "结构先验", ["社区攻略方向 · 本局后验", "选项效应 → 修复方向档位"], "signal")
    c.layer(36, 798, 1128, 188, "03 · .NET 原生执行", "runtime")
    c.card(66, 836, 320, 108, "CombatSolver", ["束搜索战斗计划", "回合开局选择随计划执行"], "runtime")
    c.card(440, 836, 320, 108, "7 个常驻 worker", ["合法菜单 · 整局 rollout", "检查点恢复 · F1 路线部署"], "runtime")
    c.card(814, 836, 320, 108, "游戏 DLL", ["用户安装的游戏", "离线 TestMode · 原生规则"], "runtime")
    c.layer(36, 1018, 1128, 112, "04 · 独立验证", "proof")
    c.card(220, 1040, 360, 74, "独立新进程重放", ["从开局逐动作 · 无 advisor / 检查点 / 缓存"], "proof", band_height=34)
    c.card(700, 1040, 360, 74, "结果与证书", ["路线 · 身份 · 诊所记录 · SpireBoard 展示"], "store", band_height=34)
    c.edge("M380 236 L470 274")
    c.edge("M820 236 L730 274")
    c.edge("M600 360 V454")
    c.edge("M386 508 H440", both=True)
    c.edge("M760 508 H814", color="rose", both=True, label="判定 → 份额", at=(787, 438))
    c.edge("M974 616 V562", color="violet", dash=True)
    c.edge("M386 650 C420 650 430 600 470 562", both=True)
    c.edge("M600 562 V616", both=True)
    c.edge("M600 724 V836", both=True, label="JSON 请求 / 真实执行结果", at=(600, 780))
    c.edge("M386 890 H440", both=True)
    c.edge("M760 890 H814", both=True)
    c.edge("M760 540 H787 V1002 H400 V1040", color="green", label="获胜候选", at=(787, 1002))
    c.edge("M580 1077 H700", color="green")
    c.legend(1152, [("真实请求与结果", "slate", False), ("诊所判定", "rose", False), ("排序与先验信号", "violet", True),
                    ("验证链路", "green", False)])
    c.write(target)


def search_flow(target):
    c = Canvas(1200, 1220, "SpireExact i100 搜索流程",
               "每一轮派发核心工作、受限的辅助工作、关口重试与聚焦提案；worker 完整执行后协调器吸收结果。"
               "首领关口的失败交给关口诊所：根不对时回上一幕换选项，深度不够时在关口附近加深并优先重试。"
               "获胜候选独立重放后生成证书。")
    c.header("从一次评估到下一批搜索", "SpireExact · 搜索循环 · 根不对还是深度不够")
    c.card(300, 116, 600, 108, "派发", ["核心工作 → 辅助工作（调速器限额）", "→ 关口重试（DEPTH 先，≤ 35%）→ 聚焦 / 探索提案"],
           "planner", step="1")
    c.card(300, 262, 600, 108, "原生执行", ["worker 重放前缀或恢复检查点", "地图 · 奖励 · 商店 · 火堆 · 战斗（CombatSolver）"],
           "runtime", step="2")
    c.card(300, 408, 600, 108, "吸收结果", ["档案与检查点 · 关口模型", "诊所登记：stem、关口、入场、重试"], "store", step="3")
    c.hexagon(600, 604, 380, 96, "轨迹结局", ["获胜 · 首领关口失败 · 其他"], "boundary")
    c.card(56, 566, 236, 78, "独立新进程重放", ["原生终局 → 证书"], "proof", band_height=36)
    c.card(908, 566, 236, 78, "常规聚焦 / 探索", ["关口前的死亡或预算"], "planner", band_height=36)
    c.hexagon(600, 790, 560, 116, "关口诊所判定", ["stem × 首领关口 · ≥ 6 个不同入场",
                                                 "进度饱和 · 上端点界 · 重试提升 · 满血外推 · 结构缺口"], "clinic")
    c.card(40, 924, 340, 132, "根不对", ["85% 名额回上一幕换选项", "新 stem 优先 · 修复方向档位", "兄弟 stem 都不行 → 再上一幕"],
           "clinic", icon="↺")
    c.card(430, 924, 340, 132, "待定", ["入场不足：在关口附近加深", "≥ 6 个入场：34% 回退", "其余在关口附近"], "boundary", icon="…")
    c.card(820, 924, 340, 132, "深度不够", ["90% 名额在关口附近加深", "关口重试份额 35% 且优先", "资源型偏向火堆 · 地图 · 商店"],
           "planner", icon="↓")
    c.edge("M600 224 V262")
    c.edge("M600 370 V408")
    c.edge("M600 516 V556")
    c.edge("M410 604 H292", color="green", label="获胜候选", at=(351, 604))
    c.edge("M790 604 H908", label="其他", at=(849, 604))
    c.edge("M600 652 V732", color="rose", label="首领关口失败", at=(600, 692))
    c.edge("M320 790 C230 790 210 840 210 924", color="rose")
    c.edge("M600 848 V924")
    c.edge("M880 790 C970 790 990 840 990 924", color="teal")
    # One return line into the next round; the other sources join it.
    c.edge("M210 1056 V1098 H1166 V170 H900", dash=True, label="下一轮", at=(1166, 390))
    for x in (600, 990):
        c.edge(f"M{x} 1056 V1098", dash=True, arrow=False)
    c.edge("M1144 605 H1166", dash=True, arrow=False)
    c.legend(1160, [("真实执行与吸收", "slate", False), ("诊所判定", "rose", False), ("验证链路", "green", False),
                    ("下一轮派发", "slate", True)],
             note="判定只改变提案顺序和份额；所有真实菜单分支都保留，误判的代价是时间。")
    c.write(target)


if __name__ == "__main__":
    here = Path(__file__).resolve().parent
    architecture(here / "i100-architecture.svg")
    search_flow(here / "i100-search-flow.svg")
