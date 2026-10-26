## What changed

<!-- One short paragraph. Link the defect or the part this is about. -->

## Check

A new or changed rule needs a known-bad fixture in the project's `cad/tools/fixtures/` that fails that rule. A fixture that passes means the check went blind.

- [ ] If this adds or changes a check, `print_check.yaml` names the fixture and the rule it must fail
- [ ] The fixture fails for that rule locally (`python3 print-gate/check_print.py --project <cad> --skip-slicer --full`)
- [ ] CI on this pull request ran the real slicer. A `--skip-slicer` pass is not merge evidence
- [ ] If `print-gate/`, a workflow, or `parameters.scad` / `print_check.yaml` changed, the log shows `mode=full rebuild=1`

## Plates

<!-- Leave blank if this PR does not change meshes or plates. Otherwise note time and filament from the job summary. -->
