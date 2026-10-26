# Print settings — {{NAME}}

Example printer profile: **Bambu Lab X1C**, bed 256 × 256 mm, 0.4 mm nozzle, 0.2 mm layers, PETG. Supports off. X-Y hole compensation in the slicer stays off. Swap the profile in `print/plates.yaml` and in the workflow inputs for a different machine.

Every part is its own STL in `stl/`, already oriented (the face that should be down is at z = 0). `./export.sh` writes one STL per part. `print/plates.yaml` is the only place quantities and per-object settings are written. `print-gate/build_print_project.py` reads it and writes `print/project.3mf` and `print/fit_coupon.3mf`.

## Fit coupon first

Print `print/fit_coupon.3mf` before the functional parts. The numbers below are starting placeholders for a 0.4 mm nozzle, not a calibration of a particular printer.

`stl/fit_coupon_holes.stl` is 60 × 40 × 4 mm. The through holes are 3.2, 3.4, 5.2, and 5.4 mm, plus a 3.2 + `hole_comp` hole. They are the CAD size, with no extra compensation and no lead-in, so a caliper reads the printer.

`stl/fit_coupon_fits.stl` is 56 × 28 × 6 mm. Three slots take a 10 mm printed male. The clearances are 0.10, 0.125, and 0.15 mm per side. **0.125 is `fit_slip`, the part-to-part default.**

| What you measured | If it is wrong | Parameter |
|---|---|---|
| A hole is small by the same amount on every diameter | Set `hole_comp` to (label − measured). Holes from a 0.4 mm nozzle often print 0.15–0.2 mm small. The placeholder 0.18 mm is inside that band | `hole_comp` |
| Two printed parts are tight or loose | The gap per side should be the saddle that slides. 0.125 mm is the default | `fit_slip` |
| A printed part in a metal hole | The printed male shrinks, which adds clearance. Do not also add `hole_comp` | the male's own clearance |
| A metal pin in a printed hole | The hole needs `hole_comp` on the diameter | `hole_comp` |

Fits live in `parameters.scad`. `fit_press` 0.12 mm diametral interference, `fit_slip` **0.125 mm per side**, `fit_free` 0.45 mm diametral, `fit_loose` 0.55 mm diametral, `hole_comp` 0.18 mm diametral.

## Plates

| File | What it is |
|---|---|
| `print/project.3mf` | The parts plate. Supports off. X1C 0.4 mm nozzle, Bambu PETG Basic, 0.2 mm layers |
| `print/fit_coupon.3mf` | The coupon alone, 2 walls, 2 shells, 15% gyroid |

Open either file in Bambu Studio or OrcaSlicer. Do not run auto-arrange on top of this: it mixes the setting groups.

The GitHub Action rebuilds these files after the mesh checks pass, re-slices them, and writes the time and filament for each plate on the job summary.
