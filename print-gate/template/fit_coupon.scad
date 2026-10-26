// Fit coupon for the Bambu X1C. Bed at z = 0. One STL per part.
//
//   openscad -o stl/fit_coupon_holes.stl -D 'part="holes"' fit_coupon.scad
//   openscad -o stl/fit_coupon_fits.stl -D 'part="fits"' fit_coupon.scad
//
// holes: through holes at the CAD size, plus one hole that already
// includes hole_comp, so a caliper shows how small the printer went.
// No lead-in.
//
// fits: three slots for a 10 mm printed male. The widths are
// 10 + 2 * clearance, at 0.10, fit_slip (0.125), and 0.15 per side.
// 0.125 is the part-to-part default.
//
// Print both at 2 walls, 2 shells, 15% gyroid. PETG, 0.4 mm nozzle, 0.2 mm.
include <parameters.scad>

part = "holes"; // holes | fits

module holes() {
    difference() {
        cube([60, 40, 4]);
        translate([12, 14, -1]) cylinder(h = 6, d = 3.2, $fn = 48);
        translate([24, 14, -1]) cylinder(h = 6, d = 3.4, $fn = 48);
        translate([36, 14, -1]) cylinder(h = 6, d = 5.2, $fn = 48);
        translate([48, 14, -1]) cylinder(h = 6, d = 5.4, $fn = 48);
        translate([12, 28, -1]) cylinder(h = 6, d = 3.2 + hole_comp, $fn = 48);
    }
}

module fits() {
    difference() {
        cube([56, 28, 6]);
        translate([4, 6, -1]) cube([10 + 2 * 0.10, 16, 8]);
        translate([18, 6, -1]) cube([10 + 2 * fit_slip, 16, 8]);
        translate([32, 6, -1]) cube([10 + 2 * 0.15, 16, 8]);
    }
}

if (part == "holes") holes();
if (part == "fits") fits();
