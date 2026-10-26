# Design review — {{NAME}}

Reviewed {{DATE}}. The scaffold assumes a Bambu Lab X1C, 0.4 mm nozzle, 0.2 mm layers, until you change `print/plates.yaml` and the workflow profiles. Material is PETG unless a row below says otherwise. X-Y hole compensation in the slicer stays off. Allowances live in `parameters.scad`: `fit_slip` is 0.125 mm per side between printed parts, and `hole_comp` is 0.18 mm, a placeholder in the 0.15–0.2 mm band a 0.4 mm nozzle often prints small. The print gate is `print-gate/check_print.py`, configured by `print_check.yaml`.

## Checklist

| Item | Status | Notes |
|---|---|---|
| One watertight body, bed at z = 0, fits the bed |  |  |
| Walls ≥ 1.6 mm |  |  |
| Features ≥ 0.8 mm |  |  |
| Overhangs ≤ 45° from vertical, or a bridge ≤ 10 mm with support on both sides |  |  |
| Supports off |  |  |
| Printed-to-printed gap is `fit_slip` (0.125 mm per side). Holes that take metal include `hole_comp` |  |  |
| Fit coupon printed and measured before the functional parts |  |  |
| One STL per part, oriented, bed face at z = 0 |  |  |

## Still a bench check

-

## Lessons learned

Every defect reported from a print becomes a row in this table, a check in `print_check.yaml`, and a mesh under `tools/fixtures/` that must fail that check. A fixture that passes means the check went blind.

These rows are the standing rules. The next project can carry them forward:

`python3 print-gate/new_project.py projects/YYYY-name --name "Name" --carry-from {{CAD_PATH}}`

| What went wrong | Rule | Check |
|---|---|---|
| A feature starts in the air | No gap under a feature, and nothing past 45° from vertical unless it is a bridge of 10 mm or less | `empty_layer`, `mid_air`, `overhang_45`. Fixture `floating_island.stl` |
| A boss stands off the part and the nozzle draws spaghetti | Hull it down onto the solid under it | `empty_layer`. Fixture `boss_spaghetti.stl` |
| A one-sided ledge | Overhangs stay within 45°. Supports stay off | `overhang_45`. Fixture `cantilever.stl` |
| A nub stands proud of a round wall | Texture is recessed, or the underside sits on plastic | `overhang_45`. Fixture `proud_nub.stl` |
| A flap thinner than 0.8 mm | Minimum feature 0.8 mm | `min_feature`. Fixture `fragile_flap.stl` |
| A neck thinner than 1.6 mm joins two regions | Minimum wall 1.6 mm | `min_wall`. Fixture `thin_wall.stl` |
