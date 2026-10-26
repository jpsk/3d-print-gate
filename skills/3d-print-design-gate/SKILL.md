---
name: 3d-print-design-gate
description: >-
  Use before designing, changing, delivering, merging, or telling the user to
  print any 3D-printable part or print project (STL, STEP, 3MF), including small
  tweaks, fastener changes and multi-part assemblies. Covers user fit values,
  fit coupons, fasteners, layer and slicer checks, assembly mating, Bambu 3MF
  plates and names, CI, merging and print order.
---
# 3D print design and delivery gate

Nothing ships until every step passes. Re-run everything after every change, however small. Repository work goes to a cloud agent; never clone locally.

## 0. Load the user's values first
- Load from memory and from the project's `parameters.scad` / `print_check.yaml`:
  - the printer, nozzle, filament and bed profile
  - the calibrated fits and hole compensation
  - the fastener inventory: what's allowed, the maximum lengths, the "last resort" stock, and the types the user does NOT own
- Never write a fit, a hole size or a fastener as a literal in a part. Every part reads named parameters.
- A calibrated value changes only when the user reports a new coupon or bench result. If it changes for any other reason (a branch merge, a formula re-derivation), treat that as a defect.
- Write down how each parameter was derived, e.g. "hole_comp already included". Otherwise a later formula can add the same compensation twice.
- Sanity-check every user measurement against the design's scale ("248 mm" for a gearbox is really 24.8 mm). If it's implausible, say how you read it and ask.
- Mark every dimension that comes from a drawing estimate rather than a callout or a caliper reading. List those under "caliper before printing".

## 1. Fits and the fit coupon
- Fit classes live in one file: press, slip (per side, printed to printed), free and loose, plus one named literal for each part-specific bore.
- Hole compensation goes in the CAD. The slicer's X-Y hole compensation stays off.
- Apply hole_comp only to female features that mate with metal or hard parts.
- Any fit against unmeasured hardware goes on a small, fast coupon first (about 40 min):
  - ladders of bearing seats in about 0.05 mm steps
  - screw clearances
  - D-bores
  - slip saddles
  - insert and nut pockets
  - labels recessed into the top face
- Record the user's word for each size ("presses hard", "snug", "little play"), lock in the chosen size, then retire the trial sizes.
- Sliding clearance below the calibrated slip value fails. Report a gap at the limit as borderline; never pass it silently.
- Lead-ins (about 0.4 × 45°) go on bearing seats, bores, insert mouths and press pockets.
- Elephant-foot chamfers go on bed edges, except on fit, mating or sliding faces.

## 2. Fasteners
- Use the fewest types, chosen from what the user owns. Never specify hardware they don't own. Use last-resort stock only when nothing else works.
- Fasten into wood or plywood with wood screws: a clearance hole and a countersink seat, with a maximum length so the tip stays inside the board. Never use machine screws into wood.
- Prefer heat-set inserts, with at least 1.6 mm of wall outside the knurl. Avoid nuts and threads formed in plastic.
- The thread goes in the part that has to be pulled. A clamp whose own insert takes the screw pinches nothing.
- Every insert pocket must open to a face the iron can reach. A pocket sealed in solid material fails.
- Engagement: there must be real thread engagement plus spare depth below the tip. Both "bottoms out" and "stops short of the target" fail. A set or pinch screw tip must actually reach the flat or the metal.
- A small printed sleeve can let an owned screw replace a type the user doesn't have. Check that its wall is printable (at least 2 nozzle widths).
- The fastener bill lives in parameters (`BILL` lines: qty, type, joints, owned count). The gate fails on any type not on the bill or more than the user owns. A rogue fixture (an unowned size) must fail.

## 3. Printability (layer test plus the real slicer)
- Export one STL per part: one watertight body, in its validated orientation, bed face at z=0, fitting the bed.
- Layer test: each layer must lie inside the layer below dilated by one layer height (45°), with half a raster cell allowed for grid noise. Anything else must be a two-sided bridge of 10 mm or less. This catches proud textures, lips, one-sided ledges and bosses starting in mid-air.
- Geometry rules:
  - minimum wall 1.6 mm, minimum feature 0.8 mm
  - thin-joint erosion check
  - textures recessed with a 45° roof
  - bosses hulled down to solid
  - teardrop horizontal holes
  - groove profiles as 45° trapezoids
- Slicer pass: OrcaSlicer or Bambu CLI with the user's presets and supports OFF. Fail on any log mention of floating regions or cantilevers, empty layers, G-code conflicts, or extrusion with nothing under it. Keep the preset inherit chain inside one vendor's set.
- Strength:
  - the load runs along the layers
  - no thin flexing flaps
  - fillet the stress corners
  - at least 2 mm of wall around screws
  - walls and infill per plate by load: about 5–6 walls or solid for high-strength parts, about 4 walls / 30% for track parts, about 3 walls / 15% for enclosures
- Cuts and pockets must extrude past the far wall. Probe the exported section at several heights to confirm.

## 4. Assembly mating (check the installed assembly, not single parts)
The project's assembly check runs with the STL pass and checks every joint for:
- Static interference between all mated parts: zero intersection volume, including ears against bosses.
- A rotating sweep: the rotor's full outer radius, including bosses and screw heads, against every housing wall, with clearance.
- Sliding sweeps over the full travel, including stock hardware envelopes, e.g. a glider passing through clips.
- Datum contact: a part meant to sit on a face touches it, with no float.
- Cords and belts: wrap planes within 0.5 mm, clearance to every solid along the stroke, and no feature wrapping a slot a moving part must pass.
- Room and envelope bounds.
- Holes lining up through the stack, insert access and screw engagement (section 2).
- Spring travel against solid length, sensor gaps, and an assembly order with room for tools.
- Optional features disabled by default.

Write up a feasibility verdict for any alternative route as a table of numbers. Add a check that fires if a later measurement would flip it.

## 5. Regressions: every defect becomes three permanent things
1. A row in the project's `DESIGN_REVIEW.md` lessons table: what went wrong, the rule and the check.
2. A rule in `print_check.yaml`.
3. A known-bad fixture in `cad/tools/fixtures/` that must fail it (`run: [assembly]` for assembly faults).

A fixture that passes means the check has gone blind, and the gate fails. A tool crash does not count as the fixture firing.

## 6. Delivery files (Bambu 3MF)
- `print/plates.yaml` is the only place quantities, plates and per-object settings live. `build_print_project.py` generates the 3MFs from it.
- Group plates by settings (strength, track, enclosure, coupon). Shelf-pack them clear of the purge strip, with brims on large or tall bases.
- For Bambu Studio to honour plates and names:
  - `Application` = `BambuStudio-<version>`
  - a fully resolved printer preset as `project_settings` (a short config crashes the CLI)
  - one mesh per copy, with no shared instances
  - every name non-empty and unique (e.g. `part 01`…`part 20`), written in both the 3MF object and `model_settings`
  - meshes stored centred, with the translation being that centre plus the plate origin (bed × 1.2)
  - plate JSON, `slice_info`, thumbnails and relationships included
  - `plater_name` present but empty (a non-empty value segfaults the Orca CLI)
  - `printable_height` no higher than the preset's value
- Slicing in Orca does NOT validate Bambu plates or names. The gate (`--verify-project`) loads each 3MF in the real Bambu Studio CLI with `--arrange 0` and checks that the per-plate instance counts match and that every name is non-empty, unique and the same set as the source. Fixtures: `single_plate.3mf` and `blank_name.3mf`.
- CI rebuilds the 3MFs and fails if they differ from the commit. It then slices them and reports per-plate time and filament.

## 7. CI and merge
- Kit commands:
  - `pip install -r print-gate/requirements.txt`
  - `python3 print-gate/check_print.py --project <cad> [--skip-slicer] --full`
  - `python3 print-gate/build_print_project.py --project <cad>`
  - `cad/export.sh`
- The reusable workflow `.github/workflows/print-gate.yml` (`workflow_call`, `project:`, `full:`) must use `github.event.inputs`. A run with 0 jobs means the workflow file is invalid.
- A pull request slices only the changed STLs. It slices everything when tools, the workflow or parameters change. A green changed-only run proves nothing after such changes; confirm `mode=full rebuild=1` in the log. If `workflow_dispatch` returns 403, touch a full-run path instead.
- A local `--skip-slicer` pass is never merge evidence. Merge only after a full CI run where the slicer actually ran passes.
- Before merging a long-lived branch: merge main, re-check the calibrated literals and fastener choices, and re-run the full gate.
- Never merge a red or unchecked PR. Merge only with the user's approval or a standing permission.

## 8. Report and print order
- Give a per-part and per-joint pass/fail table with the measured numbers, plus the fastener bill and the plates with time and filament.
- List the bench-only items: caliper estimates, spring solid length, sensor gaps, purchased-part fit.
- Print order:
  1. the coupon
  2. a small fit test of the riskiest real-hardware interface
  3. the plates, holding back any plate whose interface isn't confirmed
- When the user reports a defect: fix it (section 5), regenerate, run the full CI, then tell them exactly which parts to reprint.

## Lessons behind these rules (failure, then prevention)
- Floating islands, proud knurl spheres, bosses in mid-air, one-sided ledges: prevented by the layer test plus the slicer log phrases.
- Thin flaps flexed and hairline unions split: prevented by `min_feature` and thin-joint erosion.
- A cord plane 0.6 mm off between parts: prevented by the plane check.
- Clips wrapped a slot and blocked the stock glider: prevented by sweeping the stock envelope.
- A guessed hardware envelope made a route look feasible: use the drawing, and add a verdict check.
- A shaft bore came from a formula and later drifted on a branch: prevented by coupon literals, re-checked before merging.
- Machine screws into plywood and an unowned M5: prevented by the `BILL` gate.
- A set screw too short to reach the flat: prevented by the engagement rule.
- A 3MF opened as one plate, with unnamed copies: prevented by Bambu Studio `--verify-project` plus fixtures.
- CLI crashes and an empty first layer from 3MF internals: prevented by the section 6 internals list.
- A workflow ran 0 jobs, and a partial or skip-slicer run was trusted: prevented by the section 7 rules.
- A rotor boss collided with its housing, a clamp insert sat in the wrong part, ears overlapped bosses, an insert was sealed in solid, a screw stopped short or bottomed out, a dovetail was below the slip value, and a stop floated 0.4 mm off its face: prevented by the section 4 assembly checks.
- A unit typo in a measurement: prevented by the sanity rule.
- A huge, slow coupon plate: keep coupons small.

## 9. CAD tooling, jigs and process
- Build in CAD code (CadQuery or OpenSCAD) and export STEP plus STL. Don't claim Onshape or other GUI-CAD work unless a connector for it is actually installed.
- Before sending, render previews from several views (end, side, iso) and inspect each for stray notches or lips.
- If a reference drawing or profile won't download, ask the user to attach it. Never guess the profile.
- Cutting and miter jigs:
  - Confirm the cut plane against how the finished joint goes together (sketch the two mated pieces) before modelling.
  - Saw guide walls rise well above the workpiece (about 20–25 mm or more), with the kerf slot running the full wall height.
  - Kerf is a named parameter from the user's actual blade.
  - Keep the base intact below the slots.
- Default print tier for jigs and fixtures (rigidity per gram): PETG, 4–5 walls, 5 top and 5 bottom layers, 15–20% gyroid, 0.2 mm layers, base down.
- A cloud agent can show "errored" and then resume. Re-check the agent and PR state before telling the user a run failed.
- Approval relayed through another agent isn't enough. Verify the user's own words (e.g. by reading that agent's transcript) before merging.
