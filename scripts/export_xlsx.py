# -*- coding: utf-8 -*-
"""从 plan.json 导出 Excel 作业方案

sheet 结构:
  方案汇总    —— 前置条件/约束清单 + 各方案对比
  货物明细    —— SKU 尺寸箱数重量 + 堆码层数上限
  <批次>_<柜型>_托盘装载卡 —— 逐托逐层明细 (作业现场用)
  各柜汇总    —— 每柜 SKU 分布 + 装载率
"""
import os
import sys
from collections import defaultdict

from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from openpyxl.utils import get_column_letter

import i18n

HEAD_F = Font(name="Microsoft YaHei", size=10, bold=True, color="FFFFFFFF")
BODY_F = Font(name="Microsoft YaHei", size=10)
BOLD_F = Font(name="Microsoft YaHei", size=10, bold=True)
TITLE_F = Font(name="Microsoft YaHei", size=13, bold=True, color="FFFFFFFF")
SECT_F = Font(name="Microsoft YaHei", size=11, bold=True, color="FF2F5597")
NOTE_F = Font(name="Microsoft YaHei", size=9, color="7F7F7F")
FILL_HEAD = PatternFill("solid", fgColor="FF4472C4")
FILL_TITLE = PatternFill("solid", fgColor="FF2F5597")
FILL_SECT = PatternFill("solid", fgColor="FFEDF2FA")
FILL_OK = PatternFill("solid", fgColor="FFC6EFCE")
FILL_WARN = PatternFill("solid", fgColor="FFFFEB9C")
FILL_ERR = PatternFill("solid", fgColor="FFFFC7CE")
thin = Side(style="thin", color="FFBFBFBF")
BORDER = Border(left=thin, right=thin, top=thin, bottom=thin)


def _title(ws, text, ncol):
    ws.cell(1, 1, text).font = TITLE_F
    ws.cell(1, 1).fill = FILL_TITLE
    ws.merge_cells(start_row=1, start_column=1, end_row=1, end_column=ncol)
    ws.row_dimensions[1].height = 26


def _header(ws, row, headers, widths=None):
    for j, h in enumerate(headers, 1):
        c = ws.cell(row, j, h)
        c.font = HEAD_F
        c.fill = FILL_HEAD
        c.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
        c.border = BORDER
        if widths:
            ws.column_dimensions[get_column_letter(j)].width = widths[j - 1]
    ws.row_dimensions[row].height = 28


def _row(ws, row, values, bold=False, fill=None):
    for j, v in enumerate(values, 1):
        c = ws.cell(row, j, v)
        c.font = BOLD_F if bold else BODY_F
        c.border = BORDER
        c.alignment = Alignment(horizontal="center", vertical="center")
        if fill:
            c.fill = fill


def _cons_lines(c):
    """从 cons 字典生成人类可读的约束清单 (与 Constraints.describe 一致)。

    每行格式为 "<key>: <value>"，其中 key 是 i18n 文案目录里的键名,
    这样说明列 (cons_explain) 可以直接按 key 匹配。
    """
    L = []
    mode = c.get("mode", "pallet")
    en = (i18n.get_lang() == "en")
    L.append(f"{i18n.xt('k_mode')}: {i18n.xt('v_pallet') if mode == 'pallet' else i18n.xt('v_nopallet')}")
    if mode == "pallet":
        L.append(f"{i18n.xt('k_mono')}: {i18n.xt('v_yes') if c.get('mono_sku') else i18n.xt('v_no')}")
        L.append(f"{i18n.xt('k_top')}: {i18n.xt('v_allow') if c.get('allow_top') else i18n.xt('v_forbid')}")
        L.append(f"{i18n.xt('k_dbl')}: {i18n.xt('v_allow') if c.get('allow_double_pallet') else i18n.xt('v_forbid')}")
        if c.get("tihi_layers"):
            if en:
                L.append(f"{i18n.xt('k_tihi')}: {c['tihi_layers']} "
                         f"{i18n.xt('tihi_note')}")
            else:
                L.append(f"{i18n.xt('k_tihi')}: {c['tihi_layers']} 层, 货高上限按 TiHi 标准")
        if c.get("max_pallet_stack_h"):
            L.append(f"{i18n.xt('k_maxh')}: {c['max_pallet_stack_h']}mm")
        if c.get("max_pallet_weight"):
            L.append(f"{i18n.xt('k_maxw')}: {c['max_pallet_weight']} kg")
    pol = c.get("support_policy", "relaxed_all")
    pol_txt = {"relaxed_all": i18n.xt("exp_pol_relaxed"),
               "balanced": i18n.xt("exp_pol_balanced"),
               "strict": i18n.xt("exp_pol_strict")}.get(pol, pol)
    L.append(f"{i18n.xt('k_pol')}: {pol} —— {pol_txt}" if not en
             else f"{i18n.xt('k_pol')}: {pol} — {pol_txt}")
    if c.get("reorder_layers"):
        L.append(f"{i18n.xt('k_reorder')}: {i18n.xt('exp_reorder')}")
    if c.get("min_support_ratio"):
        L.append(f"{i18n.xt('k_ratio')}: {c['min_support_ratio']*100:.0f}%")
    if c.get("max_cog_offset"):
        pct = f"{c['max_cog_offset']*100:.0f}%"
        L.append(f"{i18n.xt('k_cog')}: {pct}" if not en
                 else f"{i18n.xt('k_cog')}: {pct} of container width")
    obj = i18n.xt("exp_obj") if c.get("optimize") == "containers" else i18n.xt("exp_obj_strict")
    L.append(f"{i18n.xt('k_goal')}: {obj}")
    return L


def _match_spec(specs, target):
    """按尺寸 (L/W/H) 在规格库里回查匹配的条目。

    用于 plan.json 是旧版本生成、缺少 label_en 字段时,
    仍能按当前语言显示正确的规格名。
    """
    try:
        tL, tW, tH = float(target.get("L", 0)), float(target.get("W", 0)), float(target.get("H", 0))
    except (TypeError, ValueError):
        return None
    for spec in specs.values():
        if (abs(float(spec.get("L", -1)) - tL) < 0.01
                and abs(float(spec.get("W", -1)) - tW) < 0.01
                and abs(float(spec.get("H", -1)) - tH) < 0.01):
            return spec
    return None


def export(plan, out_path):
    meta = plan["meta"]
    box = dict(plan["box"])
    pal = dict(plan["pal"])
    # 规格显示名按当前语言。
    # plan.json 可能是旧版本生成 (没有 label_en), 因此按尺寸回查 catalog 取最新 label。
    try:
        import catalog as CAT
        lang = i18n.get_lang()
        box["label"] = CAT.label_of(_match_spec(CAT.CONTAINERS, box), lang) or box.get("label", "")
        pal["label"] = CAT.label_of(_match_spec(CAT.PALLETS, pal), lang) or pal.get("label", "")
    except Exception:
        pass
    conts = plan["containers"]
    smap = {s["code"]: s for s in meta["sku_list"]}
    cmap = meta.get("color_map", {})
    wb = Workbook()

    # ================= 方案汇总 =================
    ws = wb.active
    ws.title = i18n.xt("sheet_summary")
    _title(ws, f"{i18n.xt('title')} · {box['label']} · {meta['generated']}", 8)
    for j, w in enumerate([26, 30, 12, 12, 12, 40, 16, 16], 1):
        ws.column_dimensions[get_column_letter(j)].width = w

    r = 3
    ws.cell(r, 1, i18n.xt("sec1")).font = SECT_F
    ws.cell(r, 1).fill = FILL_SECT
    ws.merge_cells(start_row=r, start_column=1, end_row=r, end_column=8)
    r += 1
    _header(ws, r, [i18n.xt("h_item"), i18n.xt("h_value"), "", "", "", i18n.xt("h_note"), "", ""])
    r += 1
    cons_desc = plan.get("cons_desc") or []
    if not cons_desc:
        # plan.json 里的 cons 是 dict, 直接用 Constraints.describe 的等价实现
        c = plan.get("cons", {}) or {}
        cons_desc = _cons_lines(c)
    # 说明列按 i18n key 取 (key = 约束项文案)
    cons_explain = {
        i18n.xt("k_mode"): i18n.xt("exp_mode"),
        i18n.xt("k_mono"): i18n.xt("exp_mono"),
        i18n.xt("k_top"): i18n.xt("exp_top"),
        i18n.xt("k_dbl"): i18n.xt("exp_dbl"),
        i18n.xt("k_tihi"): i18n.xt("exp_tihi"),
        i18n.xt("k_maxh"): i18n.xt("exp_maxh"),
        i18n.xt("k_maxw"): i18n.xt("exp_maxw"),
        i18n.xt("k_stack"): i18n.xt("exp_stack"),
        i18n.xt("k_order"): i18n.xt("exp_order"),
        i18n.xt("k_ratio"): i18n.xt("exp_ratio"),
        i18n.xt("k_cog"): i18n.xt("exp_cog"),
        i18n.xt("k_layers"): i18n.xt("exp_layers"),
        i18n.xt("k_goal"): i18n.xt("exp_goal"),
    }
    for line in cons_desc:
        key = line.split(":")[0].strip()
        val = line.split(":", 1)[1].strip() if ":" in line else ""
        _row(ws, r, [key, val, "", "", "", cons_explain.get(key, ""), "", ""])
        r += 1
    box_spec_txt = (f"{box['label']}  {i18n.xt('box_inner')} {box['L']}×{box['W']}×{box['H']}mm  "
                    f"{i18n.xt('box_door')} {box['doorH']}mm  {i18n.xt('box_vol')} {box['vol']}m³")
    pal_spec_txt = (f"{pal.get('label', pal['code'] if 'code' in pal else '')}  "
                    f"{pal['L']}×{pal['W']}×{pal['H']}mm")
    tot_ctr_key = i18n.xt("k_total_ctr")
    for k, v in ((i18n.xt("k_box_spec"), box_spec_txt),
             (i18n.xt("k_pal_spec"), pal_spec_txt),
             (i18n.xt("k_total_cartons"), meta["total_cartons"]),
             (i18n.xt("k_total_cbm"), f"{meta['total_cbm']} CBM"),
             (tot_ctr_key, meta["total_containers"])):
        _row(ws, r, [k, v, "", "", "", "", "", ""], bold=(k == tot_ctr_key))
        r += 1

    r += 1
    ws.cell(r, 1, i18n.xt("sec2")).font = SECT_F
    ws.cell(r, 1).fill = FILL_SECT
    ws.merge_cells(start_row=r, start_column=1, end_row=r, end_column=8)
    r += 1
    _header(ws, r, [i18n.xt("h_ctr"), i18n.xt("h_batch"), i18n.xt("h_cartons"), "CBM",
                   i18n.xt("h_util"), i18n.xt("h_sku"), i18n.xt("h_hmax"), i18n.xt("h_npal")])
    r += 1
    for c in conts:
        sk = "  ".join(f"{k}×{v}" for k, v in c["per_sku"].items())
        _row(ws, r, [c["idx"], c.get("batch", ""), c["cartons"], c["cbm"],
                     c["util"], sk, c["hmax"], c["npallets"]])
        r += 1

    r += 1
    ws.cell(r, 1, i18n.xt("sec_validate")).font = SECT_F
    ws.cell(r, 1).fill = FILL_SECT
    ws.merge_cells(start_row=r, start_column=1, end_row=r, end_column=8)
    r += 1
    v = plan.get("validation", {})
    _row(ws, r, [i18n.xt("k_val_result"),
                 ("通过 ✓" if v.get("passed") else "不通过 ✗") if i18n.get_lang() != "en"
                 else ("PASSED ✓" if v.get("passed") else "FAILED ✗"),
                 (f"错误 {v.get('errors', 0)}" if i18n.get_lang() != "en"
                  else f"{v.get('errors', 0)} error(s)"),
                 (f"警告 {v.get('warns', 0)}" if i18n.get_lang() != "en"
                  else f"{v.get('warns', 0)} warning(s)"),
                 "", "", "", ""],
         bold=True, fill=FILL_OK if v.get("passed") else FILL_ERR)
    r += 1
    for chk in v.get("checks", []):
        fill = FILL_ERR if chk["level"] == "error" else (
            FILL_WARN if chk["level"] == "warn" else None)
        _row(ws, r, [chk["layer"], chk["level"], chk["code"], chk["msg"],
                     "", "", "", ""], fill=fill)
        r += 1

    # ---- 外部加固建议 (支撑率策略为「放任」时, 由缠绕膜/捆扎兜底)
    r += 1
    ws.cell(r, 1, i18n.xt("sec5")).font = SECT_F
    ws.cell(r, 1).fill = FILL_SECT
    ws.merge_cells(start_row=r, start_column=1, end_row=r, end_column=8)
    r += 1
    _header(ws, r, [i18n.xt("h_project"), i18n.xt("h_req"), "", "", "", i18n.xt("h_basis"), "", ""])
    r += 1
    c = plan.get("cons", {}) or {}
    pol = c.get("support_policy", "relaxed_all")
    n_low = sum(1 for c2 in v.get("checks", [])
                if c2["code"].startswith("SUPPORT_RATIO"))
    n_broken = sum(1 for c2 in v.get("checks", [])
                   if c2["code"] == "LOAD_PATH_BROKEN")
    items = [
        ("缠绕膜", "所有托盘整体缠绕, 底部起缠、边角加缠, 使货物与托盘成一体",
         "GB/T 16470-2008 §4.3"),
        ("捆扎带", "支撑率低于 75% 的托盘加十字捆扎 (塑料带 ≥15mm×0.8mm)",
         "GB/T 16470-2008 §4.3.1.4"),
        ("层间垫板", "重货在下、且上下箱型不一致的层间加设隔板, 分散压力",
         "GB/T 16470-2008 §4.2.2.1"),
        ("标签", "混装托盘须四面贴标并注明「Mixed SKU」; 每托贴库位卡",
         "Amazon FBA 托盘标签要求"),
        ("重货置底", "密度货 (>300 kg/m3) 置于托盘下部, 轻抛货在上",
         "GB/T 16470-2008 §4.1.5 / 作业规范「重不压轻」"),
    ]
    for k, req, src in items:
        _row(ws, r, [k, req, "", "", "", src, "", ""])
        r += 1
    note = (f"本次支撑率策略 = {pol}; 支撑率低于 75% 的托盘 {n_low} 个"
            f"{'；完全无支撑(已自动重排, 需人工复核) ' + str(n_broken) + ' 个' if n_broken else ''}。"
            "上述手段为交付前的作业要求, 须在作业卡中明确标注。")
    _row(ws, r, ["本次提示", note, "", "", "", "", "", ""])
    r += 1

    # ================= 货物明细 =================
    ws = wb.create_sheet(i18n.xt("sheet_cargo"))
    _title(ws, f"{i18n.xt('sheet_cargo')} · {i18n.xt('cargo_sub')}", 11)
    _header(ws, 3,
             [i18n.xt("h_sku_code"), i18n.xt("h_desc"), i18n.xt("h_len"), i18n.xt("h_wid"),
              i18n.xt("h_hgt"), i18n.xt("h_cartons"), i18n.xt("h_pcs"),
              i18n.xt("h_wt"), i18n.xt("h_wt_total"), "CBM/box", "Total CBM",
              i18n.xt("h_maxlay")],
            [14, 22, 10, 10, 10, 9, 10, 13, 11, 11, 11, 11])
    r = 4
    for s in meta["sku_list"]:
        ml = s.get("max_stack_layers")
        _row(ws, r, [s["code"], s["name"], s["L"], s["W"], s["H"], s["cartons"],
                     s["pcs_per_carton"], s["weight_kg"],
                     round(s["weight_kg"] * s["cartons"], 1),
                     round(s["cbm_per_carton"], 6), s["total_cbm"],
                     ml if ml else i18n.xt("v_auto")])
        r += 1
    r += 1
    ws.cell(r, 1, i18n.xt("cargo_note")).font = NOTE_F

    # ================= 托盘装载卡 (按批次×柜型) =================
    groups = defaultdict(list)
    for c in conts:
        groups[(c.get("batch", ""), c["idx"])].append(c)
    by_batch = defaultdict(list)
    for c in conts:
        by_batch[c.get("batch", "")].append(c)

    for bname, cs in by_batch.items():
        ws = wb.create_sheet(f"{bname}_{i18n.xt('sheet_pallet')}"[:31])
        _title(ws, f"{bname} · {i18n.xt('sheet_pallet')} · {box['label']} · "
                   f"{pal.get('label','')}", 12)
        _header(ws, 3,
                [i18n.xt("h_ctr"), i18n.xt("h_palpos"), i18n.xt("h_rot"),
                 i18n.xt("h_cx"), i18n.xt("h_cy"), i18n.xt("h_palsize"),
                 i18n.xt("h_layer"), "SKU", i18n.xt("h_perlayer"),
                 i18n.xt("h_layerh"), i18n.xt("h_cumh"), i18n.xt("h_loadh")],
                [7, 8, 7, 12, 12, 16, 7, 13, 10, 10, 13, 13])
        r = 4
        for c in cs:
            for p in c["pallets"]:
                acc = p.get("z0", pal["H"])
                for li, l in enumerate(p["layers"], 1):
                    _row(ws, r, [c["idx"], p["pi"], p["rot"], p["cx"], p["cy"],
                                 f"{p['w']}×{p['d']}", li, l["sku"], l["n"],
                                 l["vz"], round(acc + l["vz"], 0),
                                 round(p["body_h"], 0)])
                    r += 1
                if p.get("top"):
                    for ti, tp in enumerate(p["top"], 1):
                        _row(ws, r, [c["idx"], p["pi"], p["rot"], p["cx"], p["cy"],
                                     f"{p['w']}×{p['d']}", (f"顶{ti}" if i18n.get_lang() != "en" else f"Top {ti}"), tp["sku"],
                                     tp["n"], tp["vz"], round(p["body_h"], 0),
                                     round(p["h"], 0)],
                             fill=FILL_WARN)
                        r += 1
        ws.freeze_panes = "A4"

    # ================= 各柜汇总 =================
    ws = wb.create_sheet(i18n.xt("sheet_ctrsum"))
    _title(ws, f"{i18n.xt('sheet_ctrsum')} · {i18n.xt('ctrsum_sub')}", 9)
    _header(ws, 3, [i18n.xt("h_ctr"), i18n.xt("h_batch"), i18n.xt("h_cartons"), "CBM", i18n.xt("h_util"), i18n.xt("h_npal"),
                    i18n.xt("h_hmax"), i18n.xt("h_wt_total"), i18n.xt("h_sku")],
            [7, 10, 9, 11, 11, 9, 13, 11, 46])
    r = 4
    for c in conts:
        sk = "  ".join(f"{k}×{v}" for k, v in c["per_sku"].items())
        _row(ws, r, [c["idx"], c.get("batch", ""), c["cartons"], c["cbm"],
                     c["util"], c["npallets"], c["hmax"], c.get("weight", 0), sk])
        r += 1

    os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
    wb.save(out_path)
    return out_path


if __name__ == "__main__":
    import json
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--plan", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--lang", default=None, choices=["zh", "en"],
                    help="Sheet language (default: zh; env WB_LANG=en also works)")
    a = ap.parse_args()
    if a.lang:
        i18n.set_lang(a.lang)
    else:
        i18n.resolve_cli_lang()
    with open(a.plan, encoding="utf-8") as f:
        plan = json.load(f)
    p = export(plan, a.out)
    print(f"→ {p}")
