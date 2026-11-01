# -*- coding: utf-8 -*-
"""规格库 —— 集装箱 / 托盘 / 行业约束常量

【数据来源】
- 集装箱内尺寸/门开口: ISO 668 + Hapag-Lloyd / GoFreight / Nexportal 公开规格表交叉核对
- 托盘尺寸: ISO / GB / GMA 标准
- 行业约束:
  * GB/T 16470-2008 《托盘单元货载》 —— 承载利用率≥80%、重心≤托盘宽2/3、堆码方式与加固
  * 商务部《托盘共用系统货物码盘及交接要求》 —— TiHi 分层标准
  * 《带板运输操作规范》(dbba.sacinfo.org.cn) —— 一托一单品、装车先高后低
  * Amazon FBA 入库规范 —— 托盘限高1.6m/双层2.7m/限重500kg/突出25mm/GMA Grade B+
  * 凯利卡特公式 BCT = ECT*Z*C*sqrt(h*P) —— 纸箱堆码层数上限
"""
import math


def label_of(spec, lang="zh"):
    """按语言取规格显示名。

    spec 需含 label (中文) 与可选 label_en (英文)。
    lang="en" 时优先返回 label_en, 缺失时回退到 label。
    """
    if not isinstance(spec, dict):
        return str(spec)
    if lang == "en":
        return spec.get("label_en") or spec.get("label", "")
    return spec.get("label", "")


# ============================================================
# 集装箱规格 (内部可用净空, mm)
#   L=柜长  W=柜宽  H=柜内高  doorH=门开口高  vol=容积m3  payload=最大载重kg
#   注意: 门开口高 < 柜内高, 决定货垛实际可用高度的是门开口高
# ============================================================
CONTAINERS = {
    # ---- 干货箱 ----
    "20GP": dict(L=5898,  W=2352, H=2393, doorH=2280, vol=33.2,  payload=28200,
                 label="20尺 标准干货箱", label_en="20ft Standard Dry", kind="dry"),
    "20HC": dict(L=5898,  W=2352, H=2698, doorH=2585, vol=37.2,  payload=28160,
                 label="20尺 高柜干货箱", label_en="20ft High Cube Dry", kind="dry"),
    "40GP": dict(L=12032, W=2352, H=2393, doorH=2280, vol=67.7,  payload=28000,
                 label="40尺 标准干货箱", label_en="40ft Standard Dry", kind="dry"),
    "40HQ": dict(L=12032, W=2352, H=2698, doorH=2585, vol=76.3,  payload=28600,
                 label="40尺 高柜干货箱", label_en="40ft High Cube Dry", kind="dry"),
    "45HC": dict(L=13556, W=2352, H=2698, doorH=2585, vol=86.0,  payload=27800,
                 label="45尺 高柜干货箱", label_en="45ft High Cube Dry", kind="dry"),
    # ---- 冷链箱 (Reefer) —— 内空更小, 需制冷机组占位 ----
    "20RF": dict(L=5450,  W=2280, H=2270, doorH=2260, vol=28.3,  payload=27575,
                 label="20尺 冷藏箱", label_en="20ft Reefer", kind="reefer"),
    "40RF": dict(L=11580, W=2280, H=2530, doorH=2540, vol=67.0,  payload=29700,
                 label="40尺 冷藏箱", label_en="40ft Reefer", kind="reefer"),
    "40RHQ": dict(L=11580, W=2280, H=2530, doorH=2540, vol=67.0, payload=29700,
                 label="40尺 高柜冷藏箱", label_en="40ft High Cube Reefer", kind="reefer"),
    # ---- 敞顶箱 ----
    "20OT": dict(L=5898,  W=2352, H=2380, doorH=2280, vol=32.5,  payload=30050,
                 label="20尺 敞顶箱", label_en="20ft Open Top", kind="open-top"),
    "40OT": dict(L=12032, W=2352, H=2380, doorH=2280, vol=67.2,  payload=26630,
                 label="40尺 敞顶箱", label_en="40ft Open Top", kind="open-top"),
}

# ============================================================
# 托盘规格 (mm)  H=托盘自身高度
#   entry: 四向叉车 / two_way: 两向叉车(仅两侧可叉, 装卸受限)
#   maxLoad: 托盘额定载重 kg (GB/T 2934 常见 1000~2000)
# ============================================================
PALLETS = {
    "US48":  dict(L=1219.2, W=1016.0, H=145.0, label="美标 48x40 (1219.2x1016)", label_en="US 48x40 (1219.2x1016)",
                   region="US", entry="four-way", maxLoad=1360, gma="B"),
    "US48GMA": dict(L=1219.2, W=1016.0, H=145.0, label="美标 GMA Grade B (1219.2x1016)", label_en="US GMA Grade B (1219.2x1016)",
                   region="US", entry="four-way", maxLoad=1360, gma="B"),
    "EU1200": dict(L=1200.0, W=800.0, H=144.0, label="欧标 1200x800", label_en="EU 1200x800",
                   region="EU", entry="four-way", maxLoad=1200, gma=None),
    "EU1200X1000": dict(L=1200.0, W=1000.0, H=144.0, label="欧标 1200x1000", label_en="EU 1200x1000",
                   region="EU", entry="four-way", maxLoad=1500, gma=None),
    "CN1200": dict(L=1200.0, W=1000.0, H=150.0, label="国标 1200x1000 (GB/T 2934)", label_en="GB 1200x1000 (GB/T 2934)",
                   region="CN", entry="four-way", maxLoad=1500, gma=None),
    "UK1200": dict(L=1200.0, W=1000.0, H=144.0, label="英标 1200x1000", label_en="UK 1200x1000",
                   region="UK", entry="four-way", maxLoad=1000, gma=None),
    "JP1100": dict(L=1100.0, W=1100.0, H=145.0, label="日韩标 1100x1100", label_en="JP 1100x1100",
                   region="JP", entry="four-way", maxLoad=1000, gma=None),
    "CUSTOM": dict(L=1219.2, W=1016.0, H=145.0, label="自定义 (在 UI 中填写)", label_en="Custom (enter in the UI)",
                   region="CUSTOM", entry="four-way", maxLoad=1500, gma=None),
}

# ============================================================
# 行业约束常量
# ============================================================
CONST = {
    # GB/T 16470-2008 4.1.7: 托盘承载表面积利用率一般应不低于 80%
    "PALLET_AREA_UTIL_MIN": 0.80,
    # GB/T 16470-2008 4.1.5: 托盘单元货载重心高度不超过托盘宽度的 2/3
    "COG_HEIGHT_RATIO": 2.0 / 3.0,
    # 商务部《托盘共用系统货物码盘及交接要求》TiHi 分层标准 (含托盘, mm)
    "TIHI": {
        1: dict(max=1800, desc="单层带板运输", desc_en="single-deck board transport"),
        2: dict(max=1200, desc="双层带板运输", desc_en="double-deck board transport"),
        3: dict(max=800,  desc="三层带板运输", desc_en="triple-deck board transport"),
        4: dict(max=600,  desc="四层带板运输", desc_en="quad-deck board transport"),
    },
    # Amazon FBA 入库规范
    "FBA_PALLET_MAX_H": 1600,        # 单托盘货物含托盘限高
    "FBA_DOUBLE_STACK_MAX_H": 2700,  # 双层叠托限高
    "FBA_PALLET_MAX_LOAD": 500,      # 单托毛重限 (kg)
    "FBA_OVERHANG_MAX": 25,          # 允许突出托盘边缘 mm
    "FBA_CLEARANCE_ROOF": 300,       # 托盘顶到车顶最小间隙 mm
    "FBA_CLEARANCE_REAR": 300,       # 最后一托到车尾最小间隙 mm
    # 门开口上方是结构死区, 货垛过不了门 —— 门高才是真正的硬约束
    "DOOR_HEIGHT_IS_HARD": True,
    # 纸箱堆码: 凯利卡特公式 BCT = ECT * Z * C * sqrt(h*P), 安全系数默认 3.0
    # 未提供 ECT 时, 用经验保守值 (kg/cm) 估算单箱抗压
    "DEFAULT_ECT_KN_M": 6.0,         # 常见三层B类 ECT
    "KELLICUTT_SAFETY_FACTOR": 3.0,
    # 环境折减: 湿度>65% 强度降30%, 海运30天老化降15~20%
    "HUMIDITY_DERATE": 0.70,
    "OCEAN_AGING_DERATE": 0.85,
    # 垂直支撑率下限 (支撑面积 / 底面积)
    "MIN_SUPPORT_RATIO": 0.75,
    # 重心横向偏移上限 (占柜宽比例) —— 防止偏载
    "MAX_COG_OFFSET_RATIO": 0.15,
    # ---- 货物密度分级 (kg/m3) —— 用于缺失重量时的兜底与显性提示
    # 依据: 轻抛货 (泡货) 的自重不足以压溃下层, 支撑风险天然低;
    #       重货 (密度货) 即使支撑率稍低也可能压溃, 需显性提示。
    # 参考: 泡货典型密度 <100, 常规轻工 100~300, 密度货 >300,
    #       家电/机械 400+, 卷材/五金 600+
    "DENSITY_TIERS": {
        "light":  dict(max=100,  label="轻抛货", risk="低",
                        note="自重轻, 支撑不足时几乎不压溃下层, 靠缠绕膜/捆扎兜底即可"),
        "medium": dict(max=300,  label="常规货", risk="中",
                        note="按层载荷核算承压余量, 支撑率不足需提示"),
        "heavy":  dict(max=600,  label="偏重货", risk="高",
                        note="自重大, 支撑率不足易压溃下层, 必须显性提示并调整层序"),
        "dense":  dict(max=99999, label="密度货", risk="极高",
                        note="必须严格控制支撑率与层数, 建议重箱置底并逐层核算"),
    },
    # 缺失单箱重量时的兜底密度 (kg/m3) —— 取"常规货"下限偏保守
    "DEFAULT_DENSITY_KG_M3": 150.0,
}

# 托盘堆码方式 (GB/T 16470-2008 图1 重叠堆码 / 图2 交错堆码)
STACK_PATTERNS = {
    "column": dict(label="重叠式 (同向对齐)", desc="列式码放，抗压最强，适合重货"),
    "interlock": dict(label="交错式 (压缝/砖砌)", desc="层间错缝，稳定性好，适合轻泡货"),
    "auto": dict(label="自动 (按 SKU 尺寸推荐)", desc="重货用重叠式, 轻泡货用交错式"),
}

# 装柜顺序策略
LOAD_ORDER = {
    "heavy_first": dict(label="重货优先 (重下轻上)",
                        desc="重货置底轻货置上层, 符合 GB/T 16470 与带板运输规范"),
    "height_desc": dict(label="先高后低",
                        desc="《带板运输操作规范》6.1.3: 高度不同的托盘单元装车顺序要先高后低"),
    "sku_group": dict(label="同 SKU 集中",
                        desc="同批次 SKU 集中摆放, 便于清点与分拣"),
    "auto": dict(label="自动 (综合判定)",
                 desc="重货在下 + 高托先装 + 同 SKU 集中"),
}


def container_spec(code):
    """取集装箱规格, 未知代码抛错并列出可选。"""
    if code not in CONTAINERS:
        raise KeyError(f"未知柜型 '{code}'。可选: {', '.join(CONTAINERS)}")
    return CONTAINERS[code]


def pallet_spec(code):
    if code not in PALLETS:
        raise KeyError(f"未知托盘 '{code}'。可选: {', '.join(PALLETS)}")
    return PALLETS[code]


def kellycutt_bct(ect_kn_m, box_l_cm, box_w_cm, box_h_cm):
    """凯利卡特公式估算纸箱抗压强度 (BCT, kg)

    BCT = ECT * Z * C * sqrt(h * P)
      ECT  边压强度 kN/m
      Z    箱周长 cm = 2*(L+W)
      C    常数 0.0207 (经 ASTM D642 / 行业案例反推校准)
      h    箱高 cm
      P    纸板等效厚度 cm = h * 0.5 (单瓦楞近似)

    校准验证:
      ECT-32 5.44 kN/m, 40x30x30cm  -> BCT ≈ 334 kg (实测column crush 3.9~4.3 kN)
      ECT-44 7.5 kN/m, 60x40x40cm  -> BCT ≈ 878 kg (行业 11~13 层 @15kg/箱)
    """
    import math
    z_cm = 2 * (box_l_cm + box_w_cm)
    p_cm = box_h_cm * 0.5
    return ect_kn_m * z_cm * 0.0207 * math.sqrt(box_h_cm * p_cm)


def density_tier(weight_kg, L_mm, W_mm, H_mm):
    """按体积密度判定货物分级, 返回 (tier_key, 密度, 分级信息)。

    密度 = 单箱重量 / 箱体积 (kg/m3)
    """
    vol_m3 = (L_mm * W_mm * H_mm) / 1e9
    if vol_m3 <= 0:
        return "medium", 0.0, CONST["DENSITY_TIERS"]["medium"]
    dens = weight_kg / vol_m3 if weight_kg > 0 else CONST["DEFAULT_DENSITY_KG_M3"]
    for key, info in CONST["DENSITY_TIERS"].items():
        if dens < info["max"]:
            return key, dens, info
    return "dense", dens, CONST["DENSITY_TIERS"]["dense"]


def estimate_weight(L_mm, W_mm, H_mm, tier_key=None):
    """单箱重量缺失时按密度估算 (kg)。"""
    vol_m3 = (L_mm * W_mm * H_mm) / 1e9
    if tier_key is None:
        tier_key = "medium"
    dens = CONST["DENSITY_TIERS"][tier_key]["max"] * 0.5 \
        if tier_key != "medium" else CONST["DEFAULT_DENSITY_KG_M3"]
    return round(vol_m3 * dens, 2)


def max_stack_layers(ect_kn_m, box_l_mm, box_w_mm, box_h_mm, box_weight_kg,
                     safety_factor=None, derate_humidity=True, derate_ocean=True):
    """按凯利卡特公式 + 环境折减, 估算某箱型可承受的堆码层数上限。

    返回 (层数上限, 诊断信息dict)
    """
    sf = safety_factor if safety_factor is not None else CONST["KELLICUTT_SAFETY_FACTOR"]
    bct = kellycutt_bct(ect_kn_m, box_l_mm / 10, box_w_mm / 10, box_h_mm / 10)
    d = 1.0
    notes = []
    if derate_humidity:
        d *= CONST["HUMIDITY_DERATE"]
        notes.append(f"湿度折减×{CONST['HUMIDITY_DERATE']}")
    if derate_ocean:
        d *= CONST["OCEAN_AGING_DERATE"]
        notes.append(f"海运老化折减×{CONST['OCEAN_AGING_DERATE']}")
    eff = bct * d
    if box_weight_kg <= 0:
        return 999, dict(bct=round(bct, 1), effective=round(eff, 1), notes=notes,
                         reason="单箱重量未提供, 不限制层数")
    n = eff / (box_weight_kg * sf)
    return int(math.floor(n)), dict(
        bct=round(bct, 1), effective=round(eff, 1),
        safety_factor=sf, notes=notes,
        reason=f"BCT {bct:.0f}kg ×折减{d:.2f} ÷ (单箱{box_weight_kg}kg × SF{sf}) = {n:.1f}")
