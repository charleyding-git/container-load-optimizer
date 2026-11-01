---
name: container-load-optimizer
description: 集装箱/货柜装载优化。当用户需要做货柜装载方案、装箱优化、码垛方案、托盘打托、装柜数量计算、装载率分析、3D 装载可视化、生成装柜作业方案 (xlsx) 时使用。支持有托盘/无托盘两种模式，20/40 尺各类柜型（标柜/高柜/冷链），美标/欧标/国标等托盘规格，以及混装、顶部堆叠、TiHi 带板层数、收货区堆高上限、单托限重等约束，内置四层校验门禁 (几何/稳定性承重/三方一致性/视觉审查) 与本地网页 UI 控制台。触发词：装柜、装箱、码垛、打托、托盘、集装箱、货柜、装载率、装载方案、3D 装载、packing plan、container loading、pallet loading。
---

# 集装箱装载优化器

把一份货物清单变成**可交付的装柜方案**：柜数、装载率、逐托作业卡、3D 可视化。

## 何时用

- 「这批货要几个柜」「怎么装最省」「做个装载方案」
- 需要逐托作业卡（现场叉车照着打托）
- 需要 3D 装载图给客户或仓库确认
- 需要对比不同柜型/托盘/约束下的方案

## 三条铁律

1. **校验不通过 = 禁止交付。** L1/L2/L3 任一出现 error，停止并报告，不要"先给个大概"。
2. **渲染与方案必须同源。** 3D 图的每个箱子坐标来自求解器输出的 `plan.json`，渲染器零布局逻辑。不允许在渲染层"凭感觉摆箱子"。
3. **必须做视觉审查。** 渲染后用 Playwright 截图 → `ViewImage` 目视复核 → 有问题就修再验。数值校验通过 ≠ 视觉正确。

## 快速开始（CLI）

```bash
SKILL=~/.workbuddy/skills/container-load-optimizer

# 1. 生成输入模板（发给用户填）
python $SKILL/scripts/make_template.py --out <输入路径>.xlsx --demo

# 2. 写配置文件
cat > cfg.json << 'EOF'
{
  "container": "40HQ",
  "pallet": "US48",
  "constraints": {
    "mode": "pallet", "mono_sku": false, "allow_top": true,
    "allow_double_pallet": false, "tihi_layers": null,
    "max_pallet_stack_h": null, "max_pallet_weight": null,
    "min_support_ratio": null, "max_cog_offset": 0.15,
    "optimize": "containers", "max_containers": 7, "restarts": 12
  }
}
EOF

# 3. 求解 + 校验（校验失败返回码 1）
python $SKILL/scripts/pipeline.py --input <输入>.xlsx --config cfg.json --outdir out/

# 4. 导出三件套
python $SKILL/scripts/export_xlsx.py --plan out/plan.json --out 装载方案_作业方案.xlsx
python $SKILL/scripts/export_viz.py  --plan out/plan.json --out 装载方案_3D可视化.html --scenes out/scene_data.json

# 4b. 双模式合并（一个 HTML 里用「模式」下拉切换两种装载方式）
python $SKILL/scripts/export_viz.py --plan out/托盘.plan.json --scenes out/scene_data.json \
    --alt-plan out/无托盘.plan.json --out 装载方案_3D可视化.html
```

## 本地网页 UI（输入→输出全流程，推荐给非技术用户）

**位置**：`~/.workbuddy/skills/container-load-optimizer/ui/`（`server.py` 后端 + `index.html` 前端，零第三方依赖）

**启动方式（推荐双击）**：桌面已有 `装载优化UI.bat`（原件在 skill 根目录 `启动装载优化UI.bat`）。
双击即自动起服务 → 等端口就绪 → 自动打开浏览器；已在运行则直接开浏览器。
命令行为：

```bash
python $SKILL/ui/server.py --port 8770
# 浏览器打开 http://127.0.0.1:8770
```

> Windows 提示：Git Bash 里的 `timeout` 会抢占 Windows `timeout`，脚本内已改用 `ping` 延时。
> 服务是**前台进程**，关掉窗口即停止；需要常驻可用开机自启任务。

**页面即完整工作流**（左输入右输出）：

| 区域 | 内容 |
|---|---|
| 左栏 ① 输入数据 | 上传 Excel（每 sheet 一批次）/ 下载空白模板 |
| 左栏 ② 集装箱柜型 | 柜型下拉 + 托盘下拉 + 自定义托盘尺寸 |
| 左栏 ③ 装载方式 | **有托盘 / 无托盘** 单选（切换时自动隐藏托盘专属约束） |
| 左栏 ④ 装载约束 | 一托一SKU、顶部平躺、双层托盘、TiHi、堆高、限重、支撑率策略、层序优化、重心 |
| 左栏 ⑤ 求解目标 | 严格按设定 / 自动优化 + 重启次数 + **柜数硬上限** |
| 左栏 ⑤ | **同时求解另一装载方式**（默认开）→ 一次算出托盘+无托盘两套 |
| 右栏 | 统计卡 + **模式/批次下拉** + 3D 预览（旋转/缩放/播放装载顺序/线框） + 校验报告 + 各柜明细表 |
| 底部 | 一键导出三件套 zip（3D可视化 + 作业方案 xlsx + plan.json + scene_data.json + 另一模式作业方案） |

**双模式**：勾选「同时求解另一装载方式」后，3D 预览与导出的可视化 HTML 都含**两套方案**，
用「模式」下拉切换对比（模式A 无托盘 / 模式B 有托盘），明细表带「模式」列。

**约束语义（重要）**：
- 手动勾选 = **硬约束**，求解器绝不违反（如一托一 SKU、TiHi 层数、堆高上限、单托限重）
- 顶部平躺散放 = **软约束**，自动优化模式可放宽
- 优化目标选「严格按设定」= 硬约束优先，柜数可能变多；选「自动优化」= 柜数优先

## 输入格式

Excel，**每个 working sheet = 一个批次**（同批一起发运）。表头在第 1~8 行内，解析器按关键词识别列。

| 列 | 必填 | 说明 |
|---|---|---|
| SKU编码 | ✅ | |
| 长(mm) / 宽(mm) / 高(mm) | ✅ | **单位必须 mm**，解析器不做换算 |
| 箱数 | ✅ | |
| 品名 | | |
| 每箱件数 | | |
| 单箱毛重(kg) | | 填了才能做承重/重心校验 |
| 边压强度ECT(kN/m) | | 缺省 6.0；用于凯利卡特公式推算堆码层数 |
| 限堆层数 | | 缺省由 ECT + 环境折减 + 安全系数3 自动推算 |

## 四层校验

| 层 | 内容 | 失败级别 |
|---|---|---|
| **L1 几何** | 尺寸有效性、箱出托盘、箱出柜壁、托盘互不重叠、箱互不重叠、门开口高、装载率≤100% | error |
| **L2 稳定性承重** | 垂直支撑率(≥75%警告/<50%错误)、单托载重、堆码层数上限、重心横向偏移(≤15%警告)、托盘承载利用率(≥80%警告) | error / warn |
| **L3 三方一致性** | plan.json ↔ xlsx ↔ 3D渲染 三方数值比对；配色映射一致 | error |
| **L4 视觉审查** | Playwright 截图 + ViewImage 目视 | 由上层执行 |

L1/L2 在 `pipeline.py` 内自动执行；L3 在导出后用 `verify_consistency.py`；L4 必须人工/Agent 执行。

## 交付前必做（流程）

```
1. python pipeline.py ...              → 确认输出「结论: 通过 ✓」
2. python verify_consistency.py        → 确认 L3 三方一致
2.5 python scripts/build_report.py \
     --plan out/plan.json --scenes out/scene_data.json \
     --out 装载优化方案.html            → 生成单文件 HTML 报告（结论/对比/裸地机制/逐柜3D/校验/选型）
3. Playwright 截图全部场景             → 确认无箱体出柜壁、无 SKU 错位
4. ViewImage 逐张目视复核              → 确认视觉正确
5. 三件套 + 报告交付
```

> `build_report.py` 是报告型交付物的**唯一生成器**，从 `plan.json`+`scene_data.json` 读数据、零硬编码业务数值（柜数/箱数/装载率全部取自流水线产物）。无托盘模式（plan.json 不含 `pal` 字段）时自动跳过柜底裸地章节并渲染无托盘说明，不崩溃。

任何一步不过，回到对应环节修复后**重新跑完整流程**。

## 关键设计决策（改动前先读）

- **MaxRects `_split` 必须用「全量重算空闲矩形」**，不能用增量切割 —— 增量切割产生重叠空闲矩形，装箱数会少一半（实测 cpl 3 vs 6）。
- **托盘面平铺用三段式 Y 交错**（`engine.pallet_floor_layout`）：纯带状只能放 20 个美标托盘，三段式才能放满 21 个。差 1 个托盘位 = 差 1 个柜。
- **B 朝向托盘的 2D 布局需 90° 刚体变换**：`new_x=y, new_y=PAL_L-x-w, new_w=h, new_h=w`。漏了会导致上万个箱位越界。
- **层间混装必须做支撑对齐**（`stacker.py`）：不同 SKU 的箱型尺寸不同，各层独立做 MaxRects 会让上层悬空。`SupportMap` 用 X 方向区间索引加速。
- **支撑率不是风险指标 —— 判据必须载荷驱动**（见 `references/modeling_basis.md` §4.1）：
  - 纸箱短时悬空**不会**失效（抗弯余量实测 100~50000 倍），重心也不出支撑面；
  - 真正失效是**局部压溃**：`承压需求(上方累积载荷) > 承压能力(BCT × 支撑面积比例)`；
  - **重量天然进入判据** —— 同一支撑率下，8kg 轻抛货余量 9.7 倍，90kg 重货 3 层就压溃；
  - 因此默认 `support_policy="relaxed_all"`：**支撑率不作为硬约束触发修复**，
    支撑率不达标降为 info 级提示，由缠绕膜/捆扎等外部手段兜底；
  - 但**完全无支撑（支撑率 0%）无条件强制修复** —— 那是物理硬伤（轻抛货也会掉），外部手段救不了。
- **层序优化可消除完全悬空，代价是多 1 个柜**（`reorder_layers`）：把支撑不佳的层挪到托盘上部
  （上方累积载荷小 → 压溃风险低）。实测悬空 3→0，柜数 7→8。
- **箱数守恒是硬门禁**：修复/回填后必须复核每批次装箱数 == 需求箱数，否则禁止交付。
- **柜底裸地利用由 `max_containers` 驱动**（`solver._cap_to_max`）：当某批次柜数超过上限，最末柜整柜溶解，其箱作为裸地散箱（`kind="bare"`，无托盘、底层支撑率 100%）分摊进前 N 柜柜底自由区。落位用**逐箱 3D AABB 重叠检测**（公差 0.6mm），`validator._check_bare_overlap` 按柜校验，`export_viz` 以橙色虚线描边渲染。
  - **关键事实**：40HQ 满 21 托后每柜剩约 2.29 m² 裸地，但全是托盘间的狭长碎条；7 柜装法里托盘从柜门端（x=0）铺到箱头端，**柜门端 gap=0 —— 不存在"柜门端裸地"**，裸地只在内部缝隙。订单量固定则无多余货填满全部裸地，非算法遗漏。
  - 裸地机制的真正价值：把"本会逼出第 8 柜的溢出箱"吸收进前 7 柜裸地，**守住柜数上限**（非凭空增密）。
- **`max_containers` 是硬约束且按批次限定**：在 `Constraints` 中默认 `None`（不限制）；设为 7 即锁死每批次 ≤7 柜。
- **无托盘模式 = 货垛柱（`solver.solve_nopallet_mode`）**：纸箱直接码垛成柱，不使用木托盘。
  - 有效堆高取**门开口高**（无托盘货箱需逐箱过门），不是柜内高。
  - 两阶段：Phase1 地面立柱（选填充率最高的朝向在柜底 MaxRects 平铺，每柱向上堆 `int(Heff//vz)` 箱）→ Phase2 柱顶回填（footprint ≤ 柱底面，继续往上塞更小朝向）。
  - 货垛柱在 `plan.json` 中建模为 `containers[].pallets[]` 条目（复用 L1/L2 校验），但 `npallets` 记 0，3D 渲染**不画托盘底座**。
  - 托盘专属准则在该模式下自动跳过：托盘高下限、托盘面积利用率、单托限重、托盘重心高 2/3（否则货垛柱必然误报）。

## 参考资料

- `references/config_spec.md` —— 配置文件字段全解 + 约束语义
- `references/modeling_basis.md` —— 建模依据（Bortfeldt & Wäscher 约束分类、GB/T 16470、TiHi、Amazon FBA、凯利卡特公式）
- `references/container_specs.md` —— 柜型与托盘规格表（含数据来源）

## 已知限制

- 求解器是**启发式**（层构建 + MaxRects + FFD + 多轮随机重启），不保证全局最优；精确算法（MILP/CP-SAT）在此规模不可用。
- 无托盘模式（货垛柱）的支撑/重心校验较弱：托盘专属准则（托盘高下限、托盘面积利用率、单托限重、托盘重心高 2/3）在该模式下自动跳过，只靠 L1 几何（箱出柜壁、箱互不重叠、门开口高、箱数守恒）保证。
- 冷链柜规格按 ISO 常见值，实际箱况有差异，交付前建议与货代核对。
- 不建模：液体/散装/危品/温控分层/卸货顺序（LIFO）/轴荷分配。
