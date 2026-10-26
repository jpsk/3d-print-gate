#!/bin/bash
# One STL per part. OpenSCAD 2021. Run from anywhere.
set -euo pipefail
cd "$(dirname "$0")"
mkdir -p stl
openscad -o stl/block.stl block.scad
openscad -o stl/fit_coupon_holes.stl -D 'part="holes"' fit_coupon.scad
openscad -o stl/fit_coupon_fits.stl -D 'part="fits"' fit_coupon.scad
echo "wrote stl/block.stl stl/fit_coupon_holes.stl stl/fit_coupon_fits.stl"
