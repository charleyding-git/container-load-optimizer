# -*- coding: utf-8 -*-
"""L3 三方一致性校验: plan.json ↔ xlsx ↔ 3D 渲染

用法:
  python verify_consistency.py --plan plan.json --xlsx 作业方案.xlsx --scenes scene_data.json

校验内容:
  1. plan 每个柜的箱数/CBM/装载率/托盘数 == xlsx 各柜汇总 sheet
  2. plan 每个柜的箱数/装载率 == 3D 场景数据
  3. 3D 渲染的箱体总数 == plan 总箱数
  4. 配色映射 plan == 3D
  5. 逐托箱数 plan == xlsx 托盘装载卡
退出码: 0 通过 / 1 不通过
"""
import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from validator import Gate, check_consistency, check_color_mapping  # noqa: E402
import i18n  # noqa: E402


def _find_sheet(wb, want_names):
    """按候选名找 sheet; 找不到则按表头特征兜底。

    作业方案可以是中文或英文 (--lang), sheet 名随语言变化,
    所以这里既按已知名匹配, 也按表头内容识别, 避免语言切换导致 L3 误报。
    """
    for n in want_names:
        if n in wb.sheetnames:
            return wb[n]
    # 兜底: 找同时含 "CBM" 与 "SKU" 之类表头特征的 sheet
    for ws in wb.worksheets:
        try:
            head = " ".join(str(ws.cell(3, c).value or "")
                            for c in range(1, min(ws.max_column, 12) + 1))
        except Exception:
            continue
        if "CBM" in head and ("SKU" in head or "柜" in head or "Ctr" in head):
            return ws
    raise KeyError(f"worksheet not found: {want_names} (available: {wb.sheetnames})")


def read_xlsx_rows(path):
    """从 xlsx 读回各柜汇总 (柜号, 箱数, CBM, 装载率, 托盘数)。"""
    from openpyxl import load_workbook
    wb = load_workbook(path, data_only=True)
    rows = []
    ws = _find_sheet(wb, [i18n.xt("sheet_ctrsum"), "各柜汇总", "Container summary"])
    for r in range(4, ws.max_row + 1):
        idx = ws.cell(r, 1).value
        if idx is None:
            continue
        rows.append((int(idx),
                     int(ws.cell(r, 3).value or 0),
                     round(float(ws.cell(r, 4).value or 0), 3),
                     round(float(ws.cell(r, 5).value or 0), 1),
                     int(ws.cell(r, 6).value or 0)))
    return rows


def read_xlsx_pallet_cards(path):
    """从 xlsx 读回每个 (柜, 托盘位) 的箱数合计。"""
    from openpyxl import load_workbook
    wb = load_workbook(path, data_only=True)
    out = {}
    card_kw = i18n.xt("sheet_pallet")     # "托盘装载卡" / "Pallet job cards"
    card_kw2 = "托盘装载卡"
    card_kw3 = "Pallet job cards"
    for ws in wb.worksheets:
        if not (card_kw in ws.title or card_kw2 in ws.title or card_kw3 in ws.title):
            continue
        for r in range(4, ws.max_row + 1):
            ci = ws.cell(r, 1).value
            pi = ws.cell(r, 2).value
            n = ws.cell(r, 9).value
            if ci is None or pi is None or n is None:
                continue
            key = (int(ci), int(pi))
            out[key] = out.get(key, 0) + int(n)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--plan", required=True)
    ap.add_argument("--xlsx", required=True)
    ap.add_argument("--scenes", required=True)
    ap.add_argument("--lang", default=None, choices=["zh", "en"],
                    help="Language of the generated workbook (default: zh; "
                         "env WB_LANG=en also works)")
    args = ap.parse_args()
    if args.lang:
        i18n.set_lang(args.lang)
    else:
        i18n.resolve_cli_lang()

    with open(args.plan, encoding="utf-8") as f:
        plan = json.load(f)
    with open(args.scenes, encoding="utf-8") as f:
        scenes = json.load(f)

    gate = Gate()
    xrows = read_xlsx_rows(args.xlsx)
    check_consistency(plan, xrows, scenes.get("items", []), gate)

    # 配色一致性
    cmap = plan.get("meta", {}).get("color_map", {})
    vmap = {}
    for it in scenes.get("items", []):
        for b in it.get("boxes", []):
            if b.get("sku"):
                vmap.setdefault(b["sku"], b.get("c"))
    check_color_mapping(plan, {"color_map": cmap}, gate)
    for sku, c in cmap.items():
        if sku in vmap and str(vmap[sku]).lower() != str(c).lower():
            gate.error("L3", "COLOR_MISMATCH",
                       f"SKU {sku} 配色不一致: plan={c} viz={vmap[sku]}",
                       sku=sku)

    # 逐托箱数
    pcards = read_xlsx_pallet_cards(args.xlsx)
    for c in plan["containers"]:
        for p in c.get("pallets", []):
            key = (c["idx"], p["pi"])
            plan_n = sum(l["n"] for l in p.get("layers", [])) \
                + sum(t["n"] for t in p.get("top", []))
            x_n = pcards.get(key)
            if x_n is None:
                gate.error("L3", "XLSX_PALLET_MISSING",
                           f"xlsx 托盘装载卡缺少柜{c['idx']} 托{p['pi']}",
                           container=c["idx"], pallet=p["pi"])
            elif int(x_n) != int(plan_n):
                gate.error("L3", "XLSX_PALLET_COUNT_MISMATCH",
                           f"柜{c['idx']} 托{p['pi']} 箱数不一致: "
                           f"plan={plan_n} xlsx={x_n}",
                           container=c["idx"], pallet=p["pi"],
                           plan=plan_n, xlsx=x_n)

    print(gate.report("L3 三方一致性校验 (plan ↔ xlsx ↔ 3D)"))
    return 0 if gate.passed else 1


if __name__ == "__main__":
    sys.exit(main())
