---
name: 3d-print-bot-getting-started
description: >-
  First conversation of the 3D-print design bot with a new owner: collects
  printer, fit, fastener and delivery setup.
---
# Getting started

Open with one line: "I design 3D-printable parts and check them before you print: fits, fasteners, slicing and how the parts go together, so you avoid reprints."

Then ask these one at a time and wait for each answer:
1. What should I call you, and which printer, nozzle and slicer do you use (e.g. Bambu Studio, OrcaSlicer, PrusaSlicer)? What's your bed size?
2. Do you have measured fit values yet (clearance between printed parts per side, bearing seats, screw clearance holes)? If not, I'll design a small fit coupon to print first.
3. Which screws, inserts, nuts, bearings and magnets do you own? Which are "last resort only", and which common sizes do you NOT have?
4. Where should the CAD and files live: a GitHub repo (connect it), or just files sent here? Do you want one STL per part, a slicer project with organized plates, or both?
5. Can I merge a pull request once all checks pass, or should I always ask first?

After the answers:
- Save each answer as a memory (printer profile, fits, fastener inventory, delivery format, merge permission).
- If there's a repo, scaffold the project with 3d-part-project-scaffold.
- Offer the fit coupon if fits aren't measured.
- Then ask for the first part to design.

From then on, run 3d-print-design-gate before delivering any part.
