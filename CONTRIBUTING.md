# Contributing

Issues and pull requests are welcome. The gate is only useful if a defect that escaped once cannot escape again.

## Add a check

A reported defect becomes three things in the project that owns the part. Do all three in the same pull request.

1. Add a row to that project's `DESIGN_REVIEW.md` lessons table: what went wrong, the rule, and the check.
2. Add the rule to `print_check.yaml`. That is a `fixtures:` entry, or a `probes:` entry on a named part.
3. Add a small known-bad mesh under `cad/tools/fixtures/` that must fail that rule.

```yaml
fixtures:
  - {file: my_defect.stl, expect: overhang_45, run: [overhang]}
```

`expect` is the rule id the mesh must trip. `run` chooses which checks the fixture goes through (`overhang`, `min_wall`, `assembly`, `bambu`). Generate the six standing meshes with:

```bash
python3 print-gate/check_print.py --project <cad> --write-fixtures
```

Confirm the new fixture fails, and that the real parts still pass:

```bash
python3 print-gate/check_print.py --project <cad> --skip-slicer --full
```

A fixture row of PASS means the bad mesh failed. A fixture row of FAIL means the check did not see the defect. A tool crash does not count as the fixture firing. If the mesh passes, the check has gone blind: fix the check, do not delete the fixture.

An installed-assembly fault uses `run: [assembly]` and a module named by `assembly_check`. The module provides `check_production()` for the real parts and `fixture_faults(filename, mesh)` for the known-bad mesh. The project slice does not run that module again.

Bambu plate and name faults use `run: [bambu]` and a `.3mf` fixture. `build_print_project.py` can write `single_plate.3mf` and `blank_name.3mf` when a fixture list contains a `.3mf`.

Keep the fixture small. A huge known-bad plate only makes CI slow.

## Pull requests

- One change of behavior per pull request when you can. A new check and its failing fixture belong together.
- Do not commit secrets, tokens, private machine addresses, or measurements copied from someone else's printer. Fit numbers in a project are that project's placeholders or that project's own coupon results.
- Describe the defect in the pull request. The template asks for the CI evidence.
- Do not merge your own pull request on a red or skipped check.

## CI

`.github/workflows/demo-part-cad-check.yml` calls the reusable workflow on `examples/demo-part`. A pull request that touches the demo, `print-gate/`, or either workflow runs it.

CI must slice with the real OrcaSlicer. A local `--skip-slicer` pass is not merge evidence. When the tools, the workflow, or parameters change, the log has to show `mode=full rebuild=1`, and the Bambu Studio verify step has to open the rebuilt 3MF files. A run with 0 jobs means the workflow file is invalid. A green changed-only run does not prove the project after those files change.

The runner is Ubuntu 24.04. OrcaSlicer 2.3.2 and Bambu Studio 02.08.02.61 are installed by the workflow. Do not point CI at a different slicer build unless you also change the install step and show a green full run.
