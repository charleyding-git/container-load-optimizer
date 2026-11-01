# Container and pallet specifications

> 中文版见 [container_specs.md](container_specs.md). This file is the English version.

## Data sources

- **Container internal dimensions / door opening height**: ISO 668 cross-checked against the public spec sheets of Hapag-Lloyd / GoFreight / Nexportal
  (ISO 668 only fixes external dimensions; internal clearance and door opening vary slightly by manufacturer — the mainstream values are used here)
- **Pallet dimensions**: ISO / GB / GMA standards
- **Industry constraints**: see [modeling_basis.en.md](modeling_basis.en.md)

## Container specifications

| Code | Name | Length | Width | Height | Door opening | Volume | Payload | Type |
|---|---|---|---|---|---|---|---|---|
| `20GP` | 20ft Standard Dry | 5898 | 2352 | 2393 | 2280 | 33.2 m³ | 28200 kg | dry |
| `20HC` | 20ft High Cube Dry | 5898 | 2352 | 2698 | 2585 | 37.2 m³ | 28160 kg | dry |
| `40GP` | 40ft Standard Dry | 12032 | 2352 | 2393 | 2280 | 67.7 m³ | 28000 kg | dry |
| `40HQ` | 40ft High Cube Dry | 12032 | 2352 | 2698 | 2585 | 76.3 m³ | 28600 kg | dry |
| `45HC` | 45ft High Cube Dry | 13556 | 2352 | 2698 | 2585 | 86.0 m³ | 27800 kg | dry |
| `20RF` | 20ft Reefer | 5450 | 2280 | 2270 | 2260 | 28.3 m³ | 27575 kg | reefer |
| `40RF` | 40ft Reefer | 11580 | 2280 | 2530 | 2540 | 67.0 m³ | 29700 kg | reefer |
| `40RHQ` | 40ft High Cube Reefer | 11580 | 2280 | 2530 | 2540 | 67.0 m³ | 29700 kg | reefer |
| `20OT` | 20ft Open Top | 5898 | 2352 | 2380 | 2280 | 32.5 m³ | 30050 kg | open top |
| `40OT` | 40ft Open Top | 12032 | 2352 | 2380 | 2280 | 67.2 m³ | 26630 kg | open top |

All dimensions in mm.

**⚠️ Reefer caveat**: the refrigeration unit takes up real space, so the usable interior shrinks noticeably (a 40RF is only 2280 mm wide — 72 mm narrower than a dry box).
A reefer door opening of 2540 mm being *higher* than its 2530 mm internal height is a common annotation quirk in spec sheets — in practice, go by the condition of the actual unit.

**⚠️ Open-top caveat**: the roof is removable, so cargo height is not bound by the internal height — but it is still bound by the door opening height.

## Pallet specifications

| Code | Name | L × W | Height | Forklift entry | Rated load | Notes |
|---|---|---|---|---|---|---|
| `US48` | US 48×40 | 1219.2 × 1016 | 145 | four-way | 1360 kg | US FBA; 21 fit in a 40HQ |
| `US48GMA` | US GMA Grade B | 1219.2 × 1016 | 145 | four-way | 1360 kg | Required by Amazon FBA |
| `EU1200` | EU 1200×800 | 1200 × 800 | 144 | four-way | 1200 kg | Europe; 25 fit in a 40HQ |
| `EU1200X1000` | EU 1200×1000 | 1200 × 1000 | 144 | four-way | 1500 kg | Common in Europe |
| `CN1200` | GB 1200×1000 | 1200 × 1000 | 150 | four-way | 1500 kg | GB/T 2934 |
| `UK1200` | UK 1200×1000 | 1200 × 1000 | 144 | four-way | 1000 kg | UK |
| `JP1100` | JP 1100×1100 | 1100 × 1100 | 145 | four-way | 1000 kg | Japan/Korea; 20 fit in a 40HQ |
| `CUSTOM` | Custom | fill in | fill in | — | — | Enter L/W/H in the UI |

## Measured pallet positions on a 40HQ floor (40HQ = 12032 × 2352)

| Pallet | Positions | Arrangement | Notes |
|---|---|---|---|
| US 1219.2×1016 | **21** | three-band Y interleave | A pure banded layout only reaches 20 — the interleave is what fills it |
| EU 1200×800 | **25** | three-band | |
| GB 1200×1000 | **22** | three-band | |
| JP 1100×1100 | **20** | three-band | Square, so placement freedom is limited |

**⚠️ One pallet position short = one container short.** The number of pallet positions is a hard ceiling on throughput.

## How the three-band Y interleave works

The structure that fits 21 US pallets into a 40HQ:

```
Band ① bottom row: 11 pallets in B orientation (1016 wide × 1219.2 deep)  y ∈ [0, 1219.2)
Band ② upper row:  10 pallets in A orientation (1219.2 wide × 1016 deep)  y ∈ [1016, 2235.2)
         Their X ranges partly overlap; the Y offset keeps them apart → 21 in total
Container width 2352, used up to 2235.2 → 116.8 mm left over
```

Implemented as a parametric enumeration in `engine.pallet_floor_layout` — a purely structural solution, zero randomness, fully reproducible.
