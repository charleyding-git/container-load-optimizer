# Configuration reference (config.json)

> 中文版见 [config_spec.md](config_spec.md). This file is the English version.

## Top-level structure

```json
{
  "container": "40HQ",
  "pallet": "US48",
  "custom_pallet": null,
  "constraints": { ... }
}
```

| Field | Type | Notes |
|---|---|---|
| `container` | string | Container code — see [container_specs.en.md](container_specs.en.md) |
| `pallet` | string | Pallet code; required together with `custom_pallet` when set to `CUSTOM` |
| `custom_pallet` | object\|null | `{L, W, H}` in mm |
| `constraints` | object | See below |

## All `constraints` fields

```json
{
  "mode": "pallet",
  "mono_sku": false,
  "allow_top": true,
  "allow_double_pallet": false,
  "tihi_layers": null,
  "max_pallet_stack_h": null,
  "max_pallet_weight": null,
  "min_support_ratio": null,
  "repair_support": true,
  "max_cog_offset": 0.15,
  "stack_pattern": "auto",
  "load_order": "auto",
  "optimize": "containers",
  "seed": 0,
  "restarts": 12,
  "max_containers": null
}
```

| Field | Default | Hard/Soft | Notes |
|---|---|---|---|
| `mode` | `"pallet"` | hard | `pallet` (palletized) / `nopallet` (cartons stacked directly into columns). In `nopallet` mode the pallet-specific constraints (`mono_sku` / `allow_top` / `allow_double_pallet` / `tihi_layers` / `max_pallet_stack_h` / `max_pallet_weight`) are automatically inactive; the usable stack height is the **door opening height** (every carton has to pass through the door) |
| `mono_sku` | `true` | **hard** | One SKU per pallet. `false` allows mixed SKUs per layer — higher pallet fill rate and fewer containers, but Mixed SKU labels become required |
| `allow_top` | `true` | soft | Flat-stacked boxes on top of the pallet, using the leftover height. May be relaxed in auto-optimize mode |
| `allow_double_pallet` | `false` | **hard** | Double-stacked pallets (a pallet on top of a pallet). Affects forklift handling and receiving-area height |
| `tihi_layers` | `null` | **hard** | TiHi board-deck layers 1–4, giving load-height limits of 1800 / 1200 / 800 / 600 mm (pallet included) |
| `max_pallet_stack_h` | `null` | **hard** | Max pallet load height in the receiving area, mm (pallet included). Goods above the rack or receiving height are rejected on arrival |
| `max_pallet_weight` | `null` | **hard** | Max pallet gross weight, kg. Amazon FBA uses 500 |
| `support_policy` | `"relaxed_all"` | — | Support policy: `relaxed_all` / `balanced` / `strict` — see below. **Default: relaxed** |
| `reorder_layers` | `false` | soft | Layer reordering: poorly supported layers are moved higher on the pallet. Removes fully unsupported boxes, at the cost of one extra container |
| `min_support_ratio` | `null` | **hard** | Minimum vertical support ratio during solving. Normally left null and controlled by `support_policy` instead |
| `repair_support` | `true` | soft | Targeted repair of support risks after solving (layer order preserved, only the 2D layout is rearranged) |
| `max_cog_offset` | `0.15` | soft | Max lateral centre-of-gravity offset for the whole container (fraction of container width). Exceeding it raises a warning |
| `stack_pattern` | `"auto"` | soft | `column` (columnar) / `interlock` (interlocked) / `auto` |
| `load_order` | `"auto"` | soft | `heavy_first` / `height_desc` / `sku_group` / `auto` (currently a sort hint only, not a path optimization) |
| `optimize` | `"containers"` | — | `containers` (fewest containers) / `strict` (exactly as configured) |
| `seed` | `0` | — | Random seed. Changing it yields a different — but equally good — plan |
| `restarts` | `12` | — | Number of solver restarts. More is steadier but slower: 12 rounds ≈ 3 s, 24 rounds ≈ 6 s |
| `max_containers` | `null` | **hard** | Hard cap on container count (**per batch**). Set 7 and, if a batch solves to more than 7 containers, the last container is dissolved and its cartons are redistributed as "bare-floor cartons" into the free floor space of the first 7 containers (never adds a container, carton count is conserved). `null` = no limit |

## Support policy (`support_policy`) — three levels

The support ratio is a **geometric** metric (share of supported area). It is **not a direct measure of risk** — derived from measurements:

- A carton that is briefly unsupported does **not** fail: the measured bending margin is 100–50000×, and its centre of gravity stays within the support area.
- Failure has two independent mechanisms:
  1. **Broken load path** (support ratio = 0%): the load has nowhere to go → the whole box falls, **regardless of weight**.
  2. **Local crushing** (partial support): crushing occurs when bearing demand exceeds bearing capacity
     - bearing demand = cumulative load from above (propagated top-down, shared by overlap area)
     - bearing capacity = BCT (box compression strength) × supported area ratio
     - **Light goods are naturally low-risk**: at 40% support, an 8 kg box still has a 9.7× margin;
       at 40% support a 90 kg box drops to 1.4× and crushes after 3 layers.

Hence the three policies:

| Policy | During solving | During validation | Measured (7 SKUs / 4935 cartons / 40HQ) |
|---|---|---|---|
| `relaxed_all` | Repairs only fully unsupported boxes | Below-target support ratio → **info** (external securing covers it) | **7 containers / 66.0%** |
| `balanced` | Repairs layers below 55% support | Same | 8 containers / 57.7% |
| `strict` | Support ratio is a hard constraint, never relaxed | Same | 8 containers / 57.7% (≈40× slower) |

**`relaxed_all` is the default** — the reasoning: the support ratio should not by itself trigger a hard repair; external securing (stretch wrap, strapping) is the normal remedy.
But **zero support is always repaired** — that is a physical certainty (even light goods fall) and no amount of wrap fixes it.

### Under-filled layers are moved to the top (automatic, no switch)

Built in as a **fixed layer-construction rule**: when a layer holds only a few cartons because the remaining volume does not fill it and the pallet face is not covered, the layer is moved to the top of the pallet — because in the middle, the boxes above it would be partly unsupported (and would fall). At the top it is harmless: nothing rests on it, and the layer below is fully covered → support ratio 100%.

The test uses **footprint coverage** (≥85% counts as covered), **not `n < cpl`** — the latter misjudges: 4 boxes of 428×423 on a 1219.2×1016 pallet cover only 856×846, i.e. not a full layer.

Measured effect: fully unsupported pallets drop from 3 to 1, support-ratio info messages drop from 47 to 12, and **the container count does not change (7 containers)**.

### Layer reordering (`reorder_layers`)

Moves poorly supported layers as high up the pallet as possible. Physical basis: boxes near the top carry less cumulative load from above → lower crushing risk; and even if the top layer has overhangs, nothing rests on it to pull the layers below down.

Measured effect: **eliminates all fully unsupported cases (3 → 0)**, at the cost of **one extra container** (7 → 8).
Reordering disturbs the otherwise tidy layer order → fewer cartons per pallet → more pallets → hence it is only enabled for pallets that actually have a support problem.

### Weight tiers (automatic)

| Density kg/m³ | Tier | Support risk |
|---|---|---|
| < 100 | Light goods | Low — self weight cannot crush the layer below; stretch wrap / strapping is enough |
| 100–300 | Normal goods | Medium — bearing margin is computed per layer; insufficient support should be flagged |
| 300–600 | Heavy-ish goods | High — crushes the layer below easily; must be flagged explicitly and layer order adjusted |
| ≥ 600 | Dense goods | Very high — control support ratio and layer count strictly; heavy boxes at the bottom, bearing margin computed per layer |

**When per-box weight is missing**, it is estimated from volumetric density and the report notes that the conclusion is indicative only.

### Validation severity levels

| Code | Level | Meaning |
|---|---|---|
| `LOAD_PATH_BROKEN` | warn | Fully unsupported boxes exist (they would fall) — layer rearranged automatically, human review still needed |
| `BEARING_OVERLOAD` | error | Bearing demand > bearing capacity (it will crush) |
| `BEARING_MARGIN_LOW` | warn | Bearing margin < 60% |
| `SUPPORT_RATIO_LOW_INFO` | info | Support ratio below 75% but bearing margin is sufficient — external securing covers it |
| `SUPPORT_RATIO_LOW` | warn | Support ratio below 75% **and** bearing margin insufficient |
| `HEAVY_CARGO_NOTICE` | info | Heavy cargo present — explicit notice (by density tier) |
| `WEIGHT_MISSING` | warn | Per-box weight not provided — bearing conclusions are indicative only |

## Hard vs soft constraints

**Hard constraints**: the solver never returns a solution that violates them. If one makes the problem infeasible, only the **support ratio** is relaxed and downgraded to a warning; any other hard constraint simply makes the instance infeasible.

**Soft constraints**: may be relaxed in auto-optimize mode, but are listed explicitly in the plan notes and the validation report.

**What `optimize` does**:
- `containers` (auto-optimize): fewest containers wins; soft constraints may be relaxed
- `strict` (exactly as configured): hard constraints are never violated, even at the cost of extra containers

## Layer height vs door height

**The door opening height is the hard limit, not the internal height.**
- 40HQ: internal height 2698 mm, door opening 2585 mm → usable stack height is 2585 mm
- 40GP: internal height 2393 mm, door opening 2280 mm
- Stack height = pallet height (145) + cargo height, and must stay ≤ the door opening height

A stack taller than the door opening **cannot physically be pushed into the container**. This is not a theoretical concern.

## Bare-floor utilization

**Background**: once a 40HQ is filled with 21 US48 pallets (1219.2×1016 mm), 26.01 m² of the 28.30 m² floor is taken, leaving about **2.29 m² (8.1%)** — but that is **narrow slivers** cut by the pallet edges, not open floor.
In a 7-container solution the pallets already run from the door end (x=0) to the far wall (x≈11988 of a 12032 mm box), so the **door-end gap is 0** — there is no "bare floor at the door". The real bare floor is the **internal gaps between pallets**.

**Mechanism** (triggered by `max_containers`):

1. Solving produces N containers; if a batch yields N > `max_containers`, the **last container is dissolved** (its cartons are taken off the pallets).
2. The dissolved cartons become **bare-floor cartons** (`kind="bare"`). `floor_free_rects` computes the free floor area of the first N containers, and each carton is placed one by one with **3D AABB overlap detection** (0.6 mm tolerance), distributed evenly with the shortfall carried forward so no carton is dropped.
3. Bare-floor cartons **rest directly on the container floor** (no pallet) → the bottom layer has a 100% support ratio; the top is still bound by the door opening height.
4. `validator._check_bare_overlap` verifies per container that bare-floor cartons do not overlap; `export_viz` renders them with an **orange dashed outline** and adds them to the legend.

**Conservation**: dissolved cartons = cartons placed. Any residue surfaces through the carton-count conservation gate — meaning `max_containers` was set too low and the batch genuinely does not fit.

**Note**: bare floor only fills internal gaps. With a fixed order quantity (e.g. batch 1 has exactly 4935 cartons) there is no surplus goods to fill all 2.29 m² per container — that is determined by the order size, **not an oversight by the algorithm**. The point of `max_containers` is to absorb "the overflow cartons that would have forced an 8th container" into the bare floor of the first 7, thereby **holding the container cap**.

## Validation gates

| Level | Meaning | Consequence |
|---|---|---|
| `error` | Hard error | **Delivery blocked**, exit code 1 |
| `warn` | Must be documented | Deliverable, but the plan notes must list it |

L1 geometry errors: box outside pallet, box outside container, boxes overlap, pallets overlap, above door height, utilization > 100%, invalid dimensions.
L2 errors: pallet over weight, stack layers over limit, support ratio < 50% (collapse risk).
L2 warnings: support ratio < 75%, pallet footprint utilization < 80%, CoG too high, whole-container CoG offset > 15%.
L3 errors: plan ↔ xlsx ↔ 3D numbers disagree, colour mapping disagrees.
