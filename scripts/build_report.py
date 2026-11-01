# -*- coding: utf-8 -*-
"""FSA 装载优化方案报告生成器（可复用）。

从流水线产物生成单文件 HTML 报告（报告型），含结论、柜数对比、
柜底裸地利用机制、空间机会点核算、逐柜明细 + 3D 等轴测线框、L1/L2/L3 校验状态、选型建议。

数据源（均由 pipeline.py 产出）：
  --plan    plan.json        结构: { meta{generated,total_cartons,color_map,...},
                                     pal{L,W,H}|可选, cons, containers[{idx,cartons,cbm,
                                     util,npallets,hmax,per_sku,batch,...}], validation{passed,errors,warns} }
  --scenes  scene_data.json  结构: { ct{L,W,H,doorH,vol}, items[{idx,batch,cartons,cbm,
                                     util,npallets,boxes[{x,y,z,w,d,h,kind,sku,c}]}], batches }

健壮性：
  * 无托盘模式 plan.json 不含 "pal" 字段时，柜底裸地相关章节自动降级为说明性注释，不崩溃。
  * 批次名自适应（不硬编码 "批次1/批次2"），单批次时按 "全部" 处理。
  * 容器缺 npallets 字段时按 0 处理。

用法:
  python build_report.py --plan <plan.json> --scenes <scene_data.json> --out <report.html>

  # 对比模式: 额外传入另一模式 (如无托盘) 的产物, 报告中并列呈现两方案
  python build_report.py --plan <B.plan.json> --scenes <B.scene_data.json> \
      --alt-plan <A.plan.json> --alt-scenes <A.scene_data.json> \
      --alt-name "模式A 无托盘" --out <report.html>

典型位置：
  SKILL_DIR/scripts/build_report.py
"""
import json
import os
import argparse

import i18n
from report_text import RPT

# ---------- 等轴测投影 ----------
def p_iso(x, y, z, ox, oy, s):
    ex, ey, ez = (0.866, 0.5), (-0.866, 0.5), (0.0, -1.0)
    return (ox + (x * ex[0] + y * ey[0]) * s, oy + (x * ex[1] + y * ey[1] + z * ez[1]) * s)

def box3d(x, y, z, w, d, h, ox, oy, s, fill, stroke="#334155", sw=0.5, dashed=False):
    v = {}
    for (a, b, c) in [(0, 0, 0), (w, 0, 0), (w, d, 0), (0, d, 0),
                      (0, 0, h), (w, 0, h), (w, d, h), (0, d, h)]:
        v[(a, b, c)] = p_iso(x + a, y + b, z + c, ox, oy, s)
    out = []
    for (pts, sh) in [([(0, 0, h), (w, 0, h), (w, d, h), (0, d, h)], 1.00),
                      ([(0, 0, 0), (w, 0, 0), (w, 0, h), (0, 0, h)], 0.80),
                      ([(w, 0, 0), (w, d, 0), (w, d, h), (w, 0, h)], 0.62)]:
        pp = " ".join(f"{v[p][0]:.1f},{v[p][1]:.1f}" for p in pts)
        da = ' stroke-dasharray="5,3"' if dashed else ""
        out.append(f'<polygon points="{pp}" fill="{fill}" fill-opacity="{0.55*sh:.3f}" '
                   f'stroke="{stroke}" stroke-width="{sw}"{da} stroke-linejoin="round"/>')
    return "".join(out)

def frame(L, W, H, ox, oy, s, doorH):
    o = []
    c = [(0, 0), (L, 0), (L, W), (0, W)]
    p = [p_iso(a, b, 0, ox, oy, s) for a, b in c]
    o.append('<polygon points="' + " ".join(f"{q[0]:.1f},{q[1]:.1f}" for q in p) +
             '" fill="none" stroke="#0f172a" stroke-width="1.5"/>')
    for (a, b) in c:
        q1 = p_iso(a, b, 0, ox, oy, s); q2 = p_iso(a, b, H, ox, oy, s)
        o.append(f'<line x1="{q1[0]:.1f}" y1="{q1[1]:.1f}" x2="{q2[0]:.1f}" y2="{q2[1]:.1f}" '
                 f'stroke="#0f172a" stroke-width="1.2"/>')
    p = [p_iso(a, b, H, ox, oy, s) for a, b in c]
    o.append('<polygon points="' + " ".join(f"{q[0]:.1f},{q[1]:.1f}" for q in p) +
             '" fill="none" stroke="#0f172a" stroke-width="1.5"/>')
    q2 = p_iso(0, 0, doorH, ox, oy, s); q3 = p_iso(0, W, doorH, ox, oy, s)
    o.append(f'<line x1="{q2[0]:.1f}" y1="{q2[1]:.1f}" x2="{q3[0]:.1f}" y2="{q3[1]:.1f}" '
             f'stroke="#dc2626" stroke-width="2.4" stroke-dasharray="6,3"/>')
    return "".join(o)

def svg_cab(ct, item, VW=980, VH=440):
    L, W, H = ct["L"], ct["W"], ct["H"]
    PAD = 70
    s = min((VW - 2*PAD)/((L+W)*0.866), (VH - 2*PAD - 12)/((L+W)*0.5 + H))
    pts = [p_iso(a, b, c, 0, 0, 1) for a in (0, L) for b in (0, W) for c in (0, H)]
    ox = PAD - min(p[0] for p in pts)*s
    oy = PAD - min(p[1] for p in pts)*s
    g = [frame(L, W, H, ox, oy, s, ct["doorH"])]
    # 托盘先画 (棕), 再画货箱, 按 x 降序保证遮挡正确
    items = []
    for b in item["boxes"]:
        if b["kind"] == "pallet":
            items.append((b, "#b45309"))
        elif b["kind"] == "bare":
            items.append((b, "#ea580c"))
        else:
            items.append((b, b.get("c", "#64748b")))
    for b, col in sorted(items, key=lambda t: (-t[0]["x"], t[0]["y"])):
        dashed = (b["kind"] == "bare")
        g.append(box3d(b["x"], b["y"], b["z"], b["w"], b["d"], b["h"], ox, oy, s,
                       col, stroke=("#ea580c" if dashed else "#334155"),
                       sw=(1.3 if dashed else 0.5), dashed=dashed))
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {VW} {VH}">'
            f'<rect width="{VW}" height="{VH}" fill="#f8fafc"/>' + "".join(g) + '</svg>')



def rt(key, **vars):
    """取报告文案 (按当前语言), 支持占位符替换。"""
    lang = i18n.get_lang()
    src = RPT.get(lang, RPT["zh"]).get(key)
    if src is None:
        src = RPT["zh"].get(key, key)
    if isinstance(src, list):
        return [s.format(**vars) if vars else s for s in src]
    return src.format(**vars) if vars else src


def build(plan, scenes, out_path, alt_plan=None, alt_scenes=None, alt_name="模式A 无托盘",
          title=None, subtitle=None):
    ct = scenes["ct"]
    items = scenes["items"]
    L, W, H, doorH, vol = ct["L"], ct["W"], ct["H"], ct["doorH"], ct["vol"]
    pal = plan.get("pal")                 # 无托盘模式无此字段
    # 模式判据以 cons.mode 为准 (plan.json 顶层可能仍带 pal 占位)
    has_pal = plan.get("cons", {}).get("mode", "pallet") == "pallet"
    pl = pw = ph = 0
    if has_pal and pal:
        pl, pw, ph = pal["L"], pal["W"], pal["H"]

    # ---- 批次分组（自适应，不硬编码 批次1/批次2）----
    batch_names = list(dict.fromkeys(c.get("batch", "全部") for c in plan["containers"]))
    if batch_names == ["全部"]:
        b1, b2 = plan["containers"], []
    else:
        b1 = [c for c in plan["containers"] if c.get("batch") == batch_names[0]]
        b2 = [c for c in plan["containers"] if c.get("batch") != batch_names[0]]
    primary_batch = batch_names[0]
    b1_cartons = sum(c["cartons"] for c in b1)
    b1_cbm = round(sum(c["cbm"] for c in b1), 1)
    n_b1 = len(b1) or 1
    b1_util = round(sum(c["cbm"] for c in b1) / (n_b1 * vol) * 100, 1)
    b2_cartons = sum(c["cartons"] for c in b2)
    n_b2 = len(b2) or 1
    b2_util = round(sum(c["cbm"] for c in b2) / (n_b2 * vol) * 100, 1) if b2 else None
    total_cartons = plan["meta"]["total_cartons"]

    # ---- 备选方案 (对比模式) 的汇总指标 ----
    alt = None
    if alt_plan and alt_scenes:
        aplan = alt_plan
        ab = list(dict.fromkeys(c.get("batch", "全部") for c in aplan["containers"]))
        ab1 = ([c for c in aplan["containers"] if c.get("batch") == ab[0]]
               if ab != ["全部"] else aplan["containers"])
        ab2 = ([c for c in aplan["containers"] if c.get("batch") != ab[0]]
               if ab != ["全部"] else [])
        a_util = round(sum(c["cbm"] for c in ab1) / ((len(ab1) or 1) * vol) * 100, 1)
        a_util2 = (round(sum(c["cbm"] for c in ab2) / ((len(ab2) or 1) * vol) * 100, 1)
                   if ab2 else None)
        _alt_is_pal = aplan.get("cons", {}).get("mode") == "pallet"
        if i18n.get_lang() == "en":
            _alt_name = ("Mode B - palletized" if _alt_is_pal else "Mode A - no pallet")
        else:
            _alt_name = alt_name
        alt = dict(name=_alt_name, n1=len(ab1), n2=len(ab2),
                   cartons1=sum(c["cartons"] for c in ab1),
                   cartons2=sum(c["cartons"] for c in ab2),
                   cbm1=round(sum(c["cbm"] for c in ab1), 1),
                   util1=a_util, util2=a_util2,
                   total=len(aplan["containers"]),
                   total_cartons=aplan["meta"]["total_cartons"],
                   generated=aplan["meta"].get("generated", ""),
                   passed=aplan["validation"].get("passed"),
                   errors=aplan["validation"].get("errors", 0),
                   warns=aplan["validation"].get("warns", 0),
                   per_cab=[dict(idx=c["idx"], cartons=c["cartons"], cbm=c["cbm"],
                                 util=c["util"], hmax=c.get("hmax", 0),
                                 stacks=len(c.get("pallets", [])))
                            for c in ab1])

    # ---- 裸地统计 ----
    bare_total = sum(1 for it in items for b in it["boxes"] if b["kind"] == "bare")
    bare_by_cab = {}
    for it in items:
        if it.get("batch") == primary_batch:
            bare_by_cab[it["idx"]] = sum(1 for b in it["boxes"] if b["kind"] == "bare")

    # 几何事实: 柜底铺满托盘位后的剩余裸地 (仅托盘模式有意义)
    # —— 全部由方案数据推导, 不写死任何具体数值
    pal_floor = pl * pw
    cont_floor = L * W / 1e6          # m^2 (mm->m)
    n_slots = 0                       # 每柜托盘位数 (取自实际托盘数)
    if has_pal:
        n_slots = max([c.get("npallets", 0) for c in b1] or [0])
    pal_area_m2 = pal_floor * n_slots / 1e6 if has_pal else 0.0
    bare_m2 = round(cont_floor - pal_area_m2, 2) if has_pal else 0.0
    bare_pct = round(bare_m2 / cont_floor * 100, 1) if has_pal and cont_floor else 0.0
    # 分层填充率 (每层箱面积 / 托盘面积, 对所有层取平均) —— 动态计算
    fill_avg = 0.0
    if has_pal and pal_floor:
        fills = []
        for c in b1:
            for pl in c.get("pallets", []):
                lays = []
                for l in pl.get("layers", []):
                    used = sum(lw * lh for (_lx, _ly, lw, lh) in l.get("cells", []))
                    lays.append(used / pal_floor)
                if lays:
                    fills.append(sum(lays) / len(lays))
        fill_avg = round(sum(fills) / len(fills) * 100, 1) if fills else 0.0
    # 门高死区
    dead_h = H - doorH

    v = plan["validation"]
    gen = plan["meta"].get("generated", "")

    # ---- HTML ----
    css = """
* { box-sizing: border-box }
body { margin: 0; font-family: Inter,"Segoe UI",system-ui,-apple-system,"PingFang SC","Microsoft YaHei",sans-serif;
 background: #f1f5f9; color: #0f172a; line-height: 1.65; font-size: 14px }
.wrap { max-width: 1180px; margin: 0 auto; padding: 28px 20px 70px }
header { background: linear-gradient(135deg,#0f172a,#1e3a8a); color: #fff; padding: 30px 34px; border-radius: 14px; margin-bottom: 22px }
header h1 { margin: 0 0 6px; font-size: 25px } header p { margin: 0; opacity: .88; font-size: 13.5px }
h2 { font-size: 19px; margin: 34px 0 12px; padding-left: 11px; border-left: 4px solid #2563eb }
h3 { font-size: 15.5px; margin: 22px 0 9px; color: #1e293b }
.card { background: #fff; border: 1px solid #e2e8f0; border-radius: 12px; padding: 20px 22px; margin-bottom: 16px; box-shadow: 0 1px 3px rgba(0,0,0,.04) }
table.tbl { width: 100%; border-collapse: collapse; font-size: 12.6px }
.tbl th { background: #f1f5f9; text-align: left; padding: 8px 9px; font-weight: 600; font-size: 12px; color: #334155; border-bottom: 1px solid #e2e8f0; white-space: nowrap }
.tbl td { padding: 7px 9px; border-bottom: 1px solid #f1f5f9; vertical-align: top }
.tbl tr:last-child td { border-bottom: none }
.tbl .num { text-align: right; font-variant-numeric: tabular-nums; white-space: nowrap }
.small { font-size: 11.8px; color: #475569 } .mono { font-family: Consolas,monospace; font-size: 12px }
.chip { display: inline-block; color: #fff; font-size: 10.5px; padding: 1px 6px; border-radius: 9px; margin: 1px 2px; white-space: nowrap }
.chips { line-height: 1.9 }
.svgbox { background: #f8fafc; border: 1px solid #e2e8f0; border-radius: 10px; padding: 8px; margin: 12px 0 }
.svgbox svg { width: 100%; height: auto; display: block }
.cap { font-size: 12px; color: #64748b; margin: 2px 0 10px }
.alert { border-left: 4px solid #dc2626; background: #fef2f2; padding: 12px 16px; border-radius: 0 8px 8px 0; margin: 12px 0; font-size: 13px }
.note { border-left: 4px solid #0891b2; background: #f0f9ff; padding: 12px 16px; border-radius: 0 8px 8px 0; margin: 12px 0; font-size: 13px }
.ok { border-left: 4px solid #16a34a; background: #f0fdf4; padding: 12px 16px; border-radius: 0 8px 8px 0; margin: 12px 0; font-size: 13px }
.warn { border-left: 4px solid #d97706; background: #fffbeb; padding: 12px 16px; border-radius: 0 8px 8px 0; margin: 12px 0; font-size: 13px }
.tag { display: inline-block; background: #1e3a8a; color: #fff; font-size: 11px; padding: 2px 9px; border-radius: 5px; margin-right: 7px; vertical-align: 1px }
.tag.rec { background: #16a34a } .tag.new { background: #7c3aed } .tag.orange { background: #ea580c }
ol.steps { padding-left: 20px; margin: 8px 0 } ol.steps li { margin: 5px 0 }
"""

    h = []
    A = h.append
    en = (i18n.get_lang() == "en")
    # 柜型/托盘显示名按当前语言 (plan.json 里可能是求解时生成的中文 label)
    box_label = ct.get("label", "")
    pal_label = (pal or {}).get("label", "")
    cfg_box_code = None
    cfg_pal_code = None
    try:
        import catalog as CAT
        lang = i18n.get_lang()
        # plan.json 通常没有 code, 按尺寸回查
        def _match(specs, t):
            try:
                L_, W_, H_ = float(t.get("L", 0)), float(t.get("W", 0)), float(t.get("H", 0))
            except (TypeError, ValueError):
                return None
            for code, sp in specs.items():
                if (abs(float(sp.get("L", -1)) - L_) < 0.01
                        and abs(float(sp.get("W", -1)) - W_) < 0.01
                        and abs(float(sp.get("H", -1)) - H_) < 0.01):
                    return code
            return None
        cfg_box_code = _match(CAT.CONTAINERS, plan.get("box", {}))
        cfg_pal_code = _match(CAT.PALLETS, plan.get("pal", {}))
        box_label = CAT.label_of(CAT.CONTAINERS.get(cfg_box_code, {}), lang) or box_label
        pal_label = CAT.label_of(CAT.PALLETS.get(cfg_pal_code, {}), lang) or pal_label
    except Exception:
        pass
    # 标题: 优先用调用方传入, 否则按柜型/模式自动生成 (不绑定任何特定业务方)
    doc_title = title or f"{rt('title')} · {box_label}"
    if en:
        mode_label = "Pallet mode" if has_pal else "No-pallet mode (cartons stacked directly)"
        if has_pal and pal_label:
            mode_label = f"Pallet mode ({pal_label})"
        head_sub = subtitle or (f'Container {box_label} · {mode_label} · '
                                f'recomputed {gen} · all plans passed L1/L2/L3 validation')
    else:
        mode_label = "托盘模式" if has_pal else "无托盘模式(纸箱直接码垛)"
        if has_pal and pal_label:
            mode_label = f"托盘模式({pal_label})"
        head_sub = subtitle or (f'柜型 {box_label} · {mode_label} · '
                                f'重算于 {gen} · 全部方案通过 L1/L2/L3 校验')
    A(f'<!DOCTYPE html><html lang="{"en" if en else "zh-CN"}"><head><meta charset="utf-8">'
      f'<meta name="viewport" content="width=device-width,initial-scale=1">'
      f'<title>{doc_title}</title><style>{css}</style></head><body><div class="wrap">')

    A(f'<header><h1>{doc_title}</h1>'
      f'<p>{head_sub}</p></header>')

    # 一、结论
    A(f'<h2>{rt("h_conclusion")}</h2>')
    b2_rows = (f'<tr><td>{batch_names[1] if len(batch_names)>1 else "批次2"}</td>'
               f'<td class="num">{len(b2)}</td><td class="num">{b2_cartons}</td>'
               f'<td class="num">—</td><td class="num">{b2_util if b2_util is not None else "—"}%</td>'
               f'<td class="num">0</td></tr>') if b2 else ""
    A(f'<div class="card"><p style="margin-top:0">{rt("intro")}</p>'
      f'<table class="tbl"><thead><tr><th>{rt("th_batch")}</th><th>{rt("th_ctr")}</th><th>{rt("th_cartons")}</th><th>{rt("th_cbm")}</th>'
      f'<th>{rt("th_util")}</th><th>{rt("th_bare")}</th></tr></thead><tbody>'
      f'<tr><td><b>{primary_batch}</b></td><td class="num"><b>{len(b1)}</b></td><td class="num">{b1_cartons}</td>'
      f'<td class="num">{b1_cbm}</td><td class="num"><b>{b1_util}%</b></td>'
      f'<td class="num">{sum(bare_by_cab.values())} {rt("th_cartons")}</td></tr>'
      f'{b2_rows}'
      f'<tr><td><b>{rt("th_total")}</b></td><td class="num"><b>{len(plan["containers"])}</b></td><td class="num"><b>{total_cartons}</b></td>'
      f'<td class="num">—</td><td class="num">—</td><td class="num">{bare_total} {rt("th_cartons")}</td></tr>'
      f'</tbody></table>')
    A(f'<div class="ok"><b>{rt("achieve")}</b>: {primary_batch} — <b>{len(b1)} {rt("th_ctr")} / {b1_cartons} {rt("th_cartons")} / {b1_util}%</b>'
      f'{rt("achieve_pal") if has_pal else ""}; '
      f'{rt("achieve_bare", bare=bare_total, n=len(b1)) if has_pal else rt("achieve_nopal")}</div>'
      + (f'<div class="warn"><b>{rt("bench")}</b>: {rt("bench_alt", alt=alt["name"], n1=alt["n1"], n2=alt["n2"], total=alt["total"], util1=alt["util1"], n=len(b1))}</div>'
         if alt else
         f'<div class="warn"><b>{rt("bench")}</b>: {rt("bench_noalt", n=len(b1))}</div>')
      + '</div>')

    # 二、柜数对比
    A(f'<h2>{rt("h_compare")}</h2>')
    alt_row = (f'<tr><td><b>{alt["name"]}</b> <span class="tag rec">&#9733;</span></td>'
               f'<td class="num"><b>{alt["n1"]}</b></td>'
               f'<td class="num"><b>{alt["util1"]}%</b></td>'
               f'<td class="small">{rt("row_alt_when", gen=alt["generated"])}'
               f'{rt("row_alt")}</td></tr>'
               if alt else
               f'<tr><td><b>{rt("row_noalt")}</b></td><td class="num">—</td>'
               f'<td class="num">—</td><td class="small">—</td></tr>')
    A(f'<div class="card"><table class="tbl"><thead><tr><th>{rt("th_plan")}</th><th>{primary_batch} {rt("th_ctr")}</th>'
      f'<th>{rt("th_util")}</th><th>{rt("th_note")}</th></tr></thead><tbody>'
      + alt_row +
      f'<tr><td><b>{rt("row_main_title")}</b> <span class="tag new">&#9733;</span></td>'
      f'<td class="num"><b>{len(b1)}</b></td><td class="num"><b>{b1_util}%</b></td>'
      f'<td class="small">{rt("row_main")}</td></tr>'
      '</tbody></table>'
      f'<div class="note">{rt("note_mix", n=len(b1))}</div>'
      + (f'<div class="ok"><b>{rt("note_same_title")}</b>: {rt("note_same")}</div>' if alt else '')
      + '</div>')

    # 二·5: 备选方案逐柜明细
    if alt:
        A(f'<h3 style="margin-top:22px">{alt["name"]} · {rt("alt_detail_title")}</h3>')
        rows_a = []
        for c in alt["per_cab"]:
            rows_a.append(f'<tr><td class="num">{c["idx"]}</td><td class="num">{c["cartons"]}</td>'
                          f'<td class="num">{c["cbm"]}</td><td class="num"><b>{c["util"]}%</b></td>'
                          f'<td class="num">{c["stacks"]}</td><td class="num">{c["hmax"]}</td></tr>')
        A(f'<div class="card"><table class="tbl"><thead><tr><th>{rt("th_ctr2")}</th><th>{rt("th_cartons")}</th><th>{rt("th_cbm")}</th>'
          f'<th>{rt("th_util")}</th><th>{rt("th_stacks")}</th><th>{rt("th_top")}</th></tr></thead><tbody>'
          + "".join(rows_a) + '</tbody></table>'
          f'<div class="cap">{rt("alt_val_state", err=alt["errors"], warn=alt["warns"], ok="✓" if alt["passed"] else "✗", cartons=alt["cartons1"], batch=primary_batch)}</div></div>')

    # 三、柜底裸地利用机制（仅托盘模式）
    if has_pal:
        A(f'<h2>{rt("h_bare")}</h2>')
        A(f'<div class="card">'
          f'<h3 style="margin-top:0">{rt("bare_overview")}</h3>'
          f'<p>{rt("bare_geom", box=box_label, slots=n_slots, floor=f"{cont_floor:.2f}", pal_area=f"{pal_area_m2:.2f}", bare=f"{bare_m2:.2f}", pct=bare_pct)}</p>'
          f'<ol class="steps">'
          + "".join(f'<li>{s}</li>' for s in rt("bare_steps", n=len(b1)))
          + f'</ol>'
          f'<div class="ok"><b>{rt("bare_measured_title")}</b> {rt("bare_measured", batch=primary_batch, bare=bare_total, n=len(b1), dist=bare_by_cab, cartons=b1_cartons)}</div>'
          f'<h3>{rt("bare_clarify")}</h3>'
          f'<p>{rt("bare_clarify_txt", n=len(b1), L=L)}</p>'
          f'<div class="note">{rt("bare_note", batch=primary_batch, cartons=b1_cartons, bare=f"{bare_m2:.2f}")}</div>'
          f'</div>')
    else:
        A(f'<h2>{rt("h_bare_none")}</h2>')
        A(f'<div class="card"><p class="small">{rt("nopal_txt")}</p></div>')

    # 四、空间机会点量化核算（全部由方案数据推导）
    A(f'<h2>{rt("h_space")}</h2>')
    A(f'<div class="card">'
      f'<h3 style="margin-top:0">{rt("op1")}</h3>'
      f'<p>{rt("op1_txt", floor=f"{cont_floor:.2f}", slots=n_slots, pal_area=f"{pal_area_m2:.2f}", bare=f"{bare_m2:.2f}", pct=bare_pct)}</p>'
      f'<h3>{rt("op2")}</h3>'
      f'<table class="tbl"><thead><tr><th>{rt("th_box")}</th><th>{rt("th_inner")}</th>'
      f'<th>{rt("th_door")}</th><th>{rt("th_dead")}</th>'
      f'<th>{rt("th_pass")}</th></tr></thead><tbody>'
      f'<tr><td>{box_label}</td><td class="num">{H} mm</td><td class="num">{doorH} mm</td>'
      f'<td class="num">{dead_h} mm</td><td class="small">{rt("no_pass")}</td></tr>'
      '</tbody></table>'
      f'<div class="alert">{rt("op2_alert", H=H, doorH=doorH, dead=dead_h)}</div>'
      f'<h3>{rt("op3")}</h3>'
      f'<p>{rt("op3_txt", fill=fill_avg)}</p>'
      f'</div>')

    # 五、逐柜方案明细 + 3D
    A(f'<h2>{rt("h_detail", batch=primary_batch, box=box_label)}</h2>')
    rows = []
    for c in b1:
        sk = " ".join(f'<span class="chip" style="background:{plan["meta"]["color_map"].get(k,"#64748b")}">{k} {v}</span>'
                      for k, v in c.get("per_sku", {}).items())
        bc = sum(1 for it in items if it["idx"] == c["idx"] and it.get("batch") == primary_batch
                 for b in it["boxes"] if b["kind"] == "bare")
        rows.append(f'<tr><td class="num">{c["idx"]}</td><td class="num">{c["cartons"]}</td>'
                    f'<td class="num">{c["cbm"]}</td><td class="num"><b>{c["util"]}%</b></td>'
                    f'<td class="num">{c.get("npallets", 0)}</td><td class="num">{c.get("hmax", 0)}</td>'
                    f'<td class="num">{bc}</td><td class="chips">{sk}</td></tr>')
    A('<div class="card"><table class="tbl"><thead><tr><th>{rt("th_ctr2")}</th><th>{rt("th_cartons")}</th><th>{rt("th_cbm")}</th>'
      f'<th>{rt("th_util")}</th><th>{rt("th_pallets")}</th><th>{rt("th_top")}</th>'
      f'<th>{rt("th_bare")}</th><th>{rt("th_sku")}</th></tr></thead><tbody>'
      + "".join(rows) + '</tbody></table></div>')

    for it in items:
        if it.get("batch") == primary_batch and it["idx"] <= 3:
            A('<div class="svgbox">' + svg_cab(ct, it) +
              f'<div class="cap"><b>{rt("cap")}</b> · {rt("th_ctr2")}{it["idx"]}（{primary_batch}）— {it["cartons"]} {rt("th_cartons")} / '
              f'{it["cbm"]} CBM / {rt("th_util")} {it["util"]}% / {it.get("npallets", 0)} {rt("th_pallets")} · '
              f'{rt("cap_legend")}</div></div>')

    # 六、校验状态
    A(f'<h2>{rt("h_validation")}</h2>')
    n_err = v.get("errors", 0)
    n_warn = v.get("warns", 0)
    cls = "ok" if v.get("passed") else "alert"
    A(f'<div class="{cls}"><b>{rt("val_verdict")}: {rt("val_pass") if v.get("passed") else rt("val_fail")}</b> — '
      f'{rt("val_txt", err=n_err, warn=n_warn)}<br>'
      f'{rt("val_l1")}<br>{rt("val_l2")}<br>{rt("val_l3")}</div>')
    A(f'<div class="note">{rt("val_note")}</div>')

    # 七、选型建议
    A(f'<h2>{rt("h_choice")}</h2>')
    box_label = ct.get("label", "")
    if alt:
        rec1 = (f'<tr><td><b>1</b> <span class="tag rec">&#9733;</span></td><td><b>{alt["name"]} + {box_label}</b></td>'
                f'<td class="num"><b>{alt["n1"]} + {alt["n2"]} = {alt["total"]}</b></td>'
                f'<td class="num"><b>{alt["util1"]}% / {alt["util2"] if alt["util2"] is not None else "—"}%</b></td>'
                f'<td class="small">{rt("rec1")}</td></tr>')
        rec2 = (f'<tr><td><b>2</b> <span class="tag new">&#9733;</span></td><td>{rt("rec_main_title")} + {box_label}</td>'
                f'<td class="num">{len(b1)} + {len(b2)} = {len(b1)+len(b2)}</td>'
                f'<td class="num">{b1_util}% / {b2_util if b2_util is not None else "—"}%</td>'
                f'<td class="small">{rt("rec2")}</td></tr>')
    else:
        rec1 = (f'<tr><td><b>1</b> <span class="tag rec">&#9733;</span></td><td><b>{rt("rec_main_title")} + {box_label}</b></td>'
                f'<td class="num">{len(b1)} + {len(b2)} = {len(b1)+len(b2)}</td>'
                f'<td class="num">{b1_util}% / {b2_util if b2_util is not None else "—"}%</td>'
                f'<td class="small">{rt("rec3")}</td></tr>')
        rec2 = ""
    A(f'<div class="card"><table class="tbl"><thead><tr><th>{rt("th_prio")}</th><th>{rt("th_plan")}</th>'
      f'<th>{rt("th_total_ctr")}</th><th>{rt("th_util")}</th>'
      f'<th>{rt("th_apply")}</th></tr></thead><tbody>'
      + rec1 + rec2 +
      '</tbody></table>'
      f'<div class="note">{rt("note_ui")}</div>'
      '</div>')

    A(f'<div class="note" style="margin-top:26px">{rt("note_method")}</div>')

    A('</div></body></html>')
    out = "".join(h)
    os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(out)
    print(f"生成 {out_path} ({len(out)/1024:.0f} KB) | {primary_batch} {len(b1)} 柜/{b1_cartons} 箱 | 裸地 {bare_total} 箱 | 校验 passed={v.get('passed')}")


if __name__ == "__main__":
    arg_parser = argparse.ArgumentParser()
    arg_parser.add_argument("--plan", required=True)
    arg_parser.add_argument("--scenes", required=True)
    arg_parser.add_argument("--out", required=True)
    arg_parser.add_argument("--alt-plan", default=None,
                            help="对比模式: 备选方案 (如无托盘) 的 plan.json")
    arg_parser.add_argument("--alt-scenes", default=None,
                            help="对比模式: 备选方案的 scene_data.json")
    arg_parser.add_argument("--alt-name", default="模式A 无托盘",
                            help="对比模式: 备选方案显示名")
    arg_parser.add_argument("--title", default=None,
                            help="报告标题 (默认按柜型自动生成)")
    arg_parser.add_argument("--subtitle", default=None,
                            help="报告副标题/头部说明 (默认自动生成)")
    arg_parser.add_argument("--lang", default=None, choices=["zh", "en"],
                            help="报告语言 (默认: zh; env WB_LANG=en 也可)")
    a = arg_parser.parse_args()
    if a.lang:
        i18n.set_lang(a.lang)
    else:
        i18n.resolve_cli_lang()
    P = json.load(open(a.plan, encoding="utf-8"))
    S = json.load(open(a.scenes, encoding="utf-8"))
    AP = json.load(open(a.alt_plan, encoding="utf-8")) if a.alt_plan else None
    AS = json.load(open(a.alt_scenes, encoding="utf-8")) if a.alt_scenes else None
    build(P, S, a.out, alt_plan=AP, alt_scenes=AS, alt_name=a.alt_name,
          title=a.title, subtitle=a.subtitle)
