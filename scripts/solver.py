# -*- coding: utf-8 -*-
"""托盘码垛 + 多柜分配 + 无托盘模式 —— 求解器

在重构版 engine.py 基础上, 完整支持 Constraints 的所有约束开关。
"""
import math
import random
from collections import defaultdict

from engine import (SKU, Constraints, MaxRects, orientations,
                    pallet_layer_modes, pallet_floor_layout)
from stacker import align_layer_to_support, SupportMap
from barearea import floor_free_rects, fill_bare_area
from catalog import CONST


# ============================================================
# 托盘码垛
# ============================================================
def _xform_cells(cells, rot, pal_L, pal_W=None):
    """A 朝向布局 → B 朝向布局 (托盘局部坐标口径: 长边沿 X)。

    B 朝向时托盘长边沿柜宽, 故托盘局部坐标需**转置**:
        new_x = y, new_y = x, new_w = h, new_h = w
    这与「90° 旋转」不同 —— 旋转保持长边沿 X, 而 B 朝向需要长边沿 Y。
    (托盘局部坐标始终以托盘自身长边为 X, 与摆放朝向解耦)
    """
    if rot == "A":
        return [(round(x, 1), round(y, 1), round(w, 1), round(h, 1))
                for (x, y, w, h) in cells]
    return [(round(y, 1), round(x, 1), round(h, 1), round(w, 1))
            for (x, y, w, h) in cells]


def _sku_dim(code, skus):
    """取某 SKU 的三个尺寸 (mm), 升序。"""
    for s in skus:
        if s.code == code:
            return sorted([s.L, s.W, s.H])
    return []


def _lay_options(skus, remain, pal, mono_main=None):
    """可平躺的箱型选项: (sku_code, 立高, 底边1, 底边2) 按立高升序。"""
    opts = []
    for s in skus:
        if remain.get(s.code, 0) <= 0:
            continue
        if mono_main is not None and s.code != mono_main:
            continue
        dims = sorted([s.L, s.W, s.H])
        opts.append((s.code, dims[0], dims[1], dims[2]))
    opts.sort(key=lambda t: t[1])
    return opts


def _layer_support_ok(prev_cells, new_cells, min_ratio, smap=None):
    """检查新层相对下层的垂直支撑率。

    prev_cells: 下层箱的 (x, y, w, h) 列表 (托盘局部坐标)
    new_cells:  新层箱的 (x, y, w, h) 列表
    返回 (是否通过, 最差支撑率)
    """
    if not prev_cells:
        return True, 1.0
    if smap is None:
        smap = SupportMap(prev_cells)
    worst = 1.0
    for (nx, ny, nw, nh) in new_cells:
        r = smap.ratio(nx, ny, nw, nh)
        if r < worst:
            worst = r
    return worst >= min_ratio - 1e-6, worst


def _build_with_top(main, skus, remain, pool, Hlim, rnd, cons,
                    top_h, lay_opts, pal_L, pal_W):
    """主体码垛 (上限 body_limit) + 顶部平躺区 (高 top_h)。"""
    rem = dict(remain)
    body_limit = Hlim - top_h
    layers = []
    h = 0.0
    cur = None
    guard = 0
    # 支撑率硬约束: 底层由托盘支撑(100%), 之上各层需满足最小支撑率
    # 支撑率硬约束阈值:
    #   strict 策略 → 用策略定义的阈值 (求解阶段即不可违反)
    #   其余        → 用显式传入的 min_support_ratio (None = 不约束求解)
    policy = Constraints.SUPPORT_POLICY.get(
        getattr(cons, "support_policy", "balanced"), None)
    if policy and policy.get("fix_threshold") is None:
        # strict: 支撑率为硬约束
        min_sup = max(cons.min_support_ratio or 0.0, 0.75)
    else:
        min_sup = cons.min_support_ratio if cons.min_support_ratio is not None else 0.0
    prev_cells = []
    smap = None
    while h < body_limit - 1e-6 and guard < 60:
        guard += 1
        cands = []
        # ---- 第一轮: 收集候选 (不做昂贵的对齐装箱)
        raw = []
        for s in skus:
            if rem.get(s.code, 0) <= 0:
                continue
            if cons.mono_sku and s.code != main:
                continue
            lim = s.max_stack_layers
            if lim is None:
                lim = cons.max_stack_layers_per_sku.get(s.code)
            for vz, (cpl, lab, lay, fx, fy) in pool[s.code].items():
                if h + vz > body_limit + 1e-6:
                    continue
                if lim is not None:
                    used = sum(1 for l in layers
                               if l["sku"] == s.code and abs(l["vz"] - vz) < 1e-6)
                    if used >= lim:
                        continue
                bonus = 10000 if s.code == main else 0
                raw.append((bonus + cpl, cpl, s.code, vz, lab, fx, fy))
        if not raw:
            break
        raw.sort(key=lambda t: (-t[0], -t[1]))

        # ---- 第二轮: 按优先级检查支撑, 只对有希望的候选做对齐装箱
        # (对齐装箱较贵, 限制尝试次数保证性能)
        align_budget = 6 if cons.optimize == "containers" else 3
        tried = 0
        for (_, cpl0, code, vz, lab, fx, fy) in raw:
            _lay = pool[code][vz][2]
            if not _lay:
                continue
            if not prev_cells or min_sup <= 0:
                cands.append(((10000 if code == main else 0) + cpl0, cpl0,
                              code, vz, lab, fx, fy, None))
                continue
            if smap is None:
                smap = SupportMap(prev_cells)
            ok, _ = _layer_support_ok(prev_cells, list(_lay)[:cpl0], min_sup, smap)
            if ok:
                cands.append(((10000 if code == main else 0) + cpl0, cpl0,
                              code, vz, lab, fx, fy, None))
                continue
            # 自由布局不达标 → 试探与下层对齐的布局 (受限预算)
            if tried >= align_budget:
                continue
            tried += 1
            an, alay, aratio = align_layer_to_support(
                prev_cells, fx, fy, pal_L, pal_W, min_sup)
            if an < 1:
                continue
            cands.append(((10000 if code == main else 0) + an, an,
                          code, vz, lab, fx, fy, alay))
        if not cands:
            # 支撑约束太严 → 放宽重试, 避免无解
            if prev_cells and min_sup > 0:
                min_sup = max(0.0, min_sup - 0.15)
                guard -= 1
                continue
            break
        cands.sort(key=lambda t: (-t[0], -t[1]))
        # ---- 连续性偏好: 上一层用过的 SKU 优先延续, 让同 SKU 的层相邻
        # (不改变选中的 SKU/层高/箱数, 只改变"同等条件下"的先后次序)
        if cur is not None:
            cands.sort(key=lambda t: (t[2] != cur, -t[0], -t[1]))
        if rnd.random() < 0.22:
            for _ in range(max(1, len(cands) // 6)):
                i = rnd.randrange(len(cands))
                j = rnd.randrange(len(cands))
                cands[i], cands[j] = cands[j], cands[i]
            # 随机打乱后重新施加连续性偏好 (否则随机会把同 SKU 拆开)
            if cur is not None:
                cands.sort(key=lambda t: (t[2] != cur, -t[0], -t[1]))
        _, cpl, code, vz, lab, fx, fy, alay = cands[0]
        n = min(cpl, rem.get(code, 0))
        if n <= 0:
            continue
        if alay is not None:
            cells = list(alay)[:n]
        else:
            _lay = pool[code][vz][2] if vz in pool[code] else None
            cells = list(_lay)[:n] if _lay else []
        layers.append(dict(sku=code, n=n, vz=vz, lab=lab, fx=fx, fy=fy,
                           cpl=cpl, cells=cells))
        rem[code] = rem.get(code, 0) - n
        prev_cells = cells
        smap = None
        h += vz
        cur = code
        if cons.mono_sku and rem.get(code, 0) <= 0:
            break

    if not layers:
        return None

    # ---- 「没铺满托盘面」的层 → 平躺挪到托盘顶部
    # 物理依据: 某层因余量不足只放了少量箱 (没铺满托盘面) 时,
    #   ① 若它排在中间 → 上层箱有一部分完全悬空 (必坠落);
    #   ② 它占满一整层高度 (如 388mm 只放 2 个箱) 也是浪费。
    # 做法: 把这些箱**平躺**放到托盘顶部区域 (立高 = 该箱型最短边),
    #   主体层就只剩「铺满」的层 → 上层箱有满支撑, 且不浪费整层高度。
    top_lay = []
    if len(layers) > 1 and getattr(cons, "extract_partial", False):
        layers, top_lay = _extract_partial_layers(layers, pool, pal_L, pal_W,
                                                 max_h=body_limit)
        # 提取后必须重算主体层高度 (partial 层已移出)
        h = sum(l["vz"] for l in layers)

    # ---- 顶部平躺区 (含「没铺满层」的平躺箱)
    top = []
    for t in top_lay:
        top.append(t)
    if top_h > 0:
        mr = MaxRects(pal_L, pal_W)
        # 连续性: 主体层已有的 SKU 优先进平躺区 (让同 SKU 的平躺箱紧邻其主体层),
        # 其余 SKU 按立高升序补位 —— 不改变平躺区的箱数与体积, 只改变 SKU 顺序。
        body_skus = set(l["sku"] for l in layers)
        lay_opts_sorted = sorted(
            lay_opts,
            key=lambda t: (0 if t[0] in body_skus else 1, t[1]))
        for (code, lh, f1, f2) in lay_opts_sorted:
            if rem.get(code, 0) <= 0:
                continue
            if cons.mono_sku and code != main:
                continue
            if lh > top_h + 1e-6:
                continue
            foot = (f1, f2) if f1 <= f2 else (f2, f1)
            placed = False
            for (fw, fd) in (foot, (foot[1], foot[0])):
                r = mr.place(fw, fd, "BSSF")
                if not r:
                    continue
                n = min(rem.get(code, 0), int(top_h // lh))
                if n < 1:
                    continue
                top.append(dict(sku=code, n=n, vz=lh,
                                lab=f"平躺 {f1}x{f2}x{lh}",
                                fx=fw, fy=fd, lay=True,
                                cells=[(r[0], r[1], r[2], r[3])]))
                rem[code] = rem.get(code, 0) - n
                placed = True
                break
            if placed:
                continue

    cartons = sum(l["n"] for l in layers) + sum(t["n"] for t in top)
    vol = 0.0
    smap = {s.code: s for s in skus}
    for l in layers:
        vol += smap[l["sku"]].cbm_per_carton * l["n"]
    for t in top:
        vol += smap[t["sku"]].cbm_per_carton * t["n"]
    consumed = {k: remain.get(k, 0) - rem.get(k, 0) for k in remain}
    return dict(layers=layers, top=top, h=h + (max([t["vz"] for t in top]) if top else 0),
                body_h=h, top_h=top_h, cartons=cartons, cbm=vol,
                consumed=consumed, main=main, _hlim=Hlim,
                skus=sorted(set([l["sku"] for l in layers] + [t["sku"] for t in top])))


def _try_build(main, skus, remain, pool, Hlim, rnd, cons, pal_L, pal_W):
    """尝试不同的顶部预留高度, 取最优。"""
    lay_opts = _lay_options(skus, remain, None, mono_main=main if cons.mono_sku else None)
    top_h_options = [0.0]
    if cons.allow_top:
        for (code, lh, f1, f2) in lay_opts[:4]:
            if lh <= Hlim - 200:
                top_h_options.append(lh)
    top_h_options = sorted(set(top_h_options))

    best = None
    for top_h in top_h_options:
        plan = _build_with_top(main, skus, remain, pool, Hlim, rnd, cons,
                               top_h, lay_opts, pal_L, pal_W)
        if plan is None:
            continue
        # 硬约束: 托盘货高不得超过可用高度 (门开口高 / TiHi / 限高)
        #   含「没铺满层平躺」的顶部箱 —— 那部分高度容易超预算
        if plan["h"] > Hlim + 1e-6:
            continue
        # 硬约束: 托盘货物高度上限
        if cons.max_pallet_stack_h and plan["h"] > cons.max_pallet_stack_h + 1e-6:
            continue
        # 硬约束: 单托限重
        if cons.max_pallet_weight:
            smap = {s.code: s for s in skus}
            wt = sum(smap[l["sku"]].weight_kg * l["n"] for l in plan["layers"])
            wt += sum(smap[t["sku"]].weight_kg * t["n"] for t in plan["top"])
            if wt > cons.max_pallet_weight + 1e-6:
                continue
        key = (plan["cartons"], plan["cbm"])
        if best is None or key > best[0]:
            best = (key, plan)
    return best[1] if best else None


def build_pallet(skus, remain, pool, Hlim, rnd, cons, pal_L, pal_W):
    """构建一个托盘: 以主SKU贯穿, 其它SKU按层高互补插入。"""
    cands = sorted([s.code for s in skus if remain.get(s.code, 0) > 0],
                   key=lambda c: -remain[c])[:3]
    best = None
    for main in cands:
        plan = _try_build(main, skus, remain, pool, Hlim, rnd, cons, pal_L, pal_W)
        if plan is None:
            continue
        key = (plan["cartons"], plan["cbm"])
        if best is None or key > best[0]:
            best = (key, plan)
    return best[1] if best else None


# ============================================================
# 定向修复: 消除支撑率风险
# ============================================================
def _layer_min_support(layers):
    """一组层中最差的层间支撑率 (相邻层)。"""
    prev = []
    worst = 1.0
    for l in layers:
        cells = l.get("cells", [])
        if not cells:
            continue
        if prev:
            smap = SupportMap(prev)
            for (x, y, w, h) in cells:
                r = smap.ratio(x, y, w, h)
                if r < worst:
                    worst = r
        prev = cells
    return worst


def _resync_top(tops, body_h, max_h=None):
    """重新同步顶部平躺箱的高度位置与总高。

    top 项的 z_base 是「托盘顶面起算」的高度起点。
    主体层被修复/重排后高度会变 → 必须重算 z_base, 否则箱会画在错误高度
    (曾导致超门高 + 箱体重叠)。

    返回 (tops, 总高)
    """
    if not tops:
        return tops, body_h
    # 按 z_base 升序排列, 依次叠放 (每组的高度加在前一组之上)
    tops = sorted(tops, key=lambda t: t.get("z_base", 0))
    cur = body_h
    keep = []
    for t in tops:
        if max_h is not None and cur + t["vz"] > max_h + 1e-6:
            # 放不下 → 丢弃这组平躺箱 (箱数退回需求, 由 _rebalance 补齐)
            continue
        t["z_base"] = cur
        cur += t["vz"]
        keep.append(t)
    return keep, cur


def _repair_all(pallets, pool, cons, pal_L, pal_W):
    """扫描全部托盘, 对支撑率不足者做对齐重排。

    修复门槛由 support_policy 决定:
      relaxed_all → 只修支撑率 = 0% 的 (必坠落, 与重量无关, 物理硬伤)
      balanced    → 修支撑率 < 0.55 的
      strict      → 求解阶段即硬约束, 正常不会到这里
    """
    policy = Constraints.SUPPORT_POLICY.get(
        getattr(cons, "support_policy", "balanced"), None)
    threshold = policy["fix_threshold"] if policy else 0.55
    if threshold is None:
        return pallets
    out = []
    for p in pallets:
        worst = _layer_min_support(p.get("layers", []))
        if worst <= 1e-9:
            fixed, changed = repair_support(p, pool, cons, pal_L, pal_W,
                                           CONS_CRITICAL_SUPPORT)
            if fixed is not None and changed:
                out.append(fixed)
                continue
        elif worst < threshold:
            fixed, changed = repair_support(p, pool, cons, pal_L, pal_W, threshold)
            if fixed is not None and changed:
                out.append(fixed)
                continue
        out.append(p)
    return out


def repair_support(pal, pool, cons, pal_L, pal_W, min_ratio=0.50):
    """对支撑率不足的托盘做定向重排: 整托逐层对齐重建。

    底层保持原布局 (托盘板支撑), 之后每层都在下层箱的顶面投影内重新排布。
    某层若在对齐区放不下原箱数, 则减少该层箱数 (差额由调用方回填)。
    """
    layers = pal.get("layers", [])
    if len(layers) < 2:
        return None, False
    new_layers = []
    prev_cells = []
    changed = False
    for li, l in enumerate(layers):
        fx, fy = l["fx"], l["fy"]
        n = l["n"]
        if li == 0:
            cells = list(l.get("cells", []))[:n]
            if not cells:
                return None, False
        else:
            if not prev_cells:
                return None, False
            an, alay, aratio = align_layer_to_support(
                prev_cells, fx, fy, pal_L, pal_W, min_ratio)
            if an < 1:
                return None, False
            cells = list(alay)[:min(an, n)]
            if len(cells) != n:
                changed = True
            elif [tuple(round(v, 1) for v in c) for c in cells] != \
                    [tuple(round(v, 1) for v in c) for c in list(l.get("cells", []))[:n]]:
                changed = True
        new_layers.append(dict(l, n=len(cells), cells=cells))
        prev_cells = cells
    if not changed:
        return None, False
    fixed = dict(pal)
    fixed["layers"] = new_layers
    fixed["cartons"] = sum(x["n"] for x in new_layers) + sum(t["n"] for t in fixed.get("top", []))
    fixed["body_h"] = sum(x["vz"] for x in new_layers)
    tops, tot_h = _resync_top(fixed.get("top", []), fixed["body_h"],
                             max_h=pal.get("_hlim"))
    fixed["top"] = tops
    fixed["h"] = tot_h
    fixed["repaired"] = True
    return fixed, True


def _rebalance(pallets, skus, pool, Hlim, cons, pal_L, pal_W, pal_h=145.0):
    """修复后若有箱数变化, 优先回填有空高的原托盘, 最后才开新托盘。

    关键: 修复会减少某些层的箱数, 那些托盘往往还有高度余量。
    若直接开新托盘, 托盘数增加 → 柜数增加 (实测 146→151 托, 7 柜→8 柜)。
    故先尝试回填, 尽量不增加托盘数。
    """
    consumed = {}
    for p in pallets:
        for l in p["layers"]:
            consumed[l["sku"]] = consumed.get(l["sku"], 0) + l["n"]
        for t in p.get("top", []):
            consumed[t["sku"]] = consumed.get(t["sku"], 0) + t["n"]
    rnd = random.Random(cons.seed * 31 + 7)
    guard = 0
    while guard < 80:
        guard += 1
        remain = {}
        for s in skus:
            r = s.cartons - consumed.get(s.code, 0)
            if r > 0:
                remain[s.code] = r
        if not remain:
            break
        filled = _refill_existing(pallets, remain, pool, Hlim, cons, pal_L, pal_W,
                                  pal_h=pal_h)
        if filled:
            for code, n in filled.items():
                consumed[code] = consumed.get(code, 0) + n
            continue
        extra = build_pallet(skus, remain, pool, Hlim, rnd, cons, pal_L, pal_W)
        if extra is None or extra["cartons"] == 0:
            break
        pallets.append(extra)
        for l in extra["layers"]:
            consumed[l["sku"]] = consumed.get(l["sku"], 0) + l["n"]
        for t in extra.get("top", []):
            consumed[t["sku"]] = consumed.get(t["sku"], 0) + t["n"]
    return pallets


def _refill_existing(pallets, remain, pool, Hlim, cons, pal_L, pal_W, pal_h=145.0):
    """把剩余箱回填到有空高的原托盘 (优先高度余量大的)。"""
    filled = {}
    order = sorted(range(len(pallets)),
                   key=lambda i: -(Hlim - pallets[i].get("body_h", 0)))
    for pi in order:
        p = pallets[pi]
        # 有顶部平躺的托盘不回填 —— 新层会与平躺箱在空间上冲突
        if p.get("top"):
            continue
        body_h = p.get("body_h", 0)
        cur_h = p.get("h", body_h)
        # 已有顶部平躺箱占用的高度不能再被回填占用
        top_h_used = sum(t["vz"] for t in p.get("top", []))
        avail = Hlim - top_h_used
        for s_code in list(remain.keys()):
            if remain[s_code] <= 0:
                continue
            best = None
            for vz, (cpl, lab, lay, fx, fy) in pool[s_code].items():
                if body_h + vz > avail + 1e-6:
                    continue
                # 门开口高硬约束: 托盘高 + 当前总货高 + 新层高 必须 ≤ 门开口
                if cons and getattr(cons, "door_height", None) is not None:
                    if pal_h + cur_h + vz > cons.door_height + 1e-6:
                        continue
                if any(l["sku"] == s_code for l in p["layers"]):
                    continue
                if best is None or cpl > best[0]:
                    best = (cpl, vz, lab, fx, fy)
            if best is None:
                continue
            cpl, vz, lab, fx, fy = best
            layers = p["layers"]
            prev = layers[-1].get("cells", []) if layers else []
            if prev:
                smap = SupportMap(prev)
                ok, _ = _layer_support_ok(prev, list(lay)[:cpl], 0.55, smap)
                if ok:
                    cells = list(lay)[:cpl]
                    n = cpl
                else:
                    an, alay, _ = align_layer_to_support(prev, fx, fy,
                                                        pal_L, pal_W, 0.55)
                    if an < 1:
                        continue
                    cells = list(alay)[:an]
                    n = an
            else:
                cells = list(lay)[:cpl]
                n = cpl
            n = min(n, remain[s_code])
            if n <= 0:
                continue
            # 关键: cells 必须截断到 n, 且 n 取自 cells 实际长度
            cells = cells[:n]
            n = len(cells)
            if n <= 0:
                continue
            layers.append(dict(sku=s_code, n=n, vz=vz, lab=lab, fx=fx, fy=fy,
                               cpl=cpl, cells=cells))
            p["body_h"] = body_h + vz
            tops, tot_h = _resync_top(p.get("top", []), p["body_h"])
            p["top"] = tops
            p["h"] = tot_h
            p["cartons"] = p["cartons"] + n
            remain[s_code] -= n
            filled[s_code] = filled.get(s_code, 0) + n
            break
    return filled


# 支撑率低于此值视为塌垛风险, 必须修复
CONS_CRITICAL_SUPPORT = 0.55
# 层序调整的触发阈值
CONS_REORDER_TRIGGER = 0.60


def _extract_partial_layers(layers, pool, pal_L, pal_W, max_h=None):
    """把「没铺满托盘面」的层提取出来, 平躺后放到托盘顶部。

    返回 (剩余主体层, 平躺箱列表)
      平躺箱项: dict(sku, n, vz, lab, fx, fy, lay=True, z_base, cells)
        z_base = 该组平躺箱的高度起点 (托盘顶面起算, 即剩余主体层总高)

    判据「没铺满」: 该层底面积覆盖率 明显低于「同箱型单层满布的覆盖率」。
    """
    pal_area = float(pal_L) * float(pal_W)
    if pal_area <= 0 or len(layers) < 2:
        return layers, []
    if max_h is None:
        max_h = float("inf")

    ref_cov = {}

    def coverage(l):
        cs = l.get("cells", [])
        a = sum(c[2] * c[3] for c in cs)
        return a / pal_area if pal_area else 0.0

    def is_partial(l):
        key = (l["sku"], round(l["vz"], 1))
        if key not in ref_cov:
            ent = pool.get(l["sku"], {}).get(l["vz"])
            if ent is None:
                ref_cov[key] = coverage(l)
            else:
                a = sum(c[2] * c[3] for c in ent[2])
                ref_cov[key] = a / pal_area if pal_area else 0.0
        ref = ref_cov[key]
        if ref <= 0:
            return False
        return coverage(l) < ref * 0.90

    # 判据 1: 该层自身没铺满 (相对同箱型满布覆盖率)
    # 判据 2: 该层**撑不住上一层** —— 下层覆盖率 < 上层覆盖率时,
    #         上层会有箱悬空。此时应把下层「平躺提取」而不是减上层箱数。
    def support_gap(layers):
        """返回撑不住上层的层下标集合。"""
        gaps = set()
        for i in range(len(layers) - 1):
            cov_lo = coverage(layers[i])
            cov_hi = coverage(layers[i + 1])
            # 上层覆盖率明显更高 → 下层撑不住
            if cov_hi > cov_lo * 1.10 + 0.02:
                gaps.add(i)
        return gaps

    gap_idx = support_gap(layers)
    keep = []
    partial = []
    for i, l in enumerate(layers):
        if is_partial(l) or i in gap_idx:
            partial.append(l)
        else:
            keep.append(l)
    if not partial or not keep:
        return layers, []

    # 平躺: 立高 = 该箱型最短边, 底脚印 = 另两边
    body_h = sum(l["vz"] for l in keep)
    # 主体层本身必须先满足高度限制 (移除 partial 层后不应更高)
    if body_h > max_h + 1e-6:
        return layers, []
    top_lay = []
    for l in partial:
        sku = l["sku"]
        n = l["n"]
        # 从 pool 找该箱型的三个尺寸
        dims = None
        for (vz, (cpl, lab, lay, fx, fy)) in pool[sku].items():
            dims = sorted([fx, fy, vz])
            # 取最短边立高, 另两边为底脚印
            cand = sorted([fx, fy, vz])
            if cand[0] == min(cand):
                dims = cand
                break
        if dims is None:
            continue
        lay_h = dims[0]
        foot = (dims[1], dims[2])
        if foot[0] > pal_L + 0.6 or foot[1] > pal_W + 0.6:
            # 底脚印放不下 (托盘面太窄) → 保留原层
            keep.append(l)
            body_h = sum(x["vz"] for x in keep)
            continue
        # 平躺后能叠几层 (顶部空间)
        mr = MaxRects(pal_L, pal_W)
        cells = []
        for _ in range(n):
            r = mr.place(foot[0], foot[1], "BSSF")
            if not r:
                break
            cells.append(r)
        if len(cells) < n:
            keep.append(l)
            body_h = sum(x["vz"] for x in keep)
            continue
        top_lay.append(dict(sku=sku, n=n, vz=lay_h,
                            lab=f"平躺 {foot[0]}x{foot[1]}x{lay_h}",
                            fx=foot[0], fy=foot[1], lay=True,
                            z_base=body_h, cells=cells,
                            z_container=body_h))
    if not top_lay:
        return layers, []
    # 高度校验: 平躺后总高 = 主体层高 + 平躺层高, 必须 ≤ 允许高度
    extra_h = max([t["vz"] for t in top_lay])
    if body_h + extra_h > max_h + 1e-6:
        return layers, []          # 平躺后反而更高 → 放弃, 保持原样
    return keep, top_lay


def _move_partial_layers_to_top(layers, pool, pal_L, pal_W):
    """把「没铺满」的层挪到托盘上部, 并重排各层 2D 布局。

    「没铺满」判据 = 该层的**底面积覆盖率** 明显低于「同箱型单层满布的覆盖率」。

    为什么不能用 `n < cpl`: cpl 只是该箱型单层最多能放几个, 不保证铺满托盘面。
      例: 428×423 的箱 cpl=6 (铺 82.2%), 但放 4 个时 n==cpl 却只铺 58%
      (2×2 排列, 四周留空) —— 若它排在中间, 上层箱就悬空。
    也不能用固定阈值 (如 85%): 托盘面"铺满"的极限本就是 80~95%
      (取决于箱型尺寸能否整除托盘面), 固定阈值会把正常满层误判。

    正确基准 = **同箱型在该托盘面上的自由 MaxRects 满布覆盖率**。
    partial 层之间按覆盖率**升序**排 —— 越不铺满的越靠上。
    """
    pal_area = float(pal_L) * float(pal_W)
    if pal_area <= 0:
        return layers

    ref_cov = {}

    def coverage(l):
        cs = l.get("cells", [])
        a = sum(c[2] * c[3] for c in cs)
        return a / pal_area if pal_area else 0.0

    def is_partial(l):
        key = (l["sku"], round(l["vz"], 1))
        if key not in ref_cov:
            p = pool.get(l["sku"], {})
            ent = p.get(l["vz"])
            if ent is None:
                ref_cov[key] = coverage(l)
            else:
                full = ent[2]
                a = sum(c[2] * c[3] for c in full)
                ref_cov[key] = a / pal_area if pal_area else 0.0
        ref = ref_cov[key]
        if ref <= 0:
            return False
        return coverage(l) < ref * 0.90

    full = [l for l in layers if not is_partial(l)]
    partial = [l for l in layers if is_partial(l)]
    if not partial or not full:
        return layers

    order_idx = sorted(range(len(partial)), key=lambda i: (coverage(partial[i]), i))
    partial_sorted = [partial[i] for i in order_idx]
    new_order = full + partial_sorted

    new_layers = []
    prev = []
    for l in new_order:
        n = l["n"]
        if not prev:
            cells = list(l.get("cells", []))[:n]
        else:
            smap = SupportMap(prev)
            ok, _ = _layer_support_ok(prev, list(l.get("cells", []))[:n], 0.75, smap)
            if ok:
                cells = list(l.get("cells", []))[:n]
            else:
                an, alay, _ = align_layer_to_support(prev, l["fx"], l["fy"],
                                                    pal_L, pal_W, 0.55)
                if an < 1:
                    cells = list(l.get("cells", []))[:n]
                else:
                    cells = list(alay)[:min(an, n)]
        if len(cells) != n:
            cells = list(l.get("cells", []))[:n]
        new_layers.append(dict(l, n=len(cells), cells=cells))
        prev = cells
    return new_layers


def _lift_problem_layers(pallets, pool, cons, pal_L, pal_W):
    """把「支撑不佳」的层尽量挪到托盘上部 —— 弱化层位置带来的影响。"""
    out = []
    n_reordered = 0
    for p in pallets:
        layers = p.get("layers", [])
        if len(layers) < 2:
            out.append(p)
            continue
        worst = _layer_min_support(layers)
        if worst > CONS_REORDER_TRIGGER:
            out.append(p)
            continue
        areas = [max((c[2] * c[3] for c in l.get("cells", [])), default=0)
                 for l in layers]
        idxs = list(range(len(layers)))
        best_order = idxs[:]
        best_score = None
        import itertools
        if len(layers) <= 8:
            for perm in itertools.permutations(idxs):
                score = 0.0
                for k in range(1, len(perm)):
                    a = areas[perm[k]]
                    b = areas[perm[k - 1]]
                    if b > 0:
                        score += abs(a - b) / b
                if best_score is None or score < best_score:
                    best_score = score
                    best_order = list(perm)
        if best_order == idxs:
            out.append(p)
            continue
        new_layers = []
        prev = []
        ok = True
        for li in best_order:
            l = layers[li]
            n = l["n"]
            if not prev:
                cells = list(l.get("cells", []))[:n]
            else:
                smap = SupportMap(prev)
                good, _ = _layer_support_ok(prev, list(l.get("cells", []))[:n],
                                            0.75, smap)
                if good:
                    cells = list(l.get("cells", []))[:n]
                else:
                    an, alay, _ = align_layer_to_support(
                        prev, l["fx"], l["fy"], pal_L, pal_W, 0.55)
                    if an < n:
                        ok = False
                        break
                    cells = list(alay)[:n]
            if len(cells) != n:
                ok = False
                break
            new_layers.append(dict(l, n=n, cells=cells))
            prev = cells
        if not ok:
            out.append(p)
            continue
        fixed = dict(p)
        fixed["layers"] = new_layers
        fixed["cartons"] = (sum(x["n"] for x in new_layers)
                            + sum(t["n"] for t in fixed.get("top", [])))
        fixed["body_h"] = sum(x["vz"] for x in new_layers)
        tops, tot_h = _resync_top(fixed.get("top", []), fixed["body_h"],
                             max_h=pal.get("_hlim"))
        fixed["top"] = tops
        fixed["h"] = tot_h
        fixed["reordered"] = True
        n_reordered += 1
        out.append(fixed)
    return out, n_reordered


def _reorient_pallet(p, pool, cons, pal_L, pal_W):
    """按托盘实际摆放朝向, 重算各层的 2D 布局 (保证层间支撑关系真实)。

    坐标系约定 (关键):
      * cells 始终是 **A 朝向的托盘局部坐标** (长边 pal_L 沿 X)。
      * 托盘转成 B 朝向时, 层与层的 footprints 重叠关系会改变
        (转置后 A 的 (x,y) 变成 B 的 (y,x))。
        所以这里做的不是「转置坐标」, 而是**按 B 朝向重新做层间对齐**:
        把每层的布局按 B 朝向的对应关系重新排布, 使层间支撑达标。
      * 容器坐标的换算由 pipeline 的 _xform 统一完成 (局部 → 容器)。

    实现: 对 B 朝向托盘, 逐层用「下层(B朝向坐标)作为支撑区」重新对齐。
    """
    rot = p.get("_rot", "A")
    if rot == "A":
        return
    layers = p.get("layers", [])
    if len(layers) < 2:
        return
    # B 朝向: 局部坐标 = A 朝向转置 (new_x=y, new_y=x, new_w=h, new_h=w)
    #   求解期算的层布局是 A 朝向的; 转成 B 朝向后要重新对齐。
    new_layers = []
    prev_b = []
    for l in layers:
        n = l["n"]
        # 本层在 B 朝向下的候选位置 (由 A 朝向 cells 转置得到)
        cand = _transpose_cells(l.get("cells", []))
        if not prev_b:
            cells = cand[:n]
        else:
            smap = SupportMap(prev_b)
            ok, _ = _layer_support_ok(prev_b, cand[:n], 0.75, smap)
            if ok:
                cells = cand[:n]
            else:
                an, alay, _ = align_layer_to_support(prev_b, l["fy"], l["fx"],
                                                    pal_L, pal_W, 0.55)
                if an < 1:
                    cells = cand[:n]
                else:
                    cells = list(alay)[:min(an, n)]
        new_layers.append(dict(l, n=len(cells), cells=cells))
        prev_b = cells
    p["layers"] = new_layers
    p["_reoriented"] = True


def _transpose_cells(cells):
    """A 朝向局部坐标 → B 朝向局部坐标 (转置)。"""
    out = []
    for (x, y, w, h) in cells:
        out.append((round(y, 1), round(x, 1), round(h, 1), round(w, 1)))
    return out


BARE_PLAN = []          # 交给柜底裸地装的箱 [(sku, n), ...]


def _bare_total(bare_cap):
    return sum(bare_cap.values()) if bare_cap else 0


def smap_of(skus):
    return {s.code: s for s in skus}


def _bare_capacity(box_spec, pos, pal_L, pal_W, skus, smap, door_h):
    """估算柜底裸地能容纳的箱数 (各 SKU 分别估算)。

    做法: 先算裸地区域 (扫描线求差集), 再对每个 SKU 估算能在裸地里
    堆多少箱 (按其最扁朝向的网格排布, 层数受门开口高约束)。

    返回 {sku_code: 可放箱数}
    """
    L, W = box_spec["L"], box_spec["W"]
    rects = []
    for (cx, cy, t) in pos:
        w, h = ((pal_L, pal_W) if t == "A" else (pal_W, pal_L))
        rects.append((cx - w / 2, cy - h / 2, cx + w / 2, cy + h / 2))
    free = floor_free_rects(L, W, rects)
    if not free:
        return {}
    # 各 SKU 在裸地里的装箱能力 (按最扁朝向)
    caps = {}
    for s in skus:
        dims = sorted([s.L, s.W, s.H])
        best = 0
        for (fx, fy, vz) in ((dims[0], dims[1], dims[2]),
                             (dims[0], dims[2], dims[1]),
                             (dims[1], dims[2], dims[0])):
            n_layers = int(door_h // vz)
            if n_layers < 1:
                continue
            cnt = 0
            for (rx, ry, rw, rh) in free:
                nx = int((rw + 0.6) // fx)
                ny = int((rh + 0.6) // fy)
                if nx < 1 or ny < 1:
                    continue
                cnt += nx * ny * n_layers
            if cnt > best:
                best = cnt
        caps[s.code] = best
    return caps


def _move_tail_pallet_to_bare(pallets, skus, smap, pos, box_spec,
                              pal_L, pal_W, Hlim, cons):
    """把「装不满的尾托」改为裸地散装。

    条件:
      * 该托有 SKU 未装完 (装不满)
      * 该托的箱数不多 (移除它不会明显降低装载率)
      * 余量能放进柜底裸地 (按裸地容量估算)
    效果: 托盘数 -1 → 可能少一个柜; 裸地空间被利用。
    """
    if not pallets:
        return
    # 找最后一个装不满的托
    idx = None
    for i in range(len(pallets) - 1, -1, -1):
        p = pallets[i]
        # 该托的 SKU 是否还有剩余
        has_partial = False
        for l in p["layers"]:
            if l["n"] < l["cpl"]:
                has_partial = True
        if has_partial:
            idx = i
            break
    if idx is None:
        return
    p = pallets[idx]
    # 该托的箱数不能太多 (否则拆掉损失大)
    n_box = p.get("cartons", 0)
    if n_box > 40:
        return
    # 裸地容量必须够
    cap = _bare_capacity(box_spec, pos, pal_L, pal_W, skus, smap,
                         box_spec["doorH"])
    if not cap:
        return
    # 统计该托各 SKU 的箱数 (含顶部平躺)
    cnt = defaultdict(int)
    for l in p["layers"]:
        cnt[l["sku"]] += l["n"]
    for t in p.get("top", []):
        cnt[t["sku"]] += t["n"]
    # 裸地必须装得下 (按容量, 保守取 70%)
    enough = all(cnt[k] <= cap.get(k, 0) * 0.7 for k in cnt)
    if not enough:
        return
    # 移除该托, 把它的箱记入裸地计划
    pallets.pop(idx)
    for (code, n) in cnt.items():
        BARE_PLAN.append((code, n))


def _strip_from_pallet(plan, remain, skus):
    """从托盘里取出「剩余需求」那部分箱, 改放柜底裸地。

    取法: 按层从顶往下, 把该层箱数减到能腾出所需数量的最小值。
    返回 [(sku, n), ...] 实际取出的箱。
    """
    want = {k: v for k, v in remain.items() if v > 0}
    if not want:
        return []
    moved = []
    # 从最后一层开始削
    for l in reversed(plan.get("layers", [])):
        code = l["sku"]
        if code not in want or want[code] <= 0:
            continue
        n = l.get("n", 0)
        take = min(want[code], n)
        if take <= 0:
            continue
        l["n"] = n - take
        if l["cells"]:
            l["cells"] = l["cells"][:l["n"]]
        want[code] -= take
        moved.append((code, take))
        if all(v <= 0 for v in want.values()):
            break
    if not moved:
        return []
    # 顶部平躺箱也削
    tops = plan.get("top", [])
    for t in reversed(tops):
        code = t["sku"]
        if code not in want or want[code] <= 0:
            continue
        take = min(want[code], t.get("n", 0))
        if take <= 0:
            continue
        t["n"] -= take
        want[code] -= take
        moved.append((code, take))
        if all(v <= 0 for v in want.values()):
            break
    # 重算托盘高度与箱数
    plan["cartons"] = sum(l["n"] for l in plan.get("layers", [])) \
        + sum(t["n"] for t in plan.get("top", []))
    plan["body_h"] = sum(l["vz"] for l in plan.get("layers", []))
    tops_h = max([t["vz"] for t in plan.get("top", []) if t.get("n", 0) > 0],
                 default=0)
    plan["h"] = plan["body_h"] + tops_h
    plan["consumed"] = {l["sku"]: 0 for l in plan.get("layers", [])}
    for l in plan.get("layers", []):
        plan["consumed"][l["sku"]] = plan["consumed"].get(l["sku"], 0) + l["n"]
    for t in plan.get("top", []):
        plan["consumed"][t["sku"]] = plan["consumed"].get(t["sku"], 0) + t["n"]
    return moved


def solve_pallet_mode(box_spec, skus, cons: Constraints, pal_spec):
    """托盘模式求解。

    返回 (containers, pos, pallets)
      containers: [[(slot, 托盘dict), ...], ...]  每个元素是一个柜
      pos:       [(cx, cy, 'A'|'B'), ...]         柜底托盘位
      pallets:   [托盘dict, ...]                   全部托盘
    """
    L = box_spec["L"]
    W = box_spec["W"]
    DH = box_spec["doorH"]
    PAL_H = pal_spec["H"]
    pal_L, pal_W = pal_spec["L"], pal_spec["W"]

    hcap = DH - PAL_H
    if cons.allow_double_pallet:
        hcap = DH - 2 * PAL_H
    Hlim = hcap
    if cons.max_pallet_stack_h:
        Hlim = min(Hlim, cons.max_pallet_stack_h - PAL_H)
    if cons.tihi_layers:
        tihi_max = CONST["TIHI"][cons.tihi_layers]["max"]
        Hlim = min(Hlim, tihi_max - PAL_H)
    Hlim = max(Hlim, 0)
    cons.door_height = DH

    del BARE_PLAN[:]          # 清空上次的裸地计划
    pos = pallet_floor_layout(L, W, pal_L, pal_W)
    pool = {s.code: pallet_layer_modes(s, pal_L, pal_W) for s in skus}

    # ---- 柜底裸地容量 (装不下整托盘的零散空间)
    # 托盘位铺满后仍有约 8% 的柜底是裸地 (40HQ 约 2.29 m2)。这些空间
    # 放不下整托盘, 但能放散装货垛 —— 不利用就是浪费。
    # 先算裸地能装多少箱, 托盘构建时把余量留给它, 而不是硬开第 N+1 个柜。
    bare_cap = _bare_capacity(box_spec, pos, pal_L, pal_W, skus, smap_of(skus),
                              DH)

    # ---- 裸地配额: 柜底零散空间能装多少箱 (先定配额, 托盘只装其余的)
    #      裸地虽小 (40HQ 约 2.29 m2), 但它是「放不下整托盘却能放下几个箱」的空间。
    #      让它承担尾数, 就不必为此多开一个几乎空的托盘 → 可能省一个柜。
    bare_quota = {}
    if getattr(cons, "use_bare_area", False):
        _bq = _bare_capacity(box_spec, pos, pal_L, pal_W, skus, smap_of(skus), DH)
        for _s in skus:
            # 保守取 60%: 裸地形状不规则, 理论容量难以完全利用
            bare_quota[_s.code] = int(_bq.get(_s.code, 0) * 0.6)

    best = None
    best_key = None
    best_bare = []
    # ---- 裸地配额: 柜底零散空间 (装不下整托盘却能放下几个箱) 承担一部分尾数,
    #      这样就不必为此多开一个几乎空的托盘 → 可能省一个柜。
    #      配额在 restart 循环**外**确定一次, 避免各轮重复扣减。
    bare_reserve = {}
    if getattr(cons, "use_bare_area", True) and bare_quota:
        for _c, _q in bare_quota.items():
            if _q > 0:
                bare_reserve[_c] = _q

    for rs in range(cons.restarts):
        rnd = random.Random(cons.seed * 104729 + rs * 7919)
        del BARE_PLAN[:]          # 每轮 restart 重置裸地计划 (否则会累积)
        round_bare = []
        remain = {s.code: s.cartons for s in skus}
        # 裸地配额: 本轮从托盘需求里扣掉, 这些箱留给柜底裸地
        for _c, _q in bare_reserve.items():
            if remain.get(_c, 0) > 0 and _q > 0:
                _take = min(_q, remain[_c])
                round_bare.append((_c, _take))
                remain[_c] -= _take
        pallets = []
        guard = 0
        while sum(remain.values()) > 0 and guard < 400:
            guard += 1
            main = max(remain, key=lambda c: remain[c]) if remain else None
            if main is None or remain[main] <= 0:
                main = next((c for c in remain if remain[c] > 0), None)
            if main is None:
                break
            plan = build_pallet(skus, remain, pool, Hlim, rnd, cons, pal_L, pal_W)
            if plan is None or plan["cartons"] == 0:
                break
            for k, v in plan["consumed"].items():
                remain[k] = remain.get(k, 0) - v
            pallets.append(plan)
        if not pallets:
            continue
        key = (len(pallets), -sum(p["cartons"] for p in pallets))
        if best_key is None or key < best_key:
            best_key = key
            best = pallets
            best_bare = list(round_bare)
    if best is None:
        return [], pos, []
    del BARE_PLAN[:]
    BARE_PLAN.extend(best_bare)

    # ---- 定向修复
    if cons.repair_support or cons.reorder_layers:
        for _round in range(6):
            before = sum(p["cartons"] for p in best)
            if cons.reorder_layers:
                best, _nre = _lift_problem_layers(best, pool, cons, pal_L, pal_W)
            best = _repair_all(best, pool, cons, pal_L, pal_W)
            if sum(p["cartons"] for p in best) < before:
                best = _rebalance(best, skus, pool, Hlim, cons, pal_L, pal_W,
                                  pal_h=pal_spec.get('H', 145.0))
                best = _repair_all(best, pool, cons, pal_L, pal_W)
            after = sum(p["cartons"] for p in best)
            if after == before:
                break

    # ---- 箱数守恒自检 (硬门禁)
    for s in skus:
        got = sum(l["n"] for p in best for l in p["layers"] if l["sku"] == s.code)
        got += sum(t["n"] for p in best for t in p.get("top", []) if t["sku"] == s.code)
        if got != s.cartons:
            remain_fix = {s.code: s.cartons - got}
            if remain_fix[s.code] > 0:
                extra = build_pallet(skus, remain_fix, pool, Hlim,
                                     random.Random(cons.seed * 97 + 13),
                                     cons, pal_L, pal_W)
                if extra and extra["cartons"] > 0:
                    best.append(extra)

    # ---- 分配到柜位 (FFD)
    # 关键: pos 是「柜内托盘位」, 每个柜复用同一套 pos[0..n-1]。
    #       分配时**直接绑定位置**到托盘, 后续排序/校验/导出都读托盘自带的
    #       _slot/_rot, 不再反查索引 (反查会因排序重置索引而错位)。
    containers = []
    for i, p in enumerate(best):
        if containers and len(containers[-1]) < len(pos):
            slot = len(containers[-1])
            containers[-1].append((slot, p))
        else:
            slot = 0
            containers.append([(slot, p)])
        p["_slot"] = slot
        p["_rot"] = pos[slot][2] if slot < len(pos) else "A"

    # 注: 层布局是在**托盘自身平面**内算的 (长边 pal_L 沿局部 X), 与摆放朝向无关。
    #     托盘转成 B 朝向时只是「同一张平面图在柜内转了 90°」,
    #     坐标换算由 pipeline 的 _xform 统一完成 (托盘局部 → 容器)。
    #     因此这里**不需要**按朝向重排层布局 —— 早期版本误加此步骤导致双重转置,
    #     表现为「箱子超出托盘边界」。

    # 柜内排序: 重货托在前 (靠近柜门), 同重按高度降序
    smap = {s.code: s for s in skus}
    for cc in containers:
        cc.sort(key=lambda t: (-_pallet_weight(t[1], smap), -t[1]["h"]))

    # ---- 柜底裸地填充: 把余量箱装进柜内零散空间 (不放托盘的地方)
    #      仅在显式开启且已验证时启用 (当前默认关闭, 见 use_bare_area)
    if BARE_PLAN and getattr(cons, "use_bare_area", False):
        _fill_bare_areas(containers, pos, BARE_PLAN, skus, smap, box_spec,
                         pal_L, pal_W, DH)

    # ---- 柜数硬上限: 超出部分最末柜溶解为柜底裸地 (守住 max_containers)
    #      放在裸地填充之后: 先填零散余量, 再压柜数上限, 二者叠加利用裸地。
    _cap_to_max(containers, pos, cons.max_containers, skus, smap, box_spec,
                pal_L, pal_W, DH)

    return containers, pos, best


def _fill_one_container_bare(cc, pos, want, limit, skus, smap, box_spec,
                             pal_L, pal_W, door_h):
    """在单个柜的柜底裸地 (托盘占位外的地面) 堆放散箱, 上限 limit 个。

    做法: 逐 SKU (按剩余量从多到少优先), 在每块裸地自由区域 (free rect) 的网格上
    逐箱落位, **每次落位都与已放置裸地箱做 3D AABB 重叠检测** —— 因此不同 SKU
    可以共用同一块区域而不会重叠, 同时把散箱尽量密铺进碎片化的裸地, 避免丢箱。

    从可变 dict want {sku: n} 扣除已放置数; 返回实际放置数。
    裸地箱直接落在柜底 (无托盘) → 底层支撑率 100%。
    """
    L, W = box_spec["L"], box_spec["W"]
    pallets_in_cab = [(slot, p) for (slot, p) in cc
                      if isinstance(slot, int) and slot < len(pos)]
    if not pallets_in_cab:
        return 0
    rects = []
    for (slot, p) in pallets_in_cab:
        cx, cy, t = pos[slot]
        w, h = ((pal_L, pal_W) if t == "A" else (pal_W, pal_L))
        rects.append((cx - w / 2, cy - h / 2, cx + w / 2, cy + h / 2))
    free = floor_free_rects(L, W, rects)
    if not free:
        return 0

    def _overlap(a, b):
        # cand = (x, y, z, fx, fy, vz)  → 轴向包络: x+fx, y+fy, z+vz
        return not (a[0] + a[3] <= b[0] + 0.6 or b[0] + b[3] <= a[0] + 0.6
                    or a[1] + a[4] <= b[1] + 0.6 or b[1] + b[4] <= a[1] + 0.6
                    or a[2] + a[5] <= b[2] + 0.6 or b[2] + b[5] <= a[2] + 0.6)

    placed = []          # 已落位裸地箱 (x,y,z,fx,fy,vz) 用于重叠检测
    placed_out = []      # 输出 dict
    placed_count = 0
    for code in sorted(want.keys(), key=lambda c: -want[c]):
        if want[code] <= 0 or placed_count >= limit:
            continue
        s = smap.get(code)
        if not s:
            continue
        dims = sorted([s.L, s.W, s.H])
        for (fx, fy, vz) in ((dims[0], dims[1], dims[2]),
                             (dims[0], dims[2], dims[1]),
                             (dims[1], dims[2], dims[0])):
            if want[code] <= 0 or placed_count >= limit:
                break
            n_layers = int(door_h // vz)
            if n_layers < 1:
                continue
            for (rx, ry, rw, rh) in free:
                if want[code] <= 0 or placed_count >= limit:
                    break
                nx = int((rw + 0.6) // fx)
                ny = int((rh + 0.6) // fy)
                if nx < 1 or ny < 1:
                    continue
                for il in range(n_layers):
                    z = il * vz
                    if z + vz > door_h + 1e-6:
                        break
                    for iy in range(ny):
                        if want[code] <= 0 or placed_count >= limit:
                            break
                        for ix in range(nx):
                            if want[code] <= 0 or placed_count >= limit:
                                break
                            cand = (round(rx + ix * fx, 1), round(ry + iy * fy, 1),
                                    round(z, 1), fx, fy, vz)
                            if any(_overlap(cand, p) for p in placed):
                                continue
                            placed.append(cand)
                            placed_out.append(dict(
                                sku=code, fx=fx, fy=fy, vz=vz,
                                x=cand[0], y=cand[1], z=cand[2]))
                            want[code] -= 1
                            placed_count += 1
    if placed_out:
        pallets_in_cab[0][1].setdefault("_bare", []).extend(placed_out)
    return placed_count


def _fill_bare_areas(containers, pos, bare_plan, skus, smap, box_spec,
                     pal_L, pal_W, door_h):
    """把裸地箱均匀分摊到各柜的柜底裸地 (每柜约相等, 含柜门口区域)。

    做法: 按柜数均分箱数, 逐柜填充; 某柜装不下的缺口顺延给下一柜, 保证不丢箱。
    裸地箱直接落在柜底 (无托盘) → 底层支撑率 100%。
    记录在该柜第一个托盘的 _bare 字段 (便于 pipeline 统一展开)。
    返回未装下的余量 {sku: n} (由上层箱数守恒门禁拦截)。
    """
    want = defaultdict(int)
    for (code, n) in bare_plan:
        want[code] += n
    if not want:
        return {}
    total_want = sum(want.values())
    n_cont = len(containers)
    if n_cont == 0:
        return dict(want)
    base = total_want // n_cont
    rem = total_want % n_cont
    deficit = 0
    for ci_idx, cc in enumerate(containers):
        if total_want <= 0:
            break
        share = base + (1 if ci_idx < rem else 0) + deficit
        if share <= 0:
            continue
        placed = _fill_one_container_bare(cc, pos, want, share, skus, smap,
                                          box_spec, pal_L, pal_W, door_h)
        deficit = max(0, share - placed)   # 未装下的缺口顺延给下一柜
        total_want = sum(want.values())
    return {k: v for k, v in want.items() if v > 0}


def _cap_to_max(containers, pos, max_c, skus, smap, box_spec, pal_L, pal_W, DH):
    """把柜数压到 max_c: 超出的最末柜整柜溶解, 其箱改放前 max_c 柜的柜底裸地。

    前提: 前 max_c 柜都已按 pos 满铺 (每柜 len(pos) 托), 仅最末柜为溢出 (未满)。
    溶解最末若干柜 → 这些箱作为裸地散箱重分布到前 max_c 柜的裸地 (free rects),
    既守住柜数上限, 又利用柜底裸地 (含柜门口), 箱数守恒。

    若裸地装不下全部溶解箱 (want 残留), 说明 max_c 设得过小 (该批真实装不下),
    此时不强行压柜 —— 返回残留量由上层箱数守恒门禁暴露。
    """
    if not max_c or len(containers) <= max_c:
        return {}
    pool = defaultdict(int)
    while len(containers) > max_c:
        overflow = containers.pop()           # 最末柜 (溢出, 托盘数最少)
        for (slot, p) in overflow:
            for l in p.get("layers", []):
                pool[l["sku"]] += l["n"]
            for t in p.get("top", []):
                pool[t["sku"]] += t["n"]
    bare_plan = [(c, n) for c, n in pool.items() if n > 0]
    if not bare_plan:
        return {}
    leftover = _fill_bare_areas(containers, pos, bare_plan, skus, smap,
                                box_spec, pal_L, pal_W, DH)
    return leftover


def _pallet_weight(pal, smap):
    w = 0.0
    for l in pal["layers"]:
        w += smap[l["sku"]].weight_kg * l["n"]
    for t in pal.get("top", []):
        w += smap[t["sku"]].weight_kg * t["n"]
    return w


def vol_c(cc):
    v = 0.0
    for _, p in cc:
        for l in p["layers"]:
            v += smap_cbm(p, l)
        for t in p.get("top", []):
            v += smap_cbm(p, t)
    return v


def smap_cbm(p, item):
    return item.get("_cbm", 0.0)


# ============================================================
# 无托盘模式 (货垛柱) 求解
# ============================================================
def solve_nopallet_mode(box, skus, cons):
    """无托盘模式求解: 纸箱直接码垛为货垛柱, 不使用木托盘。

    算法 (启发式, 沿用旧引擎 loadplan/engine.py 的 solve_mode_a):
      1. 单柜 Phase1 地面立柱: 反复选「当前朝向填充率最高」的 SKU/朝向, 在柜底用
         MaxRects 平铺该朝向的箱底面 (fx×fy), 每根柱向上堆 k=int(Heff//vz) 箱;
      2. Phase2 柱顶回填: 在每根柱顶面 (footprint ≤ 柱底面) 继续往上塞更小朝向的箱,
         直到逼近 Heff;
      3. 多轮随机重启, 选 (柜数最少, 装载最多) 的解。

    有效堆高 Heff: 无托盘货箱需逐箱过门, 故以门开口高 doorH 为上限 (不可超过)。

    返回 (containers, pos):
      containers: [[(pi, p), ...], ...]  每个元素是一个柜
        p: {layers:[{sku,vz,n,cells:[[x,y,w,h],...]}],   # cells 为**容器绝对坐标**
            h, cartons, cbm, skus, _fw, _fd, _ox, _oy}
        —— layers 已「去堆叠」: 垂直叠放的 n 箱拆成 n 个单层 (每层 1 箱),
           与托盘模式后处理 (z 逐层递增) 同构, 复用 L1/L2 校验零改动。
      pos: []  (无托盘不使用托盘位; 柱心/底面由 p 的 _ox/_oy/_fw/_fd 携带)
    """
    L = box["L"]
    W = box["W"]
    doorH = box["doorH"]
    # 无托盘: 货箱逐箱过门, 有效堆高取门开口高 (不可超过)
    Heff = float(doorH)

    def best_orientation(s, Heff):
        best = None
        for (fx, fy, vz, lab) in orientations(s):
            k = int(Heff // vz)
            if k < 1:
                continue
            hfill = k * vz / Heff
            # 先比高度填充率, 再比底面积 (更密)
            key = (hfill, -(fx * fy))
            if best is None or key > best[0]:
                best = (key, fx, fy, vz, lab, k)
        return best

    def fill_container(L, W, Heff, remain, rnd):
        mr = MaxRects(L, W)
        cols = []
        # ---- Phase1: 地面立柱
        while True:
            best = None
            for si in range(len(skus)):
                if remain[si] <= 0:
                    continue
                bo = best_orientation(skus[si], Heff)
                if bo is None:
                    continue
                _, fx, fy, vz, lab, k = bo
                probe = mr.copy()
                placed_cells = 0
                while True:
                    r = probe.place(fx, fy, "BSSF")
                    if not r:
                        break
                    placed_cells += 1
                if placed_cells == 0:
                    continue
                can = min(remain[si], placed_cells * k)
                dens = can / (fx * fy * k) if (fx * fy * k) else 0.0
                sc = (can, dens)
                if best is None or sc > best[0]:
                    best = (sc, si, fx, fy, vz, lab, k)
            if best is None:
                break
            _, si, fx, fy, vz, lab, k = best
            placed = 0
            while remain[si] > 0:
                r = mr.place(fx, fy, "BSSF")
                if not r:
                    break
                n = min(k, remain[si])
                # 货垛柱: 底面 (r[0],r[1],fx,fy), 向上堆 n 箱 (每层1箱, 共 n 层)
                cols.append(dict(x0=r[0], y0=r[1], w=fx, d=fy,
                                 stacks=[(si, n, vz, lab, fx, fy)]))
                remain[si] -= n
                placed += 1
            if placed == 0:
                break
        # ---- Phase2: 柱顶回填 (footprint ≤ 柱底面)
        for c in cols:
            h = sum(n * vz for (_, n, vz, _, _, _) in c["stacks"])
            while h < Heff - 1e-6:
                best = None
                for si in range(len(skus)):
                    if remain[si] <= 0:
                        continue
                    for (fx, fy, vz, lab) in orientations(skus[si]):
                        if fx > c["w"] + 1e-6 or fy > c["d"] + 1e-6:
                            continue
                        n = min(remain[si], int((Heff - h) // vz))
                        if n < 1:
                            continue
                        sc = (n * fx * fy, n)   # 优先塞体积大的
                        if best is None or sc > best[0]:
                            best = (sc, si, n, vz, lab, fx, fy)
                if best is None:
                    break
                _, si, n, vz, lab, fx, fy = best
                c["stacks"].append((si, n, vz, lab, fx, fy))
                remain[si] -= n
                h += n * vz
        return cols, remain

    # ---- 多轮随机重启, 选 (柜数最少, 装载最多)
    best_containers = None
    best_key = None
    restarts = cons.restarts or 12
    for rs in range(restarts):
        rnd = random.Random((cons.seed or 0) * 7919 + rs * 104729)
        remain = [s.cartons for s in skus]
        containers = []
        for _ in range(40):
            if sum(remain) == 0:
                break
            cols, remain = fill_container(L, W, Heff, remain, rnd)
            if not cols:
                break
            containers.append(cols)
        loaded = sum(sum(st[1] for st in c["stacks"]) for cc in containers for c in cc)
        key = (len(containers), -loaded)
        if best_key is None or key < best_key:
            best_key = key
            best_containers = containers

    # ---- 转为标准 p 结构 (每根 col → (pi, p), cells 为容器绝对坐标)
    out = []
    for cc in (best_containers or []):
        plist = []
        for pi, c in enumerate(cc):
            layers = []
            z = 0.0
            for (si, n, vz, lab, fx, fy) in c["stacks"]:
                s = skus[si]
                for _ in range(int(n)):
                    layers.append(dict(
                        sku=s.code, vz=round(vz, 1), n=1,
                        cells=[[round(c["x0"], 1), round(c["y0"], 1),
                                round(fx, 1), round(fy, 1)]]))
                    z += vz
            h = sum(l["vz"] for l in layers)
            cartons = sum(int(st[1]) for st in c["stacks"])
            cbm = sum(skus[st[0]].cbm_per_carton * int(st[1]) for st in c["stacks"])
            skus_in_col = []
            for st in c["stacks"]:
                code = skus[st[0]].code
                if code not in skus_in_col:
                    skus_in_col.append(code)
            plist.append((pi, dict(
                pi=pi, layers=layers, h=round(h, 1), cartons=cartons,
                cbm=round(cbm, 3), skus=skus_in_col,
                _fw=c["w"], _fd=c["d"], _ox=c["x0"], _oy=c["y0"])))
        out.append(plist)
    return out, []


if __name__ == "__main__":
    import time
    t0 = time.time()
    B1 = [s.code for s in SKUS_ALL] if False else None
    print("用 pipeline.py 调用本模块。耗时基准:", round(time.time() - t0, 2))
