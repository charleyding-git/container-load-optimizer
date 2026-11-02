---
name: container-load-optimizer
description: Container / cargo loading optimization. Use when the user needs a container loading plan, bin packing / palletizing optimization, a loading plan, container count calculation, utilization analysis, 3D loading visualization, or a loading job sheet (xlsx). Supports both palletized and no-pallet modes, 20/40 ft container types (standard / high cube / reefer / open top), US / EU / GB / UK / JP pallet standards, and constraints such as mixed-SKU layers, flat-stacked top layers, TiHi board-deck layers, receiving-area height limits and pallet weight limits. Ships four validation gates (geometry / stability & bearing / three-way consistency / visual review) plus a local web console. Trigger words: container loading, loading plan, packing plan, pallet loading, container optimization, bin packing, utilization, 装柜, 装箱, 码垛, 打托, 托盘, 集装箱, 货柜, 装载率, 装载方案, 3D 装载.
---

# Container Load Optimizer

Turn a cargo manifest into a **deliverable loading plan**: container count, utilization, per-pallet job cards, and a 3D loading view.

> 中文版见 [SKILL.md](SKILL.md). This file is the English version.

## When to use

- "How many containers does this order need?" / "what's the cheapest way to load it?"
- The warehouse needs per-pallet job cards (a forklift operator works from them)
- The customer or warehouse needs a 3D view to confirm the plan
- Comparing plans across container types, pallet standards or constraint sets

## Three non-negotiable rules

1. **A failed validation blocks delivery.** If L1/L2/L3 reports any error, stop and report it — do not "give an approximate plan first".
2. **The rendering and the plan must come from the same source.** Every box coordinate in the 3D view comes from the solver's `plan.json`; the renderer has zero layout logic. Never "place boxes by feel" in the rendering layer.
3. **Visual review is mandatory.** After rendering, screenshot with Playwright → inspect with `ViewImage` → fix and re-check if anything is wrong. Passing the numeric checks does **not** mean the picture is right.

## Quick start (CLI)

```bash
SKILL=~/.workbuddy/skills/container-load-optimizer

# 1. Generate an input template (for the user to fill in)
python $SKILL/scripts/make_template.py --out <input>.xlsx --demo

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
python $SKILL/scripts/pipeline.py --input <input>.xlsx --config cfg.json --outdir out/

# 4. Export
python $SKILL/scripts/export_xlsx.py --plan out/plan.json --out loading_plan.xlsx
python $SKILL/scripts/export_viz.py  --plan out/plan.json --out loading_3d.html --scenes out/scene_data.json

# 4b. Merge both loading modes into one HTML (Mode dropdown switches between them)
python $SKILL/scripts/export_viz.py --plan out/pallet.plan.json --scenes out/scene_data.json \
    --alt-plan out/nopallet.plan.json --out loading_3d.html
```

**Language**: add `--lang en` to `pipeline.py`, `export_viz.py` or `build_report.py` for English
CLI output and validation reports, or set `WB_LANG=en` once for the shell.

## Local web console (full input→output workflow, recommended for non-technical users)

**Location**: `~/.workbuddy/skills/container-load-optimizer/ui/` (`server.py` backend + `index.html` frontend, standard library only)

**Launch**: double-click `启动装载优化UI.bat` on Windows (starts the server, waits for the port, opens the browser).

```bash
python $SKILL/ui/server.py --port 8770
# open http://127.0.0.1:8770
```

The page *is* the whole workflow (inputs left, outputs right):

| Area | Content |
|---|---|
| ① Input data | Upload Excel (one sheet per batch) / download the blank template |
| ② Container & pallet | Container dropdown + pallet dropdown + custom pallet dimensions |
| ③ Loading mode | **Pallet / No-pallet** radio (pallet-specific constraints hide automatically) |
| ④ Constraints | One SKU per pallet, flat top layer, double stack, TiHi, height limit, weight limit, support policy, layer reordering, CoG |
| ⑤ Objective | Strict / auto-optimize + restarts + **container cap** |
| ⑤ | **Also solve the other loading mode** (default on) → both plans in one go |
| Right | Stat cards + **mode/batch dropdowns** + 3D preview (rotate / zoom / play load sequence / wireframe) + validation report + per-container table |
| Bottom | Export the deliverables as a zip (3D view + job card xlsx + plan.json + scene_data.json + the other mode's job card) |

A language switch lives in the header (Chinese / English); the choice is remembered and applies to the validation report, constraint list and exported artifacts.

> Windows note: the `timeout` binary in Git Bash shadows Windows `timeout`, so the launcher uses `ping` for delays.
> The service is a **foreground process** — closing the window stops it. For always-on use, register a startup task.

## Input format

Excel, **each working sheet = one batch** (shipped together). The parser finds the header row within the first 1–8 rows and matches columns by keyword.

**All quantities and dimensions are expressed in terms of the shipping carton (outer box).**

| Column | Unit | Required | Notes |
|---|---|---|---|
| SKU code | — | ✅ | |
| Description | — | | |
| Carton length / width / height (mm) | mm | ✅ | External dimensions of one carton — **must be millimetres**, the parser does no unit conversion |
| Cartons (outer) | boxes | ✅ | How many such cartons are needed |
| Pieces per carton | pcs/carton | | How many **individual products** fit in one carton. Not used in the packing computation — only printed on the job card |
| Gross weight per carton (kg) | kg/carton | | **Whole-carton gross weight** = all products inside + the carton packaging. Required for bearing and CoG checks |
| Edge crush test ECT (kN/m) | kN/m | | Default 6.0; used with the Kellicutt formula to derive stack limits |
| Max stack layers | layers | | Derived from ECT + environmental derating + safety factor 3 when blank |

**The three easily-confused fields**: `total individual products = Cartons × Pieces per carton`;
`total gross weight = Cartons × Gross weight per carton`.
If the user only has a per-piece net weight, it must be converted to a whole-carton weight
first — otherwise the container total weight is underestimated by dozens of times.

## Four validation layers

| Layer | Content | Severity |
|---|---|---|
| **L1 Geometry** | Valid dimensions, box inside pallet, box inside container, no pallet/box overlap, door opening height, utilization ≤ 100%, carton-count conservation | error |
| **L2 Stability & bearing** | Vertical support ratio, pallet gross weight, stack layer limit, lateral CoG offset, pallet footprint utilization (GB/T 16470 ≥ 80%) | error / warn |
| **L3 Three-way consistency** | plan.json ↔ xlsx ↔ 3D rendering — numbers and colour mapping | error |
| **L4 Visual review** | Playwright screenshot + human/agent inspection | performed by the caller |

L1/L2 run inside `pipeline.py`; L3 is checked after export with `verify_consistency.py`; L4 must be done by a human or an agent.

## Before delivering (process)

```
1. python pipeline.py ...        → confirm "Result: ... PASSED ✓"
2. python verify_consistency.py  → confirm L3 three-way consistency
3. Playwright screenshot all scenes → confirm no box escapes the container, no SKU misplacement
4. ViewImage each screenshot    → confirm the picture is right
5. Deliver the artifacts
```

If any step fails, fix the corresponding stage and **re-run the whole process**.

## Key design decisions (read before changing anything)

- **MaxRects `_split` must recompute the free rectangles wholesale** — incremental splitting produces overlapping free rectangles and roughly halves the packing count (measured: 3 vs 6).
- **The pallet floor layout uses a three-band Y interleave** (`engine.pallet_floor_layout`): a pure banded layout only fits 20 US pallets; the interleave is what reaches 21. One pallet position short = one container short.
- **A B-orientation pallet needs a 90° rigid transform**: `new_x=y, new_y=PAL_L−x−w, new_w=h, new_h=w`. Missing it puts tens of thousands of boxes outside the container.
- **Mixed layers need support alignment** (`stacker.py`): SKUs have different box sizes, and laying out each layer independently leaves the layer above hanging. `SupportMap` indexes by X-interval to stay fast.
- **The support ratio is not a risk metric — the criterion must be load-driven** (see `references/modeling_basis.en.md` §4.1):
  - A carton under brief overhang does **not** fail (measured bending margin 100–50000×), and its CoG does not leave the support area;
  - What actually fails is **local crushing**: `bearing demand (cumulative load above) > bearing capacity (BCT × supported area ratio)`;
  - **Weight therefore enters naturally** — at the same support ratio, an 8 kg light good has a 9.7× margin while a 90 kg heavy good crushes after 3 layers;
  - Hence the default `support_policy="relaxed_all"`: **a support ratio below target does not by itself trigger a repair**; it is covered externally (stretch wrap, strapping);
  - But **zero support (0%) is always repaired** — that is a physical certainty (even light goods fall) and no external means can fix it.
- **Layer reordering can remove all overhang, at the cost of one extra container**: moving poorly supported layers to the top (less load above → lower crush risk) took fully unsupported cases from 3 to 0, containers from 7 to 8.
- **Carton-count conservation is a hard gate**: after any repair or backfill, the loaded carton count must equal the requested count — otherwise delivery is blocked.
- **Bare-floor utilization is driven by `max_containers`** (`solver._cap_to_max`): when a batch exceeds the cap, the last container is dissolved and its cartons become bare-floor cartons (`kind="bare"`, no pallet, 100% support on the floor) spread across the free floor space of the first N containers. Placement uses **per-box 3D AABB overlap detection** (0.6 mm tolerance); `validator._check_bare_overlap` checks it per container and `export_viz` renders those cartons with an orange dashed outline.
  - **Key fact**: after 21 pallets fill a 40HQ, about 2.29 m² of floor remains, but it is narrow slivers between pallet edges; in a 7-container solution the pallets run from the door end (x=0) to the far wall, so the **door-end gap is 0** — there is no "bare floor at the door". The real bare floor is the internal gaps.
  - The value of the mechanism: absorbing "the overflow cartons that would have forced an 8th container" into the bare floor of the first 7, thereby **holding the container cap** — not inventing density out of nothing.
- **`max_containers` is a hard constraint, applied per batch**: `None` (no limit) by default in `Constraints`; setting 7 locks every batch to ≤ 7 containers.
- **No-pallet mode = column stacks** (`solver.solve_nopallet_mode`): cartons are stacked directly, without wooden pallets.
  - The usable stack height is the **door opening height** (every carton has to pass through the door), not the internal height.
  - Two phases: Phase 1 places floor columns (best height-fill orientation, MaxRects on the floor, `int(Heff//vz)` cartons per column) → Phase 2 back-fills the column tops (footprint ≤ the column footprint) with smaller orientations.
  - Columns are modelled as `containers[].pallets[]` entries in `plan.json` (which reuses the L1/L2 validation unchanged), but `npallets` is 0 and the 3D view draws **no pallet base**.
  - Pallet-specific criteria are skipped in this mode: pallet-height floor, pallet footprint utilization, pallet weight limit, and pallet CoG height (otherwise every column would be falsely flagged).

## References

- `references/config_spec.en.md` — every config field and constraint semantic (English); `config_spec.md` — 中文版
- `references/modeling_basis.en.md` — modeling basis (Bortfeldt & Wäscher constraint taxonomy, GB/T 16470, TiHi, Amazon FBA, Kellicutt formula); `modeling_basis.md` — 中文版
- `references/container_specs.en.md` — container and pallet spec tables (English); `container_specs.md` — 中文版
- `README.en.md` — full English documentation including a comparison with professional loading software

## Known limitations

- The solver is **heuristic** (layer construction + MaxRects + FFD + multi-start random restarts) and does not guarantee a global optimum; exact methods (MILP / CP-SAT) are not usable at this scale.
- No-pallet mode has weaker support and CoG validation — it relies mainly on L1 geometry.
- Cold-chain dimensions follow common ISO values; actual box condition varies, so confirm with the forwarder before delivery.
- Not modelled: liquids / bulk / dangerous goods / temperature zoning / unloading order (LIFO) / axle-load distribution.
- Unloading order and multi-stop delivery are not optimized (`load_order` is reserved but unimplemented).
