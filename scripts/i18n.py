# -*- coding: utf-8 -*-
"""国际化 (i18n) 支持 —— 消息目录与语言切换。

设计要点
--------
校验消息按 **code + 参数** 编写, 文案集中在本文件的目录里, 中英两套
不会分叉。新增校验项时只需在 CATALOG 里加一条。

用法:
    from i18n import T, set_lang, tr
    set_lang("en")
    gate.error("L1", "BOX_OUT_OF_CONTAINER", ..., params={"container": 3})
"""
import os

# 当前语言: "zh" (默认) / "en"
_LANG = "zh"

# 支持的语言
LANGS = ("zh", "en")

# 语言显示名
LANG_NAMES = {"zh": "中文", "en": "English"}


def set_lang(lang):
    """设置全局语言。未知语言回退到中文。"""
    global _LANG
    _LANG = lang if lang in LANGS else "zh"
    return _LANG


def get_lang():
    return _LANG


def tr(key, **params):
    """按当前语言取文案并做参数替换。

    key 不存在时回退到 key 本身 (便于发现遗漏)。
    """
    entry = CATALOG.get(key)
    if entry is None:
        return key
    text = entry.get(_LANG) or entry.get("en") or key
    if params:
        try:
            return text.format(**params)
        except (KeyError, IndexError):
            return text
    return text


def resolve_cli_lang():
    """按优先级决定语言: --lang 参数 > 环境变量 WB_LANG > 中文。

    英文用户无需每次都记 --lang: 设一次 WB_LANG=en 即可。
    """
    for arg in os.sys.argv:
        if arg.startswith("--lang="):
            return set_lang(arg.split("=", 1)[1])
        if arg == "--lang" and os.sys.argv.index(arg) + 1 < len(os.sys.argv):
            return set_lang(os.sys.argv[os.sys.argv.index(arg) + 1])
    env = os.environ.get("WB_LANG", "").strip().lower()
    if env.startswith("en"):
        return set_lang("en")
    return set_lang("zh")


# ============================================================
# 消息目录
#   key -> {"zh": "...", "en": "..."}
#   占位符用 Python str.format 语法: {name}
# ============================================================
CATALOG = {
    # ---------- L1 几何 ----------
    "BARE_OVERLAP": {
        "zh": "柜{container} 裸地货垛相互重叠",
        "en": "Bare-floor stacks overlap in container {container}",
    },
    "CARTON_COUNT_MISMATCH": {
        "zh": "批次「{batch}」装载箱数 {loaded} ≠ 需求 {required}",
        "en": "Batch '{batch}': loaded {loaded} cartons != required {required}",
    },
    "PALLET_OUT_OF_CONTAINER": {
        "zh": "柜{container} 托盘{pallet} 超出柜体内壁",
        "en": "Container {container}: pallet {pallet} exceeds container walls",
    },
    "BOX_OUT_OF_PALLET": {
        "zh": "柜{container} 托{pallet} 箱子超出托盘边界",
        "en": "Container {container}: box on pallet {pallet} exceeds pallet boundary",
    },
    "BOX_OUT_OF_CONTAINER": {
        "zh": "柜{container} 箱子超出柜体内壁",
        "en": "Container {container}: box exceeds container walls",
    },
    "BOX_OVER_DOOR_HEIGHT": {
        "zh": "柜{container} 箱高 {box_top}mm 超门开口 {door_height}mm",
        "en": "Container {container}: box top {box_top}mm exceeds door opening {door_height}mm",
    },
    "INVALID_BOX_DIM": {
        "zh": "柜{container} 箱子尺寸非法 {w}x{d}x{h}",
        "en": "Container {container}: invalid box dimensions {w}x{d}x{h}",
    },
    "STACK_OUT_OF_CONTAINER": {
        "zh": "柜{container} 货垛柱超出柜体内壁",
        "en": "Container {container}: stack column exceeds container walls",
    },
    "PALLET_HEIGHT_INVALID": {
        "zh": "柜{container} 托盘货高 {h}mm 低于托盘自身高 {pal_h}mm",
        "en": "Container {container}: pallet load height {h}mm is below pallet height {pal_h}mm",
    },
    "BARE_OUT_OF_CONTAINER": {
        "zh": "柜{container} 裸地箱超出柜体内壁",
        "en": "Container {container}: bare-floor box exceeds container walls",
    },
    "BARE_OVER_DOOR_HEIGHT": {
        "zh": "柜{container} 裸地箱高 {top}mm 超门开口 {door_height}mm",
        "en": "Container {container}: bare-floor box top {top}mm exceeds door opening {door_height}mm",
    },
    "UTIL_OVER_100": {
        "zh": "柜{container} 装载率 {util}% 超过 100%",
        "en": "Container {container}: utilization {util}% exceeds 100%",
    },
    "LAYER_GAP": {
        "zh": "柜{container} 托{pallet} 层间存在 {gap}mm 空隙",
        "en": "Container {container}: {gap}mm gap between layers on pallet {pallet}",
    },
    "PALLET_OVERLAP": {
        "zh": "柜{container} 托盘 {a} 与 {b} 重叠",
        "en": "Container {container}: pallets {a} and {b} overlap",
    },
    "SOLVE_TIME": {
        "zh": "批次 {batch} 求解耗时 {elapsed}s",
        "en": "Batch {batch}: solved in {elapsed}s",
    },
    "BOX_OVERLAP": {
        "zh": "柜{container} 托{pallet} 箱子相互重叠",
        "en": "Container {container}: boxes overlap on pallet {pallet}",
    },
    "WEIGHT_MISSING": {
        "zh": "柜{container} 托{pallet} SKU {skus} 未提供单箱重量 —— 已跳过承重与重心精算",
        "en": "Container {container}: pallet {pallet} SKU {skus} has no per-box weight — "
              "bearing and CoG computation skipped",
    },
    "BEARING_OVERLOAD": {
        "zh": "柜{container} 托{pallet} SKU {sku} 承压不足: 承压需求 {demand}kg > 承压能力 {capacity}kg",
        "en": "Container {container}: pallet {pallet} SKU {sku} bearing overload — "
              "demand {demand}kg exceeds capacity {capacity}kg",
    },
    "BEARING_MARGIN_LOW": {
        "zh": "柜{container} 托{pallet} SKU {sku} 承压余量偏低: 需求 {demand}kg / 能力 {capacity}kg",
        "en": "Container {container}: pallet {pallet} SKU {sku} low bearing margin — "
              "demand {demand}kg / capacity {capacity}kg",
    },
    "XLSX_MISSING_CONTAINER": {
        "zh": "xlsx 缺少柜{container} 的记录",
        "en": "Spreadsheet is missing the record for container {container}",
    },
    "XLSX_MISMATCH": {
        "zh": "柜{container} 第{index}项不一致: plan={plan} xlsx={xlsx}",
        "en": "Container {container}: item {index} differs — plan={plan} xlsx={xlsx}",
    },
    "VIZ_MISSING_CONTAINER": {
        "zh": "3D 渲染缺少柜{container}",
        "en": "3D rendering is missing container {container}",
    },
    "VIZ_CARTON_MISMATCH": {
        "zh": "柜{container} 箱数不一致: plan={plan} viz={viz}",
        "en": "Container {container}: carton count differs — plan={plan} viz={viz}",
    },
    "VIZ_UTIL_MISMATCH": {
        "zh": "柜{container} 装载率不一致: plan={plan}% viz={viz}%",
        "en": "Container {container}: utilization differs — plan={plan}% viz={viz}%",
    },
    "VIZ_BOX_COUNT_MISMATCH": {
        "zh": "柜{container} 3D 货箱数 {viz} ≠ plan 箱数 {plan}",
        "en": "Container {container}: 3D box count {viz} != plan carton count {plan}",
    },
    "VIZ_TOTAL_COUNT_MISMATCH": {
        "zh": "3D 渲染货箱总数 {viz} ≠ plan 箱数 {plan}",
        "en": "3D rendering total box count {viz} != plan carton count {plan}",
    },
    "COLOR_MISSING": {
        "zh": "3D 渲染缺少 SKU {sku} 的配色",
        "en": "3D rendering is missing a color for SKU {sku}",
    },
    "COLOR_MISMATCH": {
        "zh": "SKU {sku} 配色不一致: plan={plan} viz={viz}",
        "en": "SKU {sku} color differs — plan={plan} viz={viz}",
    },

    # ---------- L2 稳定性 / 承重 ----------
    "PALLET_OVER_WEIGHT": {
        "zh": "柜{container} 托{pallet} 载重 {weight}kg 超限 {limit}kg",
        "en": "Container {container}: pallet {pallet} weighs {weight}kg, over limit {limit}kg",
    },
    "PALLET_AREA_UTIL_LOW": {
        "zh": "柜{container} 托{pallet} 托盘承载利用率 {util}% 低于 GB/T 16470 建议的 {min_util}%",
        "en": "Container {container}: pallet {pallet} footprint utilization {util}% is below the "
              "{min_util}% advised by GB/T 16470",
    },
    "SUPPORT_RATIO_LOW_INFO": {
        "zh": "柜{container} 托{pallet} SKU {sku} 最低垂直支撑率 {ratio}% 低于 GB/T 16470 建议的 "
              "{min_ratio}% —— 承压余量充足, 建议以缠绕膜/捆扎兜底",
        "en": "Container {container}: pallet {pallet} SKU {sku} minimum vertical support ratio {ratio}% "
              "is below the {min_ratio}% advised by GB/T 16470 — bearing margin is sufficient, "
              "cover with stretch wrap / strapping",
    },
    "SUPPORT_RATIO_LOW": {
        "zh": "柜{container} 托{pallet} SKU {sku} 最低垂直支撑率 {ratio}% 低于 GB/T 16470 建议的 {min_ratio}%",
        "en": "Container {container}: pallet {pallet} SKU {sku} minimum vertical support ratio {ratio}% "
              "is below the {min_ratio}% advised by GB/T 16470",
    },
    "SUPPORT_ZERO_FIXED": {
        "zh": "柜{container} 托{pallet} SKU {sku} 存在完全无支撑的箱 (支撑率 0%), 已强制重排",
        "en": "Container {container}: pallet {pallet} SKU {sku} had fully unsupported boxes "
              "(0% support) — layout was forcibly rearranged",
    },
    "LOAD_PATH_BROKEN": {
        "zh": "柜{container} 托{pallet} 存在完全无支撑的箱 (支撑率 0%): {skus} —— 载荷路径断裂, "
              "运输中必坠落; 已自动重排该层, 请复核作业卡",
        "en": "Container {container}: pallet {pallet} has fully unsupported boxes (0% support): {skus} — "
              "load path broken, boxes would fall in transit; layer was rearranged automatically, "
              "please review the job card",
    },
    "STACK_LAYERS_EXCEED": {
        "zh": "柜{container} 托{pallet} SKU {sku} 堆了 {layers} 层, 超上限 {limit} 层",
        "en": "Container {container}: pallet {pallet} stacks SKU {sku} {layers} layers, "
              "over the limit of {limit}",
    },
    "COG_TOO_HIGH": {
        "zh": "柜{container} 最高托{pallet} 货载重心高 {cog}mm 超 GB/T 16470 建议的 {limit}mm (托盘宽2/3)",
        "en": "Container {container}: highest load CoG on pallet {pallet} is {cog}mm, above the "
              "{limit}mm advised by GB/T 16470 (2/3 of pallet width)",
    },
    "COG_OFFSET": {
        "zh": "柜{container} 整柜重心横向偏移 {offset}% 超上限 {limit}%",
        "en": "Container {container}: lateral CoG offset {offset}% exceeds the {limit}% limit",
    },
    "HEAVY_CARGO_NOTICE": {
        "zh": "柜{container} 托{pallet} 含重货: {sku}(偏重货 {density}kg/m3) —— "
              "重货支撑不足易压溃下层, 建议重箱置底、逐层核算承压余量",
        "en": "Container {container}: pallet {pallet} contains heavy cargo {sku} "
              "(density {density} kg/m3) — insufficient support under heavy boxes risks crushing the "
              "layers below; place heavy boxes at the bottom and check bearing margin per layer",
    },

    # ---------- UI / CLI 通用 ----------
    "BATCH": {"zh": "批次", "en": "Batch"},
    "CONTAINERS": {"zh": "柜", "en": "containers"},
    "CARTONS": {"zh": "箱", "en": "cartons"},
    "CBM": {"zh": "CBM", "en": "CBM"},
    "UTILIZATION": {"zh": "装载率", "en": "Utilization"},
    "PALLETS": {"zh": "托盘", "en": "pallets"},
    "PASSED": {"zh": "通过", "en": "PASSED"},
    "FAILED": {"zh": "失败", "en": "FAILED"},
    "VALIDATION": {"zh": "校验", "en": "validation"},
}


# ============================================================
# Excel 作业方案文案目录
#   表格标题、章节名、表头、说明文字
# ============================================================
XLSX_TEXT = {
    "zh": {
        "sheet_summary": "方案汇总",
        "sheet_containers": "各柜装载",
        "sheet_pallet": "托盘装载卡",
        "sheet_nopallet": "货垛柱明细",
        "sheet_specs": "规格与标准",
        "sheet_secure": "外部加固与作业要求",
        "title": "集装箱装载优化方案",
        "sec1": "【一】前置条件与约束（硬约束 = 求解器绝不违反）",
        "sec2": "【二】各柜装载结果",
        "sec3": "【三】托盘装载卡（现场作业卡）",
        "sec4": "【四】规格与合规依据",
        "sec5": "【五】外部加固与作业要求",
        "sec_validate": "【三】校验结论",
        "k_val_result": "校验结果",
        "h_item": "约束项", "h_value": "取值", "h_note": "说明",
        "h_ctr": "柜号", "h_batch": "批次", "h_cartons": "纸箱",
        "h_util": "装载率%", "h_sku": "SKU 分布", "h_hmax": "最高货高(mm)",
        "h_npal": "托盘数", "h_project": "项目", "h_req": "要求", "h_basis": "依据",
        "h_pallet": "托盘号", "h_sku_mix": "SKU 搭配", "h_layers": "层序",
        "h_layer": "层", "h_warehouse": "仓库/货位", "h_note2": "备注",
        "k_box_spec": "集装箱规格", "k_pal_spec": "托盘规格",
        "k_total_cartons": "总箱数", "k_total_cbm": "总货量",
        "k_total_ctr": "总柜数",
        "box_inner": "内空", "box_door": "门开口高", "box_vol": "容积",
        "exp_mode": "有托盘走托盘模式; 无托盘走货垛柱模式",
        "exp_mono": "硬约束: 严格执行, 违反即判定方案不可行",
        "exp_top": "软约束: 自动优化模式可放宽",
        "exp_dbl": "硬约束: 涉及叉车作业与收货区高度",
        "exp_tihi": "GB/T 商务部托盘共用系统 TiHi 标准",
        "exp_maxh": "硬约束: 超过货架/收货区高度会被拒收",
        "exp_maxw": "Amazon FBA 单托毛重上限 500kg",
        "exp_stack": "重叠式抗压强; 交错式稳定性好",
        "exp_order": "重货在下; 高托先装; 同SKU集中",
        "exp_ratio": "箱底被支撑面积占比, 防塌垛",
        "exp_cog": "防偏载, 保障运输安全",
        "exp_layers": "凯利卡特公式 + 环境折减 + 安全系数3",
        "exp_goal": "containers=柜数最少; strict=严格按设定",
        "exp_pol_relaxed": "仅修完全悬空, 其余提示后由外部加固兜底",
        "exp_pol_balanced": "修复支撑率明显不足的层",
        "exp_pol_strict": "支撑率作硬约束, 绝不放宽",
        "exp_reorder": "支撑不佳的层尽量安排到托盘上部",
        "exp_bare": "装不下整托盘的零散空间用于散装码放",
        "exp_obj": "柜数最少 (软约束可放宽)",
        "exp_obj_strict": "严格按上述设定",
        "k_mode": "装载方式", "k_mono": "一托一SKU", "k_top": "顶部平躺散放",
        "k_dbl": "双层托盘", "k_tihi": "TiHi 带板层数", "k_maxh": "收货区托盘货物限高",
        "k_maxw": "单托限重", "k_pol": "支撑率策略", "k_reorder": "层序优化",
        "k_ratio": "垂直支撑率下限", "k_cog": "重心横向偏移上限",
        "k_layers": "各SKU堆码层数上限", "k_goal": "优化目标",
        "k_stack": "堆码方式", "k_order": "装柜顺序",
        "v_yes": "是 (硬约束)", "v_no": "否 (允许混装)",
        "v_allow": "允许", "v_forbid": "禁止",
        "v_pallet": "有托盘 (托盘模式)", "v_nopallet": "无托盘 (货垛柱模式)",
        "tihi_note": "层, 货高上限按 TiHi 标准",
        "secure_intro": "支撑率不达标处由外部手段兜底, 详见各柜作业卡备注。",
        "sec_wrap": "整体缠绕膜",
        "sec_strap": "十字捆扎",
        "sec_board": "层间垫板",
        "sec_heavy": "重货置底",
        "sec_note": "托盘承载利用率不低于 80%, 重心高度不超过托盘宽度 2/3。",
        "spec_gbt": "GB/T 16470-2008 托盘单元货载",
        "spec_iso": "ISO 668 集装箱尺寸",
        "spec_tihi": "商务部托盘共用系统 TiHi 标准",
        "spec_fba": "Amazon FBA 入库规范",
        "spec_kelly": "GB/T 4857.4 凯利卡特公式",
        "h_rule": "规则", "h_value2": "本方案取值",
        "sheet_cargo": "货物明细", "sheet_ctrsum": "各柜汇总",
        "cargo_sub": "尺寸与堆码参数", "ctrsum_sub": "SKU 分布与装载率",
        "h_sku_code": "SKU编码", "h_desc": "品名", "h_len": "长(mm)",
        "h_wid": "宽(mm)", "h_hgt": "高(mm)", "h_pcs": "每箱件数",
        "h_wt": "单箱毛重(kg)", "h_wt_total": "总重(kg)", "h_maxlay": "限堆层数",
        "h_palpos": "托盘位", "h_rot": "朝向", "h_cx": "中心X(mm)",
        "h_cy": "中心Y(mm)", "h_palsize": "托盘尺寸(mm)", "h_perlayer": "每层箱数",
        "h_layerh": "层高(mm)", "h_cumh": "层累计高(mm)", "h_loadh": "托盘货高(mm)",
        "v_auto": "自动推算",
        "cargo_note": "限堆层数说明: 未指定时按凯利卡特公式 BCT=ECT×Z×C×√(h×P) 估算, 并计湿度折减×0.70、海运老化折减×0.85、安全系数3.0。",
    },
    "en": {
        "sheet_summary": "Plan summary",
        "sheet_containers": "Container loading",
        "sheet_pallet": "Pallet job cards",
        "sheet_nopallet": "Column stack detail",
        "sheet_specs": "Specs and standards",
        "sheet_secure": "External securing & handling",
        "title": "Container Load Optimization Plan",
        "sec1": "【1】Preconditions and constraints (hard constraints are never violated)",
        "sec2": "【2】Loading result per container",
        "sec3": "【3】Pallet job cards (for the floor)",
        "sec4": "【4】Specifications and compliance basis",
        "sec5": "【5】External securing and handling requirements",
        "sec_validate": "【3】Validation result",
        "k_val_result": "Validation",
        "h_item": "Constraint", "h_value": "Value", "h_note": "Notes",
        "h_ctr": "Ctr", "h_batch": "Batch", "h_cartons": "Cartons",
        "h_util": "Utilization%", "h_sku": "SKU mix", "h_hmax": "Max load height (mm)",
        "h_npal": "Pallets", "h_project": "Item", "h_req": "Requirement", "h_basis": "Basis",
        "h_pallet": "Pallet", "h_sku_mix": "SKU mix", "h_layers": "Layer order",
        "h_layer": "Layer", "h_warehouse": "Warehouse / slot", "h_note2": "Note",
        "k_box_spec": "Container spec", "k_pal_spec": "Pallet spec",
        "k_total_cartons": "Total cartons", "k_total_cbm": "Total volume",
        "k_total_ctr": "Total containers",
        "box_inner": "Internal", "box_door": "Door opening", "box_vol": "Volume",
        "exp_mode": "Palletized mode uses pallets; no-pallet mode stacks columns directly",
        "exp_mono": "Hard constraint: strictly enforced — violating it makes the plan infeasible",
        "exp_top": "Soft constraint: may be relaxed in auto-optimize mode",
        "exp_dbl": "Hard constraint: affects forklift handling and receiving-area height",
        "exp_tihi": "MOFCOM pallet-pooling TiHi standard",
        "exp_maxh": "Hard constraint: goods above the rack or receiving height are rejected",
        "exp_maxw": "Amazon FBA limits a pallet to 500 kg gross weight",
        "exp_stack": "Columnar stacks are stronger; interlocked stacks are more stable",
        "exp_order": "Heavy at the bottom; tall pallets first; same SKU grouped",
        "exp_ratio": "Share of the box base that is supported — prevents collapse",
        "exp_cog": "Prevents uneven loading, protecting transport safety",
        "exp_layers": "Kellicutt formula + environmental derating + safety factor 3",
        "exp_goal": "containers=fewest containers; strict=exactly as configured",
        "exp_pol_relaxed": "Repairs only fully unsupported boxes; the rest are covered externally",
        "exp_pol_balanced": "Repairs layers with clearly insufficient support",
        "exp_pol_strict": "Support ratio is a hard constraint, never relaxed",
        "exp_reorder": "Poorly supported layers are moved as high up the pallet as possible",
        "exp_bare": "Floor space that cannot hold a full pallet is used for loose cartons",
        "exp_obj": "Fewest containers (soft constraints may be relaxed)",
        "exp_obj_strict": "Strictly as configured above",
        "k_mode": "Loading mode", "k_mono": "One SKU per pallet", "k_top": "Flat-stacked boxes on top",
        "k_dbl": "Double-stacked pallets", "k_tihi": "TiHi board-deck layers",
        "k_maxh": "Max pallet load height in receiving area",
        "k_maxw": "Max pallet gross weight", "k_pol": "Support policy",
        "k_reorder": "Layer reordering", "k_ratio": "Min vertical support ratio",
        "k_cog": "Max lateral CoG offset", "k_layers": "Max stack layers per SKU",
        "k_goal": "Objective", "k_stack": "Stacking pattern", "k_order": "Loading order",
        "v_yes": "yes (hard)", "v_no": "no (mixing allowed)",
        "v_allow": "allowed", "v_forbid": "forbidden",
        "v_pallet": "palletized", "v_nopallet": "no pallet (column stacks)",
        "tihi_note": "layers; load height per the TiHi standard",
        "secure_intro": "Where the support ratio is below target, external securing covers it — see the per-container notes.",
        "sec_wrap": "Full stretch wrap",
        "sec_strap": "Cross strapping",
        "sec_board": "Interlayer boards",
        "sec_heavy": "Heavy goods at the bottom",
        "sec_note": "Pallet footprint utilization is not below 80%, and CoG height does not exceed 2/3 of the pallet width.",
        "spec_gbt": "GB/T 16470-2008 Pallet unit loads",
        "spec_iso": "ISO 668 container dimensions",
        "spec_tihi": "MOFCOM pallet-pooling TiHi standard",
        "spec_fba": "Amazon FBA inbound requirements",
        "spec_kelly": "GB/T 4857.4 Kellicutt formula",
        "h_rule": "Rule", "h_value2": "Value in this plan",
        "sheet_cargo": "Cargo detail", "sheet_ctrsum": "Container summary",
        "cargo_sub": "Dimensions and stacking parameters", "ctrsum_sub": "SKU mix and utilization",
        "h_sku_code": "SKU code", "h_desc": "Description", "h_len": "Length (mm)",
        "h_wid": "Width (mm)", "h_hgt": "Height (mm)", "h_pcs": "Pieces/carton",
        "h_wt": "Gross weight/carton (kg)", "h_wt_total": "Total weight (kg)",
        "h_maxlay": "Max stack layers",
        "h_palpos": "Pallet pos.", "h_rot": "Orientation", "h_cx": "Centre X (mm)",
        "h_cy": "Centre Y (mm)", "h_palsize": "Pallet size (mm)", "h_perlayer": "Cartons/layer",
        "h_layerh": "Layer height (mm)", "h_cumh": "Cumulative height (mm)",
        "h_loadh": "Pallet load height (mm)",
        "v_auto": "auto-derived",
        "cargo_note": "Max stack layers: when not specified, it is estimated with the Kellicutt formula "
                     "BCT=ECT×Z×C×√(h×P), including humidity derating ×0.70, ocean aging derating ×0.85 "
                     "and safety factor 3.0.",
    },
}


def xt(key, **params):
    """取 Excel 作业方案文案 (按当前语言)。"""
    lang = get_lang()
    src = XLSX_TEXT.get(lang, XLSX_TEXT["zh"]).get(key)
    if src is None:
        src = XLSX_TEXT["zh"].get(key, key)
    if params:
        try:
            return src.format(**params)
        except (KeyError, IndexError):
            return src
    return src
