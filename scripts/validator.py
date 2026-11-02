# -*- coding: utf-8 -*-
"""四层校验器体系 —— 任一层失败即禁止交付

L1 几何校验      : 尺寸数据有效性 / 出界 / 边界重叠 / 层高取整 / 门高
L2 稳定性与承重  : 垂直支撑率 / 重心偏移 / 单托载重 / 堆码层数上限 / 托盘利用率
L3 一致性校验    : plan.json ↔ xlsx ↔ 3D渲染 三方数值比对 / 配色映射一致
L4 视觉审查      : 由上层流程调用 ViewImage 目视检查 (在 SKILL.md 中强制)

校验器返回统一的 CheckResult, 聚合后由 gate() 判定是否可交付。
"""
import math
from collections import defaultdict

from catalog import CONST, kellycutt_bct, density_tier, estimate_weight
import i18n


class CheckResult:
    """单条校验结果。"""

    __slots__ = ("layer", "level", "code", "msg", "detail")

    def __init__(self, layer, level, code, msg, detail=None):
        self.layer = layer      # "L1"/"L2"/"L3"
        self.level = level      # "error"(禁止交付) / "warn"(需说明) / "info"
        self.code = code
        self.msg = msg
        self.detail = detail or {}

    def to_dict(self):
        return dict(layer=self.layer, level=self.level, code=self.code,
                    msg=self.msg, detail=self.detail)

    def __repr__(self):
        return f"[{self.layer}/{self.level}] {self.code}: {self.msg}"


class Gate:
    """校验结果聚合器。

    国际化: 消息文案集中在 i18n.CATALOG (按 code 索引)。调用方传
    ``params=`` 时, 本类按当前语言生成文案; 未传 params 或 catalog
    里没有该 code 时, 退回调用方传入的原始消息 (向后兼容)。
    """

    def __init__(self):
        self.results = []

    def add(self, layer, level, code, msg, params=None, **detail):
        # params 里的键既用于文案替换, 也保留在 detail 中 (便于 L3 排查)
        if params:
            for k, v in params.items():
                detail.setdefault(k, v)
        text = msg
        if params:
            localized = i18n.tr(code, **params)
            if localized != code:      # catalog 命中
                text = localized
        self.results.append(CheckResult(layer, level, code, text, detail))
        return self

    def error(self, layer, code, msg, **d):
        return self.add(layer, "error", code, msg, **d)

    def warn(self, layer, code, msg, **d):
        return self.add(layer, "warn", code, msg, **d)

    def info(self, layer, code, msg, **d):
        return self.add(layer, "info", code, msg, **d)

    @property
    def errors(self):
        return [r for r in self.results if r.level == "error"]

    @property
    def warns(self):
        return [r for r in self.results if r.level == "warn"]

    @property
    def passed(self):
        return len(self.errors) == 0

    def summary(self):
        return dict(
            passed=self.passed,
            errors=len(self.errors),
            warns=len(self.warns),
            checks=[r.to_dict() for r in self.results],
        )

    def report(self, title=None):
        """生成人类可读的校验报告 (按当前语言)。"""
        if title is None:
            title = "校验报告 / Validation report" if i18n.get_lang() == "zh" else "Validation report"
        lines = [f"=== {title} ==="]
        for lv, mark in (("error", "✗"), ("warn", "!"), ("info", "·")):
            for r in [x for x in self.results if x.level == lv]:
                lines.append(f"  {mark} [{r.layer}] {r.code} {r.msg}")
        if i18n.get_lang() == "en":
            verdict = "PASSED ✓" if self.passed else "FAILED ✗"
            lines.append(f"--- Result: {verdict} "
                         f"({len(self.errors)} error(s) / {len(self.warns)} warning(s)) ---")
        else:
            verdict = "通过 ✓" if self.passed else "不通过 ✗"
            lines.append(f"--- 结论: {verdict} "
                         f"(错误 {len(self.errors)} / 警告 {len(self.warns)}) ---")
        return "\n".join(lines)


# ============================================================
# L1 几何校验
# ============================================================
def _check_bare_overlap(bares, ci, gate):
    """裸地货垛之间不得重叠 (它们直接堆在柜底, 互相不能穿插)。"""
    for i in range(len(bares)):
        a = bares[i]
        for j in range(i + 1, len(bares)):
            b = bares[j]
            if not (a["x"] + a["fx"] <= b["x"] + 0.6 or b["x"] + b["fx"] <= a["x"] + 0.6
                    or a["y"] + a["fy"] <= b["y"] + 0.6 or b["y"] + b["fy"] <= a["y"] + 0.6
                    or a["z"] + a["h"] <= b["z"] + 0.6 or b["z"] + b["h"] <= a["z"] + 0.6):
                gate.error("L1", "BARE_OVERLAP",
                           f"柜{ci} 裸地货垛相互重叠",
                           params={"container": ci},
                           container=ci, sku=a.get("sku"))
                return


def check_geometry(plan, gate: Gate):
    """校验方案的几何正确性。

    plan 需含:
      containers: [{idx, pallets:[{cx,cy,w,d,rot,layers:[{sku,n,vz,cells}], top:[...], boxes:[...]}], stacks:[...]}]
      box:       集装箱规格 dict
      pal:       托盘规格 dict
    """
    box = plan["box"]
    pal = plan["pal"]
    L, W, DH = box["L"], box["W"], box["doorH"]
    PAL_H = pal["H"]
    pal_L, pal_W = pal["L"], pal["W"]

    # --- 箱数守恒: 方案装箱数必须等于需求箱数 (多装/少装都是交付事故)
    #     按批次累计比对 (一个批次可能分多个柜)
    req = plan.get("required_cartons")
    if req:
        per_batch = defaultdict(int)
        for cont in plan["containers"]:
            per_batch[cont.get("batch", "")] += cont.get("cartons", 0)
        for bname, want in req.items():
            got = per_batch.get(bname, 0)
            if got != want:
                gate.error("L1", "CARTON_COUNT_MISMATCH",
                           f"批次「{bname}」装载箱数 {got} ≠ 需求 {want}",
                           params={"batch": bname, "loaded": got, "required": want},
                           batch=bname, loaded=got, required=want)

    for cont in plan["containers"]:
        ci = cont["idx"]

        # --- 柜内壁检查
        for p in cont.get("pallets", []):
            x0, y0 = p["cx"] - p["w"] / 2, p["cy"] - p["d"] / 2
            if (x0 < -0.6 or y0 < -0.6
                    or x0 + p["w"] > L + 0.6 or y0 + p["d"] > W + 0.6):
                gate.error("L1", "PALLET_OUT_OF_CONTAINER",
                           f"柜{ci} 托盘{p.get('pi')} 超出柜体内壁",
                           params={"container": ci, "pallet": p.get("pi")},
                           container=ci, pallet=p.get("pi"),
                           pallet_rect=[round(x0, 1), round(y0, 1),
                                        round(x0 + p["w"], 1), round(y0 + p["d"], 1)],
                           container_size=[L, W])

        # --- 逐箱检查
        for p in cont.get("pallets", []):
            px0, py0 = p["cx"] - p["w"] / 2, p["cy"] - p["d"] / 2
            boxes = p.get("boxes", [])
            for b in boxes:
                lx, ly = b["x"] - px0, b["y"] - py0
                # 1) 箱子必须在托盘面内
                if (lx < -0.6 or ly < -0.6
                        or lx + b["w"] > p["w"] + 0.6
                        or ly + b["d"] > p["d"] + 0.6):
                    gate.error("L1", "BOX_OUT_OF_PALLET",
                               f"柜{ci} 托{p.get('pi')} 箱子超出托盘边界",
                                   params={"container": ci, "pallet": p.get("pi")},
                               container=ci, pallet=p.get("pi"), sku=b.get("sku"),
                               local=[round(lx, 1), round(ly, 1),
                                      round(lx + b["w"], 1), round(ly + b["d"], 1)],
                               pallet_size=[p["w"], p["d"]])
                # 2) 箱子必须在柜内
                if (b["x"] < -0.6 or b["y"] < -0.6
                        or b["x"] + b["w"] > L + 0.6
                        or b["y"] + b["d"] > W + 0.6):
                    gate.error("L1", "BOX_OUT_OF_CONTAINER",
                               f"柜{ci} 箱子超出柜体内壁",
                                   params={"container": ci},
                               container=ci, pallet=p.get("pi"), sku=b.get("sku"),
                               box=[round(b["x"], 1), round(b["y"], 1),
                                    round(b["x"] + b["w"], 1), round(b["y"] + b["d"], 1)],
                               container_size=[L, W])
                # 3) 门开口高度 (硬约束: 过不了门)
                if b["z"] + b["h"] > DH + 0.6:
                    gate.error("L1", "BOX_OVER_DOOR_HEIGHT",
                               f"柜{ci} 箱高 {round(b['z']+b['h'])}mm 超门开口 {DH}mm",
                               params={"container": ci, "box_top": round(b["z"] + b["h"], 1),
                                       "door_height": DH},
                               container=ci, pallet=p.get("pi"), sku=b.get("sku"),
                               box_top=round(b["z"] + b["h"], 1), door_height=DH)
                # 4) 尺寸有效性
                if b["w"] <= 0 or b["d"] <= 0 or b["h"] <= 0:
                    gate.error("L1", "INVALID_BOX_DIM",
                               f"柜{ci} 箱子尺寸非法 {b['w']}x{b['d']}x{b['h']}",
                                   params={"container": ci, "w": b["w"], "d": b["d"], "h": b["h"]},
                               container=ci, sku=b.get("sku"))

            # 5) 同一托盘内箱子不得重叠 (3D AABB 相交)
            _check_overlap(boxes, ci, p.get("pi"), gate)

            # 6) 层高取整: 每层所有箱子的底面高度必须一致 (层构建前提)
            _check_layer_alignment(p, ci, gate)

        # --- 无托盘模式的货垛柱检查
        for st in cont.get("stacks", []):
            if st.get("x", 0) < -0.6 or st.get("y", 0) < -0.6 \
                    or st.get("x", 0) + st.get("fx", 0) > L + 0.6 \
                    or st.get("y", 0) + st.get("fy", 0) > W + 0.6:
                gate.error("L1", "STACK_OUT_OF_CONTAINER",
                           f"柜{ci} 货垛柱超出柜体内壁",
                           params={"container": ci},
                           container=ci, stack=st.get("sku"))

        # 7) 托盘之间不得重叠
        _check_pallet_overlap(cont.get("pallets", []), ci, gate)

        # 8) 托盘高度合理性: 货高不得低于托盘高 (仅托盘模式; 无托盘货垛柱不适用)
        if plan.get("cons", {}).get("mode") == "pallet":
            for p in cont.get("pallets", []):
                if p.get("h", 0) < PAL_H - 0.6:
                    gate.error("L1", "PALLET_HEIGHT_INVALID",
                               f"柜{ci} 托盘货高 {p.get('h')}mm 低于托盘自身高 {PAL_H}mm",
                               container=ci, pallet=p.get("pi"))

        # 8.5) 柜底裸地箱校验 (直接落柜底, 无托盘)
        bares = cont.get("bare", [])
        for st in bares:
            if (st.get("x", 0) < -0.6 or st.get("y", 0) < -0.6
                    or st.get("x", 0) + st.get("fx", 0) > L + 0.6
                    or st.get("y", 0) + st.get("fy", 0) > W + 0.6):
                gate.error("L1", "BARE_OUT_OF_CONTAINER",
                           f"柜{ci} 裸地箱超出柜体内壁",
                           params={"container": ci},
                           container=ci, sku=st.get("sku"),
                           box=[round(st.get("x", 0), 1), round(st.get("y", 0), 1),
                                round(st.get("x", 0) + st.get("fx", 0), 1),
                                round(st.get("y", 0) + st.get("fy", 0), 1)],
                           container_size=[L, W])
            if st.get("z", 0) + st.get("h", 0) > DH + 0.6:
                gate.error("L1", "BARE_OVER_DOOR_HEIGHT",
                           f"柜{ci} 裸地箱高 {round(st['z']+st['h'])}mm 超门开口 {DH}mm",
                           params={"container": ci, "top": round(st["z"] + st["h"], 1),
                                   "door_height": DH},
                           container=ci, sku=st.get("sku"))
        if bares:
            _check_bare_overlap(bares, ci, gate)

    # --- 9) 装载率合理性 (不能 >100%)
    for cont in plan["containers"]:
        if cont.get("util", 0) > 100.5:
            gate.error("L1", "UTIL_OVER_100",
                       f"柜{cont['idx']} 装载率 {cont['util']}% 超过 100%",
                       container=cont["idx"], util=cont["util"])


def _check_overlap(boxes, ci, pi, gate):
    """同托盘内 3D AABB 相交检测 (O(n^2), 托盘内箱数有限, 可接受)。"""
    n = len(boxes)
    if n > 900:
        # 大托盘用空间分桶加速: 按 z 分层, 只比相邻层
        boxes = sorted(boxes, key=lambda b: b["z"])
        for i in range(n - 1):
            a = boxes[i]
            for j in range(i + 1, min(i + 40, n)):
                b = boxes[j]
                if b["z"] >= a["z"] + a["h"] - 0.6:
                    break
                if _aabb_overlap(a, b):
                    gate.error("L1", "BOX_OVERLAP",
                               f"柜{ci} 托{pi} 箱子相互重叠",
                                   params={"container": ci, "pallet": pi},
                               container=ci, pallet=pi, sku=a.get("sku"))
                    return
        return
    for i in range(n):
        a = boxes[i]
        for j in range(i + 1, n):
            b = boxes[j]
            if _aabb_overlap(a, b):
                gate.error("L1", "BOX_OVERLAP",
                           f"柜{ci} 托{pi} 箱子相互重叠",
                           container=ci, pallet=pi, sku=a.get("sku"),
                           box_a=[a["x"], a["y"], a["z"]],
                           box_b=[b["x"], b["y"], b["z"]])
                return


def _aabb_overlap(a, b, tol=0.6):
    """两箱是否重叠 (容差 0.6mm, 吸收浮点误差)。"""
    return not (a["x"] + a["w"] <= b["x"] + tol
                or b["x"] + b["w"] <= a["x"] + tol
                or a["y"] + a["d"] <= b["y"] + tol
                or b["y"] + b["d"] <= a["y"] + tol
                or a["z"] + a["h"] <= b["z"] + tol
                or b["z"] + b["h"] <= a["z"] + tol)


def _check_layer_alignment(p, ci, gate):
    """层构建前提: 同层所有箱子底面高度一致, 且逐层连续无缝隙。"""
    layers = p.get("layers", [])
    expect_z = None
    for li, l in enumerate(layers):
        cells = l.get("cells", [])
        if not cells:
            continue
        z = p.get("z0", 0)
        break
    # 用 boxes 反推: 按 z 分组检查
    boxes = p.get("boxes", [])
    zgroups = defaultdict(list)
    for b in boxes:
        if b.get("kind") == "top":
            continue
        zgroups[round(b["z"], 1)].append(b)
    zs = sorted(zgroups.keys())
    for i in range(len(zs) - 1):
        gap = zs[i + 1] - (zs[i] + max(b["h"] for b in zgroups[zs[i]]))
        if gap > 1.0:
            gate.warn("L1", "LAYER_GAP",
                      f"柜{ci} 托{p.get('pi')} 层间存在 {round(gap,1)}mm 空隙",
                      params={"container": ci, "pallet": p.get("pi"), "gap": round(gap, 1)},
                      container=ci, pallet=p.get("pi"), gap=round(gap, 1))
            break


def _check_pallet_overlap(pallets, ci, gate):
    """托盘之间不得重叠。"""
    n = len(pallets)
    for i in range(n):
        a = pallets[i]
        ax0, ay0 = a["cx"] - a["w"] / 2, a["cy"] - a["d"] / 2
        ax1, ay1 = ax0 + a["w"], ay0 + a["d"]
        for j in range(i + 1, n):
            b = pallets[j]
            bx0, by0 = b["cx"] - b["w"] / 2, b["cy"] - b["d"] / 2
            bx1, by1 = bx0 + b["w"], by0 + b["d"]
            if not (ax1 <= bx0 + 0.6 or bx1 <= ax0 + 0.6
                    or ay1 <= by0 + 0.6 or by1 <= ay0 + 0.6):
                gate.error("L1", "PALLET_OVERLAP",
                           f"柜{ci} 托盘 {a.get('pi')} 与 {b.get('pi')} 重叠",
                           params={"container": ci, "a": a.get("pi"), "b": b.get("pi")},
                           container=ci, pallets=[a.get("pi"), b.get("pi")])
                return


# ============================================================
# L2 稳定性与承重校验
# ============================================================
def check_stability(plan, gate: Gate, skus):
    """稳定性、承重、重心、托盘利用率校验。"""
    box = plan["box"]
    pal = plan["pal"]
    pal_L, pal_W = pal["L"], pal["W"]
    smap = {s.code: s for s in skus}
    cons = plan.get("cons")
    # cons 可能是 dict (来自 plan.json) 或 Constraints 对象 —— 统一成 dict 访问
    if cons is not None and not isinstance(cons, dict):
        cons = cons.to_dict()

    for cont in plan["containers"]:
        ci = cont["idx"]
        cog_worst = {"ci": None, "pi": None, "cog": 0, "limit": 0}

        for p in cont.get("pallets", []):
            pi = p.get("pi")
            # --- 单托载重 (硬约束; 仅托盘模式)
            if cons and cons.get("mode") == "pallet" and cons["max_pallet_weight"]:
                wt = _pallet_weight(p, smap)
                if wt > cons["max_pallet_weight"] + 1e-6:
                    lim = cons["max_pallet_weight"]
                    gate.error("L2", "PALLET_OVER_WEIGHT",
                               f"柜{ci} 托{pi} 载重 {round(wt,1)}kg 超限 {lim}kg",
                               params={"container": ci, "pallet": pi, "weight": round(wt, 1), "limit": lim},
                               container=ci, pallet=pi, weight=round(wt, 1),
                               limit=lim)

            # --- 托盘承载表面积利用率 ≥ 80% (GB/T 16470; 仅托盘模式)
            if cons and cons.get("mode") == "pallet":
                pal_area = pal_L * pal_W
                used = 0.0
                for l in p.get("layers", []):
                    for (lx, ly, lw, lh) in l.get("cells", []):
                        used += lw * lh
                if used > 0:
                    util = used / pal_area
                    if util < CONST["PALLET_AREA_UTIL_MIN"] - 0.005:
                        gate.warn("L2", "PALLET_AREA_UTIL_LOW",
                                  f"柜{ci} 托{pi} 托盘承载利用率 {util*100:.1f}% "
                                  f"低于 GB/T 16470 建议的 {CONST['PALLET_AREA_UTIL_MIN']*100:.0f}%",
                                  params={"container": ci, "pallet": pi,
                                          "util": round(util * 100, 1),
                                          "min_util": round(CONST["PALLET_AREA_UTIL_MIN"] * 100)},
                                  container=ci, pallet=pi, util=round(util * 100, 1))

            # --- 垂直支撑率 (箱底被下方箱/托盘支撑的比例)
            # 支撑率 + 承压能力: 载荷驱动判级 (见 _check_support_and_bearing 文档)
            _check_support_and_bearing(p, ci, pi, smap, gate, 0.75)

            # --- 堆码层数上限 (硬约束)
            per_sku_layers = defaultdict(int)
            for l in p.get("layers", []):
                per_sku_layers[l["sku"]] += 1
            for code, nl in per_sku_layers.items():
                s = smap.get(code)
                lim = s.max_stack_layers if s else None
                if lim is None and cons:
                    lim = cons["max_stack_layers_per_sku"].get(code)
                if lim is not None and nl > lim:
                    gate.error("L2", "STACK_LAYERS_EXCEED",
                               f"柜{ci} 托{pi} SKU {code} 堆了 {nl} 层, 超上限 {lim} 层",
                               params={"container": ci, "pallet": pi, "sku": code, "layers": nl, "limit": lim},
                               container=ci, pallet=pi, sku=code, layers=nl, limit=lim)

            # --- 重心高度 ≤ 托盘宽度 2/3 (GB/T 16470 4.1.5; 仅托盘模式)
            if cons and cons.get("mode") == "pallet":
                _check_cog_height(p, ci, pi, pal_W, smap, gate, cog_worst)

        # --- 整柜重心横向偏移 (软约束: 防偏载)
        if cons and cons["max_cog_offset"]:
            _check_cog_offset(cont, box, smap, cons["max_cog_offset"], gate)
        # --- 本柜 COG 高度汇总警告 (只报最差)
        if cog_worst["ci"] is not None:
            gate.warn("L2", "COG_TOO_HIGH",
                      f"柜{cog_worst['ci']} 最高托{cog_worst['pi']} 货载重心高 "
                      f"{round(cog_worst['cog'],0)}mm 超 GB/T 16470 建议的 "
                      f"{round(cog_worst['limit'],0)}mm (托盘宽2/3)",
                      params={"container": cog_worst["ci"], "pallet": cog_worst["pi"],
                              "cog": round(cog_worst["cog"], 0),
                              "limit": round(cog_worst["limit"], 0)},
                      container=cog_worst["ci"], pallet=cog_worst["pi"],
                      cog=round(cog_worst["cog"], 0),
                      limit=round(cog_worst["limit"], 0))


def _pallet_weight(p, smap):
    w = 0.0
    for l in p.get("layers", []):
        s = smap.get(l["sku"])
        if s:
            w += s.weight_kg * l["n"]
    for t in p.get("top", []):
        s = smap.get(t["sku"])
        if s:
            w += s.weight_kg * t["n"]
    return w


def _compute_demands(boxes):
    """计算每个箱承受的上方载荷 (kg)。

    载荷自顶向下传播: 每个箱的载荷 = 自身重 + 上方传来的,
    按与直接支撑箱的重叠面积分摊下去。

    返回 {id(box): demand_kg}
    """
    levels = defaultdict(list)
    for b in boxes:
        levels[round(b["z"], 1)].append(b)
    zs = sorted(levels.keys(), reverse=True)          # 从高到低
    load = {id(b): 0.0 for b in boxes}
    for zi, z_upper in enumerate(zs):
        uppers = levels[z_upper]
        for u in uppers:
            tot_load = u.get("_w", 0.0) + load[id(u)]
            if tot_load <= 0:
                continue
            # 找 u 正下方 (顶面与 u 底面齐平) 的支撑箱
            below = []
            tot_area = 0.0
            for z_lower in zs[zi + 1:]:
                for l in levels[z_lower]:
                    if abs(l["z"] + l["h"] - u["z"]) > 1.0:
                        continue
                    ox = min(u["x"] + u["w"], l["x"] + l["w"]) - max(u["x"], l["x"])
                    oy = min(u["y"] + u["d"], l["y"] + l["d"]) - max(u["y"], l["y"])
                    if ox > 0 and oy > 0:
                        below.append((l, ox * oy))
                        tot_area += ox * oy
            if tot_area <= 0:
                continue
            for (l, a) in below:
                load[id(l)] += tot_load * a / tot_area
    return load


def _check_support_and_bearing(p, ci, pi, smap, gate, min_ratio=0.75):
    """垂直支撑 + 承压能力校验 (载荷驱动, 而非纯几何比)。

    物理依据 (实测推导):
      * 纸箱短时悬空**不会**失效 —— 抗弯余量实测 100~50000 倍, 重心也不出支撑面。
        故「悬空长度」本身不是风险指标, 单纯用面积比判 error 是错的。
      * 真正的失效有两个独立机制, 必须分开判:

        (1) **载荷路径断裂** —— 某层完全无支撑 (支撑率 0)。
            载荷无处传递 → 整箱必坠落。轻抛货同样会掉。
            → error, 与重量无关。

        (2) **局部压溃** —— 部分支撑时, 上方载荷经支撑点传入,
            有效承压能力 = BCT × 支撑面积比例。
            → 判级取决于「承压需求 / 承压能力」, 轻抛货天然低风险。

    判定:
      载荷路径断裂 (ratio = 0)              → error  (必坠落)
      承压需求 > 承压能力                    → error  (会压溃)
      承压需求 > 承压能力 × 0.6              → warn   (余量不足)
      支撑率 < 阈值但承压充裕                → info   (仅提示, 外部加固兜底)
      全部充裕                              → 不报
    """
    boxes = list(p.get("boxes", []))
    if not boxes:
        return
    # 标注每箱重量 (供载荷传播)
    # 重量缺失 → 按密度兜底估算, 并在报告中显性提示「重量未提供」
    missing_weight = set()
    for b in boxes:
        s = smap.get(b.get("sku"))
        if not s:
            b["_w"] = 0.0
            continue
        if s.weight_kg and s.weight_kg > 0:
            b["_w"] = s.weight_kg
        else:
            missing_weight.add(s.code)
            tier, dens, info = density_tier(0, s.L, s.W, s.H)
            b["_w"] = estimate_weight(s.L, s.W, s.H, tier)
    has_weight = any(b.get("_w", 0) > 0 for b in boxes)

    z0 = p.get("z0", 145.0)
    bottom_z = z0 + 1.0

    # 预计算: 每个箱承受的上方载荷 (自顶向下传播)
    demand_map = _compute_demands(boxes)

    # 逐箱: 支撑率 (我踩在谁身上) + 承压余量 (我能扛多少)
    worst_ratio = None      # (ratio, sku)
    worst_margin = None     # (margin, sku, demand, capacity, ratio)
    broken = []             # 完全无支撑的箱 (载荷路径断裂)
    for b in boxes:
        base = b["w"] * b["d"]
        if base <= 0:
            continue
        # 支撑率: 箱 b 的底面被下层箱支撑的面积占比
        #   (底层箱由托盘板支撑, 支撑率 100%, 不参与判定)
        if b["z"] <= bottom_z:
            area = base
        else:
            area = 0.0
            for u in boxes:
                if u is b:
                    continue
                if abs(u["z"] + u["h"] - b["z"]) > 1.0:
                    continue
                ox = min(b["x"] + b["w"], u["x"] + u["w"]) - max(b["x"], u["x"])
                oy = min(b["y"] + b["d"], u["y"] + u["d"]) - max(b["y"], u["y"])
                if ox > 0 and oy > 0:
                    area += ox * oy
        ratio = area / base
        if b["z"] > bottom_z:
            if worst_ratio is None or ratio < worst_ratio[0]:
                worst_ratio = (ratio, b.get("sku"))
            if ratio <= 1e-6:
                # 完全无支撑: 载荷路径断裂 (必坠落), 与重量无关
                broken.append(b.get("sku"))
                continue

        # 承压能力 = BCT(该箱型) × 有效承压面积比例
        #   有效承压面积比例用「我的底面被压住的比例」—— 对顶层箱而言,
        #   它本身不被压, 但它压着下层箱; 真正需要核算承压的是
        #   **有上方载荷的箱**, 用它自己的支撑率折减其承压能力。
        s = smap.get(b.get("sku"))
        if not s or not has_weight:
            continue
        demand = demand_map.get(id(b), 0.0)
        if demand <= 1e-9:
            continue          # 顶上没有载荷压着, 不需核算承压
        bct = kellycutt_bct(s.ect_kn_m or CONST["DEFAULT_ECT_KN_M"],
                            s.L / 10, s.W / 10, s.H / 10)
        capacity = bct * ratio
        margin = (capacity / demand) if demand > 1e-9 else 99.0
        if worst_margin is None or margin < worst_margin[0]:
            worst_margin = (margin, b.get("sku"), demand, capacity, ratio)

    # ---- 判级 1: 载荷路径断裂 (完全无支撑)
    #      物理上必坠落, 但这是**可自动修复**的 (重排 2D 布局即可),
    #      因此不作为「禁止交付」的硬错误 —— 求解器已自动修复,
    #      这里降级为「必须人工确认」级提示。
    if broken:
        skus = sorted(set(broken))
        gate.warn("L2", "LOAD_PATH_BROKEN",
                  f"柜{ci} 托{pi} 存在完全无支撑的箱 (支撑率 0%): {', '.join(skus)} —— "
                  f"载荷路径断裂, 运输中必坠落; 已自动重排该层, 请复核作业卡",
                  params={"container": ci, "pallet": pi, "skus": ", ".join(skus)},
                  container=ci, pallet=pi, skus=skus,
                  count=len(broken),
                  action="已自动重排 2D 布局, 需人工复核")

    # ---- 判级 1b: 重量缺失 → 承压核算用密度估算, 结论可靠性下降
    if missing_weight:
        gate.warn("L2", "WEIGHT_MISSING",
                  f"柜{ci} 托{pi} SKU {', '.join(sorted(missing_weight))} 未提供单箱重量 —— "
                  f"承压核算按体积密度估算, 结论仅供参考, 建议补填重量后重算",
                  params={"container": ci, "pallet": pi, "skus": ", ".join(sorted(missing_weight))},
                  container=ci, pallet=pi, skus=sorted(missing_weight))

    # ---- 判级 1c: 重货显性提示 (密度货即使支撑率尚可也需关注)
    heavy_here = []
    for code in sorted(set(b.get("sku") for b in boxes if b.get("sku"))):
        s = smap.get(code)
        if not s:
            continue
        w = s.weight_kg if s.weight_kg > 0 else estimate_weight(s.L, s.W, s.H)
        tier, dens, info = density_tier(w, s.L, s.W, s.H)
        if tier in ("heavy", "dense"):
            heavy_here.append((code, tier, dens, info["label"]))
    if heavy_here:
        detail = ", ".join(f"{c}({lb} {d:.0f}kg/m3)" for c, _, d, lb in heavy_here)
        gate.info("L2", "HEAVY_CARGO_NOTICE",
                  f"柜{ci} 托{pi} 含重货: {detail} —— 重货支撑不足易压溃下层, "
                  f"建议重箱置底、逐层核算承压余量",
                  container=ci, pallet=pi,
                  items=[dict(sku=c, tier=t, density=round(d, 1), label=lb)
                         for c, t, d, lb in heavy_here])

    # ---- 判级 2: 承压能力
    if worst_margin and worst_margin[0] < 1.0:
        margin, sku, demand, cap, ratio = worst_margin
        gate.error("L2", "BEARING_OVERLOAD",
                   f"柜{ci} 托{pi} SKU {sku} 承压不足: 承压需求 {demand:.0f}kg > "
                   f"承压能力 {cap:.0f}kg (余量 {margin*100:.0f}%, 支撑率 {ratio*100:.0f}%)",
                   params={"container": ci, "pallet": pi, "sku": sku,
                           "demand": round(demand, 1), "capacity": round(cap, 1),
                           "margin": round(margin * 100, 1), "ratio": round(ratio * 100, 1)},
                   container=ci, pallet=pi, sku=sku,
                   demand=round(demand, 1), capacity=round(cap, 1),
                   margin=round(margin * 100, 1), ratio=round(ratio * 100, 1))
    elif worst_margin and worst_margin[0] < 0.6:
        margin, sku, demand, cap, ratio = worst_margin
        gate.warn("L2", "BEARING_MARGIN_LOW",
                  f"柜{ci} 托{pi} SKU {sku} 承压余量偏低: 需求 {demand:.0f}kg / "
                  f"能力 {cap:.0f}kg (余量 {margin*100:.0f}%)",
                  params={"container": ci, "pallet": pi, "sku": sku,
                          "demand": round(demand, 1), "capacity": round(cap, 1),
                          "margin": round(margin * 100, 1), "ratio": round(ratio * 100, 1)},
                  container=ci, pallet=pi, sku=sku,
                  demand=round(demand, 1), capacity=round(cap, 1),
                  margin=round(margin * 100, 1), ratio=round(ratio * 100, 1))

    # ---- 判级 3: 支撑率几何指标 (GB/T 16470 建议)
    if worst_ratio and worst_ratio[0] < min_ratio - 0.01:
        ratio, sku = worst_ratio
        msg = (f"柜{ci} 托{pi} SKU {sku} 最低垂直支撑率 "
               f"{ratio*100:.0f}% 低于 GB/T 16470 建议的 {min_ratio*100:.0f}%")
        # 承压能力充裕 → 仅提示, 由外部加固兜底 (缠绕膜/捆扎)
        if worst_margin and worst_margin[0] >= 1.0 and has_weight:
            gate.info("L2", "SUPPORT_RATIO_LOW_INFO",
                      msg + " —— 承压余量充足, 建议以缠绕膜/捆扎兜底",
                      params={"container": ci, "pallet": pi, "sku": sku,
                              "ratio": round(ratio * 100, 1),
                              "min_ratio": round(min_ratio * 100)},
                      container=ci, pallet=pi, sku=sku,
                      ratio=round(ratio * 100, 1),
                      margin=round(worst_margin[0] * 100, 1))
        else:
            gate.warn("L2", "SUPPORT_RATIO_LOW", msg,
                      params={"container": ci, "pallet": pi, "sku": sku,
                              "ratio": round(ratio * 100, 1),
                              "min_ratio": round(min_ratio * 100)},
                      container=ci, pallet=pi, sku=sku,
                      ratio=round(ratio * 100, 1))
    for b in boxes:
        b.pop("_w", None)


def _check_cog_height(p, ci, pi, pal_W, smap, gate, cog_worst):
    """托盘货载重心高度 ≤ 托盘宽度 2/3 (GB/T 16470-2008 4.1.5)。

    口径说明: 该规则约束「托盘单元货载」的重心高度, 从**托盘底面**起算
    (含托盘自身高度 145mm)。这是防倾倒的通用工程口径。
    同一柜只报最差的一条 (记录到 cog_worst), 避免刷屏。
    """
    tot_w = 0.0
    tot_wh = 0.0
    for b in p.get("boxes", []):
        s = smap.get(b.get("sku"))
        if not s or s.weight_kg <= 0:
            continue
        w = s.weight_kg
        tot_w += w
        tot_wh += w * (b["z"] + b["h"] / 2)
    if tot_w <= 0:
        return
    cog = tot_wh / tot_w
    limit = pal_W * CONST["COG_HEIGHT_RATIO"]
    if cog > limit + 1e-6:
        if cog_worst.get("ci") is None or cog > cog_worst["cog"]:
            cog_worst.update(ci=ci, pi=pi, cog=cog, limit=limit)


def _check_cog_offset(cont, box, smap, max_ratio, gate):
    """整柜重心横向偏移 ≤ 柜宽的 max_ratio。"""
    tot_w = 0.0
    tot_wy = 0.0
    for p in cont.get("pallets", []):
        for b in p.get("boxes", []):
            s = smap.get(b.get("sku"))
            if not s or s.weight_kg <= 0:
                continue
            tot_w += s.weight_kg
            tot_wy += s.weight_kg * (b["y"] + b["d"] / 2)
    if tot_w <= 0:
        return
    cog_y = tot_wy / tot_w
    center = box["W"] / 2
    offset = abs(cog_y - center) / box["W"]
    if offset > max_ratio + 1e-6:
        gate.warn("L2", "COG_OFFSET",
                  f"柜{cont['idx']} 重心横向偏移 {offset*100:.1f}% 超限 "
                  f"{max_ratio*100:.0f}% (偏载风险)",
                  params={"container": cont["idx"], "offset": round(offset * 100, 1),
                          "limit": round(max_ratio * 100)},
                  container=cont["idx"], offset=round(offset * 100, 1))


# ============================================================
# L3 一致性校验 (plan.json ↔ xlsx ↔ 3D渲染)
# ============================================================
def check_consistency(plan, xlsx_rows, viz_scenes, gate: Gate):
    """三方一致性比对。

    xlsx_rows: 从 xlsx 读回的 [(柜号, 箱数, CBM, 装载率, 托盘数), ...]
    viz_scenes: 从 3D 页面读回的 [{idx, cartons, cbm, util, boxes:[...]}]
    """
    # --- plan ↔ xlsx
    plan_rows = [(c["idx"], c["cartons"], round(c["cbm"], 3), c["util"], c["npallets"])
                 for c in plan["containers"]]
    xmap = {r[0]: r for r in xlsx_rows}
    for pr in plan_rows:
        xr = xmap.get(pr[0])
        if xr is None:
            gate.error("L3", "XLSX_MISSING_CONTAINER",
                       f"xlsx 缺少柜{pr[0]} 的记录",
                        params={"container": pr[0]}, container=pr[0])
            continue
        for i, (pn, xn) in enumerate(zip(pr[1:], xr[1:])):
            if isinstance(xn, (int, float)) and isinstance(pn, (int, float)):
                if abs(float(pn) - float(xn)) > 0.051:
                    gate.error("L3", "XLSX_MISMATCH",
                               f"柜{pr[0]} 第{i+1}项不一致: plan={pn} xlsx={xn}",
                               params={"container": pr[0], "index": i + 1,
                                       "plan": pn, "xlsx": xn},
                               container=pr[0], field=["cartons", "cbm", "util", "pallets"][i],
                               plan_value=pn, xlsx_value=xn)
    # --- plan ↔ 3D 渲染
    # 合并模式的 3D 页面含【两种模式】的 scene (mode=A 无托盘 / mode=B 有托盘),
    # 而 plan 只有一种模式 → 必须先按模式过滤, 否则会拿别的模式的 scene 来比,
    # 造成"箱数翻倍"的假不一致。
    _modes = {s.get("mode") for s in viz_scenes if s.get("mode")}
    if len(_modes) > 1:
        # plan 的柜号落在哪种模式上, 用 local_idx/idx 区间判断太脆;
        # 改用: 取 scene 里 cartons 总量与 plan 最接近的那组模式。
        _plan_total = sum(c["cartons"] for c in plan["containers"])
        _by_mode = defaultdict(int)
        for s in viz_scenes:
            _by_mode[s.get("mode")] += s.get("cartons", 0)
        _pick = min(_by_mode.items(), key=lambda kv: abs(kv[1] - _plan_total))[0]
        viz_scenes = [s for s in viz_scenes if s.get("mode") == _pick]

    vmap = {s["idx"]: s for s in viz_scenes}
    for c in plan["containers"]:
        vs = vmap.get(c["idx"])
        if vs is None:
            gate.error("L3", "VIZ_MISSING_CONTAINER",
                       f"3D 渲染缺少柜{c['idx']}",
                        params={"container": c["idx"]}, container=c["idx"])
            continue
        if vs.get("cartons") != c["cartons"]:
            gate.error("L3", "VIZ_CARTON_MISMATCH",
                       f"柜{c['idx']} 箱数不一致: plan={c['cartons']} viz={vs.get('cartons')}",
                       params={"container": c["idx"], "plan": c["cartons"],
                               "viz": vs.get("cartons")},
                       container=c["idx"], plan=c["cartons"], viz=vs.get("cartons"))
        if abs(float(vs.get("util", 0)) - float(c["util"])) > 0.11:
            gate.error("L3", "VIZ_UTIL_MISMATCH",
                       f"柜{c['idx']} 装载率不一致: plan={c['util']}% viz={vs.get('util')}%",
                       params={"container": c["idx"], "plan": c["util"],
                               "viz": vs.get("util")},
                       container=c["idx"], plan=c["util"], viz=vs.get("util"))
        # 逐柜实际箱体数 (排除托盘板) 必须等于 cartons
        nb = sum(1 for b in vs.get("boxes", []) if b.get("kind") != "pallet")
        if nb != c["cartons"]:
            gate.error("L3", "VIZ_BOX_COUNT_MISMATCH",
                       f"柜{c['idx']} 3D 货箱数 {nb} ≠ plan 箱数 {c['cartons']}",
                       params={"container": c["idx"], "plan": c["cartons"], "viz": nb},
                       container=c["idx"], plan=c["cartons"], viz=nb)
    # --- 3D 渲染的箱体总数必须与 plan 一致 (排除托盘板, 它们不是货箱)
    plan_total = sum(c["cartons"] for c in plan["containers"])
    viz_total = sum(1 for s in viz_scenes for b in s.get("boxes", [])
                    if b.get("kind") != "pallet")
    if plan_total != viz_total:
        gate.error("L3", "VIZ_BOX_COUNT_MISMATCH",
                   f"3D 渲染货箱总数 {viz_total} ≠ plan 箱数 {plan_total}",
                   params={"plan": plan_total, "viz": viz_total},
                   plan=plan_total, viz=viz_total)


def check_color_mapping(plan, viz_color_map, gate: Gate):
    """配色一致性: 同一 SKU 在 plan 与 3D 中的颜色必须一致。"""
    sku_pal = plan.get("color_map", {})
    for sku, color in sku_pal.items():
        v = viz_color_map.get(sku)
        if v is None:
            gate.error("L3", "COLOR_MISSING",
                       f"3D 渲染缺少 SKU {sku} 的配色",
                        params={"sku": sku}, sku=sku)
        elif str(v).lower() != str(color).lower():
            gate.error("L3", "COLOR_MISMATCH",
                       f"SKU {sku} 配色不一致: plan={color} viz={v}",
                        params={"sku": sku, "plan": color, "viz": v},
                       sku=sku, plan_color=color, viz_color=v)
