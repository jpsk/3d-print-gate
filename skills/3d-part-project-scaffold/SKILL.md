---
name: 3d-part-project-scaffold
description: >-
  Use when starting a new 3D-printable part project in a repo that has the
  shared print-gate kit (print-gate/ folder), to scaffold the CAD folder, fit
  parameters, checks, and CI.
---
# Scaffold a new 3D-part project

Repository work goes to a cloud agent; never clone locally.

1. Ask the cloud agent to run, from the repo root:
   `pip install -r print-gate/requirements.txt`
   `python3 print-gate/new_project.py projects/<year>-<slug> --name "<Part name>"`
   Add `--carry-from <existing project>/cad` to carry its lessons-learned forward (recommended).
2. Scaffolded files: fits/parameters (fit_slip 0.125 mm per side, hole_comp 0.18 mm by default), check config, DESIGN_REVIEW.md, PRINTING.md, fit coupon, plates.yaml, one STL per part, plus a thin caller workflow for `.github/workflows/print-gate.yml`.
3. Design parts using those parameters; never hardcode fits or hole sizes.
4. Run the `3d-print-design-gate` skill before delivering any part.
5. Every defect the user reports becomes a permanent check plus a known-bad fixture in the project's fixtures/ folder.
6. CI: a run with 0 jobs means the workflow file is invalid. A green changed-only run is not proof; after changing tools, workflows or parameters, make sure a full run (mode=full) passed before trusting it.
7. Never merge with red checks.
