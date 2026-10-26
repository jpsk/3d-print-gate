---
name: New check
about: A printed defect that should become a permanent rule and a failing fixture
title: ""
---

## What went wrong on the print

## Rule

The rule in one sentence. What must be true before a part is printable.

## Fixture

Describe a small known-bad mesh that must fail the rule. A fixture that passes means the check went blind.

- Suggested filename:
- Expected rule id:
- `run:` list (`overhang`, `min_wall`, `assembly`, `bambu`, …):

## Where it lives

- [ ] A row in that project's `DESIGN_REVIEW.md` lessons table
- [ ] A rule or fixture entry in `print_check.yaml`
- [ ] The known-bad mesh under `cad/tools/fixtures/`
