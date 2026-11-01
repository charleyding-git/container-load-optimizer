# -*- coding: utf-8 -*-
"""柜底裸地区域的散装货垛填充

背景: 托盘模式下, 柜底按固定托盘位铺放。托盘位之间的缝隙、柜头柜尾余量
      往往形成"裸地" —— 这些空间虽然放不下整托盘 (1219×1016), 但能放
      散装货垛 (不用托盘的箱柱)。
      旧版 engine2 的「模式C 混合装载」有此能力, 重构时丢失, 现补回。

用途:
  1. 把因层结构导致"半空层/悬空"的余量箱, 改放柜底裸地 —— 零额外柜数
  2. 提升柜内空间利用率 (裸地 + 托盘缝隙)

几何:
  - 裸地 = 柜底矩形 减去 全部托盘矩形
  - 用扫描线求差集 (不能简单 guillotine 拆分, 会产生重叠矩形)
  - 在裸地矩形内放货垛柱 (垂直堆叠的同 SKU 箱), 层高受门开口高约束
"""
from collections import defaultdict

from engine import MaxRects


def floor_free_rects(L, W, pallet_rects):
    """求柜底未被托盘覆盖的裸地区域 (极大矩形)。

    扫描线做法:
      1. 收集所有 x 边界 (柜边 + 托盘边) → 切成 x 段
      2. 对每个 x 段, 收集与该段相交的托盘, 求它们在 y 上的并集覆盖
      3. 未被覆盖的 y 区间即该 x 段上的裸地
      4. 合并相邻同构的 x 段 (减少碎片)

    返回 [(x0, y0, w, h), ...]  裸地矩形列表
    """
    # x 切分点
    xs = set([0.0, float(L)])
    for (x0, y0, x1, y1) in pallet_rects:
        xs.add(round(max(0.0, x0), 1))
        xs.add(round(min(float(L), x1), 1))
    xs = sorted(x for x in xs if 0 <= x <= L)
    out = []
    for i in range(len(xs) - 1):
        x0, x1 = xs[i], xs[i + 1]
        if x1 - x0 < 1:
            continue
        xm = (x0 + x1) / 2.0
        # 与该 x 段相交的托盘
        spans = []
        for (px0, py0, px1, py1) in pallet_rects:
            if px0 - 0.6 < xm < px1 + 0.6:
                spans.append((py0, py1))
        spans.sort()
        # 求 y 上的空隙
        covered = []
        for (a, b) in spans:
            if covered and a <= covered[-1][1] + 0.6:
                covered[-1] = (covered[-1][0], max(covered[-1][1], b))
            else:
                covered.append((a, b))
        gaps = []
        prev = 0.0
        for (a, b) in covered:
            if a - prev > 1:
                gaps.append((prev, a))
            prev = max(prev, b)
        if W - prev > 1:
            gaps.append((prev, W))
        for (gy0, gy1) in gaps:
            if gy1 - gy0 >= 1:
                out.append((round(x0, 1), round(gy0, 1),
                            round(x1 - x0, 1), round(gy1 - gy0, 1)))
    return _merge_x(out)


def _merge_x(rects):
    """合并沿 x 方向可合并的裸地 (相同 y 区间且 x 相邻)。"""
    if not rects:
        return []
    by_y = defaultdict(list)
    for (x0, y0, w, h) in rects:
        by_y[(y0, h)].append((x0, x0 + w))
    out = []
    for (y0, h), xs in by_y.items():
        xs.sort()
        cur_a, cur_b = xs[0]
        for (a, b) in xs[1:]:
            if a <= cur_b + 0.6:
                cur_b = max(cur_b, b)
            else:
                out.append((cur_a, y0, cur_b - cur_a, h))
                cur_a, cur_b = a, b
        out.append((cur_a, y0, cur_b - cur_a, h))
    return sorted(out)


def fill_bare_area(L, W, Hlim, free_rects, remain, skus, smap,
                    min_support=0.75):
    """在裸地区域放置散装货垛柱。

    每个裸地矩形独立处理: 选一个能放下的 SKU, 垂直堆叠若干层。
    箱底必须被下层箱 (或地面) 支撑 → 第一层支撑率 100% (地面),
    后续层用 align_layer_to_support 对齐。

    剩余未装完的 SKU 数量从 remain 中扣除 (原地修改)。

    返回 [(rect_x, rect_y, stacks), ...]
      stacks: [dict(sku, n, fx, fy, vz, x, y, cells), ...]
    """
    result = []
    if not free_rects:
        return result
    # 按面积降序, 优先用大块
    for (rx, ry, rw, rh) in sorted(free_rects, key=lambda r: -r[2] * r[3]):
        # 找能放进这个裸地的 SKU (底面积能放下至少 1 个)
        cands = []
        for code, cnt in remain.items():
            if cnt <= 0:
                continue
            s = smap.get(code)
            if not s:
                continue
            dims = sorted([s.L, s.W, s.H])
            for (fx, fy, vz) in ((dims[0], dims[1], dims[2]),
                                 (dims[0], dims[2], dims[1]),
                                 (dims[1], dims[2], dims[0])):
                if fx <= rw + 0.6 and fy <= rh + 0.6 and vz <= Hlim + 1e-6:
                    # 优先: 单箱底面积小(装得多) + 立高矮(层多)
                    cands.append((fx * fy, vz, code, fx, fy))
                    break
        if not cands:
            continue
        cands.sort()
        for (foot_area, vz, code, fx, fy) in cands:
            # 该裸地能放几个 (网格)
            nx = int((rw + 0.6) // fx)
            ny = int((rh + 0.6) // fy)
            if nx < 1 or ny < 1:
                continue
            per_layer = nx * ny
            # 最多堆几层 (受门高限制)
            n_layers = int((Hlim + 1e-6) // vz)
            if n_layers < 1:
                continue
            want = remain[code]
            # 每层最多 per_layer 个, 但不能超过剩余量
            total_slots = per_layer * n_layers
            n_total = min(want, total_slots)
            if n_total < 1:
                continue
            stacks = []
            placed_total = 0
            z = 0.0
            prev_cells = []
            while placed_total < n_total and z + vz <= Hlim + 1e-6:
                # 本层放几个
                k = min(per_layer, n_total - placed_total)
                cells = []
                for iy in range(ny):
                    for ix in range(nx):
                        if len(cells) >= k:
                            break
                        cells.append((round(ix * fx, 1), round(iy * fy, 1),
                                      float(fx), float(fy)))
                    if len(cells) >= k:
                        break
                if not cells:
                    break
                stacks.append(dict(sku=code, n=len(cells), fx=fx, fy=fy, vz=vz,
                                   x=rx, y=ry, z=z, cells=cells))
                placed_total += len(cells)
                z += vz
                prev_cells = cells
            if placed_total > 0:
                remain[code] = remain.get(code, 0) - placed_total
                result.append((rx, ry, stacks))
                break
    return result
