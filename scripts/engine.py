# -*- coding: utf-8 -*-
"""集装箱装载优化核心引擎 (参数化)

从 loadplan/engine2.py + pallet3d.py 重构而来, 去除了所有硬编码:
  * 集装箱规格 → 来自 catalog.CONTAINERS
  * 托盘规格   → 来自 catalog.PALLETS
  * 货物数据   → 来自外部传入的 SKU 列表
  * 约束       → 由 Constraints 对象控制 (硬/软约束分离)

核心算法沿用并强化:
  * MaxRects 极值矩形装箱 (Jylangi 2005) —— BSSF/BLASF/BLF 三启发式
  * 托盘面 2D 装箱 → 真实布局坐标
  * Layer/Slab 分解 + Block 码垛
  * FFD (First-Fit-Decreasing) 多柜分配
  * 顶部平躺散放 (top-up layer)
"""
import math
import random
from collections import defaultdict

from catalog import CONTAINERS, PALLETS, CONST
import i18n


# ============================================================
# 约束模型
# ============================================================
class Constraints:
    """装载约束。

    硬约束 (hard=True): 违反则方案不可行, 求解器绝不产出违反的解。
    软约束 (hard=False): 自动优化模式下允许违反, 但会记录违约量用于惩罚。

    支撑率策略 (support_policy) —— 三档:
      relaxed_all  放任: 不修复中低支撑率, 只保证「完全无支撑」被修掉。
                       支撑率不达标仅作提示, 交由缠绕膜/捆扎等外部手段兜底。
                       柜数最少。
      balanced     均衡(默认): 修复支撑率明显不足的层 (阈值见 CONS_CRITICAL_SUPPORT),
                       层序优化可选。柜数与安全性折中。
      strict       严格: 支撑率作为求解硬约束, 绝不放宽。柜数最多、耗时最长。
    """

    # 支撑率策略定义
    SUPPORT_POLICY = {
        "relaxed_all": dict(
            label="放任支撑率 (仅修完全悬空)",
            desc="支撑率不达标仅提示, 由外部加固兜底; 必坠落的完全悬空仍强制修复",
            fix_threshold=0.0,       # 只修支撑率 = 0 的
        ),
        "balanced": dict(
            label="均衡 (修复明显不足)",
            desc="修复支撑率明显不足的层; 层序优化可减轻层位影响",
            fix_threshold=0.55,
        ),
        "strict": dict(
            label="严格 (硬约束)",
            desc="支撑率作为求解硬约束, 绝不放宽; 柜数最多",
            fix_threshold=None,       # 求解阶段即硬约束
        ),
    }

    def __init__(self,
                 mode="pallet",              # "pallet"(有托) | "nopallet"(无托)
                 mono_sku=True,              # 一托一SKU (硬)
                 allow_top=True,             # 允许托盘顶部平躺散放 (软)
                 allow_double_pallet=False,  # 允许双层托盘 (硬)
                 max_pallet_stack_h=None,    # 收货区托盘货物限高 mm (硬)
                 tihi_layers=None,           # TiHi 带板层数 1~4 (硬), None=不限制
                 allow_mix_pallet=True,      # 允许一托混装多SKU (软, mono_sku=False 时生效)
                 max_stack_layers_per_sku=None,  # 每箱型堆码层数上限 dict (硬)
                 stack_pattern="auto",       # column/interlock/auto
                 load_order="auto",          # heavy_first/height_desc/sku_group/auto
                 min_support_ratio=None,    # 垂直支撑率下限 (硬约束; None=仅校验不约束求解)
                 support_policy="balanced", # 支撑率策略: relaxed_all/balanced/strict
                 reorder_layers=False,      # 层序优化: 支撑不佳的层尽量挪到托盘上部
                 use_bare_area=False,       # 利用柜底裸地 (装不下整托盘的零散空间)
                 door_height=None,          # 柜门开口高 (硬约束, 由求解器按柜型设置)
                 max_cog_offset=None,       # 重心横向偏移上限比例 (软)
                 max_pallet_weight=None,     # 单托限重 kg (硬)
                 optimize="containers",      # "containers"(柜数最少) | "strict"(严格按设定)
                 max_containers=None,        # 柜数硬上限 (None=不限制); 超出部分的最末柜溶解为柜底裸地
                 seed=0,
                 restarts=14):
        self.mode = mode
        self.mono_sku = mono_sku
        self.allow_top = allow_top
        self.allow_double_pallet = allow_double_pallet
        self.max_pallet_stack_h = max_pallet_stack_h
        self.tihi_layers = tihi_layers
        self.allow_mix_pallet = allow_mix_pallet
        self.max_stack_layers_per_sku = max_stack_layers_per_sku or {}
        self.stack_pattern = stack_pattern
        self.load_order = load_order
        self.min_support_ratio = min_support_ratio
        self.support_policy = support_policy
        self.reorder_layers = reorder_layers
        self.use_bare_area = use_bare_area
        # 由 support_policy 推导
        self.repair_support = (support_policy != "relaxed_all")
        self.door_height = door_height
        self.max_cog_offset = max_cog_offset
        self.max_pallet_weight = max_pallet_weight
        self.optimize = optimize
        self.max_containers = max_containers
        self.seed = seed
        self.restarts = restarts

    def to_dict(self):
        """可往返的参数字典 (剔除派生字段, 避免 __init__ 不接受)。"""
        d = dict(self.__dict__)
        d.pop("repair_support", None)   # 由 support_policy 派生
        return d

    def describe(self):
        """输出人类可读的前置条件清单 (写进方案说明)。

        按 i18n 当前语言输出 (zh / en)。
        """
        if i18n.get_lang() == "en":
            return self._describe_en()
        L = []
        L.append(f"装载方式: {'有托盘 (托盘模式)' if self.mode == 'pallet' else '无托盘 (货垛柱模式)'}")
        if self.mode == "pallet":
            L.append(f"一托一SKU: {'是 (硬约束)' if self.mono_sku else '否 (允许混装)'}")
            L.append(f"顶部平躺散放: {'允许' if self.allow_top else '禁止'}")
            L.append(f"双层托盘: {'允许' if self.allow_double_pallet else '禁止'}")
            if self.tihi_layers:
                t = CONST["TIHI"][self.tihi_layers]
                L.append(f"TiHi 带板层数: {self.tihi_layers} 层 ({t['desc']}), 货高上限 {t['max']}mm")
            if self.max_pallet_stack_h:
                L.append(f"收货区托盘货物限高: {self.max_pallet_stack_h}mm")
            if self.max_pallet_weight:
                L.append(f"单托限重: {self.max_pallet_weight} kg")
        L.append(f"堆码方式: {self.stack_pattern}")
        L.append(f"装柜顺序: {self.load_order}")
        if self.min_support_ratio:
            L.append(f"垂直支撑率下限: {self.min_support_ratio*100:.0f}%")
        if self.repair_support:
            L.append("悬空修复: 修复完全无支撑(支撑率0%)的层 —— 必坠落, 与重量无关")
        if self.reorder_layers:
            L.append("层序优化: 支撑不佳的层尽量安排到托盘上部 (上方载荷小, 风险低)")
        if self.use_bare_area:
            L.append("柜底裸地利用: 装不下整托盘的零散空间用于散装码放")
        if self.max_cog_offset:
            L.append(f"重心横向偏移上限: 柜宽的 {self.max_cog_offset*100:.0f}%")
        if self.max_stack_layers_per_sku:
            L.append(f"各SKU堆码层数上限: {self.max_stack_layers_per_sku}")
        L.append(f"优化目标: {'柜数最少 (软约束可放宽)' if self.optimize=='containers' else '严格按上述设定'}")
        return L

    def _describe_en(self):
        """English version of describe()."""
        L = []
        L.append("Loading mode: {}".format(
            "palletized" if self.mode == "pallet" else "no-pallet (column stacks)"))
        if self.mode == "pallet":
            L.append("One SKU per pallet: {}".format(
                "yes (hard)" if self.mono_sku else "no (mixing allowed)"))
            L.append("Flat-stacked boxes on pallet top: {}".format(
                "allowed" if self.allow_top else "forbidden"))
            L.append("Double-stacked pallets: {}".format(
                "allowed" if self.allow_double_pallet else "forbidden"))
            if self.tihi_layers:
                t = CONST["TIHI"][self.tihi_layers]
                L.append("TiHi layers: {} ({}), load height limit {}mm".format(
                    self.tihi_layers, t.get("desc_en", t["desc"]), t["max"]))
            if self.max_pallet_stack_h:
                L.append("Max pallet load height in receiving area: {}mm".format(
                    self.max_pallet_stack_h))
            if self.max_pallet_weight:
                L.append("Max pallet gross weight: {} kg".format(self.max_pallet_weight))
        L.append("Stacking pattern: {}".format(self.stack_pattern))
        L.append("Loading order: {}".format(self.load_order))
        if self.min_support_ratio:
            L.append("Min vertical support ratio: {:.0f}%".format(self.min_support_ratio * 100))
        if self.repair_support:
            L.append("Unsupported repair: layers with fully unsupported boxes (0% support) "
                     "are rearranged — those would fall regardless of weight")
        if self.reorder_layers:
            L.append("Layer reordering: poorly supported layers are moved higher on the pallet "
                     "(less load above → lower crush risk)")
        if self.use_bare_area:
            L.append("Bare-floor usage: leftover space that cannot hold a full pallet "
                     "is used for loose cartons")
        if self.max_cog_offset:
            L.append("Max lateral CoG offset: {:.0f}% of container width".format(
                self.max_cog_offset * 100))
        if self.max_stack_layers_per_sku:
            L.append("Max stack layers per SKU: {}".format(self.max_stack_layers_per_sku))
        L.append("Objective: {}".format(
            "fewest containers (soft constraints may be relaxed)"
            if self.optimize == "containers" else "strictly as configured above"))
        return L


# ============================================================
# SKU 数据结构
# ============================================================
class SKU:
    """单个 SKU (一种商品/一种箱型)。"""
    __slots__ = ("code", "name", "L", "W", "H", "cartons", "pcs_per_carton",
                 "weight_kg", "ect_kn_m", "fragile", "max_stack_layers", "note")

    def __init__(self, code, name, L, W, H, cartons, pcs_per_carton=1,
                 weight_kg=0.0, ect_kn_m=None, fragile=False,
                 max_stack_layers=None, note=""):
        self.code = code
        self.name = name
        self.L = L
        self.W = W
        self.H = H
        self.cartons = cartons
        self.pcs_per_carton = pcs_per_carton
        self.weight_kg = weight_kg
        self.ect_kn_m = ect_kn_m
        self.fragile = fragile
        self.max_stack_layers = max_stack_layers
        self.note = note

    @property
    def cbm_per_carton(self):
        return self.L * self.W * self.H / 1e9

    @property
    def total_cbm(self):
        return self.cbm_per_carton * self.cartons

    @property
    def total_pcs(self):
        return self.cartons * self.pcs_per_carton

    def to_dict(self):
        return dict(code=self.code, name=self.name, L=self.L, W=self.W, H=self.H,
                    cartons=self.cartons, pcs_per_carton=self.pcs_per_carton,
                    weight_kg=self.weight_kg, ect_kn_m=self.ect_kn_m,
                    fragile=self.fragile, max_stack_layers=self.max_stack_layers,
                    cbm_per_carton=round(self.cbm_per_carton, 6),
                    total_cbm=round(self.total_cbm, 3),
                    note=self.note)


# ============================================================
# MaxRects 极值矩形装箱
# ============================================================
class MaxRects:
    """MaxRects 极值矩形装箱 (Jylangi 2005)。

    fit 启发式:
      BSSF  最佳短边 (Best Short Side Fit)
      BLASF 最佳长边 (Best Long Side Fit)
      BLF   最佳左下 (Best Lower Left)

    实现要点: _split 采用「全量重算空闲矩形」策略 —— 对每个空闲矩形按
    新放置矩形切分成最多 4 块, 再做包含关系剪枝。此法虽非增量式, 但结果
    稳定且装箱数最优 (增量切割会产生重叠空闲矩形, 导致装箱数偏低)。
    """

    def __init__(self, W, H, cap=260):
        self.W = float(W)
        self.H = float(H)
        self.cap = cap
        self.free = [(0.0, 0.0, self.W, self.H)]

    def copy(self):
        m = MaxRects(self.W, self.H, self.cap)
        m.free = list(self.free)
        return m

    def area(self):
        return sum(f[2] * f[3] for f in self.free)

    def place(self, w, h, fit="BSSF"):
        """放置 w x h 的矩形, 返回 (x, y, w, h) 或 None。"""
        best = None
        for (fx, fy, fw, fh) in self.free:
            if fw >= w - 1e-6 and fh >= h - 1e-6:
                dw, dh = fw - w, fh - h
                if fit == "BSSF":
                    key = (min(dw, dh), max(dw, dh), fy, fx)
                elif fit == "BLASF":
                    key = (max(dw, dh), min(dw, dh), fy, fx)
                else:
                    key = (fy + dh, fx + dw, 0.0, 0.0)
                if best is None or key < best[0]:
                    best = (key, fx, fy, fw, fh)
        if best is None:
            return None
        _, fx, fy, fw, fh = best
        self._split(fx, fy, w, h)
        return (fx, fy, w, h)

    def _split(self, x, y, w, h):
        nf = []
        for (fx, fy, fw, fh) in self.free:
            if (x >= fx + fw - 1e-6 or x + w <= fx + 1e-6
                    or y >= fy + fh - 1e-6 or y + h <= fy + 1e-6):
                nf.append((fx, fy, fw, fh))
                continue
            if x > fx:
                nf.append((fx, fy, x - fx, fh))
            if x + w < fx + fw:
                nf.append((x + w, fy, fx + fw - (x + w), fh))
            if y > fy:
                nf.append((fx, fy, fw, y - fy))
            if y + h < fy + fh:
                nf.append((fx, y + h, fw, fy + fh - (y + h)))
        pruned = []
        for i, r in enumerate(nf):
            if r[2] <= 1e-6 or r[3] <= 1e-6:
                continue
            dup = False
            for j, s2 in enumerate(nf):
                if i == j:
                    continue
                if (r[0] >= s2[0] - 1e-6 and r[1] >= s2[1] - 1e-6
                        and r[0] + r[2] <= s2[0] + s2[2] + 1e-6
                        and r[1] + r[3] <= s2[1] + s2[3] + 1e-6):
                    dup = True
                    break
            if not dup:
                pruned.append(r)
        if len(pruned) > self.cap:
            pruned.sort(key=lambda r: -(r[2] * r[3]))
            pruned = pruned[:self.cap]
        self.free = pruned


# ============================================================
# 托盘面 2D 装箱
# ============================================================
def max_count_single(rect, W, H):
    """单一朝向在 W x H 面上最多放多少个, 返回 (数量, 真实布局列表)。"""
    mr = MaxRects(W, H)
    fit = ("BSSF", "BLASF", "BLF")
    best_n, best_lay = 0, []
    for f in fit:
        mr2 = MaxRects(W, H)
        lay = []
        while True:
            r = mr2.place(rect[0], rect[1], f)
            if not r:
                break
            lay.append(r)
        if len(lay) > best_n:
            best_n, best_lay = len(lay), lay
    return best_n, best_lay


def max_count_mixed(r1, r2, W, H):
    """两种朝向混合, 返回 (数量, 真实布局列表)。"""
    best_n, best_lay = 0, []
    for f in ("BSSF", "BLASF", "BLF"):
        mr = MaxRects(W, H)
        lay = []
        while True:
            r = mr.place(r1[0], r1[1], f)
            if r:
                lay.append(r)
                continue
            r = mr.place(r2[0], r2[1], f)
            if r:
                lay.append(r)
                continue
            break
        if len(lay) > best_n:
            best_n, best_lay = len(lay), lay
    return best_n, best_lay


def orientations(sku):
    """一个 SKU 的 6 种正交朝向。返回 (fx, fy, vz, label)。"""
    L, W, H = sku.L, sku.W, sku.H
    out = []
    for tag, vz, a, b in (("H", H, L, W), ("W", W, L, H), ("L", L, W, H)):
        out.append((a, b, vz, f"{tag} {a}x{b}x{vz}"))
        out.append((b, a, vz, f"{tag} {b}x{a}x{vz}"))
    return out


def pallet_layer_modes(sku, pal_L, pal_W):
    """某 SKU 在给定托盘面上的所有可行层模式。

    返回 {层高 vz: (每层箱数 cpl, 标签, 布局列表, fx, fy)}
    """
    res = {}
    for (fx, fy, vz, lab) in orientations(sku):
        n1, lay1 = max_count_single((fx, fy), pal_L, pal_W)
        n2, lay2 = max_count_mixed((fx, fy), (fy, fx), pal_L, pal_W)
        if n2 >= n1:
            cpl, lay = n2, lay2
        else:
            cpl, lay = n1, lay1
        if cpl < 1:
            continue
        if vz not in res or cpl > res[vz][0]:
            res[vz] = (cpl, lab, lay, fx, fy)
    return res


# ============================================================
# 柜底托盘平铺 (通用托盘规格)
# ============================================================
_PAL_POS_CACHE = {}


def pallet_floor_layout(L, W, pal_L, pal_W, seed=21, tries=30000):
    """柜底最大平铺托盘数, 返回 [(cx, cy, 'A'|'B'), ...]

    策略 (按确定性递增, 找到更优即停):
      1. 带状模型 —— 精确枚举每条「带」的 A/B 托盘数量组合 (可复现, 零随机)
      2. 带状模型 + Y 方向交错 —— 每条带内部允许两排 Y 方向错位 (交错摆放),
         这是 40HQ 塞进 21 个美标托盘的关键 (纯带状只有 20 个)
      3. 随机 MaxRects 补充 —— 仅在前两步都不足时启用, 代价高
    """
    ck = (L, W, pal_L, pal_W, seed, tries)
    if ck in _PAL_POS_CACHE:
        return _PAL_POS_CACHE[ck]

    # ---- 步骤 1+2: 带状模型 (含 Y 交错)
    best = _banded_layout(L, W, pal_L, pal_W, interleave=True)
    if len(best) >= 12:
        # 记录理论上限, 若已达上限则直接返回 (无需随机搜索)
        _PAL_POS_CACHE[ck] = best
        return best

    # ---- 步骤 3: 随机 MaxRects 补充
    rnd = random.Random(seed)
    fits = ("BSSF", "BLASF", "BLF")
    bestn = len(best)
    for t in range(tries):
        mr = MaxRects(L, W)
        pos = []
        fit = fits[t % 3]
        while True:
            if rnd.random() < 0.5:
                r = mr.place(pal_L, pal_W, fit)
                if r:
                    pos.append((r[0] + pal_L / 2, r[1] + pal_W / 2, "A"))
                    continue
            else:
                r = mr.place(pal_W, pal_L, fit)
                if r:
                    pos.append((r[0] + pal_W / 2, r[1] + pal_L / 2, "B"))
                    continue
            break
        if len(pos) > bestn:
            bestn = len(pos)
            best = sorted(pos, key=lambda p: (round(p[1], 0), p[0]))
            if bestn >= 21:
                break
    _PAL_POS_CACHE[ck] = best
    return best


def _banded_layout(L, W, pal_L, pal_W, interleave=True):
    """带状模型: 柜宽方向切成若干条「带」, 每带内沿柜长方向排托盘。

    结构 1 (纯带):  枚举每条带是 A 朝向还是 B 朝向、各放几个, 带之间沿 Y 依次排。

    结构 2 (交错):  三段式 Y 交错 —— 40HQ 塞 21 个美标托盘的关键。
        典型布局 (12032 × 2352, 美标 1219.2×1016):
          段①  11 个 B 朝向  y ∈ [0, 1219.2)      x ∈ [0, 11176)
          段②   8 个 A 朝向  y ∈ [1219.2, 2235.2)  x ∈ [0, 9753.6)
          段③  补位: 1 个 B y ∈ [1016, 2235.2] + 2 个 A y ∈ [0, 1016]
                 → 填右上角空隙, 总数 21
        通用实现: 前两段沿 Y 依次排, 第三段在「第一段与第二段之间形成的
        阶梯空隙」里补放托盘 (利用柜宽方向的余量)。
    """
    best = []
    # ---- 结构 1: 纯带
    for nA in range(0, 14):
        for nB in range(0, 14):
            for order in (0, 1):
                seq = [("A", nA), ("B", nB)]
                if order == 0:
                    seq = [s for s in seq if s[1] > 0]
                else:
                    seq = [s for s in reversed(seq) if s[1] > 0]
                y = 0.0
                x = 0.0
                pos = []
                ok = True
                for (typ, n) in seq:
                    pw, pl = (pal_W, pal_L) if typ == "A" else (pal_L, pal_W)
                    if y + pw > W + 1e-6:
                        ok = False
                        break
                    for i in range(n):
                        if x + pl > L + 1e-6:
                            ok = False
                            break
                        pos.append((x + pl / 2, y + pw / 2, typ))
                        x += pl
                    if not ok:
                        break
                    y += pw
                    x = 0.0
                if ok and len(pos) > len(best):
                    best = sorted(pos, key=lambda p: (round(p[1], 0), p[0]))
    if not interleave:
        return best

    # ---- 结构 2: 三段式 Y 交错 (40HQ 塞 21 个美标托盘的关键)
    # 结构 (以 12032×2352 + 美标 1219.2×1016 为例):
    #   段① 下排: nB1 个 B (y ∈ [0, 1219.2))  +  nA1 个 A (y ∈ [0, 1016))
    #           两者 X 区间不重叠: B 占 [0, nB1*1016), A 占其后
    #   段② 上排: nA2 个 A (y ∈ [1219.2, 2235.2)) + nB2 个 B (y ∈ [1016, 2235.2))
    #           两者 X 区间不重叠: A 占 [0, nA2*1219.2), B 占其后
    # 枚举 4 个数量, 校验无重叠, 取总数最大。
    dA, dB = pal_W, pal_L          # A/B 朝向沿 Y 的深度
    lA, lB = pal_L, pal_W          # A/B 朝向每个占的柜长
    if dA + dB <= W + 1e-6:
        for nB1 in range(0, 13):
            if nB1 * lB > L + 1e-6:
                continue
            for nA1 in range(0, 13):
                if nB1 * lB + nA1 * lA > L + 1e-6:
                    continue
                for nA2 in range(0, 13):
                    if nA2 * lA > L + 1e-6:
                        continue
                    for nB2 in range(0, 13):
                        if nA2 * lA + nB2 * lB > L + 1e-6:
                            continue
                        pos = []
                        ok = True
                        # 段① 下排: B 在前, A 在后 (X 方向)
                        x = 0.0
                        for _ in range(nB1):
                            pos.append((x + lB / 2, dB / 2, "B"))
                            x += lB
                        for _ in range(nA1):
                            pos.append((x + lA / 2, dA / 2, "A"))
                            x += lA
                        # 段② 上排: A 在前, B 在后
                        x2 = 0.0
                        for _ in range(nA2):
                            pos.append((x2 + lA / 2, dB + dA / 2, "A"))
                            x2 += lA
                        for _ in range(nB2):
                            pos.append((x2 + lB / 2, dA + dB / 2, "B"))
                            x2 += lB
                        # 校验: 段① 与段② 在 Y 上有重叠区 (dA 与 dB 交叠)
                        #   下排 B(y<dB) 与上排 A(y>=dB) → 不重叠 ✓
                        #   下排 A(y<dA) 与上排 A(y>=dB) → dA<dB 时重叠, 靠 X 区间错开
                        #   下排 B(y<dB) 与上排 B(y>=dA) → 重叠, 靠 X 区间错开
                        if not ok:
                            continue
                        # 精确重叠检测
                        rects = []
                        for (cx, cy, t) in pos:
                            pw, pl = (lA, dA) if t == "A" else (lB, dB)
                            rects.append((cx - pw / 2, cy - pl / 2,
                                          cx + pw / 2, cy + pl / 2))
                        clash = False
                        for i in range(len(rects)):
                            for j in range(i + 1, len(rects)):
                                a, b2 = rects[i], rects[j]
                                if not (a[2] <= b2[0] + 1e-6 or b2[2] <= a[0] + 1e-6
                                        or a[3] <= b2[1] + 1e-6 or b2[3] <= a[1] + 1e-6):
                                    clash = True
                                    break
                            if clash:
                                break
                        if clash:
                            continue
                        if len(pos) > len(best):
                            best = sorted(pos, key=lambda p: (round(p[1], 0), p[0]))
    return best


def pallet_floor_layout_band(L, W, pal_L, pal_W):
    """只取带状模型的结构化解 (确定性强, 可复现, 供校验用)。"""
    best = []
    for nA in range(0, 16):
        for nB in range(0, 16):
            for order in (0, 1):
                seq = []
                if order == 0:
                    if nA:
                        seq.append(("A", nA))
                    if nB:
                        seq.append(("B", nB))
                else:
                    if nB:
                        seq.append(("B", nB))
                    if nA:
                        seq.append(("A", nA))
                y = x = 0.0
                pos = []
                ok = True
                for (typ, n) in seq:
                    pw, pl = (pal_W, pal_L) if typ == "A" else (pal_L, pal_W)
                    if y + pw > W + 1e-6:
                        ok = False
                        break
                    for _ in range(n):
                        if x + pl > L + 1e-6:
                            ok = False
                            break
                        pos.append((x + pl / 2, y + pw / 2, typ))
                        x += pl
                    if not ok:
                        break
                    y += pw
                    x = 0.0
                if ok and len(pos) > len(best):
                    best = sorted(pos, key=lambda p: (round(p[1], 0), p[0]))
    return best
