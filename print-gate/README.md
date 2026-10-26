# Print gate

Shared check for 3D-printed parts. A project keeps its own STLs, fits, and fixtures. It does not keep its own copy of the checker.

The gate is supports off, a layer-by-layer 45° overhang check, a minimum wall of 1.6 mm, and a minimum feature of 0.8 mm. OrcaSlicer runs in GitHub Actions with the Bambu Lab X1C 0.4 mm nozzle and Bambu PETG Basic profiles, unless the workflow is given different preset names. The profile inherit chain stays inside that vendor's set.

## New project

From the repo root, after `pip install -r print-gate/requirements.txt`:

```bash
python3 print-gate/new_project.py projects/2026-my-part --name "My part"
```

That writes:

- `projects/2026-my-part/cad/` — `parameters.scad` (`fit_slip` 0.125 mm per side, `hole_comp` 0.18 mm), `print_check.yaml`, `DESIGN_REVIEW.md`, `PRINTING.md`, a fit coupon, `print/plates.yaml`, one STL per part
- `.github/workflows/2026-my-part-cad-check.yml` — a thin caller of [print-gate.yml](../.github/workflows/print-gate.yml)

`fit_slip` is the gap per side between two printed parts. `hole_comp` is a placeholder for the diametral amount a 0.4 mm nozzle prints a hole small, about 0.15–0.2 mm. Both live in the CAD. Hole compensation in the slicer stays off. Print the fit coupon before treating either number as yours.

To bring an older project's lessons into the new design review:

```bash
python3 print-gate/new_project.py projects/2026-my-part --name "My part" \
  --carry-from examples/demo-part/cad
```

`examples/demo-part/` is a project this command produced. It is the demo in this repository: a plain block and a fit coupon. The fit numbers in it are conservative starting placeholders. Print the coupon and replace them with your own measurements.

Check meshes locally, without the slicer:

```bash
python3 print-gate/check_print.py --project projects/2026-my-part/cad --skip-slicer --full
```

Regenerate the arranged plates with `python3 print-gate/build_print_project.py --project projects/2026-my-part/cad`. Export STLs with `cad/export.sh` (OpenSCAD, one STL per part). The scaffold also writes those STLs so the gate can run before OpenSCAD is installed.

## A reported defect becomes a check

Every defect reported from a printed part becomes three permanent things:

1. A row in that project's `DESIGN_REVIEW.md` lessons table.
2. A rule in `print_check.yaml` (a fixture expectation, or a probe on a named part).
3. A known-bad mesh in `cad/tools/fixtures/` that must fail that rule.

A fixture that passes means the check went blind, and the gate fails. The demo's fixtures cover an empty layer, a boss in mid-air, a one-sided ledge, a proud nub, a thin flap, and a thin joint. A project adds its own when a real print fails.

A project can name an installed-assembly check:

```yaml
assembly_check: tools/check_assembly.py
fixtures:
  - {file: joint_missing.stl, expect: joint_gap, run: [assembly]}
```

The gate loads that file. On the STL pass it calls `check_production()`, which returns whether the installed parts still meet the rule. A fixture with `run: [assembly]` calls `fixture_faults(filename, mesh)` and fails if the expected fault does not come back. The project slice (`--verify-project`) does not run it again.

## Workflow

`.github/workflows/print-gate.yml` is a `workflow_call` workflow. The caller passes the CAD path and, when a run should not use the X1C PETG defaults, the Orca preset names:

```yaml
jobs:
  check:
    uses: ./.github/workflows/print-gate.yml
    with:
      project: projects/2026-my-part/cad
      full: ${{ github.event.inputs.full == 'true' }}
      # machine_profile: "Bambu Lab X1 Carbon 0.4 nozzle"
      # process_profile: "0.20mm Standard @BBL X1C"
      # filament_profile: "Bambu PETG Basic @BBL X1C"
```

Use `github.event.inputs.full`, not the `inputs` context. On a pull request the `inputs` context does not exist, and a workflow that references it is recorded as a run with no jobs.

A pull request checks the STLs touched by the diff. It slices every STL when the tools, the workflow, or the project's parameters change. `workflow_dispatch` with **full** set slices every STL even when the diff is empty. A green changed-only run is not proof that the whole project still slices.

The demo caller is `.github/workflows/demo-part-cad-check.yml`. It uses these profiles, the changed-only rule, the demo fixtures, and `print/project.3mf` plus `print/fit_coupon.3mf`.

`print-gate/bambu_x1c_project_settings.json` is one example of a resolved printer profile: Bambu Lab X1 Carbon, 0.4 mm nozzle. `build_print_project.py` embeds it so Bambu Studio can open the 3MF. It is not a measurement of anyone's printer, and it is not the only profile the gate can slice. Pass other preset names with the workflow inputs when the slicer install contains them. The default 3MF name is `project.3mf`.
