"""
OSM ↔ WBT Alignment
====================
Matches OpenStreetMap node/way IDs to their Webots (.wbt) counterparts
for Roads and Buildings, extracts rich OSM metadata for each matched object,
and saves the full alignment results to a JSON file for later inspection.

- Roads:     matched directly via shared OSM way ID (stored in WBT `id` field)
- Buildings: matched geometrically — OSM polygon centroids are projected to
             WBT local XY coordinates and matched to WBT building translations

Projection used by the Webots OSM importer (recovered empirically from crossroad pairs):
  wbt_x =  2726.2392 * (lat - LAT_0) + 64449.6220 * (lon - LON_0)
  wbt_y = 111256.3589 * (lat - LAT_0) -  1579.5608 * (lon - LON_0)

where (LAT_0, LON_0) = gpsReference from the WBT WorldInfo block.
"""

import xml.etree.ElementTree as ET
import math, re, json
from datetime import datetime, timezone
from dataclasses import dataclass, field

# ─────────────────────────────────────────────────────────────────────────────
# 0. Constants
# ─────────────────────────────────────────────────────────────────────────────

BUILDING_MATCH_TOLERANCE_M = 5.0

ROAD_TAGS = [
    "name", "name:lt", "ref",
    "highway", "oneway", "junction",
    "maxspeed", "lanes", "lanes:forward", "lanes:backward",
    "turn:lanes", "turn:lanes:forward", "turn:lanes:backward",
    "surface", "smoothness", "width",
    "lit", "foot", "bicycle", "cycleway:both", "cycleway:left", "cycleway:right",
    "bridge", "tunnel", "layer",
    "service", "access",
]

BUILDING_TAGS = [
    "building", "building:levels", "roof:shape", "roof:levels",
    "addr:street", "addr:housenumber", "addr:postcode", "addr:city",
    "name", "historic", "shop", "amenity",
    "opening_hours", "wheelchair", "disused",
    "operator", "brand", "website", "phone",
]

# ─────────────────────────────────────────────────────────────────────────────
# 1. Projection helper
# ─────────────────────────────────────────────────────────────────────────────

_LAT_0 = None
_LON_0 = None

def latlon_to_wbt_xy(lat, lon):
    if _LAT_0 is None:
        raise RuntimeError("GPS reference not set. Call set_reference(lat, lon) first.")
    dlat = lat - _LAT_0
    dlon = lon - _LON_0
    x =  2726.2392 * dlat + 64449.6220 * dlon
    y = 111256.3589 * dlat -  1579.5608 * dlon
    return x, y

def set_reference(lat, lon):
    global _LAT_0, _LON_0
    _LAT_0, _LON_0 = lat, lon

# ─────────────────────────────────────────────────────────────────────────────
# 2. Parse OSM file
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class OsmNode:
    id: str
    lat: float
    lon: float
    tags: dict = field(default_factory=dict)

@dataclass
class OsmWay:
    id: str
    node_refs: list
    tags: dict = field(default_factory=dict)

def parse_osm(path):
    tree = ET.parse(path)
    root = tree.getroot()

    nodes = {}
    for el in root.findall("node"):
        tags = {t.get("k"): t.get("v") for t in el.findall("tag")}
        nodes[el.get("id")] = OsmNode(
            id=el.get("id"),
            lat=float(el.get("lat")),
            lon=float(el.get("lon")),
            tags=tags,
        )

    road_ways, building_ways = [], []
    for el in root.findall("way"):
        tags = {t.get("k"): t.get("v") for t in el.findall("tag")}
        refs = [nd.get("ref") for nd in el.findall("nd")]
        way  = OsmWay(id=el.get("id"), node_refs=refs, tags=tags)
        if "highway" in tags:
            road_ways.append(way)
        if "building" in tags:
            building_ways.append(way)

    return nodes, road_ways, building_ways

def _pick_tags(all_tags, wanted_keys):
    return {k: all_tags[k] for k in wanted_keys if k in all_tags}

def _way_node_latlon(way, nodes):
    return [(nodes[ref].lat, nodes[ref].lon) for ref in way.node_refs if ref in nodes]

def _way_centroid_latlon(way, nodes):
    coords = _way_node_latlon(way, nodes)
    if not coords:
        return None
    return (sum(c[0] for c in coords) / len(coords), sum(c[1] for c in coords) / len(coords))

def _way_centroid_wbt(way, nodes):
    coords = [latlon_to_wbt_xy(lat, lon) for lat, lon in _way_node_latlon(way, nodes)]
    if not coords:
        return None
    return (sum(c[0] for c in coords) / len(coords), sum(c[1] for c in coords) / len(coords))

# ─────────────────────────────────────────────────────────────────────────────
# 3. Parse WBT file
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class WbtRoad:
    wbt_name: str
    osm_id: str
    translation: tuple
    way_points: list
    number_of_lanes: int
    number_of_forward_lanes: int
    speed_limit: float
    width: float
    raw_block: str

@dataclass
class WbtBuilding:
    wbt_name: str
    translation: tuple
    corners: list
    roof_shape: str
    wall_type: str
    floor_number: int
    floor_height: float
    raw_block: str

def _extract_blocks(wbt_text, keyword):
    blocks = []
    i = 0
    kw = keyword + " {"
    while True:
        idx = wbt_text.find(kw, i)
        if idx == -1: break
        line_start = wbt_text.rfind("\n", 0, idx)
        prefix = wbt_text[line_start+1:idx]
        if prefix.strip() == "":
            depth = 0
            j = idx + len(keyword) + 1
            while j < len(wbt_text):
                if wbt_text[j] == "{": depth += 1
                elif wbt_text[j] == "}":
                    depth -= 1
                    if depth == 0:
                        blocks.append(wbt_text[idx:j+1])
                        break
                j += 1
            i = j + 1
        else:
            i = idx + len(kw)
    return blocks

def _str_field(text, name):
    m = re.search(rf'\b{name}\s+"([^"]*)"', text)
    return m.group(1) if m else None

def _num_field(text, name):
    m = re.search(rf'\b{name}\s+([\-\d.]+)', text)
    return float(m.group(1)) if m else None

def _int_field(text, name):
    v = _num_field(text, name)
    return int(v) if v is not None else None

def _translation(text):
    m = re.search(r'\btranslation\s+([\-\d.]+)\s+([\-\d.]+)', text)
    return (float(m.group(1)), float(m.group(2))) if m else None

def _waypoints(text):
    m = re.search(r'\bwayPoints\s*\[(.*?)\]', text, re.DOTALL)
    if not m: return []
    nums = list(map(float, re.findall(r'[\-\d.]+', m.group(1))))
    return [[round(nums[i], 4), round(nums[i+1], 4)] for i in range(0, len(nums)-2, 3)]

def _corners(text):
    m = re.search(r'\bcorners\s*\[(.*?)\]', text, re.DOTALL)
    if not m: return []
    nums = list(map(float, re.findall(r'[\-\d.]+', m.group(1))))
    return [[round(nums[i], 4), round(nums[i+1], 4)] for i in range(0, len(nums)-1, 2)]

def _gps_reference(text):
    m = re.search(r'\bgpsReference\s+([\-\d.]+)\s+([\-\d.]+)', text)
    return (float(m.group(1)), float(m.group(2))) if m else None

def parse_wbt(path):
    text = open(path, encoding="utf-8").read()
    ref = _gps_reference(text)
    if ref: set_reference(*ref)

    roads = []
    for block in _extract_blocks(text, "Road"):
        osm_id = _str_field(block, "id")
        trans  = _translation(block)
        if not (osm_id and trans): continue
        roads.append(WbtRoad(
            wbt_name                = _str_field(block, "name") or "",
            osm_id                  = osm_id,
            translation             = trans,
            way_points              = _waypoints(block),
            number_of_lanes         = _int_field(block, "numberOfLanes") or 0,
            number_of_forward_lanes = _int_field(block, "numberOfForwardLanes") or 0,
            speed_limit             = _num_field(block, "speedLimit"),
            width                   = _num_field(block, "width"),
            raw_block               = block,
        ))

    buildings = []
    for block in _extract_blocks(text, "SimpleBuilding"):
        trans = _translation(block)
        if not trans: continue
        buildings.append(WbtBuilding(
            wbt_name     = _str_field(block, "name") or "",
            translation  = trans,
            corners      = _corners(block),
            roof_shape   = _str_field(block, "roofShape") or "",
            wall_type    = _str_field(block, "wallType") or "",
            floor_number = _int_field(block, "floorNumber") or 0,
            floor_height = _num_field(block, "floorHeight"),
            raw_block    = block,
        ))

    return roads, buildings

# ─────────────────────────────────────────────────────────────────────────────
# 4. Match Roads (UPDATED)
# ─────────────────────────────────────────────────────────────────────────────

def _canonical_osm_id(wbt_id):
    m = re.match(r'^(\d+)_(\d+)$', wbt_id)
    return (m.group(1), int(m.group(2))) if m else (wbt_id, None)

def _speed_limit_kmh(ms):
    return round(ms * 3.6) if ms is not None else None

# ADDED osm_nodes to arguments to extract GPS coordinates
def match_roads(osm_roads, wbt_roads, osm_nodes):
    osm_by_id = {w.id: w for w in osm_roads}
    results = []

    for wbt_r in wbt_roads:
        base_id, segment = _canonical_osm_id(wbt_r.osm_id)
        osm_w = osm_by_id.get(base_id)

        wbt_info = {
            "name"                   : wbt_r.wbt_name,
            "wbt_id"                 : wbt_r.osm_id,
            "segment"                : segment,
            "translation_x"          : round(wbt_r.translation[0], 4),
            "translation_y"          : round(wbt_r.translation[1], 4),
            "number_of_lanes"        : wbt_r.number_of_lanes,
            "number_of_forward_lanes": wbt_r.number_of_forward_lanes,
            "speed_limit_kmh"        : _speed_limit_kmh(wbt_r.speed_limit),
            "width_m"                : wbt_r.width,
            "waypoint_count"         : len(wbt_r.way_points),
            "way_points"             : wbt_r.way_points,
        }

        if osm_w:
            # NEW: Extract GPS coordinates [Lat, Lon] using the helper function
            osm_waypoints = [[round(lat, 7), round(lon, 7)] for lat, lon in _way_node_latlon(osm_w, osm_nodes)]
            
            osm_info = {
                "osm_id"      : osm_w.id,
                "node_count"  : len(osm_w.node_refs),
                "way_points"  : osm_waypoints,  # <--- Added way_points here
                "tags"        : _pick_tags(osm_w.tags, ROAD_TAGS),
                "all_tag_keys": sorted(osm_w.tags.keys()),
            }
        else:
            osm_info = None

        results.append({
            "matched": osm_w is not None,
            "osm"    : osm_info,
            "wbt"    : wbt_info,
        })

    return results

# ─────────────────────────────────────────────────────────────────────────────
# 5. Match Buildings
# ─────────────────────────────────────────────────────────────────────────────

def _wbt_building_abs_centroid(b):
    if b.corners:
        mean_cx = sum(c[0] for c in b.corners) / len(b.corners)
        mean_cy = sum(c[1] for c in b.corners) / len(b.corners)
        return b.translation[0] + mean_cx, b.translation[1] + mean_cy
    return b.translation

def match_buildings(osm_buildings, wbt_buildings, osm_nodes, tolerance_m=BUILDING_MATCH_TOLERANCE_M):
    osm_centroids = []
    for way in osm_buildings:
        cxy = _way_centroid_wbt(way, osm_nodes)
        if cxy: osm_centroids.append((way, cxy))

    wbt_centroids = [(b, _wbt_building_abs_centroid(b)) for b in wbt_buildings]

    used_wbt = set()
    results  = []

    for osm_way, (cx, cy) in osm_centroids:
        best_dist, best_wbt, best_idx = float("inf"), None, None
        for idx, (wbt_b, (wx, wy)) in enumerate(wbt_centroids):
            if idx in used_wbt: continue
            dist = math.sqrt((cx - wx)**2 + (cy - wy)**2)
            if dist < best_dist:
                best_dist, best_wbt, best_idx = dist, wbt_b, idx

        matched = best_wbt is not None and best_dist <= tolerance_m
        if matched: used_wbt.add(best_idx)

        if best_wbt:
            abs_cx, abs_cy = _wbt_building_abs_centroid(best_wbt)
            wbt_info = {
                "name"            : best_wbt.wbt_name,
                "translation_x"   : round(best_wbt.translation[0], 4),
                "translation_y"   : round(best_wbt.translation[1], 4),
                "centroid_x"      : round(abs_cx, 4),
                "centroid_y"      : round(abs_cy, 4),
                "centroid_dist_m" : round(best_dist, 4),
                "corner_count"    : len(best_wbt.corners),
                "corners"         : best_wbt.corners,
                "roof_shape"      : best_wbt.roof_shape,
                "wall_type"       : best_wbt.wall_type,
                "floor_number"    : best_wbt.floor_number,
                "floor_height_m"  : best_wbt.floor_height,
            }
        else: wbt_info = None

        centroid_ll = _way_centroid_latlon(osm_way, osm_nodes)
        osm_info = {
            "osm_id"         : osm_way.id,
            "node_count"     : len(osm_way.node_refs),
            "centroid_lat"   : round(centroid_ll[0], 7) if centroid_ll else None,
            "centroid_lon"   : round(centroid_ll[1], 7) if centroid_ll else None,
            "centroid_wbt_x" : round(cx, 4),
            "centroid_wbt_y" : round(cy, 4),
            "tags"           : _pick_tags(osm_way.tags, BUILDING_TAGS),
            "all_tag_keys"   : sorted(osm_way.tags.keys()),
            "node_latlon"    : [[round(lat, 7), round(lon, 7)] for lat, lon in _way_node_latlon(osm_way, osm_nodes)],
        }

        results.append({
            "matched": matched,
            "osm"    : osm_info,
            "wbt"    : wbt_info,
        })

    return results

# ─────────────────────────────────────────────────────────────────────────────
# 6. Indexes & Main Run
# ─────────────────────────────────────────────────────────────────────────────

def make_roads_index(road_matches):
    return {r["osm"]["osm_id"]: r for r in road_matches if r["osm"]}

def make_buildings_index(building_matches):
    return {b["osm"]["osm_id"]: b for b in building_matches}

def run(osm_path, wbt_path, json_path):
    print("Parsing OSM file ...")
    osm_nodes, osm_roads, osm_buildings = parse_osm(osm_path)
    print(f"  {len(osm_nodes)} nodes,  {len(osm_roads)} road ways,  {len(osm_buildings)} building ways")

    print("Parsing WBT file ...")
    wbt_roads, wbt_buildings = parse_wbt(wbt_path)
    print(f"  {len(wbt_roads)} Road objects,  {len(wbt_buildings)} SimpleBuilding objects")
    print(f"  GPS reference: lat={_LAT_0}, lon={_LON_0}")

    # UPDATED: Passing osm_nodes so match_roads can get the GPS coordinates
    road_matches     = match_roads(osm_roads, wbt_roads, osm_nodes)
    building_matches = match_buildings(osm_buildings, wbt_buildings, osm_nodes)

    n_roads_matched = sum(1 for r in road_matches     if r["matched"])
    n_bldg_matched  = sum(1 for b in building_matches if b["matched"])

    print(f"\n{'='*62}")
    print("ROADS  (matched by shared OSM way ID)")
    print(f"{'='*62}")
    print(f"Matched: {n_roads_matched} / {len(wbt_roads)} WBT roads\n")
    print(f"  {'OSM ID':<15} {'WBT Name':<25} {'highway':<14} WBT XY")
    print(f"  {'-'*75}")
    for r in road_matches:
        flag   = "OK" if r["matched"] else "NO OSM MATCH"
        hw     = r["osm"]["tags"].get("highway", "-") if r["osm"] else "-"
        tx, ty = r["wbt"]["translation_x"], r["wbt"]["translation_y"]
        name   = r["wbt"]["name"]
        osm_id = r["osm"]["osm_id"] if r["osm"] else r["wbt"]["wbt_id"]
        print(f"  {osm_id:<15} {name:<25} {hw:<14} ({tx:>8.2f}, {ty:>8.2f})  {flag}")

    output = {
        "meta": {
            "generated_at"              : datetime.now(timezone.utc).isoformat(),
            "osm_path"                  : osm_path,
            "wbt_path"                  : wbt_path,
            "gps_reference"             : {"lat": _LAT_0, "lon": _LON_0},
            "stats": {
                "roads_matched"      : n_roads_matched,
                "buildings_matched"  : n_bldg_matched,
            },
        },
        "roads"    : road_matches,
        "buildings": building_matches,
    }

    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    print(f"\nAlignment saved -> {json_path}")
    return road_matches, building_matches

if __name__ == "__main__":
    try:
        from config_local import OSM_PATH, WBT_PATH, JSON_PATH
    except ImportError:
        raise SystemExit("Please define OSM_PATH, WBT_PATH, and JSON_PATH variables.")

    road_matches, building_matches = run(OSM_PATH, WBT_PATH, JSON_PATH)