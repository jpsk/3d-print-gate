# Demo part — printed parts

One STL per part, bed face at z = 0. Fits are in `parameters.scad`. Plates are in `print/plates.yaml`.

- Design notes: [DESIGN_REVIEW.md](DESIGN_REVIEW.md)
- How to print: [PRINTING.md](PRINTING.md)
- Export: `./export.sh` (OpenSCAD)
- Mesh check, from the repo root, no slicer: `python3 print-gate/check_print.py --project examples/demo-part/cad --skip-slicer --full`

The gate fails a part that needs support, breaks the 45° layer rule, has a wall under 1.6 mm, or has a feature under 0.8 mm. Known-bad meshes in `tools/fixtures/` must keep failing. A defect from a real print is added there and to the lessons table in DESIGN_REVIEW.md.
