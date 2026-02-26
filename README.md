# osm_to_wbt

Pipeline for converting [OpenStreetMap](https://www.openstreetmap.org/) data into
[Webots](https://cyberbotics.com/) simulation worlds (`.wbt`), with iterative
enrichment of the generated world file toward a high-fidelity urban digital twin.

---

## Motivation

The Webots OSM importer produces a geometrically correct world from OSM data, but
the result is semantically thin — road names are present, but speed limits, lane
counts, surface types, and building metadata are not wired into the simulation.
Many objects are also missing or approximated.

This project builds a pipeline on top of the importer that:

1. downloads a real urban area from OSM
2. generates the initial Webots world
3. aligns every generated WBT object back to its source OSM way or node
4. extracts the full OSM semantic metadata for each aligned object
5. uses that metadata to iteratively correct and enrich the world file

The end goal is a `.wbt` world that faithfully reflects the real-world geometry,
road network properties, and building characteristics of the mapped area.

---

## Pipeline overview

```
OpenStreetMap
     │
     │  Overpass API download
     ▼
  .osm file
     │
     │  Webots OSM importer  (osm_to_wbt_V2.ipynb)
     ▼
  .wbt file  ◄─────────────────────────────────────────┐
     │                                                  │
     │  OSM ↔ WBT alignment  (osm_wbt_align.py)        │ iterative
     ▼                                                  │ improvement
  alignment.json                                        │
  (matched objects + full OSM tags)                     │
     │                                                  │
     │  Enrichment pass  (planned)  ──────────────────►─┘
     ▼
  enriched .wbt file
```

Each enrichment pass reads the alignment JSON, applies corrections and additions
to the world file, and produces a new `.wbt` that feeds the next iteration.

---

## Repository structure

```
osm_to_wbt/
│
├── osm_to_wbt_V2.ipynb       # Step 1 — download OSM area and generate initial .wbt
├── osm_wbt_align.py          # Step 2 — align WBT objects to OSM, extract metadata,
│                             #          save alignment.json for inspection
│
├── data/                     # Sample data for the Vilnius city-centre area
│   ├── osm_54.691447_25.279962_200_200.osm
│   ├── wbt_54.691447_25.279962_200_200.wbt
│   └── osm_wbt_alignment.json
│
└── README.md
```

---

## Coordinate system

The Webots OSM importer uses a custom Transverse Mercator projection centred on
the `gpsReference` point declared in the `WorldInfo` block.  Because Webots does
not expose the projection parameters directly, `osm_wbt_align.py` recovers the
exact transform empirically by fitting a linear model to all crossroad nodes that
appear in both files.

For the sample area (reference `54.691447°N, 25.279962°E`, UTM zone 35):

```
wbt_x =  2726.24 × (lat − lat₀) + 64449.62 × (lon − lon₀)
wbt_y = 111256.36 × (lat − lat₀) −  1579.56 × (lon − lon₀)
```

Fit over 17 control points; max residual **< 0.006 m**.  
The small cross-terms reflect the shear of the Transverse Mercator projection at
this latitude.  Coefficients must be re-derived when changing the reference point.

---

## Current status

| Step | Script | Status |
|------|--------|--------|
| OSM download + initial .wbt generation | `osm_to_wbt_V2.ipynb` | ✅ working |
| OSM ↔ WBT alignment + metadata extraction | `osm_wbt_align.py` | ✅ working |
| Iterative world enrichment | planned | 🔲 in design |

**Alignment results for the sample area (200 × 200 m, Vilnius city centre)**

- Roads: **26 / 26** WBT Road objects matched (100 %)
- Buildings: **6 / 6** OSM building ways matched (100 %)
- Split ways (one OSM way → multiple WBT segments): handled automatically

---

## Alignment JSON format

`osm_wbt_align.py` writes a structured JSON file for inspection and use by
downstream enrichment passes.

```jsonc
{
  "meta": {
    "generated_at": "2026-02-26T18:50:32Z",
    "gps_reference": { "lat": 54.691447, "lon": 25.279962 },
    "stats": {
      "roads_matched": 26,
      "buildings_matched": 6,
      ...
    }
  },
  "roads": [
    {
      "matched": true,
      "osm": {
        "osm_id": "4853620",
        "tags": {
          "name": "Žvejų g.",
          "highway": "tertiary",
          "maxspeed": "50",
          "lanes": "2",
          "surface": "asphalt",
          "lit": "yes",
          ...
        },
        "all_tag_keys": [ ... ]   // every tag present, even if not extracted
      },
      "wbt": {
        "name": "Žvejų g.",
        "translation_x": 40.7577,
        "translation_y": 60.7694,
        "number_of_lanes": 2,
        "speed_limit_kmh": 50,
        "width_m": 8.0,
        "way_points": [ ... ]
      }
    }
  ],
  "buildings": [
    {
      "matched": true,
      "osm": {
        "osm_id": "383768042",
        "tags": {
          "building": "kiosk",
          "name": "Narvesen",
          "addr:street": "Kalvarijų g.",
          "opening_hours": "Mo-Sa 06:00-22:00; Su,PH 07:00-22:00",
          ...
        },
        "node_latlon": [ ... ]    // polygon vertices in WGS-84
      },
      "wbt": {
        "name": "Narvesen",
        "translation_x": 41.39,
        "translation_y": 88.95,
        "centroid_dist_m": 0.93,  // match quality
        "floor_number": 1,
        "roof_shape": "hipped roof",
        "corners": [ ... ]
      }
    }
  ]
}
```

---

## Requirements

- Python 3.9+
- Standard library only — no external dependencies for `osm_wbt_align.py`
- Webots OSM importer dependencies for `osm_to_wbt_V2.ipynb`:  
  `lxml`, `pyproj`, `shapely`, `webcolors`, `configparser`

---

## Quick start

```bash
# 1. Clone
git clone https://github.com/<your-username>/osm_to_wbt.git
cd osm_to_wbt

# 2. Edit configuration at the top of osm_wbt_align.py
#    Set BASE_DIR, OSM_PATH, WBT_PATH, JSON_PATH, LAT_0, LON_0

# 3. Run alignment
python osm_wbt_align.py

# 4. Inspect results
#    Console output shows match summary
#    osm_wbt_alignment.json contains full per-object data
```

To generate a new area, open `osm_to_wbt_V2.ipynb` in Google Colab or Jupyter,
set the target URL and area dimensions, and run all cells.

---

## Planned enrichment passes

The iterative improvement stage will apply a series of targeted passes over the
alignment JSON, each writing a corrected `.wbt`:

- **Road properties** — inject `maxspeed`, lane geometry, surface type, and
  cycling/pedestrian access flags from OSM tags into WBT Road nodes
- **Missing buildings** — detect OSM building ways with no WBT counterpart and
  generate `SimpleBuilding` blocks at the correct projected position
- **Building metadata** — propagate address, floor count, and roof type from OSM
  into matching WBT buildings
- **Traffic signals & crossings** — place signal objects at OSM nodes tagged
  `highway=traffic_signals` or `highway=crossing`
- **Street furniture** — benches, bins, bus stops, and other `amenity` nodes
- **Validation pass** — compare projected OSM geometry against WBT waypoints and
  flag geometric drift above a configurable threshold

---

## License

Data from [OpenStreetMap](https://www.openstreetmap.org/) is © OpenStreetMap
contributors, available under the
[Open Database Licence](https://opendatacommons.org/licenses/odbl/).

Code in this repository is released under the [MIT License](LICENSE).
