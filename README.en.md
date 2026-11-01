# Container Load Optimizer

**Container / cargo loading optimizer** — turn a cargo manifest into a deliverable loading plan: container count, utilization, per-pallet job cards, and a 3D loading view.

One command produces the plan; a local web console is available for non-technical users.

```
Excel manifest  →  solve + validate  →  job card xlsx  +  3D loading view  +  JSON details
```

> 中文用户请看 [README.md](README.md)。This file is the English version.

---

## What it does

| | |
|---|---|
| **Two loading modes** | `pallet` (palletized: mixed layers, flat-stacked top) / `nopallet` (cartons stacked directly into columns) |
| **Container types** | 20GP / 20HC / 40GP / 40HQ / 45HC / 20RF / 40RF / 40RHQ / 20OT / 40OT |
| **Pallet standards** | US48 · US48 GMA · EU1200 · EU1200×1000 · GB1200 · UK1200 · JP1100 · custom |
| **Batch handling** | One Excel sheet = one batch (shipped together); container numbers are continuous across batches |
| **Deliverables** | ① Job card xlsx (per-pallet work instructions) ② Interactive 3D loading view (single-file HTML) ③ plan.json / scene_data.json |
| **Web console** | `ui/server.py` — upload Excel, set options, one-click solve, in-page 3D preview, export a zip |
| **Languages** | Chinese and English throughout: CLI output, validation report, web console, 3D view, job cards |

### Constraints it understands

| Constraint | Type | Notes |
|---|---|---|
| `max_containers` | **hard** | Hard cap on container count per batch. The surplus container is dissolved and its cartons are moved into the bare floor space of the first N containers |
| `mono_sku` | hard | One SKU per pallet (use when the consignee requires it) |
| `allow_mix_pallet` | soft | Allow several SKUs per pallet — complementary SKUs fill the gaps left by indivisible layer heights, raising fill rate and cutting container count |
| `allow_top` | soft | Flat-stacked boxes on top of the pallet |
| `allow_double_pallet` | hard | A pallet on top of a pallet |
| `tihi_layers` | hard | TiHi board-deck layers 1–4 (MOFCOM pallet-pooling code) |
| `max_pallet_stack_h` | hard | Max pallet load height in the receiving area |
| `max_pallet_weight` | hard | Max pallet gross weight (e.g. Amazon FBA 500 kg) |
| `max_stack_layers_per_sku` | hard | Max stack layers for a given box type |
| `min_support_ratio` / `support_policy` | — | Minimum vertical support ratio / support policy (relaxed / balanced / strict) |
| `max_cog_offset` | soft | Max lateral centre-of-gravity offset (fraction of container width) |
| `reorder_layers` | soft | Layer reordering — moves poorly supported layers higher (less load above → lower crush risk) |
| `optimize` | — | `containers` (fewest containers) / `strict` (exactly as configured) |

---

## Four validation layers — nothing ships unverified

A failed check means delivery is blocked. This is a rule, not a suggestion.

| Layer | What it checks | Severity |
|---|---|---|
| **L1 Geometry** | Valid dimensions, box inside pallet, box inside container, no pallet/box overlap, door opening height, utilization ≤ 100%, **carton count conservation** | error |
| **L2 Stability & bearing** | Vertical support ratio, pallet gross weight, stack layer limit, lateral CoG offset, pallet footprint utilization (GB/T 16470 ≥ 80%) | error / warn |
| **L3 Three-way consistency** | plan.json ↔ xlsx ↔ 3D rendering — numbers and colour mapping | error |
| **L4 Visual review** | Playwright screenshot + human/agent inspection | performed by the caller |

**Carton-count conservation is a hard gate**: after any repair or backfill, the number of loaded cartons must equal the requested number — otherwise delivery is blocked.

### The bearing criterion is load-driven, not a support-ratio cut-off

A carton that is briefly unsupported does **not** fail (measured bending margin is 100–50000×), and its centre of gravity stays inside the support area. What actually breaks is **local crushing**:

```
bearing demand (cumulative load above)  >  bearing capacity (BCT × supported area ratio)
BCT = ECT × Z × C × √(h·P)      # Kellicutt formula
```

Weight enters the criterion naturally — at the same support ratio, an 8 kg light good has a 9.7× margin while a 90 kg heavy good crushes after 3 layers. Hence the default `support_policy="relaxed_all"`: **a support ratio below target is only flagged** (stretch wrap, cross strapping, interlayer boards take care of it), but **a box with zero support is always repaired** — that is a physical certainty regardless of weight.

---

## Quick start

### CLI

```bash
pip install -r requirements.txt

# 1. Generate an input template
python scripts/make_template.py --out input.xlsx --demo

# 2. Write the config
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

# 3. Solve + validate (exit code 1 when validation fails)
python scripts/pipeline.py --input input.xlsx --config cfg.json --outdir out/

# 4. Export
python scripts/export_xlsx.py --plan out/plan.json --out loading_plan.xlsx
python scripts/export_viz.py  --plan out/plan.json --out loading_3d.html --scenes out/scene_data.json

# 5. Report-style HTML (findings / plan comparison / per-container detail / 3D / validation)
python scripts/build_report.py --plan out/plan.json --scenes out/scene_data.json \
    --out loading_report.html
```

**Output in English** — either per run or permanently:

```bash
python scripts/pipeline.py ... --lang en      # per run
export WB_LANG=en                             # for the shell / user profile
```

The same `--lang` flag exists on `export_viz.py` and `build_report.py`.

**Merge both loading modes into one HTML** (switch with the Mode dropdown):

```bash
python scripts/export_viz.py --plan out/pallet.plan.json --scenes out/scene_data.json \
    --alt-plan out/nopallet.plan.json --out loading_3d.html
```

### Web console

```bash
python ui/server.py --port 8770
# open http://127.0.0.1:8770
```

The console has a language switch in the header (Chinese / English); the choice is remembered in the browser and applies to the validation report, constraint list, and exported artifacts.

Windows users can double-click `启动装载优化UI.bat` (starts the server, waits for the port, opens the browser).

### L3 / L4 checks

```bash
python scripts/verify_consistency.py --plan out/plan.json \
    --xlsx out/loading_plan.xlsx --scenes out/scene_data.json

node scripts/visual_check.mjs out/loading_3d.html shots/
```

---

## Input format

Excel, **one working sheet = one batch**. The parser recognises header rows within the first 1–8 rows by keyword.

| Column | Required | Notes |
|---|---|---|
| SKU code | ✅ | |
| Length / Width / Height (mm) | ✅ | **Must be millimetres** — the parser does no unit conversion |
| Cartons | ✅ | |
| Description | | |
| Pieces per carton | | |
| Gross weight per carton (kg) | | Needed for bearing and CoG checks |
| Edge crush test (kN/m) | | Default 6.0; used with the Kellicutt formula to derive stack limits |
| Max stack layers | | Derived automatically from ECT + environmental derating + safety factor 3 when blank |

---

## Compared with professional container-loading software

### Where this tool wins

- **It delivers a work instruction, not a picture** — per-pallet job cards (pallet number, SKU mix, layer order) go straight into Excel for the warehouse; the 3D view is there to confirm with the warehouse and the customer.
- **Bare-floor utilization** — after pallets fill the floor, roughly 8% of the floor area is left in narrow strips (about 2.29 m² in a 40HQ). Most commercial software ignores that floor. This tool, when `max_containers` triggers, dissolves the surplus container and fills those strips with loose cartons, each placed with 3D AABB overlap detection — **holding the container cap rather than pretending to add density**.
- **Both loading modes side by side** — run pallet and no-pallet on the same manifest, then compare container count, utilization and operational complexity together, so the mode decision is backed by numbers.
- **Constraint semantics live in code, not in a footnote** — TiHi deck layers, GB/T 16470 footprint utilization 80%, CoG ≤ 2/3 pallet width, Amazon FBA height/weight limits, MOFCOM pallet-pooling rules are all parameters.
- **A load-driven bearing criterion** — instead of the crude "support ratio below target → error", it computes cumulative load vs crushing capacity, so light goods are not falsely flagged and heavy goods are not missed.
- **Near-zero setup** — only `openpyxl` beyond the standard library; a single-process HTTP server; `pip install` and run. No commercial solver, no network calls, no data upload.
- **Bilingual and Chinese-native** — reports, job cards, 3D view and validation report all ship in Chinese and English, with field names matching the vocabulary Chinese freight forwarders and warehouses actually use.

### Where professional tools win (be honest about this)

- **No global optimality guarantee.** The solver is heuristic (MaxRects + FFD + multi-start random restarts) and can end up one container above the optimum on adversarial SKU mixes. An exact solver (MILP / CP-SAT) can prove optimality but is not usable at this scale.
- **No loading-path or sequencing model.** Only geometry and stability are guaranteed — not that a forklift can reach every pallet without moving others, or that already-loaded goods stay undamaged.
- **No axle-load distribution.** Container weight is totalled, but there is no per-axle load balancing (only a whole-container lateral CoG check).
- **No modelling of liquids, bulk, dangerous goods or temperature zoning**, and no LIFO unloading-order constraints.
- **No multi-objective trade-off surface** (freight cost vs utilization vs damage rate).
- **Cold-chain dimensions follow common ISO values** — actual box condition varies, so confirm with your forwarder before delivery.
- **No commercial-solver support or certification.** If you need optimality proofs, audit trails, or two-way integration with a TMS/WMS, that is what professional software is for.

**In one line**: this tool suits "get a deliverable, validated, good-enough plan right now". If you need "an optimality proof plus full-chain integration", buy the professional solver.

---

## Who it's for

**A good fit:**

- 🏭 **Small and mid-sized exporters and freight forwarders** — an Excel manifest in hand, a plan and job cards needed today, and no appetite for an annual licence
- 📦 **Cross-border e-commerce sellers and replenishment teams** — many small shipments across many batches, where Amazon FBA pallet height/weight limits and TiHi layer counts must be right first time
- 🚚 **Warehouse and floor operations staff** — who want "build the pallet from this card", not a pretty 3D render
- 🧑‍💻 **Teams building their first WMS/TMS** — the algorithms, constraint semantics and validation rules are readable and forkable; MIT licensed
- 🎓 **Teaching and research** — a complete, legible implementation of packing heuristics, bearing criteria and multi-layer validation; useful as a course project or an experimental baseline

**A poor fit:**

- Compliance scenarios demanding optimality proofs or strict audit trails
- Production environments that need deep integration with an existing TMS/WMS/ERP
- Fleet-scale (thousands of containers) global scheduling — this solves the 3D packing sub-problem for a single batch

---

## Project layout

```
container-load-optimizer/
├── SKILL.md                    # Agent usage notes (skill format for Claude Code / WorkBuddy, etc.)
├── README.md                   # 中文说明 (Chinese) — this file's counterpart
├── README.en.md                # English (this file)
├── scripts/
│   ├── catalog.py              # Specs: containers / pallets / industry constants (ISO 668 / GB/T 16470 / Amazon FBA)
│   ├── engine.py               # Constraints / SKU / MaxRects rectangle packing
│   ├── i18n.py                 # Message catalogue (zh / en) for validation messages
│   ├── stacker.py              # SupportMap — inter-layer support alignment
│   ├── solver.py               # Solver: pallet mode + no-pallet mode (column stacks)
│   ├── barearea.py             # Bare floor: free-rect computation and loose-carton placement
│   ├── validator.py            # Validation layers L1 geometry / L2 stability & bearing
│   ├── pipeline.py             # Main flow: input → solve → validate → plan.json
│   ├── export_xlsx.py          # Export job cards
│   ├── export_viz.py           # Export the 3D loading view (single-file HTML)
│   ├── build_report.py         # Export the report-style HTML
│   ├── make_template.py        # Generate the input template
│   ├── verify_consistency.py   # L3 three-way consistency check
│   └── scan_for_secrets.py     # Pre-release scan for leftover business data / credentials
├── ui/
│   ├── server.py               # Local web console backend (standard library only)
│   └── index.html              # Frontend (vanilla JS + Canvas 3D)
├── references/
│   ├── config_spec.md          # Config reference (Chinese)
│   ├── config_spec.en.md       # Config reference (English)
│   ├── container_specs.md      # Container / pallet spec tables (Chinese)
│   ├── container_specs.en.md   # Container / pallet spec tables (English)
│   ├── modeling_basis.md       # Modeling basis (Chinese)
│   └── modeling_basis.en.md    # Modeling basis (English)
└── assets/输入模板.xlsx
```

## References & standards

The modeling basis is documented in `references/modeling_basis.en.md`, drawn from:

- **Bortfeldt & Wäscher** — the constraint taxonomy for the container loading problem (CLP)
- **GB/T 16470-2008** *Pallet unit loads* — footprint utilization ≥ 80%, CoG ≤ 2/3 pallet width, stacking and securing
- **ISO 668** — container internal dimensions and door opening
- **MOFCOM pallet-pooling code** — TiHi board-deck layering
- **Amazon FBA inbound requirements** — pallet height 1.6 m / double stack 2.7 m / 500 kg limit / GMA Grade B+
- **Kellicutt formula** `BCT = ECT·Z·C·√(h·P)` — carton stack height limit

## License

MIT — see [LICENSE](LICENSE). Usable in commercial projects.

## Feedback

Issues and pull requests are welcome — especially for new container types, new constraints, or better solver strategies.
