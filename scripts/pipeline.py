# -*- coding: utf-8 -*-
"""主流程: 输入 → 求解 → 校验 → 标准化 plan.json

CLI:
  python pipeline.py --input <xlsx> --config <json> --outdir <dir>

配置文件 config.json 结构见 references/config_spec.md
"""
import argparse
import json
import math
import os
import sys
import time
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from catalog import CONTAINERS, PALLETS, CONST
from engine import SKU, Constraints
import solver as SV
from validator import Gate, check_geometry, check_stability
import i18n

PALETTE = ["#2563eb", "#dc2626", "#16a34a", "#d97706", "#7c3aed",
           "#0891b2", "#db2777", "#65a30d", "#ea580c", "#4f46e5"]


# ============================================================
# 输入解析: Excel → SKU 列表 + 批次
# ============================================================
def parse_excel(path):
    """解析输入 Excel。

    约定:
      * 每个 working sheet = 一个批次 (同批次一起发运)
      * sheet 内表头 (第2行): 列名映射
      * 必填列: SKU编码 / 外箱长(mm) / 外箱宽(mm) / 外箱高(mm) / 箱数(外箱)
      * 可选列: 品名 / 每箱装几件 / 外箱毛重(kg) / ECT / 限堆层数 / 备注
      * 计量口径: 所有数量与尺寸都以外箱(运输纸箱)为单位。
        外箱毛重 = 整箱毛重(含内件 + 外箱材料), 直接用于承重/重心校验。

    返回 {批次名: [SKU, ...], "_meta": {...}}
    """
    try:
        from openpyxl import load_workbook
    except ImportError:
        raise RuntimeError("需要 openpyxl: pip install openpyxl")
    wb = load_workbook(path, data_only=True)
    batches = {}
    meta = {"source": os.path.basename(path), "sheets": []}
    for ws in wb.worksheets:
        if ws.title.startswith("_"):
            continue
        rows = list(ws.iter_rows(values_only=True))
        if not rows:
            continue
        # 找到表头行: 含 "SKU" 或 "编码" 的行
        hdr_idx = None
        colmap = {}
        for i, r in enumerate(rows[:8]):
            cells = [str(c).strip() if c is not None else "" for c in r]
            for j, c in enumerate(cells):
                cl = c.lower()
                if ("sku" in cl or "编码" in cl or "料号" in cl) and "code" not in colmap:
                    colmap["code"] = j
                if ("长" in c or cl == "l" or "length" in cl) and "L" not in colmap:
                    colmap["L"] = j
                if ("宽" in c or cl == "w" or "width" in cl) and "W" not in colmap:
                    colmap["W"] = j
                if ("高" in c or cl == "h" or "height" in cl) and "H" not in colmap:
                    colmap["H"] = j
                if ("箱数" in c or "carton" in cl or "总箱数" in c) and "cartons" not in colmap:
                    colmap["cartons"] = j
                if ("名称" in c or "品名" in c or "name" in cl) and "name" not in colmap:
                    colmap["name"] = j
                if ("重量" in c or "毛重" in c or "weight" in cl or "kg" in cl) and "weight" not in colmap:
                    colmap["weight"] = j
                if ("件数" in c or "几件" in c or "pcs" in cl or "每箱" in c) and "pcs" not in colmap:
                    colmap["pcs"] = j
                if ("ect" in cl or "边压" in c) and "ect" not in colmap:
                    colmap["ect"] = j
                if ("层数" in c or "堆" in c) and "maxlay" not in colmap:
                    colmap["maxlay"] = j
                if ("备注" in c or "note" in cl or "remark" in cl) and "note" not in colmap:
                    colmap["note"] = j
            if "code" in colmap and "L" in colmap and "cartons" in colmap:
                hdr_idx = i
                break
        if hdr_idx is None:
            continue
        skus = []
        for r in rows[hdr_idx + 1:]:
            def g(k):
                j = colmap.get(k)
                if j is None or j >= len(r):
                    return None
                return r[j]
            code = g("code")
            if code is None or str(code).strip() == "":
                continue
            try:
                L = float(g("L"))
                W = float(g("W"))
                H = float(g("H"))
                cartons = int(float(g("cartons")))
            except (TypeError, ValueError):
                continue
            if L <= 0 or W <= 0 or H <= 0 or cartons <= 0:
                continue
            name = g("name")
            wt = g("weight")
            pcs = g("pcs")
            ect = g("ect")
            ml = g("maxlay")
            note = g("note")
            skus.append(SKU(
                code=str(code).strip(),
                name=str(name).strip() if name else "",
                L=L, W=W, H=H, cartons=cartons,
                pcs_per_carton=int(float(pcs)) if pcs else 1,
                weight_kg=float(wt) if wt else 0.0,
                ect_kn_m=float(ect) if ect else None,
                max_stack_layers=int(float(ml)) if ml else None,
                note=str(note).strip() if note else "",
            ))
        if skus:
            batches[ws.title] = skus
            meta["sheets"].append(dict(name=ws.title, skus=len(skus),
                                       cartons=sum(s.cartons for s in skus),
                                       header_row=hdr_idx + 1,
                                       columns={k: v + 1 for k, v in colmap.items()}))
    return batches, meta


# ============================================================
# 求解 + 展开为标准 plan
# ============================================================
def _xform(cells, rot, pal_L, pal_W=None):
    """托盘局部坐标 → 容器坐标 (相对托盘左下角)。

    托盘局部坐标口径: **长边 pal_L 沿 X, 短边 pal_W 沿 Y** (与托盘自身绑定,
    不随摆放朝向改变)。solver 输出的 cells 即此口径。

    A 朝向 (托盘长边沿柜长):
        容器 (x, y) = 局部 (x, y)          宽高不变
    B 朝向 (托盘长边沿柜宽):
        容器 (x, y) = 局部 (y, x)          宽高互换
        → 局部 x∈[0,pal_L] → 容器 y∈[0,pal_L] (柜宽方向占 pal_L)
        → 局部 y∈[0,pal_W] → 容器 x∈[0,pal_W] (柜长方向占 pal_W)
    """
    if rot == "A":
        return [(round(x, 1), round(y, 1), round(w, 1), round(h, 1))
                for (x, y, w, h) in cells]
    return [(round(y, 1), round(x, 1), round(h, 1), round(w, 1))
            for (x, y, w, h) in cells]


def make_constraints(cfg_dict):
    """从配置字典构造 Constraints, 兼容旧字段名 (忽略未知键)。"""
    d = dict(cfg_dict or {})
    # 旧字段: repair_support → 由 support_policy 推导
    if "repair_support" in d:
        d.pop("repair_support")
    valid = set(Constraints.__init__.__code__.co_varnames)
    unknown = [k for k in d if k not in valid]
    for k in unknown:
        d.pop(k)
    return Constraints(**d)


def solve_batch(batch_name, skus, cfg, gate):
    """求解一个批次, 返回标准化 container 列表。"""
    box_code = cfg["container"]
    box = CONTAINERS[box_code]
    pal_code = cfg.get("pallet")
    pal = dict(PALLETS[pal_code])
    if cfg.get("custom_pallet"):
        pal.update(L=float(cfg["custom_pallet"]["L"]),
                   W=float(cfg["custom_pallet"]["W"]),
                   H=float(cfg["custom_pallet"]["H"]))
    cons = make_constraints(cfg["constraints"])
    smap = {s.code: s for s in skus}

    t0 = time.time()
    if cons.mode == "pallet":
        containers, pos, pallets = SV.solve_pallet_mode(box, skus, cons, pal)
    else:
        containers, pos = SV.solve_nopallet_mode(box, skus, cons)
        pallets = []
    elapsed = time.time() - t0

    out = []
    for ci, cc in enumerate(containers, 1):
        ps = []
        cart = 0
        vol = 0.0
        wt = 0.0
        per = defaultdict(int)
        z0 = pal["H"] if cons.mode == "pallet" else 0
        for (pi, p) in cc:
            if cons.mode == "pallet":
                # 位置绑定在托盘自身 (solver 分配时写入 _slot), 每个柜复用同一套柜内位
                _slot = p.get("_slot", pi)
                cx, cy, t = pos[_slot] if _slot < len(pos) else pos[0]
                pw, pd = (pal["L"], pal["W"]) if t == "A" else (pal["W"], pal["L"])
                ox, oy = cx - pw / 2, cy - pd / 2
                xf = lambda cs: _xform(cs, t, pal["L"])
            else:  # 无托盘: cells 已是容器绝对坐标, 恒等变换, 原点归零
                cx = p["_ox"] + p["_fw"] / 2
                cy = p["_oy"] + p["_fd"] / 2
                t = "A"
                pw, pd = p["_fw"], p["_fd"]
                ox, oy = 0.0, 0.0
                xf = lambda cs: cs
            boxes = []
            lcells = []
            tcells = []
            z = z0
            # 层 cells 是**托盘自身平面坐标** (长边 pal_L 沿局部 X), 与摆放朝向无关。
            # 由 _xform 统一换算到容器坐标 (B 朝向时转置)。
            for l in p["layers"]:
                cs = xf(l.get("cells", []))
                # 关键: cells 数量必须与 n 一致, 否则渲染箱数与统计箱数会不符
                # (修复机制可能只改了 cells 未改 n, 或反之 —— 这里以 cells 为准)
                n_l = len(cs)
                if n_l != l["n"]:
                    l = dict(l, n=n_l)
                lcells.append(dict(sku=l["sku"], vz=l["vz"], n=l["n"],
                                   cells=[list(c) for c in cs]))
                for (lx, ly, lw, lh) in cs:
                    # 几何自检: 展开后必须在托盘占位矩形内 (仅托盘模式适用;
                    # 无托盘的 cells 已是容器绝对坐标, 由 L1 出柜壁校验兜底)
                    if cons.mode == "pallet":
                        if (ox + lx < -0.6 or oy + ly < -0.6
                                or ox + lx + lw > ox + pw + 0.6
                                or oy + ly + lh > oy + pd + 0.6):
                            print(f"[GEOM] 柜{ci+1} 托{pi} rot={t} 越界: "
                                  f"cell=({lx},{ly},{lw},{lh}) 托面=({pw}x{pd}) "
                                  f"原始cells={l.get('cells', [])[:2]}",
                                  file=sys.stderr)
                    boxes.append(dict(x=round(ox + lx, 1), y=round(oy + ly, 1),
                                      z=round(z, 1), w=lw, d=lh, h=l["vz"],
                                      sku=l["sku"], kind="layer"))
                z += l["vz"]
                # 统计与 boxes 同源, 保证三方一致
                cart += l["n"]
                vol += smap[l["sku"]].cbm_per_carton * l["n"]
                per[l["sku"]] += l["n"]
                wt += smap[l["sku"]].weight_kg * l["n"]
            for tp in p.get("top", []):
                cs = xf(tp.get("cells", []))
                n_t = len(cs)
                if n_t != tp["n"]:
                    tp = dict(tp, n=n_t)
                tcells.append(dict(sku=tp["sku"], vz=tp["vz"], n=tp["n"],
                                   cells=[list(c) for c in cs]))
                # z_base: 该组平躺箱的高度起点。
                #   solver 的 z_base 是「托盘顶面起算」→ 容器坐标需加托盘高 z0
                z_base = tp.get("z_base")
                if z_base is None:
                    z_base = z
                else:
                    z_base = z_base + z0
                # 两种语义:
                #   cells 只有 1 个 → n 个箱**垂直叠放**在同一点 (top_h 分支)
                #   cells 有 n 个   → n 个箱**平铺**在托盘面 (半空层平躺提取)
                if len(cs) <= 1:
                    for i in range(tp["n"]):
                        if cs:
                            lx, ly, lw, lh = cs[0]
                            boxes.append(dict(x=round(ox + lx, 1), y=round(oy + ly, 1),
                                              z=round(z_base + i * tp["vz"], 1),
                                              w=lw, d=lh, h=tp["vz"],
                                              sku=tp["sku"], kind="top"))
                else:
                    for (lx, ly, lw, lh) in cs[:tp["n"]]:
                        boxes.append(dict(x=round(ox + lx, 1), y=round(oy + ly, 1),
                                          z=round(z_base, 1),
                                          w=lw, d=lh, h=tp["vz"],
                                          sku=tp["sku"], kind="top"))
                cart += tp["n"]
                vol += smap[tp["sku"]].cbm_per_carton * tp["n"]
                per[tp["sku"]] += tp["n"]
                wt += smap[tp["sku"]].weight_kg * tp["n"]
            # 注: 裸地箱 (_bare) 不在本托盘 boxes 内 —— 它以容器坐标单独挂在
            #     柜级 cont["bare"] 列表 (见下方 _bare 收集循环), 按柜底坐标校验/渲染。
            #     此前误把裸地箱注入 ps[].boxes 导致: (1) 按托盘边界判越界,
            #     (2) 与柜级 bare 重复计数 → 箱数虚高。现已分离。
            ps.append(dict(pi=pi + 1, cx=round(cx, 1), cy=round(cy, 1), rot=t,
                           w=pw, d=pd, z0=z0,
                           h=round(p["h"] + z0, 0),
                           body_h=round(p.get("body_h", p["h"]) + z0, 0),
                           cartons=p["cartons"], cbm=round(p["cbm"], 4),
                           skus=p.get("skus", []),
                           layers=p["layers"], top=p.get("top", []),
                           boxes=boxes, lcells=lcells, tcells=tcells))
        # 柜底裸地箱 (无托盘, 直接落在柜底板 → 支撑率 100%)
        bare_boxes = []
        for (_slot0, p0) in cc:
            for st in p0.get("_bare", []):
                bare_boxes.append(dict(x=round(st["x"], 1), y=round(st["y"], 1),
                                      z=round(st["z"], 1), fx=st["fx"], fy=st["fy"],
                                      h=st["vz"], sku=st["sku"], kind="bare"))
                cart += 1
                vol += smap[st["sku"]].cbm_per_carton
                per[st["sku"]] += 1
                wt += smap[st["sku"]].weight_kg
        out.append(dict(idx=ci, cartons=cart, cbm=round(vol, 3),
                        util=round(vol / box["vol"] * 100, 1),
                        npallets=len(ps) if cons.mode == "pallet" else 0,
                        hmax=round(max([p["h"] for p in ps] or [0])),
                        weight=round(wt, 1),
                        per_sku=dict(per), pallets=ps, stacks=[],
                        bare=bare_boxes))
    gate.info("L1", "SOLVE_TIME", f"批次 {batch_name} 求解耗时 {elapsed:.1f}s",
              params={"batch": batch_name, "elapsed": round(elapsed, 1)})
    return out, cons, box, pal


def build_plan(batches, cfg, gate):
    """求解所有批次, 组装标准 plan。

    柜号全局连续编号 (跨批次不重复) —— 否则 xlsx 托盘装载卡与 3D 渲染会
    因柜号撞车而串行, L3 一致性校验会失败。
    """
    all_containers = []
    batch_meta = []
    global_idx = 0
    for s in batches.get("_all_skus", []):
        pass

    for bname, skus in batches.items():
        if bname.startswith("_"):
            continue
        cons_out, cons, box, pal = solve_batch(bname, skus, cfg, gate)
        # 先做全局柜号编号 (跨批次唯一), 再校验 —— 保证校验/导出/渲染三方柜号一致
        for c in cons_out:
            c["batch_idx"] = c["idx"]          # 批次内序号 (保留)
            global_idx += 1
            c["idx"] = global_idx              # 全局柜号
            c["batch"] = bname
        plan = dict(box=box, pal=pal, cons=cons.to_dict(),
                    containers=cons_out,
                    required_cartons={bname: sum(s.cartons for s in skus)})
        check_geometry(plan, gate)
        check_stability(plan, gate, skus)
        all_containers.extend(cons_out)
        batch_meta.append(dict(batch=bname, skus=[s.code for s in skus],
                               containers=len(cons_out),
                               cartons=sum(c["cartons"] for c in cons_out),
                               cbm=round(sum(c["cbm"] for c in cons_out), 3),
                               util=round(sum(c["cbm"] for c in cons_out)
                                          / (len(cons_out) * box["vol"]) * 100, 1),
                               constraints=cons.describe()))
    return all_containers, batch_meta


def make_color_map(all_skus):
    return {s.code: PALETTE[i % len(PALETTE)] for i, s in enumerate(all_skus)}


# ============================================================
# CLI
# ============================================================
def main():
    ap = argparse.ArgumentParser(description="Container load optimizer / 集装箱装载优化")
    ap.add_argument("--input", required=True, help="Input Excel (one sheet per batch)")
    ap.add_argument("--config", required=True, help="Config file (JSON)")
    ap.add_argument("--outdir", required=True, help="Output directory")
    ap.add_argument("--allow-fail", action="store_true",
                    help="Still write output when validation fails (debug only)")
    ap.add_argument("--lang", default=None, choices=["zh", "en"],
                    help="Output language (default: zh; env WB_LANG=en also works)")
    args = ap.parse_args()

    # 语言: --lang 优先, 其次环境变量 WB_LANG, 默认中文
    if args.lang:
        i18n.set_lang(args.lang)
    else:
        i18n.resolve_cli_lang()

    with open(args.config, encoding="utf-8") as f:
        cfg = json.load(f)

    gate = Gate()
    batches, meta = parse_excel(args.input)
    if not batches:
        if i18n.get_lang() == "en":
            print("!! No batches parsed from the input — check the header row")
        else:
            print("!! 未从输入解析出任何批次, 请检查表头")
        return 2
    all_skus = []
    for b in batches.values():
        all_skus.extend(b)
    batches["_all_skus"] = all_skus
    meta["batches"] = {k: len(v) for k, v in batches.items() if not k.startswith("_")}

    en = (i18n.get_lang() == "en")
    if en:
        print(f"Parsed: {len(batches)-1} batch(es), {len(all_skus)} SKU(s), "
              f"{sum(s.cartons for s in all_skus)} cartons")
        for b in meta["sheets"]:
            print(f"   sheet '{b['name']}': {b['skus']} SKU / {b['cartons']} cartons "
                  f"(header row {b['header_row']})")
    else:
        print(f"解析: {len(batches)-1} 个批次, {len(all_skus)} 个 SKU, "
              f"{sum(s.cartons for s in all_skus)} 箱")
        for b in meta["sheets"]:
            print(f"   sheet '{b['name']}': {b['skus']} SKU / {b['cartons']} 箱 "
                  f"(表头第{b['header_row']}行)")

    containers, batch_meta = build_plan(batches, cfg, gate)
    color_map = make_color_map(all_skus)

    plan = dict(
        meta=dict(
            source=meta["source"], generated=time.strftime("%Y-%m-%d %H:%M:%S"),
            config=cfg, parse=meta,
            total_cartons=sum(c["cartons"] for c in containers),
            total_cbm=round(sum(c["cbm"] for c in containers), 3),
            total_containers=len(containers),
            color_map=color_map,
            sku_list=[s.to_dict() for s in all_skus],
        ),
        box=CONTAINERS[cfg["container"]],
        pal=dict(PALLETS[cfg.get("pallet", "US48")]),
        cons=make_constraints(cfg["constraints"]).to_dict(),
        containers=containers,
        batches=batch_meta,
        validation=gate.summary(),
    )
    if cfg.get("custom_pallet"):
        plan["pal"].update(cfg["custom_pallet"])

    os.makedirs(args.outdir, exist_ok=True)
    pj = os.path.join(args.outdir, "plan.json")
    with open(pj, "w", encoding="utf-8") as f:
        json.dump(plan, f, ensure_ascii=False, separators=(",", ":"))

    print("\n" + gate.report("L1+L2 校验" if not en else "L1+L2 validation"))
    if en:
        print(f"\nResult: {len(containers)} container(s), "
              f"{sum(c['cartons'] for c in containers)} cartons, "
              f"{sum(c['cbm'] for c in containers):.2f} CBM")
        for b in batch_meta:
            print(f"   Batch {b['batch']}: {b['containers']} container(s) / "
                  f"{b['cartons']} cartons / {b['util']}%")
    else:
        print(f"\n结果: {len(containers)} 柜, "
              f"{sum(c['cartons'] for c in containers)} 箱, "
              f"{sum(c['cbm'] for c in containers):.2f} CBM")
        for b in batch_meta:
            print(f"   批次 {b['batch']}: {b['containers']} 柜 / {b['cartons']} 箱 / "
                  f"{b['util']}%")
    print(f"→ {pj}")

    if not gate.passed and not args.allow_fail:
        if en:
            print("\n✗ Validation failed — delivery is blocked by policy "
                  "(use --allow-fail for debugging)")
        else:
            print("\n✗ 校验未通过, 按规定禁止交付 (可加 --allow-fail 调试)")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
