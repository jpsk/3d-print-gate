# 3d-print-gate

Open-source checks for 3D-printable parts, before you print them. A project keeps its own CAD, fits, plates, and known-bad fixtures. The checker lives once, in [`print-gate/`](print-gate/).

The gate is supports off, a layer-by-layer 45° overhang test, a minimum wall of 1.6 mm, and a minimum feature of 0.8 mm. It then slices with a real slicer and, for a Bambu project file, opens the 3MF in Bambu Studio to confirm plates and object names. It does not need a bot. The checks are Python and GitHub Actions.

This kit pairs with the **3D Print Designer** Grok Bot template. The bot's skills are in [`skills/`](skills/). Use them if you want a design conversation that refuses to ship a part until the gate passes. Use the commands below if you do not.

## Quick start

```bash
git clone https://github.com/jpsk/3d-print-gate
cd 3d-print-gate
python3 -m pip install -r print-gate/requirements.txt
python3 print-gate/check_print.py --project examples/demo-part/cad --skip-slicer --full
```

That checks the demo (a 20 mm block and a fit coupon) and the known-bad fixtures. A fixture row marked PASS means the bad mesh failed the rule it was built to fail.

Export the demo meshes again with OpenSCAD, from `examples/demo-part/cad`:

```bash
./export.sh
```

Rebuild the arranged plates:

```bash
python3 print-gate/build_print_project.py --project examples/demo-part/cad
```

Start a new part in a repo that contains this kit:

```bash
python3 print-gate/new_project.py projects/2026-my-part --name "My part"
```

The fit numbers it writes (`fit_slip` 0.125 mm per side, `hole_comp` 0.18 mm) are conservative placeholders for a 0.4 mm nozzle. They are not anyone's measured printer. Print the fit coupon and replace them.

A local `--skip-slicer` pass is not evidence that a change is ready to merge. Merge only after the GitHub Action has sliced with OrcaSlicer and, when the project file is rebuilt, opened it in Bambu Studio.

## How the checks work

`print-gate/check_print.py` reads the project's `print_check.yaml`.

1. **Fixtures.** Each known-bad mesh must trip the rule named next to it. If it passes, the check has gone blind and the gate fails. A crash does not count as the fixture firing.
2. **Geometry.** One watertight body, bed face at z = 0, on the bed. Each layer must sit inside the layer below, dilated by one layer height (45°). A two-sided bridge of 10 mm or less is allowed. Walls thinner than 1.6 mm and features thinner than 0.8 mm fail. Hole compensation belongs in the CAD. The slicer's X-Y hole compensation stays off.
3. **Slicer.** OrcaSlicer, supports off. The log fails the part on a floating region, a floating cantilever, an empty layer, a G-code conflict, or an extrusion with nothing under it within one 45° layer.
4. **Project file.** `build_print_project.py` reads `print/plates.yaml` and writes `print/project.3mf` and `print/fit_coupon.3mf`. CI rebuilds them and fails if they differ from the commit. It slices them, then Bambu Studio loads each 3MF with `--arrange 0` and checks that the plate instance counts match and that every object name is non-empty, unique, and the same set as the file.

A pull request slices the STLs the diff touches. It slices every STL when the tools, the workflow, or the project's parameters change. The log line is `mode=full rebuild=1`. A green changed-only run is not proof after those files change.

The caller must pass `full: ${{ github.event.inputs.full == 'true' }}`. On a pull request the `inputs` context does not exist, and a workflow that references it is recorded as a run with no jobs. Inside the reusable workflow, `project` and `full` are `workflow_call` inputs, so that file reads `inputs.project` and `inputs.full`.

## Lessons learned

Every defect that shows up on a real print becomes three permanent things:

1. A row in that project's `DESIGN_REVIEW.md`.
2. A rule in `print_check.yaml`.
3. A known-bad fixture under `cad/tools/fixtures/` that must fail the rule.

The demo carries the six standing rules: a feature in the air, a boss with nothing under it, a one-sided ledge, a nub proud of a round wall, a flap under 0.8 mm, and a neck under 1.6 mm. The next project can copy those rows forward:

```bash
python3 print-gate/new_project.py projects/2026-my-part --name "My part" \
  --carry-from examples/demo-part/cad
```

## Slicers and printers

GitHub Actions installs two programs, both pinned:

| Tool | Version | Job |
|---|---|---|
| OrcaSlicer | 2.3.2, Ubuntu 24.04 AppImage | Slice STLs and 3MFs, supports off |
| Bambu Studio | 02.08.02.61, Ubuntu 24.04 AppImage | Open the 3MF and check plates and names |

The default presets are an **example printer profile**, not a requirement:

- Machine: `Bambu Lab X1 Carbon 0.4 nozzle`
- Process: `0.20mm Standard @BBL X1C`
- Filament: `Bambu PETG Basic @BBL X1C`
- Bed: 256 mm, textured PEI plate

[`print-gate/bambu_x1c_project_settings.json`](print-gate/bambu_x1c_project_settings.json) is the resolved X1C preset embedded in the 3MF so Bambu Studio can open the file. A short config crashes that CLI. It is one example profile. Pass `machine_profile`, `process_profile`, and `filament_profile` to [`.github/workflows/print-gate.yml`](.github/workflows/print-gate.yml) when you want other names. The preset inherit chain has to stay inside one vendor's set. Presets for other printers are not shipped here; they have to exist in the slicer install the workflow uses.

Supports stay off. The workflow runs on `ubuntu-24.04` because those AppImages and their library packages target that release.

## Layout

| Path | What it is |
|---|---|
| `print-gate/` | The checker, the 3MF builder, `new_project.py`, the X1C example preset |
| `examples/demo-part/` | A block and a fit coupon that pass, plus fixtures that must fail |
| `.github/workflows/print-gate.yml` | Reusable `workflow_call` workflow (`project`, `full`) |
| `.github/workflows/demo-part-cad-check.yml` | CI for this repo. Runs the kit on the demo |
| `skills/` | The three 3D Print Designer skills |

## License

[MIT](LICENSE). Copyright Mindaugas Paskevicius / jpsk.
