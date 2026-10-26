#!/usr/bin/env python3
"""Create a 3D-part project from print-gate/template.

  python3 print-gate/new_project.py projects/2026-my-part --name "My part"

Writes <dest>/cad (parameters, fit coupon, plates, check config, docs),
starter STLs, known-bad fixtures, the 3MF plates, and a thin caller
workflow. --carry-from copies new lessons-learned rows from an older
project's DESIGN_REVIEW.md.
"""
import argparse
import os
import re
import stat
import subprocess
import sys
from pathlib import Path

import trimesh
from shapely.geometry import Point, Polygon
from shapely.ops import unary_union

ROOT = Path(__file__).resolve().parent
TEMPLATE = ROOT / "template"
REPO = ROOT.parent

TEXT_SUFFIXES = {".md", ".scad", ".yaml", ".yml", ".sh", ".py"}


def substitute(text, mapping):
    for key, value in mapping.items():
        text = text.replace("{{" + key + "}}", value)
    return text


def copy_text(src, dest, mapping):
    dest.parent.mkdir(parents=True, exist_ok=True)
    text = src.read_text()
    if src.suffix in TEXT_SUFFIXES or src.name.startswith("."):
        text = substitute(text, mapping)
    dest.write_text(text)
    if src.suffix == ".sh":
        dest.chmod(dest.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def slugify(name):
    slug = name.strip().lower()
    slug = re.sub(r"[^a-z0-9]+", "-", slug).strip("-")
    if not slug:
        raise SystemExit(f"cannot make a workflow name from {name!r}")
    return slug


def _extrude(polygon, height):
    mesh = trimesh.creation.extrude_polygon(polygon, height)
    mesh.merge_vertices()
    mesh.fix_normals()
    if abs(float(mesh.bounds[0][2])) > 1e-6:
        mesh.apply_translation([0, 0, -float(mesh.bounds[0][2])])
    return mesh


def write_starter_stls(stl_dir):
    """Meshes that match fit_coupon.scad and block.scad, without OpenSCAD."""
    stl_dir.mkdir(parents=True, exist_ok=True)
    block = trimesh.creation.box(extents=[20, 20, 10])
    block.apply_translation([0, 0, 5])
    block.export(stl_dir / "block.stl")

    holes = Polygon([(0, 0), (60, 0), (60, 40), (0, 40)])
    # 3.38 = 3.2 + hole_comp (0.18). Keep this next to fit_coupon.scad.
    cutouts = [(12, 14, 3.2), (24, 14, 3.4), (36, 14, 5.2), (48, 14, 5.4), (12, 28, 3.38)]
    holes = holes.difference(unary_union([
        Point(x, y).buffer(d / 2, resolution=32) for x, y, d in cutouts
    ]))
    _extrude(holes, 4.0).export(stl_dir / "fit_coupon_holes.stl")

    plate = Polygon([(0, 0), (56, 0), (56, 28), (0, 28)])

    def rect(x, y, w, h):
        return Polygon([(x, y), (x + w, y), (x + w, y + h), (x, y + h)])

    slots = unary_union([
        rect(4, 6, 10 + 2 * 0.10, 16),
        rect(18, 6, 10 + 2 * 0.125, 16),
        rect(32, 6, 10 + 2 * 0.15, 16),
    ])
    _extrude(plate.difference(slots), 6.0).export(stl_dir / "fit_coupon_fits.stl")


def _lesson_rows(text):
    lines = text.splitlines()
    start = None
    for index, line in enumerate(lines):
        if line.strip().lower().startswith("## lessons learned"):
            start = index + 1
            break
    if start is None:
        return []
    rows = []
    for line in lines[start:]:
        if line.startswith("## "):
            break
        if not line.startswith("|"):
            continue
        inner = line.strip().strip("|").replace("|", "").replace(":", "").replace("-", "").strip()
        if not inner or "What went wrong" in line:
            continue
        rows.append(line.rstrip())
    return rows


def _first_cell(row):
    return row.strip().strip("|").split("|", 1)[0].strip()


def carry_lessons(design_path, source_cad):
    source = Path(source_cad) / "DESIGN_REVIEW.md"
    if not source.exists():
        raise SystemExit(f"no DESIGN_REVIEW.md in {source_cad}")
    existing = design_path.read_text()
    have = {_first_cell(row) for row in _lesson_rows(existing)}
    extra = [row for row in _lesson_rows(source.read_text()) if _first_cell(row) not in have]
    if not extra:
        return 0
    lines = existing.splitlines()
    insert_at = len(lines)
    in_lessons = False
    last_row = None
    for index, line in enumerate(lines):
        if line.strip().lower().startswith("## lessons learned"):
            in_lessons = True
            continue
        if in_lessons and line.startswith("## "):
            insert_at = index
            break
        if in_lessons and line.startswith("|") and _first_cell(line) and "What went wrong" not in line:
            body = line.strip().strip("|").replace("|", "").replace(":", "").replace("-", "").strip()
            if body:
                last_row = index
    if last_row is not None:
        insert_at = last_row + 1
    lines[insert_at:insert_at] = extra
    design_path.write_text("\n".join(lines).rstrip() + "\n")
    return len(extra)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("dest", help="Project directory, for example projects/2026-my-part")
    parser.add_argument("--name", required=True, help="Name used in the docs and the workflow title")
    parser.add_argument("--carry-from", default="", help="CAD directory whose lessons are copied forward")
    parser.add_argument("--no-workflow", action="store_true")
    parser.add_argument("--date", default="2026-10-23")
    args = parser.parse_args()

    dest = Path(args.dest)
    if not dest.is_absolute():
        dest = REPO / dest
    dest = dest.resolve()
    try:
        dest.relative_to(REPO)
    except ValueError:
        raise SystemExit(f"{dest} is outside the repo")
    cad = dest / "cad"
    if cad.exists():
        raise SystemExit(f"{cad} already exists")

    rel_dest = dest.relative_to(REPO).as_posix()
    cad_path = f"{rel_dest}/cad"
    slug = slugify(dest.name)
    readme_rel = Path(os.path.relpath(ROOT / "README.md", dest)).as_posix()
    mapping = {
        "NAME": args.name,
        "DATE": args.date,
        "CAD_PATH": cad_path,
        "SLUG": slug,
        "PROJECT_DIR": rel_dest,
        "PRINT_GATE_README": readme_rel,
    }

    for name in (
        "parameters.scad",
        "block.scad",
        "fit_coupon.scad",
        "export.sh",
        "print_check.yaml",
        "DESIGN_REVIEW.md",
        "PRINTING.md",
    ):
        copy_text(TEMPLATE / name, cad / name, mapping)
    copy_text(TEMPLATE / "print" / "plates.yaml", cad / "print" / "plates.yaml", mapping)
    copy_text(TEMPLATE / "cad-README.md", cad / "README.md", mapping)
    if not (dest / "README.md").exists():
        copy_text(TEMPLATE / "PROJECT_README.md", dest / "README.md", mapping)

    write_starter_stls(cad / "stl")
    subprocess.check_call(
        [sys.executable, str(ROOT / "check_print.py"), "--project", str(cad), "--write-fixtures"]
    )
    subprocess.check_call(
        [sys.executable, str(ROOT / "build_print_project.py"), "--project", str(cad)]
    )
    if args.carry_from:
        source = Path(args.carry_from)
        if not source.is_absolute():
            source = (REPO / source).resolve()
        added = carry_lessons(cad / "DESIGN_REVIEW.md", source)
        print(f"carried {added} lesson row(s) from {source}")

    if not args.no_workflow:
        workflow = REPO / ".github" / "workflows" / f"{slug}-cad-check.yml"
        if workflow.exists():
            raise SystemExit(f"{workflow} already exists")
        copy_text(TEMPLATE / "caller.yml", workflow, mapping)
        print(f"workflow {workflow.relative_to(REPO)}")

    check = subprocess.call(
        [sys.executable, str(ROOT / "check_print.py"), "--project", str(cad), "--skip-slicer", "--full"]
    )
    if check != 0:
        raise SystemExit(f"new project failed the mesh gate ({cad_path})")
    print(f"scaffolded {cad_path}")
    print(f"from the repo root: python3 print-gate/check_print.py --project {cad_path} --skip-slicer --full")
    return 0


if __name__ == "__main__":
    sys.exit(main())
