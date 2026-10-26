#!/usr/bin/env python3
"""Build the Bambu/Orca print projects from a CAD project's print/plates.yaml.

Layer 1 is one STL per part. This writes layer 2: a 3MF with one plate
per settings group, quantities from the yaml, and per-object walls,
infill, and brim. Supports are off. Objects stay in their STL orientation
(bed at z = 0). Positions are a shelf pack on the bed named in the yaml
(256 mm for the X1C), clear of the front-left purge strip, with a gap so
brims do not collide.

The OrcaSlicer CLI cannot attach a different wall count to each object
in one invocation, so the per-object values are written into Bambu's
model_settings.config. The workflow re-slices the 3MF to confirm it opens.

Run: python3 print-gate/build_print_project.py --project path/to/cad

Bambu Studio only treats a file as its own project when the model
carries Application = BambuStudio-x.y. Without that tag it splits
repeated instances into extra objects and leaves those copies unnamed.
Each copy is therefore its own object, named "bracket 1" or
"clip 01", in both the mesh object and model_settings.

Once that tag is present, Bambu loads Metadata/project_settings.config
as the printer config and dereferences keys such as printable_height.
A short config crashes the CLI. The file is therefore the resolved X1C
preset in bambu_x1c_project_settings.json, with the bed, filament, and
the few values Orca 2.3.2 rejects written over the top.

Orca and Bambu read a 3MF transform as 12 column-major numbers
(1 0 0 0 1 0 0 0 1 tx ty tz). The mesh is stored already centred on the
origin and tx/ty/tz are that centre in world space, including the plate
origin. Bambu recentres on load, which leaves a centred mesh where it
is. Orca keeps the translation once the file is tagged BambuStudio, so
a corner-origin mesh with a centre translation would float. Plates are
a grid: columns = round(sqrt(count)), stride = 1.2 × the bed, and the
next row is in −Y. Positions in layout.json stay in each plate's own
corner frame.

Orca 2.3.2's CLI segfaults when plater_name is non-empty. The key is
written with an empty value. Plate titles stay in layout.json and in
Metadata/plate_N.json.
"""
import argparse
import json
import zipfile
from pathlib import Path

import numpy as np
import trimesh
import yaml

CAD = None
STL_DIR = None
PRINT = None
PROFILES = {}
CORE = "http://schemas.microsoft.com/3dmanufacturing/core/2015/02"
REL_NS = "http://schemas.openxmlformats.org/package/2006/relationships"
CT_NS = "http://schemas.openxmlformats.org/package/2006/content-types"

# X1C prime line sits in this corner. Parts start outside it.
PURGE_X = 20.0
PURGE_Y = 32.0

# The tag Bambu Studio checks before it will honour <plate> blocks.
BAMBU_VERSION = "02.08.02.61"


def instance_name(stl, index, qty):
    """One name per copy. Two copies are 'part 1' and 'part 2'. Ten or
    more are zero-padded so 'clip 01' sorts up to 'clip 20'."""
    stem = Path(stl).stem
    if qty <= 1:
        return stem
    return f"{stem} {index + 1:0{len(str(qty))}d}"


def _png(width, height, rgba):
    """A tiny RGBA PNG. Plate thumbnails have to exist; Bambu lists a
    plate from the file even when the picture is a flat swatch."""
    import struct
    import zlib

    def chunk(tag, data):
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)

    # Filter byte, then width pixels. A short row is not a valid PNG.
    raw = (b"\x00" + rgba * width) * height
    ihdr = struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0)
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr) + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b"")


PLATE_PNG = _png(16, 16, b"\x6b\x8c\xa8\xff")
PLATE_PNG_SMALL = _png(8, 8, b"\x6b\x8c\xa8\xff")


def load_mesh(name):
    mesh = trimesh.load(STL_DIR / name, force="mesh")
    mesh.merge_vertices()
    return mesh


def pack_plate(items, bed, margin, gap):
    """Shelf-pack footprints. Each item has w, d (already brim-inflated)."""
    x0 = max(margin, PURGE_X)
    y0 = max(margin, PURGE_Y)
    limit_x = bed - margin
    limit_y = bed - margin
    x = x0
    y = y0
    row_h = 0.0
    placed = []
    for item in items:
        w, d = item["w"], item["d"]
        if w > limit_x - x0 or d > limit_y - y0:
            raise SystemExit(f"{item['stl']} ({w:.0f}×{d:.0f} mm) does not fit the bed")
        if x + w > limit_x + 1e-6:
            x = x0
            y += row_h + gap
            row_h = 0.0
        if y + d > limit_y + 1e-6:
            raise SystemExit(
                f"plate overflow at {item['stl']}: y={y:.0f} d={d:.0f} limit={limit_y:.0f}"
            )
        # Footprint includes brim on every side. The mesh corner is inset by brim.
        brim = item["brim"]
        item["x"] = x + brim
        item["y"] = y + brim
        placed.append(item)
        x += w + gap
        row_h = max(row_h, d)
    return placed


def expand(spec, bed, margin, gap):
    items = []
    for part in spec:
        mesh = load_mesh(part["stl"])
        size = mesh.bounds[1] - mesh.bounds[0]
        brim = float(part.get("brim_mm") or 0)
        qty = int(part["qty"])
        for n in range(qty):
            items.append({
                "stl": part["stl"],
                "mesh": mesh,
                "sx": float(size[0]),
                "sy": float(size[1]),
                "sz": float(size[2]),
                "w": float(size[0]) + 2 * brim,
                "d": float(size[1]) + 2 * brim,
                "brim": brim,
                "walls": int(part["walls"]),
                "infill": int(part["infill"]),
                "pattern": part.get("pattern", "gyroid"),
                "instance": n,
                "qty": qty,
            })
    return pack_plate(items, bed, margin, gap)


def mesh_local(mesh):
    """Vertices centred on the origin, so a centre translation sits on the bed.

    Bambu recentres whatever it loads and keeps the build-item translation.
    Orca does not recentre a file tagged BambuStudio, so the mesh has to
    arrive centred. z = 0 in the STL stays the bed; the stored z is shifted
    by half the height and the translation puts that half back.
    """
    v = np.asarray(mesh.vertices, dtype=np.float64).copy()
    size = mesh.bounds[1] - mesh.bounds[0]
    centre = np.array([
        mesh.bounds[0][0] + size[0] / 2,
        mesh.bounds[0][1] + size[1] / 2,
        size[2] / 2,
    ])
    v -= centre
    faces = np.asarray(mesh.faces, dtype=np.int64)
    return v, faces


def xml_escape(text):
    return (str(text).replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;").replace('"', "&quot;"))


def plate_cols(count):
    """Same rule as OrcaSlicer compute_colum_count."""
    value = count ** 0.5
    rounded = round(value)
    if value > rounded:
        return int(rounded) + 1
    return int(rounded)


def plate_origin(index, bed, count):
    """World origin of plate `index` (0-based) on an X1C-sized bed."""
    stride = bed * 1.2  # LOGICAL_PART_PLATE_GAP is 1/5
    cols = plate_cols(count)
    row, col = divmod(index, cols)
    return col * stride, -row * stride


def orca_transform(tx, ty, tz=0.0):
    """Column-major 3×4, which is what Orca and Bambu Studio both parse."""
    return f"1 0 0 0 1 0 0 0 1 {tx:.5f} {ty:.5f} {tz:.5f}"


def placed_center(item, ox, oy):
    """World centre of a mesh whose packed corner is (item x, item y)."""
    return (
        item["x"] + ox + item["sx"] / 2,
        item["y"] + oy + item["sy"] / 2,
        item["sz"] / 2,
    )


def model_xml(objects):
    """objects: list of dicts with id, name, vertices, faces, instances [(tx,ty,tz)]."""
    lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        f'<model unit="millimeter" xml:lang="en-US" xmlns="{CORE}" '
        'xmlns:BambuStudio="http://schemas.bambulab.com/package/2021">',
        f' <metadata name="Application">BambuStudio-{BAMBU_VERSION}</metadata>',
        ' <metadata name="BambuStudio:3mfVersion">1</metadata>',
        " <resources>",
    ]
    for obj in objects:
        lines.append(f'  <object id="{obj["id"]}" name="{xml_escape(obj["name"])}" type="model">')
        lines.append("   <mesh>")
        lines.append("    <vertices>")
        for x, y, z in obj["vertices"]:
            lines.append(f'     <vertex x="{x:.4f}" y="{y:.4f}" z="{z:.4f}"/>')
        lines.append("    </vertices>")
        lines.append("    <triangles>")
        for a, b, c in obj["faces"]:
            lines.append(f'     <triangle v1="{a}" v2="{b}" v3="{c}"/>')
        lines.append("    </triangles>")
        lines.append("   </mesh>")
        lines.append("  </object>")
    lines.append(" </resources>")
    lines.append(" <build>")
    for obj in objects:
        for point in obj["instances"]:
            tx, ty, tz = (point + (0.0,))[:3] if len(point) == 2 else point
            lines.append(
                f'  <item objectid="{obj["id"]}" transform='
                f'"{orca_transform(tx, ty, tz)}" printable="1"/>'
            )
    lines.append(" </build>")
    lines.append("</model>")
    return "\n".join(lines) + "\n"


def model_settings(objects, plates):
    """Per-object walls, infill, and brim, plus which object sits on which plate."""
    lines = ['<?xml version="1.0" encoding="UTF-8"?>', "<config>"]
    for obj in objects:
        brim = obj["brim"]
        brim_type = "outer_only" if brim else "no_brim"
        infill = "100%" if obj["infill"] >= 100 else f"{obj['infill']}%"
        # Keys sit directly on the object. Orca applies those as object
        # config; a nested <config> is not where it looks.
        lines.append(f' <object id="{obj["id"]}">')
        lines.append(f'  <metadata key="name" value="{xml_escape(obj["name"])}"/>')
        lines.append(f'  <metadata key="wall_loops" value="{obj["walls"]}"/>')
        lines.append(f'  <metadata key="sparse_infill_density" value="{infill}"/>')
        lines.append(f'  <metadata key="sparse_infill_pattern" value="{obj["pattern"]}"/>')
        lines.append('  <metadata key="enable_support" value="0"/>')
        lines.append(f'  <metadata key="brim_type" value="{brim_type}"/>')
        lines.append(f'  <metadata key="brim_width" value="{brim:.0f}"/>')
        lines.append('  <metadata key="layer_height" value="0.2"/>')
        if obj.get("top_shells") is not None:
            lines.append(f'  <metadata key="top_shell_layers" value="{int(obj["top_shells"])}"/>')
            lines.append(f'  <metadata key="bottom_shell_layers" value="{int(obj["bottom_shells"])}"/>')
            lines.append('  <metadata key="top_shell_thickness" value="0"/>')
            lines.append('  <metadata key="bottom_shell_thickness" value="0"/>')
        lines.append(f'  <part id="{obj["id"]}" subtype="normal_part">')
        lines.append(f'   <metadata key="name" value="{xml_escape(obj["name"])}"/>')
        lines.append('   <metadata key="matrix" value="1 0 0 0 0 1 0 0 0 0 1 0 0 0 0 1"/>')
        lines.append('   <mesh_stat edges_fixed="0" degenerate_facets="0" facets_removed="0" facets_reversed="0" backwards_edges="0"/>')
        lines.append("  </part>")
        lines.append(" </object>")
    identify = 500
    for plate in plates:
        lines.append(" <plate>")
        lines.append(f'  <metadata key="plater_id" value="{plate["id"]}"/>')
        # Empty on purpose. Orca 2.3.2 CLI calls generate_plate_name_texture
        # for any non-empty value and segfaults because m_plater is null.
        lines.append('  <metadata key="plater_name" value=""/>')
        lines.append('  <metadata key="locked" value="false"/>')
        lines.append('  <metadata key="filament_map_mode" value="Auto"/>')
        lines.append(
            f'  <metadata key="thumbnail_file" value="Metadata/plate_{plate["id"]}.png"/>'
        )
        for obj_id, instance_id in plate["members"]:
            lines.append("  <model_instance>")
            lines.append(f'   <metadata key="object_id" value="{obj_id}"/>')
            lines.append(f'   <metadata key="instance_id" value="{instance_id}"/>')
            lines.append(f'   <metadata key="identify_id" value="{identify}"/>')
            lines.append("  </model_instance>")
            identify += 1
        lines.append(" </plate>")
    lines.append(" <assemble>")
    for obj in objects:
        for instance_id, point in enumerate(obj["center_xy"]):
            tx, ty, tz = (point + (0.0,))[:3] if len(point) == 2 else point
            lines.append(
                f'  <assemble_item object_id="{obj["id"]}" instance_id="{instance_id}" '
                f'transform="{orca_transform(tx, ty, tz)}" offset="0 0 0"/>'
            )
    lines.append(" </assemble>")
    lines.append("</config>")
    return "\n".join(lines) + "\n"


def project_settings():
    """Resolved X1C config Bambu can load, with this project's bed and presets.

    Orca 2.3.2 rejects a filament index of 0 and a negative raft expansion,
    which is what Bambu writes. Those are forced to values Orca will slice.
    """
    template = json.loads(
        (Path(__file__).resolve().parent / "bambu_x1c_project_settings.json").read_text()
    )
    machine = PROFILES.get("machine", "Bambu Lab X1 Carbon 0.4 nozzle")
    process = PROFILES.get("process", "0.20mm Standard @BBL X1C")
    filament = PROFILES.get("filament", "Bambu PETG Basic @BBL X1C")
    template["printer_settings_id"] = machine
    template["print_settings_id"] = process
    template["default_print_profile"] = process
    template["filament_settings_id"] = [filament]
    template["printer_model"] = "Bambu Lab X1 Carbon"
    template["nozzle_diameter"] = ["0.4"]
    template["printer_variant"] = "0.4"
    template["printable_area"] = ["0x0", "256x0", "256x256", "0x256"]
    template["bed_exclude_area"] = ["0x0", "18x0", "18x28", "0x28"]
    # Orca 2.3.2's X1C preset is 250 mm tall. A taller value makes that CLI
    # try to shrink the bed and then crash. Bambu Studio still reads the key.
    template["printable_height"] = "250"
    template["filament_type"] = ["PETG"]
    template["filament_colour"] = ["#FFFFFF"]
    template["enable_support"] = "0"
    template["enable_prime_tower"] = "0"
    template["layer_height"] = "0.2"
    template["xy_hole_compensation"] = "0"
    template["sparse_infill_pattern"] = "gyroid"
    template["curr_bed_type"] = "Textured PEI Plate"
    if "default_bed_type" in template:
        template["default_bed_type"] = "Textured PEI Plate"
    template["version"] = BAMBU_VERSION
    template["raft_first_layer_expansion"] = "0"
    for key in (
        "solid_infill_filament",
        "sparse_infill_filament",
        "wall_filament",
        "support_filament",
        "support_interface_filament",
    ):
        if template.get(key) in (0, "0", -1, "-1"):
            template[key] = "1"
    return json.dumps(template, indent=2)


def content_types():
    return f'''<?xml version="1.0" encoding="UTF-8"?>
<Types xmlns="{CT_NS}">
 <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
 <Default Extension="model" ContentType="application/vnd.ms-package.3dmanufacturing-3dmodel+xml"/>
 <Default Extension="config" ContentType="text/xml"/>
 <Default Extension="png" ContentType="image/png"/>
 <Default Extension="json" ContentType="application/json"/>
</Types>
'''


def rels():
    return f'''<?xml version="1.0" encoding="UTF-8"?>
<Relationships xmlns="{REL_NS}">
 <Relationship Target="/3D/3dmodel.model" Id="rel-1" Type="http://schemas.microsoft.com/3dmanufacturing/2013/01/3dmodel"/>
 <Relationship Target="/Metadata/plate_1.png" Id="rel-2" Type="http://schemas.openxmlformats.org/package/2006/relationships/metadata/thumbnail"/>
 <Relationship Target="/Metadata/plate_1.png" Id="rel-4" Type="http://schemas.bambulab.com/package/2021/cover-thumbnail-middle"/>
 <Relationship Target="/Metadata/plate_1_small.png" Id="rel-5" Type="http://schemas.bambulab.com/package/2021/cover-thumbnail-small"/>
</Relationships>
'''


def model_rels():
    return f'''<?xml version="1.0" encoding="UTF-8"?>
<Relationships xmlns="{REL_NS}">
 <Relationship Target="/Metadata/model_settings.config" Id="rel-1" Type="http://schemas.bambulab.com/package/2021/model-settings"/>
 <Relationship Target="/Metadata/project_settings.config" Id="rel-2" Type="http://schemas.bambulab.com/package/2021/project-settings"/>
 <Relationship Target="/Metadata/slice_info.config" Id="rel-3" Type="http://schemas.bambulab.com/package/2021/slice-info"/>
</Relationships>
'''


def slice_info():
    return f'''<?xml version="1.0" encoding="UTF-8"?>
<config>
  <header>
    <header_item key="X-BBL-Client-Type" value="slicer"/>
    <header_item key="X-BBL-Client-Version" value="{BAMBU_VERSION}"/>
  </header>
</config>
'''


def plate_json(plate, objects):
    by_id = {obj["id"]: obj for obj in objects}
    names = [by_id[obj_id]["name"] for obj_id, _inst in plate["members"]]
    return json.dumps({
        "plate_index": plate["id"] - 1,
        "filament_colors": ["#FFFFFF"],
        "filament_ids": [1],
        "nozzle_diameter": 0.4,
        "is_seq_print": False,
        "objects": [{"name": name, "count": 1} for name in names],
    }, indent=2)


def write_3mf(path, objects, plates):
    path.parent.mkdir(parents=True, exist_ok=True)
    files = {
        "[Content_Types].xml": content_types(),
        "_rels/.rels": rels(),
        "3D/3dmodel.model": model_xml(objects),
        "3D/_rels/3dmodel.model.rels": model_rels(),
        "Metadata/model_settings.config": model_settings(objects, plates),
        "Metadata/project_settings.config": project_settings(),
        "Metadata/slice_info.config": slice_info(),
        "Metadata/plate_1_small.png": PLATE_PNG_SMALL,
    }
    for plate in plates:
        files[f"Metadata/plate_{plate['id']}.png"] = PLATE_PNG
        files[f"Metadata/plate_{plate['id']}.json"] = plate_json(plate, objects)
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for name, data in files.items():
            info = zipfile.ZipInfo(name)
            info.date_time = (2026, 10, 23, 0, 0, 0)
            info.compress_type = zipfile.ZIP_DEFLATED
            if isinstance(data, str):
                data = data.encode("utf-8")
            zf.writestr(info, data)


def build(doc):
    bed = float(doc.get("bed_mm", 256))
    margin = float(doc.get("margin_mm", 8))
    gap = float(doc.get("gap_mm", 8))
    objects = []
    plates = []
    layout = []
    next_id = 1
    plate_specs = doc["plates"]
    for plate_index, plate in enumerate(plate_specs, start=1):
        placed = expand(plate["parts"], bed, margin, gap)
        ox, oy = plate_origin(plate_index - 1, bed, len(plate_specs))
        members = []
        rows = []
        for item in placed:
            verts, faces = mesh_local(item["mesh"])
            center = placed_center(item, ox, oy)
            name = instance_name(item["stl"], item["instance"], item["qty"])
            objects.append({
                "id": next_id,
                "name": name,
                "vertices": verts,
                "faces": faces,
                "instances": [center],
                "center_xy": [center],
                "walls": item["walls"],
                "infill": item["infill"],
                "pattern": item["pattern"],
                "brim": item["brim"],
                "top_shells": item.get("top_shells"),
                "bottom_shells": item.get("bottom_shells"),
            })
            members.append((next_id, 0))
            next_id += 1
            rows.append({
                "stl": item["stl"],
                "x_mm": round(item["x"], 1),
                "y_mm": round(item["y"], 1),
                "walls": item["walls"],
                "infill": item["infill"],
                "brim_mm": item["brim"],
            })
        plates.append({"id": plate_index, "name": plate["name"], "members": members})
        layout.append({"plate": plate_index, "name": plate["name"], "parts": rows})

    project_file = doc.get("project_file", "project.3mf")
    coupon_file = doc.get("coupon_file", "fit_coupon.3mf")
    coupon = doc["coupon"]
    names = coupon.get("parts") or [coupon["stl"]]
    c_margin = float(coupon.get("margin_mm", 2))
    c_gap = float(coupon.get("gap_mm", 6))
    shells = coupon.get("shells")
    cursor_x = c_margin
    coupon_objects = []
    coupon_rows = []
    members = []
    coupon_id = 1
    for name in names:
        mesh = load_mesh(name)
        size = mesh.bounds[1] - mesh.bounds[0]
        if cursor_x + size[0] + c_margin > bed:
            raise SystemExit(f"{name} does not fit on the coupon plate")
        verts, faces = mesh_local(mesh)
        center = (cursor_x + float(size[0]) / 2, PURGE_Y + float(size[1]) / 2, float(size[2]) / 2)
        label = instance_name(name, 0, 1)
        coupon_objects.append({
            "id": coupon_id,
            "name": label,
            "vertices": verts,
            "faces": faces,
            "instances": [center],
            "center_xy": [center],
            "walls": int(coupon["walls"]),
            "infill": int(coupon["infill"]),
            "pattern": coupon.get("pattern", "gyroid"),
            "brim": float(coupon.get("brim_mm") or 0),
            "top_shells": shells,
            "bottom_shells": shells,
        })
        members.append((coupon_id, 0))
        coupon_rows.append({
            "stl": name,
            "x_mm": cursor_x,
            "y_mm": PURGE_Y,
            "walls": int(coupon["walls"]),
            "infill": int(coupon["infill"]),
            "brim_mm": 0,
        })
        cursor_x += float(size[0]) + c_gap
        coupon_id += 1
    coupon_plate = {"id": 1, "name": "Fit coupon", "members": members}
    write_3mf(PRINT / project_file, objects, plates)
    write_3mf(PRINT / coupon_file, coupon_objects, [coupon_plate])
    layout.append({
        "plate": "coupon",
        "name": "Fit coupon",
        "file": coupon_file,
        "parts": coupon_rows,
    })
    (PRINT / "layout.json").write_text(json.dumps(layout, indent=2) + "\n")
    return layout


def _cube(size=10.0):
    """A cube centred on the origin, same convention as mesh_local."""
    h = float(size) / 2
    vertices = np.array([
        [-h, -h, -h], [h, -h, -h], [h, h, -h], [-h, h, -h],
        [-h, -h, h], [h, -h, h], [h, h, h], [-h, h, h],
    ], dtype=float)
    faces = np.array([
        [0, 2, 1], [0, 3, 2],
        [4, 5, 6], [4, 6, 7],
        [0, 1, 5], [0, 5, 4],
        [1, 2, 6], [1, 6, 5],
        [2, 3, 7], [2, 7, 6],
        [3, 0, 4], [3, 4, 7],
    ], dtype=int)
    return vertices, faces


def _fixture_object(oid, name, corner):
    vertices, faces = _cube()
    center = (corner[0] + 5.0, corner[1] + 5.0, 5.0)
    return {
        "id": oid,
        "name": name,
        "vertices": vertices,
        "faces": faces,
        "instances": [center],
        "center_xy": [center],
        "walls": 2,
        "infill": 15,
        "pattern": "gyroid",
        "brim": 0,
        "top_shells": None,
        "bottom_shells": None,
    }


def write_known_bad_projects(directory):
    """3MF files the Bambu plate check must reject.

    single_plate.3mf puts three parts on the first bed and declares one
    plate. blank_name.3mf is one part with an empty name.
    """
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    one_plate = [
        _fixture_object(1, "alpha", (20.0, 20.0)),
        _fixture_object(2, "beta", (40.0, 20.0)),
        _fixture_object(3, "gamma", (60.0, 20.0)),
    ]
    write_3mf(directory / "single_plate.3mf", one_plate, [{
        "id": 1, "name": "Only plate", "members": [(1, 0), (2, 0), (3, 0)],
    }])
    blank = [_fixture_object(1, "", (20.0, 20.0))]
    write_3mf(directory / "blank_name.3mf", blank, [{
        "id": 1, "name": "Blank", "members": [(1, 0)],
    }])


def layout_markdown(layout=None):
    """Markdown table of the committed layout. Used by the job summary."""
    if layout is None:
        layout = json.loads((PRINT / "layout.json").read_text())
    lines = ["| Plate | Parts |", "|---|---|"]
    for plate in layout:
        counts = {}
        for part in plate["parts"]:
            name = part["stl"].replace(".stl", "")
            counts[name] = counts.get(name, 0) + 1
        label = ", ".join(f"{n}× {name}" if n > 1 else name for name, n in counts.items())
        lines.append(f"| {plate['name']} | {label} |")
    return "\n".join(lines)


def resolve_cad(path):
    if path:
        return Path(path).resolve()
    cwd = Path.cwd()
    if (cwd / "print" / "plates.yaml").exists():
        return cwd.resolve()
    raise SystemExit("pass --project, the CAD directory that contains print/plates.yaml")


def main():
    global CAD, STL_DIR, PRINT, PROFILES
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", default="")
    parser.add_argument("--print-layout", action="store_true")
    args = parser.parse_args()
    CAD = resolve_cad(args.project)
    STL_DIR = CAD / "stl"
    PRINT = CAD / "print"
    if args.print_layout:
        print(layout_markdown())
        return
    doc = yaml.safe_load((PRINT / "plates.yaml").read_text())
    if doc.get("supports"):
        raise SystemExit("plates.yaml has supports on; this project keeps them off")
    PROFILES = doc.get("profiles") or {}
    layout = build(doc)
    for plate in layout:
        names = []
        for part in plate["parts"]:
            names.append(part["stl"].replace(".stl", ""))
        # Collapse repeats for the one-line summary.
        counts = {}
        for name in names:
            counts[name] = counts.get(name, 0) + 1
        label = ", ".join(f"{n}× {k}" if n > 1 else k for k, n in counts.items())
        title = plate["name"]
        print(f"plate {plate['plate']}: {title}: {label}")


if __name__ == "__main__":
    main()
