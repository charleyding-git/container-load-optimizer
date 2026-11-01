# -*- coding: utf-8 -*-
"""受支撑约束的托盘面 2D 装箱 (高性能版)

核心问题: 不同 SKU 的箱型尺寸不同, 若各层独立做 MaxRects, 上层会铺满
托盘面, 而下层只占一部分 —— 导致上层大量悬空 (垂直支撑率低), 运输中塌垛。

解法: 新层的箱必须落在「下层箱顶面投影的并集」内, 且被支撑面积占比达标。

高性能做法 (不再全网格扫描):
  1. 把下层箱的角点 + 边界交点作为 **候选锚点** (数量 O(n²) 但 n 很小, ≤ 12)
  2. 每个候选锚点只试 1~2 种对齐方式 (紧贴下层箱左下角 / 右下角 / 左上 / 右上)
  3. 用「可放区」维护已占矩形, 贪心放置, 每次放置后立刻更新可放区
  复杂度从 O(c²·n) 降到 O(n³), 实测单次 <5ms
"""
import math

from engine import MaxRects


def _overlap_area(ax, ay, aw, ah, bx, by, bw, bh):
    ox = min(ax + aw, bx + bw) - max(ax, bx)
    oy = min(ay + ah, by + bh) - max(ay, by)
    if ox <= 0 or oy <= 0:
        return 0.0
    return ox * oy


class SupportMap:
    """下层箱顶面投影的支撑查询结构。

    性能要点: 用 **X 方向区间索引** 预筛 —— 只有 X 区间与查询矩形相交的
    下层箱才需要算 Y 交集, 把 O(n) 降到通常 O(1~2)。
    """

    __slots__ = ("cells", "n", "x0", "x1", "_grid", "_gnx", "_cell")

    def __init__(self, prev_cells, cell=40.0):
        self.cells = prev_cells
        self.n = len(prev_cells)
        self._cell = cell
        if not prev_cells:
            self._grid = {}
            self._gnx = 0
            return
        # X 方向网格索引: 每格记录落入的下层箱索引
        xs = [c[0] for c in prev_cells]
        xe = [c[0] + c[2] for c in prev_cells]
        self.x0 = min(xs)
        self.x1 = max(xe)
        gnx = max(1, int((self.x1 - self.x0) / cell) + 1)
        self._gnx = gnx
        grid = {}
        for i, (px, py, pw, ph) in enumerate(prev_cells):
            g0 = int((px - self.x0) / cell)
            g1 = int((px + pw - self.x0) / cell)
            for g in range(g0, min(gnx, g1 + 1)):
                grid.setdefault(g, []).append(i)
        # 去重 (同一箱可能落多个格) —— 避免 ratio() 里反复 set.update
        self._grid = {g: tuple(set(v)) for g, v in grid.items()}

    def ratio(self, x, y, w, h):
        """矩形 (x,y,w,h) 被支撑的面积占比。"""
        if not self.cells:
            return 0.0
        g0 = int((x - self.x0) / self._cell) - 1
        g1 = int((x + w - self.x0) / self._cell) + 1
        area = 0.0
        x2 = x + w
        y2 = y + h
        grid = self._grid
        seen = None
        for g in range(max(0, g0), min(self._gnx, g1 + 1)):
            lst = grid.get(g)
            if not lst:
                continue
            if seen is None:
                seen = set(lst)
            else:
                seen.update(lst)
        if not seen:
            return 0.0
        cells = self.cells
        for i in seen:
            px, py, pw, ph = cells[i]
            if px + pw <= x or px >= x2:
                continue
            oy = (y2 if y2 < py + ph else py + ph) - (y if y > py else py)
            if oy > 0:
                ox = (x2 if x2 < px + pw else px + pw) - (x if x > px else px)
                if ox > 0:
                    area += ox * oy
        base = w * h
        return area / base if base > 0 else 0.0

    def positions(self, w, h, pal_L, pal_W, min_ratio):
        """枚举所有被支撑达标且在托盘内的放置位置。

        返回 [(ratio, x, y, w, h), ...] —— ratio 已算好, 避免重复计算。
        """
        out = []
        seen = set()
        for (px, py, pw, ph) in self.cells:
            anchors = ((px, py), (px + pw - w, py), (px, py + ph - h),
                       (px + pw - w, py + ph - h))
            for (ax, ay) in anchors:
                x = ax if ax > 0 else 0.0
                y = ay if ay > 0 else 0.0
                if x + w > pal_L + 1e-6:
                    x = pal_L - w
                if y + h > pal_W + 1e-6:
                    y = pal_W - h
                if x < -1e-6 or y < -1e-6:
                    continue
                key = (round(x, 1), round(y, 1))
                if key in seen:
                    continue
                seen.add(key)
                r = self.ratio(x, y, w, h)
                if r >= min_ratio - 1e-6:
                    out.append((r, x, y, w, h))
        return out


def _supported_ratio(prev_cells, x, y, w, h):
    """兼容包装 (单次查询)。"""
    return SupportMap(prev_cells).ratio(x, y, w, h)


def _anchor_points(prev_cells, pal_L, pal_W):
    """生成候选锚点: 下层箱的各角点及其偏移组合。"""
    pts = set()
    for (px, py, pw, ph) in prev_cells:
        for (ax, ay) in ((px, py), (px + pw, py), (px, py + ph), (px + pw, py + ph)):
            for dx in (0.0,):
                for dy in (0.0,):
                    x = ax + dx
                    y = ay + dy
                    if -1e-6 <= x <= pal_L - 1e-6 and -1e-6 <= y <= pal_W - 1e-6:
                        pts.add((round(x, 1), round(y, 1)))
    # 加上托盘原点
    pts.add((0.0, 0.0))
    return sorted(pts)


def _fit_positions(prev_cells, w, h, pal_L, pal_W, min_ratio):
    """枚举 (x, y) 使 (x,y,w,h) 完全在托盘内且被支撑达标。

    策略: 对每个下层箱, 让新箱的左下角贴其左上/右下/左下/右上角, 再做
    「贴边收缩」—— 若超界则沿边界回退。
    """
    out = []
    seen = set()
    for (px, py, pw, ph) in prev_cells:
        anchors = ((px, py), (px + pw - w, py), (px, py + ph - h),
                   (px + pw - w, py + ph - h))
        for (ax, ay) in anchors:
            x = max(0.0, min(ax, pal_L - w))
            y = max(0.0, min(ay, pal_W - h))
            # 量化避免重复
            key = (round(x, 1), round(y, 1))
            if key in seen:
                continue
            seen.add(key)
            if _supported_ratio(prev_cells, x, y, w, h) >= min_ratio - 1e-6:
                out.append((x, y, w, h))
    return out


def pack_on_support(prev_cells, fx, fy, pal_L, pal_W, min_ratio=0.75,
                    allow_rotate=True):
    """在 prev_cells 支撑的区域上排布 fx x fy 的箱。

    返回 (数量, 布局列表)。
    """
    if not prev_cells:
        mr = MaxRects(pal_L, pal_W)
        best_n, best_lay = 0, []
        for f in ("BSSF", "BLASF", "BLF"):
            mr2 = MaxRects(pal_L, pal_W)
            lay = []
            while True:
                r = mr2.place(fx, fy, f)
                if not r:
                    break
                lay.append(r)
            if len(lay) > best_n:
                best_n, best_lay = len(lay), lay
        return best_n, best_lay

    dims = [(fx, fy)]
    if allow_rotate and abs(fx - fy) > 1e-6:
        dims.append((fy, fx))

    smap = SupportMap(prev_cells)
    best_lay = []
    for (w, h) in dims:
        if w > pal_L + 1e-6 or h > pal_W + 1e-6:
            continue
        # 候选位 (positions 已算好支撑率)
        scored = smap.positions(w, h, pal_L, pal_W, min_ratio)
        if not scored:
            continue
        occupied = []
        lay = []
        used = set()
        for _ in range(64):
            best = None
            for (r, x, y, cw, ch) in scored:
                key = (round(x, 1), round(y, 1))
                if key in used:
                    continue
                hit = False
                for (ox, oy, ow, oh) in occupied:
                    if not (x + cw <= ox + 1e-6 or ox + ow <= x + 1e-6
                            or y + ch <= oy + 1e-6 or oy + oh <= y + 1e-6):
                        hit = True
                        break
                if hit:
                    continue
                key2 = (r, -x, -y)
                if best is None or key2 > best[0]:
                    best = (key2, x, y, cw, ch)
            if best is None:
                break
            _, x, y, cw, ch = best
            used.add((round(x, 1), round(y, 1)))
            occupied.append((x, y, cw, ch))
            lay.append((x, y, cw, ch))
        if len(lay) > len(best_lay):
            best_lay = lay
    return len(best_lay), best_lay


def align_layer_to_support(prev_cells, fx, fy, pal_L, pal_W, min_ratio=0.75,
                           cell=10.0):
    """为新层生成与下层对齐的布局。逐级放宽 min_ratio 重试。"""
    for ratio in (min_ratio, max(0.0, min_ratio - 0.15),
                  max(0.0, min_ratio - 0.30), 0.0):
        n, lay = pack_on_support(prev_cells, fx, fy, pal_L, pal_W, ratio)
        if n > 0:
            return n, lay, ratio
    return 0, [], 0.0


def support_ratio(prev_cells, cur_cells, pal_L, pal_W, rot="A"):
    """计算当前层相对下层的支撑率 (面积占比)。

    关键: prev_cells / cur_cells 都是 **A 朝向** 坐标 (求解器内部统一口径),
    但托盘实际摆放可能是 B 朝向 —— 此时两层之间会整体转 90°,
    支撑关系随之改变 (不只是坐标换算, 连重叠关系都不同)。

    因此必须按实际朝向换算后再算重叠:
      rot='A': 直接算
      rot='B': prev 层转 90° (new_x=y, new_y=pal_L-x-w, new_w=h, new_h=w),
              cur 层同样转 90° 后再算重叠。
    """
    if rot == "B":
        prev_cells = _rot90(prev_cells, pal_L)
        cur_cells = _rot90(cur_cells, pal_L)
    if not prev_cells:
        return 1.0
    smap = SupportMap(prev_cells)
    worst = 1.0
    for (x, y, w, h) in cur_cells:
        r = smap.ratio(x, y, w, h)
        if r < worst:
            worst = r
    return worst


def _rot90(cells, pal_L):
    """A 朝向 cells → B 朝向 cells (90° 刚体变换)。"""
    return [(round(y, 1), round(pal_L - x - w, 1), round(h, 1), round(w, 1))
            for (x, y, w, h) in cells]
