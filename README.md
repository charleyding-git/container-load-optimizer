# Container Load Optimizer

**集装箱 / 货柜装载优化器** —— 把一份货物清单变成可交付的装柜方案：柜数、装载率、逐托作业卡、3D 装载图。

一条命令出结果，也可以开一个本地网页控制台给非技术同事用。

```
Excel 清单  →  求解 + 校验  →  作业方案 xlsx  +  3D 装载图  +  JSON 明细
```

---

## What it does

| | |
|---|---|
| **Two loading modes** | `pallet` 托盘模式（打托、层间混装、顶部平躺）/ `nopallet` 无托盘模式（纸箱直接码货垛柱） |
| **Container types** | 20GP / 20HC / 40GP / 40HQ / 45HC / 20RF / 40RF / 40RHQ / 20OT / 40OT |
| **Pallet standards** | US48 · US48 GMA · EU1200 · EU1200×1000 · CN1200 · UK1200 · JP1100 · 自定义 |
| **Batch handling** | 一个 Excel sheet = 一个批次，同批一起发运；跨批次柜号全局连续 |
| **Deliverables** | ① 作业方案 xlsx（逐托作业卡）② 交互式 3D 装载图（单文件 HTML）③ plan.json / scene_data.json |
| **Web console** | `ui/server.py` —— 上传 Excel、勾参数、一键求解、页面内 3D 预览、导出 zip |

### Constraints it understands

| 约束 | 性质 | 说明 |
|---|---|---|
| `max_containers` | **硬** | 柜数硬上限（按批次）。超出部分的最末柜溶解为**柜底裸地散箱**，分摊进前 N 柜的内部缝隙 |
| `mono_sku` | 硬 | 一托一 SKU（收货方要求时用） |
| `allow_mix_pallet` | 软 | 允许一托混装多 SKU —— 用不同 SKU 互补填补层高空隙，显著提高填充率 |
| `allow_top` | 软 | 托盘顶部平躺散放 |
| `allow_double_pallet` | 硬 | 双层托盘 |
| `tihi_layers` | 硬 | TiHi 带板层数 1~4（商务部《托盘共用系统货物码盘及交接要求》） |
| `max_pallet_stack_h` | 硬 | 收货区托盘货物限高 |
| `max_pallet_weight` | 硬 | 单托毛重上限（如 Amazon FBA 500kg） |
| `max_stack_layers_per_sku` | 硬 | 每箱型堆码层数上限 |
| `min_support_ratio` / `support_policy` | — | 垂直支撑率下限 / 支撑率策略（放任 / 均衡 / 严格） |
| `max_cog_offset` | 软 | 整柜重心横向偏移上限（占柜宽比例） |
| `reorder_layers` | 软 | 层序优化：把支撑不佳的层挪到托盘上部（上方载荷小 → 压溃风险低） |
| `optimize` | — | `containers` 柜数优先 / `strict` 严格按设定 |

---

## Four validation layers — nothing ships unverified

校验不通过 = 禁止交付。这是硬规则，不是建议。

| 层 | 内容 | 失败级别 |
|---|---|---|
| **L1 几何** | 尺寸有效性、箱出托盘、箱出柜壁、托盘/箱互不重叠、门开口高、装载率 ≤100%、**箱数守恒** | error |
| **L2 稳定性承重** | 垂直支撑率、单托载重、堆码层数上限、重心横向偏移、托盘承载利用率（GB/T 16470 ≥80%） | error / warn |
| **L3 三方一致性** | plan.json ↔ xlsx ↔ 3D 渲染 三方数值比对 + 配色映射一致 | error |
| **L4 视觉审查** | Playwright 截图 + 人工/Agent 目视复核 | 上层执行 |

**箱数守恒是硬门禁**：修复与回填之后必须复核「装箱数 == 需求箱数」，否则禁止交付。

### 承重判据是载荷驱动的，不是"支撑率一刀切"

纸箱短时悬空**不会**失效（抗弯余量实测 100~50000 倍），重心也不出支撑面。真正会坏的是**局部压溃**：

```
承压需求 (上方累积载荷)  >  承压能力 (BCT × 支撑面积比例)
BCT = ECT × Z × C × √(h·P)     # 凯利卡特公式
```

重量天然进入判据 —— 同一支撑率下，8kg 轻抛货余量 9.7 倍，90kg 重货 3 层就压溃。所以默认 `support_policy="relaxed_all"`：**支撑率不达标仅作提示**（由缠绕膜/十字捆扎/层间垫板兜底），但**完全无支撑（0%）无条件强制修复** —— 那是物理硬伤。

---

## 安装为 Skill

### 方式一：直接 clone 到 skills 目录（推荐）

本仓库本身就是一个标准 Skill 包（`SKILL.md` + `scripts/` + `references/`），放进对应目录即可被识别：

```bash
# WorkBuddy
git clone https://github.com/charleyding-git/container-load-optimizer.git \
    ~/.workbuddy/skills/container-load-optimizer

# Claude Code
git clone https://github.com/charleyding-git/container-load-optimizer.git \
    ~/.claude/skills/container-load-optimizer
```

装完依赖即可用：

```bash
pip install -r ~/.workbuddy/skills/container-load-optimizer/requirements.txt
```

### 方式二：只当命令行工具用（不装 skill）

```bash
git clone https://github.com/charleyding-git/container-load-optimizer.git
cd container-load-optimizer
pip install -r requirements.txt
python scripts/pipeline.py --input 订单.xlsx --config cfg.json --outdir out/
```

### 在 WorkBuddy / Claude Code 里怎么触发

安装后直接用自然语言说需求即可，无需记命令：

| 你说的话 | 会发生什么 |
|---|---|
| 「这批货要几个柜」 | 读你的 Excel → 求解 → 给出柜数与装载率 |
| 「做个装载方案」 | 输出柜数 + 逐托作业卡 + 3D 装载图 |
| 「对比有托盘和无托盘」 | 同时算两套方案，给出柜数与装载率对比 |
| 「装柜方案」/「container loading plan」 | 同上（SKILL.md 描述里已列中英触发词） |

Agent 会自动读取 `SKILL.md` 走完整流程，**并强制执行四层校验**——校验不通过会直接报错，不会给你一个"大概"的方案。

### 目录结构说明

```
container-load-optimizer/     ← 这个目录整体放进 skills 目录
├── SKILL.md                  ← Agent 指令 (frontmatter + 工作流 + 关键设计决策)
├── scripts/                  ← 可执行脚本 (Agent 调用)
├── references/               ← 按需查阅的详细文档
├── assets/                   ← 输入模板
└── ui/                       ← 本地网页控制台
```

---

## Quick start

### CLI

```bash
pip install -r requirements.txt

# 1. 生成输入模板（发给用户填）
python scripts/make_template.py --out input.xlsx --demo

# 2. 写配置
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
python scripts/pipeline.py --input input.xlsx --config cfg.json --outdir out/

# 4. 导出
python scripts/export_xlsx.py --plan out/plan.json --out 装载方案_作业方案.xlsx
python scripts/export_viz.py  --plan out/plan.json --out 装载方案_3D可视化.html --scenes out/scene_data.json

# 5. 生成报告型 HTML（结论 / 方案对比 / 逐柜明细 / 3D 线框 / 校验状态）
python scripts/build_report.py --plan out/plan.json --scenes out/scene_data.json \
    --out 装载优化方案.html
```

**双模式合并到一个 HTML**（模式下拉可切换对比）：

```bash
python scripts/export_viz.py --plan out/托盘.plan.json --scenes out/scene_data.json \
    --alt-plan out/无托盘.plan.json --out 装载方案_3D可视化.html
```

### Web console

```bash
python ui/server.py --port 8770
# 打开 http://127.0.0.1:8770
```

Windows 用户可直接双击 `启动装载优化UI.bat`（自动起服务 + 等端口就绪 + 开浏览器）。

### L3 / L4 校验

```bash
python scripts/verify_consistency.py --plan out/plan.json \
    --xlsx out/装载方案_作业方案.xlsx --scenes out/scene_data.json

node scripts/visual_check.mjs out/装载方案_3D可视化.html shots/
```

---

## Input format

Excel，**每个 working sheet = 一个批次**。表头在第 1~8 行内，解析器按关键词识别列。

| 列 | 必填 | 说明 |
|---|---|---|
| SKU编码 | ✅ | |
| 长(mm) / 宽(mm) / 高(mm) | ✅ | **单位必须 mm**，解析器不做换算 |
| 箱数 | ✅ | |
| 品名 | | |
| 每箱件数 | | |
| 单箱毛重(kg) | | 填了才能做承重/重心校验 |
| 边压强度ECT(kN/m) | | 缺省 6.0；用于凯利卡特公式推算堆码层数 |
| 限堆层数 | | 缺省由 ECT + 环境折减 + 安全系数 3 自动推算 |

---

## Compared with professional container-loading software

### Where this tool wins

- **交付的是「作业方案」，不是一张图** —— 逐托作业卡（托盘号、SKU 混装搭配、层序）直接进 Excel，现场照着打托；同时给出 3D 装载图给仓库和客户确认。
- **柜底裸地利用** —— 托盘铺满柜底后仍剩约 8% 的碎条空间（40HQ 约 2.29 m²）。多数商业软件会忽略这块地面，本工具在 `max_containers` 触发时把溢出柜溶解为**裸地散箱**，逐箱做 3D AABB 重叠检测后填进这些缝隙 —— **守住柜数上限，而不是凭空增密**。
- **两种装载方式可以并排比较** —— 托盘 vs 无托盘同一份输入跑两遍，柜数、装载率、作业复杂度三项一起看，选型决策有数据支撑。
- **约束语义写死在代码里，不靠用户理解** —— TiHi 带板层数、GB/T 16470 承载利用率 80%、托盘重心高 ≤ 托盘宽 2/3、Amazon FBA 限高限重、商务部托盘码盘要求，都是参数而非备注。
- **载荷驱动的承重判据** —— 不做"支撑率不达标就报错"这种粗糙判断，而是算累积载荷 vs 压溃能力，避免轻抛货被误判、也避免重货被漏判。
- **零依赖起步** —— 除 `openpyxl` 外全用标准库，单进程 HTTP 服务，`pip install` 后即可跑；不绑定任何商业求解器、不联网、不上传数据。
- **中文原生** —— 报告、作业卡、3D 图、校验报告全是中文，字段名与国内货代/仓库习惯对齐。

### Where professional tools win (be honest about this)

- **不保证全局最优**。求解器是启发式（MaxRects + FFD + 多轮随机重启），在极端货型组合下可能比最优解多 1 个柜。MILP / CP-SAT 类精确求解器能给出最优性证明，但在这个规模下不可用。
- **不建模装卸路径与顺序**。只保证几何与稳定性，不保证叉车在实际作业中不重复移位、不压坏已装的货。
- **不建模轴荷分配与偏载**。整柜重量有统计，但没有做车桥/轴位级的载荷分配（只做了整柜重心横向偏移的上限检查）。
- **不建模液体/散装/危品/温控分层**，不处理 LIFO 卸货顺序约束。
- **不做多目标权衡优化**（运费 vs 装载率 vs 破损率的帕累托前沿）。
- **冷链柜规格按 ISO 常见值**，实际箱况有差异，交付前建议与货代核对。
- **不提供商业级求解器支持与认证**。如果你需要最优性证明、审计追踪、或与 TMS/WMS 双向集成 —— 那些是专业软件的主场。

**一句话**：这个工具适合「先快速拿到一个可交付、可校验、够用的方案」；如果你要的是「最优性证明 + 全链路集成」，那应该上专业求解器。

---

## Who it's for

**很适合：**

- 🏭 **中小外贸工厂 / 货代的跟单与操作岗** —— 手上有 Excel 清单，需要今天就要一个装柜方案和作业卡，不想买年费几万的软件
- 📦 **跨境电商卖家 / 备货团队** —— 批量小单多批次发运，Amazon FBA 的托盘限高限重、TiHi 层数必须一次算对
- 🚚 **仓库与现场作业人员** —— 要的是「照着这张卡打托」，而不是一个漂亮的三维图
- 🧑‍💻 **技术团队自建 WMS/TMS 的第一版** —— 算法逻辑、约束语义、校验规则都读得懂、可直接改造；有 MIT 许可证可集成
- 🎓 **教学与研究** —— 装箱算法 + 承重判据 + 多层校验的完整可读实现，适合作为课程项目或算法实验基线

**不太适合：**

- 需要最优性证明或严格审计追踪的合规场景
- 需要与现有 TMS/WMS/ERP 深度集成的生产环境
- 超大规模（数千柜级）全局调度问题 —— 这里只解决单批装柜的 3D 装箱子问题

---

## Project layout

```
container-load-optimizer/
├── SKILL.md                    # Agent 使用说明（Claude Code / WorkBuddy 等 skill 格式）
├── scripts/
│   ├── catalog.py              # 规格库：柜型 / 托盘 / 行业常量（ISO 668 / GB/T 16470 / Amazon FBA）
│   ├── engine.py               # Constraints / SKU / MaxRects 极值矩形装箱
│   ├── stacker.py              # SupportMap 层间支撑对齐
│   ├── solver.py               # 求解器：托盘模式 + 无托盘模式（货垛柱）
│   ├── barearea.py             # 柜底裸地：自由区计算 + 散箱落位
│   ├── validator.py            # 四层校验器（L1 几何 / L2 稳定性承重）
│   ├── pipeline.py             # 主流程：输入 → 求解 → 校验 → plan.json
│   ├── export_xlsx.py          # 导出作业方案
│   ├── export_viz.py           # 导出 3D 装载图（单文件 HTML）
│   ├── build_report.py         # 导出报告型 HTML
│   ├── make_template.py        # 生成输入模板
│   └── verify_consistency.py   # L3 三方一致性校验
├── ui/
│   ├── server.py               # 本地网页控制台后端（纯标准库）
│   └── index.html              # 前端（原生 JS + Canvas 3D）
├── references/
│   ├── config_spec.md          # 配置文件字段全解 + 约束语义
│   ├── container_specs.md      # 柜型 / 托盘规格表（含数据来源）
│   └── modeling_basis.md       # 建模依据（Bortfeldt & Wäscher 约束分类、GB/T 16470、TiHi、凯利卡特公式）
└── assets/输入模板.xlsx
```

## References & standards

建模依据写在 `references/modeling_basis.md`，主要来源：

- **Bortfeldt & Wäscher** —— 集装箱装载问题 (CLP) 的约束分类
- **GB/T 16470-2008** 《托盘单元货载》—— 承载利用率 ≥80%、重心 ≤托盘宽 2/3、堆码与加固
- **ISO 668** —— 集装箱内部尺寸与门开口
- **商务部《托盘共用系统货物码盘及交接要求》** —— TiHi 带板分层
- **Amazon FBA 入库规范** —— 托盘限高 1.6m / 双层 2.7m / 限重 500kg / GMA Grade B+
- **凯利卡特公式** `BCT = ECT·Z·C·√(h·P)` —— 纸箱堆码层数上限

## License

MIT — 见 [LICENSE](LICENSE)。可用于商业项目。

## Feedback

遇到问题或想要新柜型 / 新约束 / 更好的求解策略，欢迎开 issue 或提 PR。
