# Modeling basis and industry constraints

> 中文版见 [modeling_basis.md](modeling_basis.md). This file is the English version.

Every constraint in this tool has a source. Nothing here was invented.

## 1. Constraint taxonomy for the container loading problem

**Bortfeldt, A. & Wäscher, G. (2013). "Constraints in container loading – A state-of-the-art review." *EJOR* 229(1), 3–22.**

The canonical classification source for CLP constraints: 10 constraint classes in 5 groups. The L1/L2 validation layers map onto it directly:

| Class | Content | Implementation here |
|---|---|---|
| Container-related · weight limit | Total weight within payload | `CATN.payload` |
| Container-related · weight distribution | CoG offset, axle loads | L2 `COG_OFFSET` (lateral offset ≤ 15%) |
| Cargo-related · loading priority | Load-before-unload ordering | `load_order` parameter (no path optimization yet) |
| Cargo-related · orientation | Restricted box orientation | All 6 orthogonal orientations enumerated |
| Cargo-related · stacking | Compression strength limit | Kellicutt formula (below) |
| Cargo-related · complete shipment | One order must travel together | Each batch is solved independently; no cross-batch mixing |
| Loading-related · stability | Vertical support, horizontal stability | L2 support-ratio validation (below) |
| Loading-related · complexity | Operational feasibility | Layer construction (robot-friendly) |

**Key conclusion from that review**: two common bearing approaches (box classification by strength, and maximum pressure) both misrepresent real loading — the pressure at a box's top edge can far exceed that at the centre. This tool therefore **does not treat "at most N layers" as a bearing model**; it derives the layer limit from the box's own compression strength.

## 2. Layer-height rounding loss

**Tresca et al. (2022). "Automating Bin Packing." *IEEE TASE*.**

That work treats *height homogeneity* and *overhang* as **violable soft constraints**, priced by a cost function rather than hard rounding.

How this tool implements it:
- Rounding loss = `Hlim − Σ(layer heights)`, shown explicitly in the plan notes
- The flat-stacked top-up layer exists precisely to fill the remainder of the last, indivisible layer
- **No single large-M rounding term** (it causes numerical degeneracy and ill-conditioning)

## 3. Bearing and stack height limits

**Kellicutt formula**:
```
BCT = ECT × Z × C × √(h × P)
  ECT  edge crush test, kN/m
  Z    box perimeter, cm
  C    constant 0.0207 (back-fitted from ASTM D642 and industry cases)
  h    box height, cm
  P    equivalent board thickness, cm = h × 0.5
```
Safe stacking layers:
```
N = BCT × environmental derating ÷ (box weight × safety factor)
environmental derating = 0.70 (humidity > 65%) × 0.85 (30-day ocean aging) = 0.595
safety factor = 3.0
```

**Calibration check** (measured in this tool):
| Box type | ECT | BCT computed | Industry measured | Result |
|---|---|---|---|---|
| 40×30×30 RSC | 5.44 kN/m (ECT-32) | 334 kg | 3.9–4.3 kN | conservative ✓ |
| 60×40×40 BC | 7.5 kN/m (ECT-44) | 878 kg | 11–13 layers @ 15 kg | matches ✓ |

Derating basis: cartons lose 30% strength above 65% humidity; ocean freight adds 15–20% loss from 30-day creep (GB/T 4857.4 stacking test, ISTA 3A/3E).

## 4. Vertical stability

**Bischoff & Ratcliff (1995); Junqueira, Morabito & Yamashita (2012, *COR* 39(1):74–85)**

Three levels of modelling:
1. **Layer count limit** (`count_j ≤ N_max`) — the most distorting; not used here for bearing
2. **Aggregated pressure constraints** (Junqueira 2012) — the mainstream approach
3. **Differentiated edge/centre bearing** — high fidelity, needs extra geometric variables, unsuitable for inner-loop solving

**Known boundary** (stated in the literature): vertical stability ≠ horizontal stability. A column stack can be vertically stable yet horizontally unstable. This tool **does not model dynamic stability** (no polynomial criteria) — it relies on "leave no large voids" plus external securing (stretch wrap / strapping).

### 4.1 The support ratio is not a risk metric (derived from measurements)

The common practice is to treat "vertical support ratio = supported area / footprint" directly as a risk threshold (e.g. 75%). **Measurement shows that is physically unfounded**:

**Derivation 1: brief overhang does not make a carton fail**
Taking a 430×390×423 mm box, ECT 6.0 kN/m, 4 mm board:

| Overhang length | Load from above | Bending safety margin |
|---|---|---|
| 50 mm | 15 kg | 53336× |
| 100 mm | 900 kg | 444× |
| 200 mm | 400 kg | 500× |
| 430 mm (fully unsupported) | 900 kg | 103× |

So **overhang length itself is not a constraint** — a carton under short-term bending does not fail. Its centre of gravity also stays inside the support area, so it does not tip.

**Derivation 2: real failure is local crushing**
The load above passes into the box below through the support points, so the effective bearing capacity is BCT × supported area ratio:

```
bearing demand  = cumulative load from above on that box (propagated top-down by overlap area)
bearing capacity = BCT (Kellicutt) × supported area ratio
```

**Derivation 3: weight enters the criterion naturally** (same 40% support ratio, different box weights):

| Box weight | Load from 3 layers above (demand) | Capacity (BCT 609 × 40%) | Margin | Verdict |
|---|---|---|---|---|
| 5 kg | 15 kg | 244 kg | 16.2× | safe |
| 12 kg | 36 kg | 244 kg | 6.8× | safe |
| 25 kg | 75 kg | 244 kg | 3.2× | safe |
| 50 kg | 150 kg | 244 kg | 1.6× | safe |
| 90 kg | 270 kg | 244 kg | 0.9× | **crushes** |

Conclusion: **at the same support ratio, light goods barely matter while heavy goods must be flagged explicitly** — which is exactly why this tool uses a load-driven criterion.

**Derivation 4: layer position matters only indirectly**
The value of a layer position is not "overhang length" but:
- (a) putting heavy boxes low → less cumulative load above → lower crushing risk
- (b) having the upper box's footprint land on the **strong zone** of the box below (the skeleton where side walls meet the base) rather than the weak centre
- (c) within a layer, placing heavy boxes where the bearing area is largest

All three are achievable by **reordering layers** → the `reorder_layers` parameter.

**Derivation 5: zero support is an independent mechanism and must be listed separately**
At 0% support the load path is broken and the box simply drops. This is independent of weight (light goods fall too), and **stretch wrap or strapping cannot save it** (there is no support face to bind to). Hence it is listed separately as `LOAD_PATH_BROKEN` and always repaired.

### 4.2 Summary of criteria

| Mechanism | Criterion | Level | Can external securing cover it? |
|---|---|---|---|
| Broken load path | support ratio = 0% | warn (layer rearranged automatically) | **No** — must be fixed |
| Local crushing | bearing demand > bearing capacity | **error** | No |
| Insufficient bearing margin | margin < 60% | warn | partly |
| Geometric metric below target | support ratio < 75% with ample bearing margin | info | **Yes** — stretch wrap / strapping |

**Where the 75% support threshold comes from**: GB/T 16470 and the public 3DPACK.ING baseline both use it.
This tool demotes it to an **info-level note** (unless bearing margin is also insufficient) and no longer treats it as a delivery blocker.

### 4.3 Cargo density tiers

| Density kg/m³ | Tier | Support risk |
|---|---|---|
| < 100 | Light goods | low |
| 100–300 | Normal goods | medium |
| 300–600 | Heavy-ish goods | high |
| ≥ 600 | Dense goods | very high |

Basis: bulky goods lack the self-weight to crush the layer below; dense goods can crush even at a slightly low support ratio.
When per-box weight is missing from the input, this tool falls back to a density estimate and notes that the conclusion is less reliable.

## 5. Chinese national standard for pallet unit loads

**GB/T 16470-2008 "Pallet unit loads"**

| Clause | Requirement | Implementation here |
|---|---|---|
| 4.1.5 | CoG height ≤ 2/3 of pallet width | L2 `COG_TOO_HIGH` |
| 4.1.7 | Pallet footprint utilization ≥ 80% | L2 `PALLET_AREA_UTIL_LOW` |
| 4.2.2 | Stacking pattern: columnar / interlocked | `stack_pattern` parameter |
| 4.3 | Securing: strapping / wrapping (stretch, shrink) | Recommendations in the plan notes |
| 4.1.4 | Pallet dimensions comply with GB/T 2934 | Pallet spec library |

## 6. TiHi board-deck layering standard

**MOFCOM "Requirements for pallet-pooling goods palletizing and handover"**

The palletized height must reconcile standard trailer interior height, rack clearance and the goods' permitted stacking height:

| Board-deck layers | Max load height (pallet included) |
|---|---|
| 1 | 1800 mm |
| 2 | 1200 mm |
| 3 | 800 mm |
| 4 | 600 mm |

The tallest standard trailer interior is 2600 mm; allowing 100–200 mm for forklift working space gives **2400 mm** as the maximum pallet load envelope.
The `tihi_layers` parameter maps directly onto this standard.

**Supplementary: "Operation specification for board-deck transport" (dbba.sacinfo.org.cn)**
- 5.2.4 each pallet unit should consist of **a single product type**
- 5.1.3 the packaged goods **must not overhang the pallet edge**
- 6.1.3 pallet units of differing heights must be loaded **tallest first**

## 7. Amazon FBA inbound requirements

**Amazon Seller Central — FBA pallet requirements**

| Clause | Value | Implementation here |
|---|---|---|
| Pallet size | 1.0 m × 1.2 m four-way wooden pallet | `US48` |
| Pallet grade | GMA Grade B or better | `US48GMA` |
| Max pallet height | 1.6 m (pallet included) | `max_pallet_stack_h` |
| Max double-stack height | 2.7 m | `allow_double_pallet` + height limit |
| Max pallet gross weight | ≤ 500 kg | `max_pallet_weight` |
| Allowed overhang past pallet edge | ≤ 25 mm | L1 geometry check (stricter: zero overhang) |
| Roof/tail clearance | ≥ 300 mm | Noted in the plan |
| Mixed-SKU pallets | allowed, but must carry a "Mixed SKU" label | `mono_sku` parameter + plan notes |
| Mixed-SKU pallet weight | ≤ 500 kg | `max_pallet_weight` |
| Stretch wrap | must fully enclose, labels on all four sides | Recommendation in the plan |
| Best stacking for single-layer pallets | Column (25–30% stronger) | `stack_pattern` |

**Amazon's own best practice note**: column stacking is stronger but prone to tipping; interlock/brick is stable but weaker. A mix is recommended — 3 column layers at the bottom + 2 interlock layers on top.

## 8. Warehouse practice in China

**Operational documents such as the "Slotting planning and goods stacking operation specification"**:
- Store different product types on separate pallets, with labels facing out, parallel to the aisle
- Never invert goods, never stack beyond the specified number of layers
- Leave 10–15 cm between pallets for the forklift to turn
- Heavy goods at the bottom, light goods on top, overall CoG centred — avoid top-heavy stacks

**Forklift operating rules**:
- Fork tine insertion depth ≥ 2/3 of the pallet depth
- In racking, pallet length should exceed the beam width by 50 mm
- Pallet overhang past the beam edge ≤ 5 cm

**Wooden pallets for cold-chain logistics** (dbba.sacinfo):
- Max goods height ≤ 1700 mm measured from the pallet base
- Goods weight ≤ 90% of the pallet's max load
- Stack footprint within 20 mm of the pallet edge
- No heavy on light, no hard on soft, no large on small; cross-lay the bottom layer, align the seams on the second

## 9. Multi-stop delivery and LIFO

**Why loading order matters for multi-stop delivery** (Bortfeldt & Wäscher's review emphasizes that multi-stop constraints have long been ignored):
- LIFO: the last stop delivered is loaded first (deepest in the container)
- With rear-door-only vehicles, LIFO is mandatory — otherwise every stop requires unloading and reloading
- One CMR per stop, stating that stop's weight and piece count rather than the whole-vehicle total

**This tool does not currently model unloading order** (the `load_order` parameter is reserved but no path optimization is implemented). This is a known limitation; multi-stop delivery optimization would require an extension.

## 10. Suggested reading order

1. Bortfeldt, A. & Wäscher, G. (2013), *EJOR* 229(1) — source of the constraint taxonomy
2. Wäscher, G., Haußner, M. & Schumann, U. (2007), *EJOR* 183(3) — problem-type definitions
3. Junqueira, E., Morabito, R. & Yamashita, K. (2012), *COR* 39(1) — stability and bearing MILP
4. Tresca et al. (2022), *IEEE TASE* — layer flushness and overhang as soft constraints
5. GB/T 16470-2008 — pallet unit loads (Chinese national standard)
6. GB/T 4857.4-2019 — stacking test for transport packages
7. MOFCOM "Requirements for pallet-pooling goods palletizing and handover" — TiHi standard
8. Amazon Seller Central FBA pallet requirements — inbound compliance
