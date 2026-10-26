#!/usr/bin/env python3
"""Shared print gate for one-STL-per-part projects.

Used by .github/workflows/print-gate.yml. A project supplies
print_check.yaml, stl/, and tools/fixtures/.

Two passes:

1. OrcaSlicer, supports off. Fail on a floating region, a floating
   cantilever, an empty layer, or a G-code conflict. A second look at
   the toolpath fails an extrusion that has no previous-layer extrusion
   within the 45° offset.
2. A layer raster at 0.2 mm. Every layer's area has to sit inside the
   layer below, dilated by 0.2 mm (45° from vertical). A wall thinner
   than the project's minimum (default 1.6 mm) and a feature thinner
   than the project's minimum (default 0.8 mm) fail the same raster.

The default profiles are the Bambu Lab X1C 0.4 mm nozzle, 0.20 mm
Standard, and Bambu PETG Basic. The workflow can override the names.
The inherit chain stays inside that vendor's profile set.

Heavy slicing runs in GitHub Actions. Locally, pass --skip-slicer and
--only for the part you just exported.

Fixtures are known-bad parts and must fail the named rule. A fixture
that passes means the check went blind. Every defect reported from a
real print becomes a new fixture and a new rule.
"""
import argparse
import importlib.util
import json
import os
import re
import subprocess
import sys
import zipfile
from pathlib import Path

import numpy as np
import trimesh
import yaml
from scipy import ndimage

CAD = None
STL_DIR = None
FIXTURE_DIR = None
LAYER = 0.2
PITCH = 0.1
# Half a cell, so a true 45° wall is not failed by the raster edge.
SLACK = PITCH * 0.5
SUPPORT_R = LAYER + SLACK
AREA_FLOOR = 0.25  # mm², below this is raster dust
# A true 45° face can sit one raster cell past the dilation. Further
# than this, and only a bridge (support on both sides, span ≤ 10 mm)
# is still allowed.
RASTER_OVER = 0.22
BRIDGE_MM = 10.0
GROOVE_MID_Z = 10.55
GROOVE_TOL = 0.5

# Historical thin-spine thresholds (0.6 mm). A project that sets
# min_feature.max_thickness_mm replaces them. The design rule is 0.8 mm;
# the template uses that. A project may pin a different thickness.
FEATURE = {
    "max_radius": 0.30,
    "min_radius": 0.12,
    "min_cells": 12,
    "min_layers": 8,
}
MIN_WALL_MM = 1.6
RULES = {
    "overhang": True,
    "min_feature": True,
    "min_wall": True,
    "bed": True,
    "manifold": True,
}
DEPS = {}
FIXTURES = []
PROBES = []
CONFIG = {}

MACHINE_NAME = "Bambu Lab X1 Carbon 0.4 nozzle"
PROCESS_NAME = "0.20mm Standard @BBL X1C"
FILAMENT_NAME = "Bambu PETG Basic @BBL X1C"

SLICER_FAIL = (
    "empty layer",
    "floating region",
    "floating cantilever",
    "g-code conflict",
    "gcode conflict",
    "conflict detected",
)


def all_stls():
    return sorted(p.name for p in STL_DIR.glob("*.stl"))


def select_stls(changed_text, full):
    if full:
        return all_stls()
    if not changed_text:
        return []
    wanted = set()
    everything = False
    for line in changed_text.splitlines():
        name = Path(line.strip()).name
        if not name:
            continue
        if name in DEPS:
            dep = DEPS[name]
            if dep is None:
                everything = True
            else:
                wanted.update(dep)
        elif name.endswith(".stl") and (STL_DIR / name).exists():
            wanted.add(name)
    if everything:
        return all_stls()
    return sorted(wanted)


def _to_planar(section):
    """Path2D plus a 4×4. trimesh 4 names this to_planar; trimesh 5 aliases to_2D."""
    if hasattr(section, "to_2D"):
        return section.to_2D()
    return section.to_planar()


def _world_loops(section):
    """Closed loops in world XY, holes included. Open section lines are dropped."""
    if section is None:
        return []
    try:
        planar, to_3d = _to_planar(section)
        polys = list(planar.polygons_full)
    except Exception:
        return []
    loops = []
    for poly in polys:
        for coords in (poly.exterior.coords, *(h.coords for h in poly.interiors)):
            xy = np.asarray(coords, dtype=float)
            if len(xy) < 3:
                continue
            pts = np.c_[xy, np.zeros(len(xy)), np.ones(len(xy))]
            loops.append((to_3d @ pts.T).T[:, :2])
    return loops


def _grid(mesh):
    origin = mesh.bounds[0][:2] - 1.0
    span = mesh.bounds[1][:2] - mesh.bounds[0][:2] + 2.0
    nx = int(span[0] / PITCH) + 2
    ny = int(span[1] / PITCH) + 2
    return origin, nx, ny


def _mask_at(mesh, z, origin, nx, ny):
    sec = mesh.section(plane_origin=[0, 0, float(z)], plane_normal=[0, 0, 1])
    return _raster(_world_loops(sec), origin, nx, ny)


def _raster(loops, origin, nx, ny):
    mask = np.zeros((ny, nx), dtype=bool)
    if not loops:
        return mask
    segs = []
    for loop in loops:
        a = loop
        b = np.roll(loop, -1, axis=0)
        segs.append(np.hstack([a, b]))
    segs = np.vstack(segs)
    ys = origin[1] + (np.arange(ny) + 0.5) * PITCH
    xs = origin[0] + (np.arange(nx) + 0.5) * PITCH
    y1, y2 = segs[:, 1], segs[:, 3]
    x1, x2 = segs[:, 0], segs[:, 2]
    for iy, y in enumerate(ys):
        cond = ((y1 <= y) & (y2 > y)) | ((y2 <= y) & (y1 > y))
        if not np.any(cond):
            continue
        xa = x1[cond] + (y - y1[cond]) * (x2[cond] - x1[cond]) / (y2[cond] - y1[cond])
        xa.sort()
        row = mask[iy]
        for a, b in zip(xa[0::2], xa[1::2]):
            row[(xs >= a) & (xs < b)] = True
    return mask


def layer_masks(mesh):
    z0 = float(mesh.bounds[0][2])
    z1 = float(mesh.bounds[1][2])
    origin, nx, ny = _grid(mesh)
    masks = []
    z = max(z0, 0.0) + LAYER * 0.5
    while z < z1 + 1e-6:
        masks.append(_mask_at(mesh, z, origin, nx, ny))
        z += LAYER
    return masks


def _disk(radius_mm):
    r = int(np.ceil(radius_mm / PITCH))
    yy, xx = np.ogrid[-r:r + 1, -r:r + 1]
    return (xx * xx + yy * yy) * (PITCH ** 2) <= radius_mm ** 2 + 1e-9


def overhang_rules(mesh):
    """Return rule ids tripped by the 0.2 mm layer test."""
    rules = set()
    masks = layer_masks(mesh)
    if not masks:
        return {"empty_layer"}
    foot = _disk(SUPPORT_R)
    prev = None
    empty_run = 0
    saw_solid = False
    thin_layers = 0
    for mask in masks:
        area = float(mask.sum()) * PITCH * PITCH
        if area < AREA_FLOOR:
            if saw_solid:
                empty_run += 1
            prev = mask if area > 0 else prev
            continue
        if empty_run > 0 and saw_solid:
            rules.add("empty_layer")
        empty_run = 0
        saw_solid = True
        if prev is not None and prev.any():
            supported = ndimage.binary_dilation(prev, structure=foot)
            bad = mask & ~supported
            if _real_overhang(bad, supported):
                rules.add("overhang_45")
            labeled, n = ndimage.label(mask)
            for i in range(1, n + 1):
                comp = labeled == i
                if comp.sum() * PITCH * PITCH < AREA_FLOOR:
                    continue
                if not np.any(comp & prev):
                    rules.add("mid_air")
                    break
        # A long thin spine, for several layers, is the old snap lip.
        # Stair-step pixels on a round wall are not.
        if _thin_spine(mask):
            thin_layers += 1
        else:
            thin_layers = 0
        if thin_layers >= FEATURE["min_layers"]:
            rules.add("min_feature")
        prev = mask
    return rules


def _bridged(comp, supported):
    """Unsupported, but walled on both sides of a span no longer than 10 mm."""
    limit = int(np.ceil(BRIDGE_MM / PITCH))

    def lines(span_axis):
        if span_axis == 1:
            index = np.nonzero(comp.any(axis=1))[0]
            for i in index:
                cols = np.nonzero(comp[i])[0]
                lo, hi = int(cols[0]), int(cols[-1])
                if (hi - lo) * PITCH > BRIDGE_MM:
                    return False
                left = supported[i, max(0, lo - limit):lo]
                right = supported[i, hi + 1:hi + 1 + limit]
                if left.size == 0 or right.size == 0 or not left.any() or not right.any():
                    return False
            return True
        index = np.nonzero(comp.any(axis=0))[0]
        for i in index:
            rows = np.nonzero(comp[:, i])[0]
            lo, hi = int(rows[0]), int(rows[-1])
            if (hi - lo) * PITCH > BRIDGE_MM:
                return False
            below = supported[max(0, lo - limit):lo, i]
            above = supported[hi + 1:hi + 1 + limit, i]
            if below.size == 0 or above.size == 0 or not below.any() or not above.any():
                return False
        return True

    return lines(1) or lines(0)


def _real_overhang(bad, supported):
    """A pixel past 45° that is not raster dust and not a short bridge."""
    if not bad.any():
        return False
    dist = ndimage.distance_transform_edt(~supported) * PITCH
    labeled, n = ndimage.label(bad)
    for i in range(1, n + 1):
        comp = labeled == i
        if float(comp.sum()) * PITCH * PITCH < AREA_FLOOR:
            continue
        if float(dist[comp].max()) <= RASTER_OVER:
            continue
        if _bridged(comp, supported):
            continue
        return True
    return False


def _thin_spine(mask):
    """True when a thin wall's medial axis runs at least 1.2 mm."""
    if mask.sum() < 4:
        return False
    dist = ndimage.distance_transform_edt(mask) * PITCH
    core = dist[1:-1, 1:-1]
    local = np.ones_like(core, dtype=bool)
    for nb in (
        dist[:-2, 1:-1], dist[2:, 1:-1], dist[1:-1, :-2], dist[1:-1, 2:],
    ):
        local &= core + 1e-9 >= nb
    thin = np.zeros_like(dist, dtype=bool)
    thin[1:-1, 1:-1] = local & (core > FEATURE["min_radius"]) & (core < FEATURE["max_radius"])
    labeled, n = ndimage.label(thin)
    if n == 0:
        return False
    counts = np.bincount(labeled.ravel())
    return bool((counts[1:] >= FEATURE["min_cells"]).any())


def thin_joint_split(mesh, z=None, radius_mm=0.8):
    if z is None:
        z = float((mesh.bounds[0][2] + mesh.bounds[1][2]) / 2)
    origin, nx, ny = _grid(mesh)
    mask = _mask_at(mesh, z, origin, nx, ny)
    r = max(1, int(round(radius_mm / PITCH)))
    yy, xx = np.ogrid[-r:r + 1, -r:r + 1]
    foot = (xx * xx + yy * yy) <= r * r
    eroded = ndimage.binary_erosion(mask, structure=foot)
    _, n = ndimage.label(eroded)
    return n != 1


def _rim_stats(mesh, z, origin, nx, ny):
    """Min and median outer radius. The bore (inside 7.5 mm) is ignored.

    Radius is from the world origin. A side boss shifts the bounding box,
    so the box centre is not the axis.
    """
    mask = _mask_at(mesh, z, origin, nx, ny)
    ys, xs = np.nonzero(mask)
    if len(xs) < 20:
        return None
    x = origin[0] + (xs + 0.5) * PITCH
    y = origin[1] + (ys + 0.5) * PITCH
    r = np.hypot(x, y)
    ang = np.arctan2(y, x)
    bins = np.linspace(-np.pi, np.pi, 73)
    sector = []
    for lo, hi in zip(bins[:-1], bins[1:]):
        sel = (ang >= lo) & (ang < hi) & (r > 7.5)
        if np.any(sel):
            sector.append(r[sel].max())
    if len(sector) < 8:
        return None
    return float(min(sector)), float(np.median(sector))


def _groove_zs(mesh):
    """Layers that carry the cord groove.

    A helical groove is a sector at least 0.2 mm under the rest of that
    layer. A straight turned-down band (the fixture) is a whole rim at
    least 0.4 mm under the rim beside it. The band is found from the rim,
    not from a fixed height, so a different drum diameter still matches.
    """
    z0 = 6.0
    z1 = min(float(mesh.bounds[1][2]), 16.0)
    origin, nx, ny = _grid(mesh)
    rows = []
    z = z0
    while z < z1:
        stats = _rim_stats(mesh, z, origin, nx, ny)
        if stats is not None:
            rows.append((z, stats[0], stats[1]))
        z += LAYER
    hits = []
    for i, (z, rmin, rmed) in enumerate(rows):
        if rmin < rmed - 0.2:
            hits.append(z)
            continue
        lo = max(0, i - 5)
        hi = min(len(rows), i + 6)
        beside = [rows[j][2] for j in range(lo, hi) if j != i]
        if beside and rmed < max(beside) - 0.4:
            hits.append(z)
    return hits


def groove_mid_z(mesh):
    hits = _groove_zs(mesh)
    if len(hits) < 3:
        return None
    return float(np.median(hits))


def braid_off(mesh, expected=GROOVE_MID_Z, tol=GROOVE_TOL):
    mid = groove_mid_z(mesh)
    if mid is None:
        return True
    return abs(mid - expected) > tol


def _contains_all(mesh, points):
    try:
        inside = mesh.contains(points)
    except Exception:
        return None
    return bool(np.all(inside))


def axis_open(mesh, points):
    got = _contains_all(mesh, points)
    if got is None:
        return True
    return not got


def min_wall_fails(mesh, radius_mm):
    """A layer splits, or erodes away, after a disk of radius_mm is removed.

    radius_mm is half the minimum wall. A neck thinner than the wall
    increases the component count. A sheet thinner than the wall disappears.
    """
    masks = layer_masks(mesh)
    r = max(1, int(round(radius_mm / PITCH)))
    yy, xx = np.ogrid[-r:r + 1, -r:r + 1]
    foot = (xx * xx + yy * yy) <= r * r
    for mask in masks:
        labeled, n0 = ndimage.label(mask)
        keep = 0
        solid = np.zeros_like(mask)
        for i in range(1, n0 + 1):
            comp = labeled == i
            if float(comp.sum()) * PITCH * PITCH < AREA_FLOOR:
                continue
            keep += 1
            solid |= comp
        if keep == 0:
            continue
        eroded = ndimage.binary_erosion(solid, structure=foot)
        _, n1 = ndimage.label(eroded)
        if n1 == 0 or n1 > keep:
            return True
    return False


def _apply_probe(mesh, probe):
    kind = probe["kind"]
    if kind == "thin_joint":
        return thin_joint_split(mesh, z=probe.get("z"), radius_mm=float(probe.get("radius_mm", MIN_WALL_MM / 2)))
    if kind == "braid_plane":
        return braid_off(
            mesh,
            expected=float(probe.get("expected", GROOVE_MID_Z)),
            tol=float(probe.get("tol", GROOVE_TOL)),
        )
    if kind == "solid":
        got = _contains_all(mesh, probe["points"])
        return got is None or not got
    if kind == "void":
        got = _contains_all(mesh, probe["points"])
        return got is None or got
    raise SystemExit(f"unknown probe kind {kind}")


def production_rules(name, mesh):
    rules = set()
    if RULES.get("overhang", True):
        rules |= overhang_rules(mesh)
    if not RULES.get("min_feature", True):
        rules.discard("min_feature")
    if RULES.get("min_wall", False) and min_wall_fails(mesh, MIN_WALL_MM / 2):
        rules.add("min_wall")
    for probe in PROBES:
        if probe.get("part") != name:
            continue
        if _apply_probe(mesh, probe):
            rules.add(probe["rule"])
    if RULES.get("bed", True):
        zmin = float(mesh.bounds[0][2])
        if abs(zmin) > 0.05:
            rules.add("bed")
    if RULES.get("manifold", True):
        if not mesh.is_watertight or mesh.body_count != 1:
            rules.add("manifold")
    return rules


def fixture_rules(spec, mesh):
    rules = set()
    run = set(spec.get("run") or [])
    if "overhang" in run:
        found = overhang_rules(mesh)
        if not RULES.get("min_feature", True):
            found.discard("min_feature")
        rules |= found
    if "thin_joint" in run or "min_wall" in run:
        radius = float(spec.get("radius_mm", MIN_WALL_MM / 2))
        if thin_joint_split(mesh, z=spec.get("z"), radius_mm=radius):
            rules.add(spec["expect"])
    if "braid_plane" in run and braid_off(
        mesh,
        expected=float(spec.get("expected", GROOVE_MID_Z)),
        tol=float(spec.get("tol", GROOVE_TOL)),
    ):
        rules.add(spec["expect"])
    if "axis_open" in run and axis_open(mesh, spec.get("points") or [[0, 0, 17.5]]):
        rules.add(spec["expect"])
    if "assembly" in run:
        mod = _load_assembly()
        if mod is not None:
            rules.update(mod.fixture_faults(spec["file"], mesh))
    return rules


_ASSEMBLY = None


def _load_assembly():
    """The project's installed-assembly check, if print_check.yaml names one."""
    global _ASSEMBLY
    if _ASSEMBLY is not None:
        return _ASSEMBLY
    rel = (CONFIG or {}).get("assembly_check")
    if not rel:
        return None
    path = (CAD / rel).resolve()
    spec = importlib.util.spec_from_file_location("project_assembly_check", path)
    if spec is None or spec.loader is None:
        raise SystemExit(f"cannot load assembly check {path}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    _ASSEMBLY = mod
    return mod


def _gcode_unsupported(text):
    """Extrusion samples with nothing under them within one 45° layer."""
    relative = True
    last_e = 0.0
    x = y = None
    layer_pts = []
    current = []
    pitch = 0.4
    for raw in text.splitlines():
        line = raw.strip()
        if line.startswith(";LAYER") or line.startswith("; LAYER_CHANGE") or line.startswith(";LAYER_CHANGE"):
            if current:
                layer_pts.append(current)
            current = []
            continue
        if line.startswith("M83"):
            relative = True
            continue
        if line.startswith("M82"):
            relative = False
            continue
        if not line.startswith("G1") and not line.startswith("G0"):
            continue
        vals = dict(re.findall(r"([XYZEF])(-?\d*\.?\d+)", line))
        nx = float(vals["X"]) if "X" in vals else x
        ny = float(vals["Y"]) if "Y" in vals else y
        extrude = False
        if "E" in vals:
            e = float(vals["E"])
            if relative:
                extrude = e > 0
            else:
                extrude = e > last_e + 1e-6
                last_e = e
        if extrude and x is not None and nx is not None:
            dist = max(((nx - x) ** 2 + (ny - y) ** 2) ** 0.5, 1e-6)
            steps = max(1, int(dist / pitch))
            for i in range(steps + 1):
                t = i / steps
                current.append((x + t * (nx - x), y + t * (ny - y)))
        x, y = nx, ny
    if current:
        layer_pts.append(current)
    # First layer is on the bed.
    bad = 0
    prev = None
    for pts in layer_pts:
        if not pts:
            continue
        if prev:
            arr = np.asarray(prev)
            samples = pts[::3] or pts
            for px, py in samples:
                d2 = (arr[:, 0] - px) ** 2 + (arr[:, 1] - py) ** 2
                if d2.min() > (SUPPORT_R + 0.25) ** 2:
                    bad += 1
                    if bad > 40:
                        return True
        prev = pts
    return False


def find_profiles(root):
    """Preset JSON files. Defaults are the OrcaSlicer 2.3.2 Bambu X1C set."""
    root = Path(root)
    machine = process = filament = None
    for path in root.rglob("*.json"):
        name = path.name
        if name == f"{MACHINE_NAME}.json" and "machine" in str(path).lower():
            machine = path
        elif name == f"{PROCESS_NAME}.json" and "process" in str(path).lower():
            process = path
        elif name == f"{FILAMENT_NAME}.json":
            filament = path
    return machine, process, filament


def _vendor_root(path):
    """resources/profiles/<vendor>, so inherits do not jump to another vendor.

    fdm_machine_common.json exists for dozens of vendors. The first hit in a
    full-tree walk depends on directory order. Another vendor's Klipper
    machine was the first hit, and that preset pushed the coupon's
    extrusion off the bed.
    """
    parts = Path(path).parts
    if "profiles" in parts:
        index = parts.index("profiles")
        return Path(*parts[: index + 2])
    return Path(path).parent


def _preset_named(vendor_root, name):
    hits = sorted(Path(vendor_root).rglob(name + ".json"))
    return hits[0] if hits else None


def _merge_nil(parent, child):
    """Orca replaces a 'nil' slot with the parent value when the lists match."""
    if not isinstance(parent, list) or len(parent) != len(child):
        return child
    return [parent[i] if item == "nil" else item for i, item in enumerate(child)]


def resolve_preset(path, search_root):
    """Walk `inherits` and return one JSON object Orca can load on its own.

    The X1C 0.4 nozzle stub lists two extruder variants (Standard and High
    Flow) against a single nozzle_diameter. Orca 2.3.2 then calls
    update_values_to_printer_extruders_for_multiple_filaments and
    dereferences a missing filament_map. Collapsing the variant list keeps
    that path off. PETG's cool-plate temperature is 0, so the bed has to
    be the textured PEI plate or the CLI exits before slicing.
    """
    chain = []
    seen = set()
    current = Path(path)
    vendor = _vendor_root(current)
    while True:
        data = json.loads(current.read_text())
        chain.append(data)
        parent = data.get("inherits")
        if not isinstance(parent, str) or not parent or parent in seen:
            break
        seen.add(parent)
        nxt = _preset_named(vendor, parent)
        if nxt is None:
            break
        current = nxt
    merged = {}
    for data in reversed(chain):
        for key, value in data.items():
            if key == "inherits":
                continue
            if isinstance(value, list) and isinstance(merged.get(key), list):
                merged[key] = _merge_nil(merged[key], value)
            else:
                merged[key] = value
    variants = merged.get("extruder_variant_list") or []
    if variants:
        first = str(variants[0]).split(",")[0].strip()
        merged["extruder_variant_list"] = [first]
    if merged.get("type") == "machine":
        merged["curr_bed_type"] = "Textured PEI Plate"
        merged["default_bed_type"] = "Textured PEI Plate"
    return merged


BAMBU = ""
BAMBU_MACHINE = "Bambu Lab X1 Carbon 0.4 nozzle"
BAMBU_PROCESS = "0.20mm Standard @BBL X1C"
BAMBU_FILAMENT = "Bambu PETG Basic @BBL X1C"


def _bambu_preset(profile_root, name, cache):
    """Walk Bambu Studio's inherits chain and return one JSON object."""
    if name in cache:
        return cache[name]
    if not cache.get("_index"):
        index = {}
        for path in Path(profile_root).rglob("*.json"):
            try:
                data = json.loads(path.read_text())
            except (OSError, json.JSONDecodeError):
                continue
            label = data.get("name")
            if isinstance(label, str):
                index.setdefault(label, data)
        cache["_index"] = index
    index = cache["_index"]
    chain = []
    seen = set()
    current = name
    while current and current not in seen:
        seen.add(current)
        data = index.get(current)
        if data is None:
            raise SystemExit(f"bambu preset {current} not found under {profile_root}")
        chain.append(data)
        parent = data.get("inherits")
        current = parent if isinstance(parent, str) and parent else None
    merged = {}
    for data in reversed(chain):
        for key, value in data.items():
            if key != "inherits":
                merged[key] = value
    variants = merged.get("extruder_variant_list") or []
    if variants:
        merged["extruder_variant_list"] = [str(variants[0]).split(",")[0].strip()]
    cache[name] = merged
    return merged


def _parse_project_plates(path):
    """Object names and per-plate instance counts from a 3MF."""
    with zipfile.ZipFile(path) as zf:
        names = zf.namelist()
        if "Metadata/model_settings.config" not in names:
            return {"names": [], "model_names": [], "plates": []}
        cfg = zf.read("Metadata/model_settings.config").decode("utf-8", "replace")
        model = ""
        if "3D/3dmodel.model" in names:
            model = zf.read("3D/3dmodel.model").decode("utf-8", "replace")
    object_names = []
    for block in re.findall(r"<object id=\"\d+\">(.*?)</object>", cfg, re.S):
        head = block.split("<part", 1)[0]
        match = re.search(r'<metadata key="name" value="([^"]*)"', head)
        object_names.append(match.group(1) if match else "")
    plates = []
    for block in cfg.split("<plate>")[1:]:
        plates.append(len(re.findall(r"<model_instance>", block)))
    model_names = re.findall(r'<object\s+id="\d+"[^>]*\bname="([^"]*)"', model)
    return {"names": object_names, "model_names": model_names, "plates": plates}


def _names_faulty(names):
    if not names:
        return True
    if any(not str(name).strip() for name in names):
        return True
    return len(names) != len(set(names))


def bambu_open(model, out_3mf):
    """Load a 3MF in Bambu Studio on the X1C bed and re-export it.

    --arrange 0 keeps the file's plate positions. The returned dict is
    what Bambu wrote back: one entry per plate, and the object names it
    listed in model_settings.
    """
    app = Path(BAMBU)
    profile_root = app.resolve().parent.parent / "resources" / "profiles"
    out_3mf = Path(out_3mf)
    out_3mf.parent.mkdir(parents=True, exist_ok=True)
    profile_dir = out_3mf.parent / "bambu-profiles"
    profile_dir.mkdir(parents=True, exist_ok=True)
    cache = {}
    written = {}
    for kind, preset in (
        ("machine", BAMBU_MACHINE),
        ("process", BAMBU_PROCESS),
        ("filament", BAMBU_FILAMENT),
    ):
        dest = profile_dir / f"{kind}.json"
        dest.write_text(json.dumps(_bambu_preset(profile_root, preset, cache)))
        written[kind] = dest
    if out_3mf.exists():
        out_3mf.unlink()
    data_dir = profile_dir / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    cmd = [
        str(app),
        "--datadir", str(data_dir),
        "--arrange", "0",
        "--load-settings", f"{written['machine']};{written['process']}",
        "--load-filaments", str(written["filament"]),
        "--export-3mf", str(out_3mf),
        str(model),
    ]
    env = os.environ.copy()
    env["LD_LIBRARY_PATH"] = str(app.resolve().parent)
    env["QT_QPA_PLATFORM"] = "offscreen"
    env["LIBGL_ALWAYS_SOFTWARE"] = "1"
    runtime = out_3mf.parent / "xdg"
    runtime.mkdir(parents=True, exist_ok=True)
    env["XDG_RUNTIME_DIR"] = str(runtime)
    try:
        proc = subprocess.run(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, env=env, timeout=180, check=False,
        )
    except subprocess.TimeoutExpired:
        return None
    if proc.returncode != 0 or not out_3mf.exists():
        log = out_3mf.with_suffix(".bambu.log")
        log.write_text(proc.stdout or "")
        return None
    return _parse_project_plates(out_3mf)


def bambu_faults(model, expect_plates=None):
    """Faults Bambu Studio reports for one 3MF.

    expect_plates is the plate count the caller requires. Production
    compares Bambu's plates to the plates declared in the file. A name
    Bambu lists has to be non-empty and unique, and the file's own
    object name attributes have to be the same.
    """
    source = _parse_project_plates(model)
    faults = set()
    if _names_faulty(source["names"]) or _names_faulty(source["model_names"]):
        faults.add("blank_name")
    if sorted(source["names"]) != sorted(source["model_names"]):
        faults.add("blank_name")
    opened = bambu_open(model, Path("/tmp") / "bambu-open" / (Path(model).stem + ".3mf"))
    if opened is None:
        faults.add("bambu_exit")
        return faults
    if _names_faulty(opened["names"]) or sorted(opened["names"]) != sorted(source["names"]):
        faults.add("blank_name")
    wanted = expect_plates
    if wanted is None:
        wanted_plates = source["plates"]
    else:
        wanted_plates = None
    if wanted is not None:
        if len(opened["plates"]) != int(wanted):
            if len(opened["plates"]) == 1 and int(wanted) > 1:
                faults.add("one_plate")
            else:
                faults.add("plate_count")
    elif opened["plates"] != wanted_plates:
        if len(opened["plates"]) == 1 and len(wanted_plates) > 1:
            faults.add("one_plate")
        else:
            faults.add("plate_count")
    return faults


def _project_plate_count(model):
    """How many <plate> entries the source 3MF declared. STL inputs have none."""
    path = Path(model)
    if path.suffix.lower() != ".3mf":
        return 0
    try:
        with zipfile.ZipFile(path) as zf:
            if "Metadata/model_settings.config" not in zf.namelist():
                return 0
            text = zf.read("Metadata/model_settings.config").decode("utf-8", "replace")
    except (OSError, zipfile.BadZipFile):
        return 0
    return text.count("<plate>")


def slice_model(slicer, model, log_path, out_3mf, check_toolpath=True):
    app = Path(slicer)
    root = app.parent
    # AppRun lives beside squashfs-root or inside it.
    search = root if (root / "resources").exists() else root.parent
    machine, process, filament = find_profiles(search)
    if not all([machine, process, filament]):
        # One more walk from /opt/orcaslicer.
        machine, process, filament = find_profiles(Path("/opt/orcaslicer"))
        search = Path("/opt/orcaslicer")
    if not all([machine, process, filament]):
        log_path.write_text("slicer profiles not found\n")
        return False, "slicer_profile"
    try:
        profile_dir = log_path.parent / "orca-profiles"
        profile_dir.mkdir(parents=True, exist_ok=True)
        written = {}
        for kind, src in (("machine", machine), ("process", process), ("filament", filament)):
            dest = profile_dir / f"{kind}.json"
            dest.write_text(json.dumps(resolve_preset(src, search)))
            written[kind] = dest
    except (OSError, json.JSONDecodeError) as exc:
        log_path.write_text(f"slicer profile resolve failed: {exc}\n")
        return False, "slicer_profile"
    machine, process, filament = written["machine"], written["process"], written["filament"]
    # A third JSON without "type" makes --load-settings reject the whole
    # set. Supports and layer height are real settings, passed as flags.
    # --flag 0 is parsed as a filename; the value has to be --flag=0.
    cmd = [
        str(app),
        "--slice", "0",
        "--export-3mf", str(out_3mf),
        "--load-settings", f"{machine};{process}",
        "--load-filaments", str(filament),
        "--enable-support=0",
        "--layer-height=0.2",
        "--allow-newer-file",
        "--debug=2",
        str(model),
    ]
    # A loose STL has to be arranged onto the bed. A project already has
    # plate positions; the default arrange stacks those three plates onto
    # one bed. The BambuStudio application tag also skips arrange. The
    # flag stays so a file without that tag still slices on its plates.
    if Path(model).suffix.lower() == ".3mf":
        cmd.insert(cmd.index("--allow-newer-file"), "--arrange=0")
    env = os.environ.copy()
    env["QT_QPA_PLATFORM"] = "offscreen"
    env["LIBGL_ALWAYS_SOFTWARE"] = "1"
    # actions/setup-python points this at its own lib directory. The
    # AppImage then loads those libraries and exits before slicing.
    env.pop("LD_LIBRARY_PATH", None)
    try:
        proc = subprocess.run(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, env=env, timeout=900, check=False,
        )
    except subprocess.TimeoutExpired as exc:
        log_path.write_text((exc.stdout or "") + "\nTIMEOUT\n")
        return False, "slicer_timeout"
    log_path.write_text(proc.stdout or "")
    if proc.returncode != 0:
        return False, "slicer_exit"
    low = (proc.stdout or "").lower()
    for phrase in SLICER_FAIL:
        if phrase in low:
            return False, "slicer_" + phrase.split()[0]
    if not out_3mf.exists():
        return False, "slicer_no_output"
    gcode = []
    with zipfile.ZipFile(out_3mf) as zf:
        for info in zf.infolist():
            if info.filename.endswith(".gcode"):
                gcode.append(zf.read(info.filename).decode("utf-8", "replace"))
    if not gcode:
        return False, "slicer_no_gcode"
    expected_plates = _project_plate_count(model)
    if expected_plates and len(gcode) < expected_plates:
        return False, "slicer_plates"
    # The arranged project is not put through the toolpath test. Parts
    # this pull request did not change may still have bridges the
    # per-STL gate owns.
    if check_toolpath and any(_gcode_unsupported(text) for text in gcode):
        return False, "unsupported_extrusion"
    return True, ""


def plate_estimates(gcode_3mf):
    rows = []
    with zipfile.ZipFile(gcode_3mf) as zf:
        names = sorted(n for n in zf.namelist() if n.endswith(".gcode"))
        for index, name in enumerate(names, start=1):
            text = zf.read(name).decode("utf-8", "replace")
            time = _first(text, r"total estimated time:\s*([^\n;]+)", r"estimated printing time.*?=\s*([^\n]+)")
            grams = _first(text, r"total filament weight \[g\]\s*[:=]\s*([0-9.]+)", r"filament used \[g\]\s*=\s*([0-9.]+)")
            rows.append((index, (time or "?").strip(), (grams or "?").strip()))
    return rows


def _first(text, *patterns):
    for pattern in patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            return match.group(1)
    return None


def write_fixtures():
    """Known-bad parts. Each one must fail the rule named in print_check.yaml."""
    FIXTURE_DIR.mkdir(parents=True, exist_ok=True)

    def box(extents, translate):
        mesh = trimesh.creation.box(extents=extents)
        mesh.apply_translation(translate)
        return mesh

    def save(name, mesh):
        path = FIXTURE_DIR / name
        mesh.export(path)
        return path

    base = box([20, 20, 2], [0, 0, 1])
    island = box([6, 6, 4], [0, 0, 10])
    save("floating_island.stl", trimesh.util.concatenate([base, island]))

    plate = box([24, 24, 2], [0, 0, 1])
    boss = trimesh.creation.cylinder(radius=3, height=8, sections=24)
    boss.apply_translation([0, 0, 8])
    save("boss_spaghetti.stl", trimesh.util.concatenate([plate, boss]))

    pad = box([20, 16, 2], [0, 0, 1])
    flap = box([8, 0.4, 8], [0, 0, 6])
    save("fragile_flap.stl", trimesh.util.concatenate([pad, flap]))

    wall = box([4, 12, 10], [0, 0, 5])
    ledge = box([10, 8, 2], [7, 0, 9])
    save("cantilever.stl", trimesh.util.concatenate([wall, ledge]))

    left = box([8, 8, 8], [-6, 0, 4])
    right = box([8, 8, 8], [6, 0, 4])
    neck = box([4, 0.3, 4], [0, 0, 4])
    save("thin_wall.stl", trimesh.util.concatenate([left, right, neck]))

    drum = trimesh.creation.cylinder(radius=10, height=12, sections=48)
    drum.apply_translation([0, 0, 6])
    # Proud of the wall, flat underside.
    nub = box([1.6, 0.8, 0.8], [10.2, 0, 6])
    save("proud_nub.stl", trimesh.util.concatenate([drum, nub]))

    if any(str(spec.get("file", "")).endswith(".3mf") for spec in FIXTURES):
        builder = Path(__file__).resolve().parent / "build_print_project.py"
        spec = importlib.util.spec_from_file_location("build_print_project", builder)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        mod.write_known_bad_projects(FIXTURE_DIR)


def _row(name, ok, rule):
    return f"| `{name}` | {'PASS' if ok else 'FAIL'} | {rule or ''} |"


def check_geometry(names, log_dir):
    rows = []
    failed = False
    for name in names:
        mesh = trimesh.load(STL_DIR / name, force="mesh")
        rules = production_rules(name, mesh)
        ok = not rules
        failed = failed or not ok
        rule = ",".join(sorted(rules))
        rows.append(_row(name, ok, rule))
        if log_dir:
            (log_dir / f"{name}.rules.txt").write_text(rule + "\n")
        print(f"{name:24} {'PASS' if ok else 'FAIL'} {rule}")
    return rows, failed


def check_fixtures():
    rows = []
    failed = False
    if not FIXTURES:
        print("FAIL no fixtures configured")
        return [_row("fixtures", False, "none configured")], True
    for spec in FIXTURES:
        name = spec["file"]
        expect = spec["expect"]
        path = FIXTURE_DIR / name
        if not path.exists():
            failed = True
            rows.append(_row("fixture/" + name, False, "missing"))
            print(f"fixture {name:22} FAIL missing {path}")
            continue
        if "bambu" in set(spec.get("run") or []):
            if not BAMBU:
                failed = True
                rows.append(_row("fixture/" + name, False, "bambu missing"))
                print(f"fixture {name:22} FAIL bambu studio not configured")
                continue
            rules = bambu_faults(path, expect_plates=spec.get("plates"))
            ok = expect in rules and "bambu_exit" not in rules
            failed = failed or not ok
            rule = ",".join(sorted(rules)) or "(no rule fired)"
            rows.append(_row("fixture/" + name, ok, f"wants {expect}; saw {rule}"))
            print(f"fixture {name:22} {'PASS' if ok else 'FAIL'} wants {expect} saw {rule}")
            continue
        mesh = trimesh.load(path, force="mesh")
        rules = fixture_rules(spec, mesh)
        ok = expect in rules
        failed = failed or not ok
        rule = ",".join(sorted(rules)) or "(no rule fired)"
        rows.append(_row("fixture/" + name, ok, f"wants {expect}; saw {rule}"))
        print(f"fixture {name:22} {'PASS' if ok else 'FAIL'} wants {expect} saw {rule}")
    return rows, failed


def self_test_angles():
    """A 45° frustum must pass. A 70° frustum must fail overhang_45."""
    def frustum(radius0, radius1, height, sections=64):
        verts = []
        faces = []
        for i, radius in ((0, radius0), (1, radius1)):
            z = 0 if i == 0 else height
            for k in range(sections):
                a = 2 * np.pi * k / sections
                verts.append([radius * np.cos(a), radius * np.sin(a), z])
        top0 = len(verts)
        verts.append([0, 0, 0])
        verts.append([0, 0, height])
        verts = np.array(verts)
        for k in range(sections):
            n = (k + 1) % sections
            faces.append([k, n, sections + n])
            faces.append([k, sections + n, sections + k])
            faces.append([k, n, top0])
            faces.append([sections + n, sections + k, top0 + 1])
        mesh = trimesh.Trimesh(vertices=verts, faces=np.array(faces), process=True)
        return mesh

    mesh45 = frustum(5, 15, 10)
    mesh70 = frustum(5, 5 + 4 * np.tan(np.deg2rad(70)), 4)

    def solid(mesh):
        return any(float(mask.sum()) * PITCH * PITCH > AREA_FLOOR for mask in layer_masks(mesh))

    # A blind raster (empty sections) must not count as a pass.
    ok45 = solid(mesh45) and "overhang_45" not in overhang_rules(mesh45)
    bad70 = solid(mesh70) and "overhang_45" in overhang_rules(mesh70)
    print(f"self-test 45° frustum {'PASS' if ok45 else 'FAIL'}")
    print(f"self-test 70° frustum {'PASS' if bad70 else 'FAIL'}")
    return ok45 and bad70


def _feature_from(raw):
    raw = raw or {}
    max_t = float(raw.get("max_thickness_mm", 0.60))
    min_t = float(raw.get("min_thickness_mm", 0.24))
    return {
        "max_radius": max_t / 2.0,
        "min_radius": min_t / 2.0,
        "min_cells": int(round(float(raw.get("min_length_mm", 1.2)) / PITCH)),
        "min_layers": int(round(float(raw.get("min_height_mm", 1.6)) / LAYER)),
    }


def load_config(project):
    path = Path(project) / "print_check.yaml"
    if not path.exists():
        raise SystemExit(f"missing {path}")
    return yaml.safe_load(path.read_text()) or {}


def apply_config(project, cfg, machine="", process="", filament=""):
    global CAD, STL_DIR, FIXTURE_DIR, FEATURE, MIN_WALL_MM, RULES
    global DEPS, FIXTURES, PROBES, CONFIG
    global MACHINE_NAME, PROCESS_NAME, FILAMENT_NAME
    CAD = Path(project).resolve()
    STL_DIR = CAD / "stl"
    FIXTURE_DIR = CAD / cfg.get("fixtures_dir", "tools/fixtures")
    FEATURE = _feature_from(cfg.get("min_feature"))
    MIN_WALL_MM = float(cfg.get("min_wall_mm", 1.6))
    rules = cfg.get("rules") or {}
    RULES = {
        "overhang": bool(rules.get("overhang", True)),
        "min_feature": bool(rules.get("min_feature", True)),
        "min_wall": bool(rules.get("min_wall", False)),
        "bed": bool(rules.get("bed", True)),
        "manifold": bool(rules.get("manifold", True)),
    }
    DEPS = cfg.get("deps") or {}
    FIXTURES = list(cfg.get("fixtures") or [])
    PROBES = list(cfg.get("probes") or [])
    CONFIG = cfg
    if machine:
        MACHINE_NAME = machine
    if process:
        PROCESS_NAME = process
    if filament:
        FILAMENT_NAME = filament


def decide_scope(cfg, changed_text, dispatch_full):
    """Return (full, rebuild_project). A dispatch full run checks every STL."""
    if dispatch_full:
        return True, True
    full = re.search(cfg.get("full_regex") or r"(?!)", changed_text, re.M) is not None
    rebuild = re.search(cfg.get("project_regex") or r"(?!)", changed_text, re.M) is not None
    return full, rebuild


def _github_output(key, value):
    path = os.environ.get("GITHUB_OUTPUT")
    line = f"{key}={value}\n"
    if path:
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(line)
    else:
        print(line, end="")


def tracked_paths(project, cfg):
    root = Path(project).resolve()
    return [str((root / rel).resolve()) for rel in (cfg.get("tracked") or [])]


def render_summary(project, log_dir, title):
    log_dir = Path(log_dir)
    lines = [f"## {title}", ""]
    summary = log_dir / "summary.md"
    if summary.exists():
        lines.append(summary.read_text().rstrip())
        lines.append("")
    if CONFIG.get("show_plates", True):
        lines.append("### Plates")
        lines.append("")
        proc = subprocess.run(
            [sys.executable, str(Path(__file__).resolve().parent / "build_print_project.py"),
             "--project", str(project), "--print-layout"],
            check=False, text=True, capture_output=True,
        )
        if proc.returncode != 0:
            sys.stderr.write(proc.stderr or proc.stdout)
            raise SystemExit(proc.returncode or 1)
        lines.append(proc.stdout.rstrip())
        lines.append("")
    for section in CONFIG.get("verify_projects") or []:
        path = log_dir / section["summary"]
        if not path.exists():
            continue
        lines.append(f"### {section['heading']}")
        lines.append("")
        lines.append(path.read_text().rstrip())
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def markdown(rows, plate_rows):
    lines = [
        "| STL | Result | Rule |",
        "|---|---|---|",
        *rows,
    ]
    if plate_rows:
        lines += [
            "",
            "| Plate | Time | Filament g |",
            "|---|---|---|",
        ]
        for index, time, grams in plate_rows:
            lines.append(f"| {index} | {time} | {grams} |")
    return "\n".join(lines) + "\n"


def _needs_project(args):
    return not (args.self_test_only and not args.project)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", default="", help="CAD directory (the one with print_check.yaml)")
    parser.add_argument("--changed-file")
    parser.add_argument("--full", action="store_true")
    parser.add_argument("--only", nargs="*")
    parser.add_argument("--slicer", default="")
    parser.add_argument("--bambu", default="", help="Bambu Studio binary (bambu-studio)")
    parser.add_argument("--skip-slicer", action="store_true")
    parser.add_argument("--log-dir", default="")
    parser.add_argument("--summary", default="")
    parser.add_argument("--no-fixtures", action="store_true")
    parser.add_argument("--fixtures-only", action="store_true")
    parser.add_argument("--verify-project", default="")
    parser.add_argument("--write-fixtures", action="store_true")
    parser.add_argument("--machine", default="", help="Orca machine preset name, without .json")
    parser.add_argument("--process", default="", help="Orca process preset name, without .json")
    parser.add_argument("--filament", default="", help="Orca filament preset name, without .json")
    parser.add_argument("--plan", action="store_true", help="Write full= and project= for GitHub Actions")
    parser.add_argument("--dispatch-full", action="store_true")
    parser.add_argument("--list-tracked", action="store_true")
    parser.add_argument("--list-verify", action="store_true")
    parser.add_argument("--render-summary", action="store_true")
    parser.add_argument("--title", default="Print gate")
    parser.add_argument("--self-test-only", action="store_true")
    args = parser.parse_args()

    global BAMBU, BAMBU_MACHINE, BAMBU_PROCESS, BAMBU_FILAMENT
    BAMBU = args.bambu
    if args.machine:
        BAMBU_MACHINE = args.machine
    if args.process:
        BAMBU_PROCESS = args.process
    if args.filament:
        BAMBU_FILAMENT = args.filament

    project = args.project
    if not project and Path("print_check.yaml").exists():
        project = "."
    if project:
        apply_config(project, load_config(project), args.machine, args.process, args.filament)
    elif not args.self_test_only:
        raise SystemExit("pass --project (a directory containing print_check.yaml)")

    if args.plan:
        changed = Path(args.changed_file).read_text() if args.changed_file else ""
        full, rebuild = decide_scope(CONFIG, changed, args.dispatch_full)
        _github_output("full", "1" if full else "0")
        _github_output("project", "1" if rebuild else "0")
        print(f"mode={'full' if full else 'changed-only'} rebuild={'1' if rebuild else '0'}")
        return 0

    if args.list_tracked:
        for path in tracked_paths(CAD, CONFIG):
            print(path)
        return 0

    if args.list_verify:
        for item in CONFIG.get("verify_projects") or []:
            print(f"{(CAD / item['path']).resolve()}\t{item['summary']}")
        return 0

    if args.render_summary:
        if not args.log_dir:
            raise SystemExit("--render-summary needs --log-dir")
        sys.stdout.write(render_summary(CAD, args.log_dir, args.title))
        return 0

    if args.write_fixtures:
        write_fixtures()
        print(f"wrote fixtures in {FIXTURE_DIR}")
        return 0

    if args.self_test_only:
        return 0 if self_test_angles() else 1

    log_dir = Path(args.log_dir) if args.log_dir else None
    if log_dir:
        log_dir.mkdir(parents=True, exist_ok=True)

    if not self_test_angles():
        print("FAIL angle self-test")
        return 1

    rows = []
    failed = False
    if not args.no_fixtures:
        f_rows, f_fail = check_fixtures()
        rows.extend(f_rows)
        failed = failed or f_fail
    if args.fixtures_only:
        text = markdown(rows, [])
        if args.summary:
            Path(args.summary).write_text(text)
        return 1 if failed else 0

    if args.only:
        names = args.only
    elif args.changed_file:
        names = select_stls(Path(args.changed_file).read_text(), args.full)
    elif args.full:
        names = all_stls()
    else:
        names = []

    g_rows, g_fail = check_geometry(names, log_dir)
    rows.extend(g_rows)
    failed = failed or g_fail

    # The project slice (--verify-project) only checks that a 3MF opens.
    # An installed-assembly check is geometry, so it runs with the STL pass.
    if not args.verify_project and CONFIG.get("assembly_check"):
        ok, detail = _load_assembly().check_production()
        rows.append(_row("assembly", ok, detail))
        failed = failed or not ok
        print(f"{'assembly':24} {'PASS' if ok else 'FAIL'} {detail}")

    plate_rows = []
    if args.slicer and not args.skip_slicer:
        for name in names:
            out = (log_dir or Path("/tmp")) / f"{name}.gcode.3mf"
            log = (log_dir or Path("/tmp")) / f"{name}.slicer.log"
            ok, rule = slice_model(args.slicer, STL_DIR / name, log, out, check_toolpath=True)
            if not ok:
                failed = True
                rows.append(_row(name + " slice", False, rule))
                print(f"{name:24} SLICER FAIL {rule}")
            else:
                rows.append(_row(name + " slice", True, ""))
                print(f"{name:24} SLICER PASS")
        if args.verify_project:
            model = Path(args.verify_project)
            stem = model.stem
            out = (log_dir or Path("/tmp")) / f"{stem}.gcode.3mf"
            log = (log_dir or Path("/tmp")) / f"{stem}.slicer.log"
            ok, rule = slice_model(args.slicer, model, log, out, check_toolpath=False)
            if not ok:
                failed = True
                rows.append(_row(model.name, False, rule))
                print(f"project SLICER FAIL {rule}")
            else:
                rows.append(_row(model.name, True, ""))
                plate_rows = plate_estimates(out)
                for index, time, grams in plate_rows:
                    print(f"plate {index}: {time}, {grams} g")
            if args.bambu:
                faults = bambu_faults(model)
                ok = not faults
                failed = failed or not ok
                detail = ",".join(sorted(faults))
                rows.append(_row(model.name + " bambu", ok, detail))
                print(f"{model.name + ' bambu':24} {'PASS' if ok else 'FAIL'} {detail}")

    text = markdown(rows, plate_rows)
    if args.summary:
        Path(args.summary).write_text(text)
    elif log_dir:
        (log_dir / "summary.md").write_text(text)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
