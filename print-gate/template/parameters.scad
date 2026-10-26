// {{NAME}} — shared fit numbers. OpenSCAD 2021.
//
// X-Y hole compensation in the slicer stays off. Every allowance is here.
//
// fit_slip is the gap per side between two printed parts. 0.125 mm per
// side (0.25 mm on the diameter) is a conservative starting clearance
// for a 0.4 mm nozzle. It is not a measurement from a particular
// printer. Printed-to-printed pairs do not get hole_comp: both sides
// shrink together. Print the fit coupon and replace this number.
//
// hole_comp is diametral, on a female feature that mates with metal or
// a hard gauge. A common 0.4 mm nozzle prints holes about 0.15–0.2 mm
// small. 0.18 mm is a placeholder inside that band. The fit coupon is
// what changes it: measure a labeled hole and set hole_comp to
// (CAD diameter − measured diameter), then re-export.
//
// Press, free, and loose are diametral. fit_slip is per side.

fit_press = 0.12;   // diametral interference
fit_slip = 0.125;   // per side. Printed-to-printed default.
fit_free = 0.45;    // diametral, free running or sliding
fit_loose = 0.55;   // diametral, locate then clamp
hole_comp = 0.18;   // diametral. Inside the 0.15–0.2 mm X1C band.
