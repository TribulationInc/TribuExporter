# Blade cuts

TribuExporter can generate executable blade workings for the custom Busellato
Jet Master T configuration. The mapping has been validated by the project owner
with repeated tests on the CNC. It uses the configured `lame.tmcr` working and
the machine profile in `Tools/bladeGenerica.json`.

Two independent options are available:

- **Use blade for profile cuts** creates native `BLADEX`/`BLADEY` workings for
  eligible sides of the finished body's XY bounding box.
- **Add Blade cut on fictive faces** creates one `BLADEXY` working for every
  selected inclined fictive face.

Either option requires a valid blade profile. Both can be enabled together.

## Machine contract

The profile must use `z_reference: "busellato_lame_w95"`. For this machine:

- `BLADEX` and `BLADEY` are the native axis-aligned squaring workings;
- `BLADEXY` accepts the calculated Alpha and the machine-verified complementary
  Beta convention;
- X/Y locate the blade-plane trace on SIDE1 at Z=0;
- Zp and Z2 are signed distances along the blade-depth coordinate;
- the vertical component of the programmed depth is
  `abs(Z) × sin(abs(Beta))`;
- compensation places the blade width on the waste side;
- `normal_offset_mm` is a calibrated residual offset and is not half the kerf;
- `breakthrough_mm` is extra travel along the blade-depth coordinate;
- `score_then_full` emits a shallow Zp pass followed by the full-depth Z2 pass;
- chord calculation remains off for these straight exterior cuts.

The configured travel angles are preferences for choosing the direction of a
`BLADEXY` cut. They do not restrict arbitrary Alpha values: when no preference
matches the plane exactly, the planner uses the deterministic plane-intersection
direction. Opposite directions retain their own compensation result.

## Profile squaring

The candidate rectangle is derived from the finished body's width and height,
translated into stock coordinates by the configured allowance. A candidate
side becomes a blade cut only when at least one straight outer-profile segment
lies on that finite bbox side. Collinear splits still produce one cut.

The four candidate sides are considered in counter-clockwise order; only the
eligible ones are emitted:

1. bottom edge: `BLADEX`;
2. right edge: `BLADEY`;
3. top edge: `BLADEX`;
4. left edge: `BLADEY`.

Each cut extends by one blade radius plus `end_clearance_mm` at both ends. The
blade plane stays on the finished bbox side while compensation puts the kerf in
waste.

Every outer segment produced by a bbox cut or by the SIDE1 trace of a selected
fictive-face `BLADEXY` is removed individually. The remaining cyclic runs are
written as separate open profiles, each with an explicit initial point. Curves,
diagonals and other uncovered geometry therefore remain machinable. When every
segment is blade-produced, `FINAL_OUTER_CONTOUR` is omitted completely.

## Fictive-face cuts

For each selected inclined planar face, extraction first builds the SIDE7+
frame and verifies that the face is exterior and coincident with its requested
cutting plane. The planner then intersects that plane with the complete stock,
checks blade reach, derives Alpha/Beta and waste-side compensation, and emits a
`BLADEXY` on SIDE1. The SIDE7+ frame and its profiles remain in the TCN for
subsequent work on that physical face.

## Execution order

The TCN is deliberately ordered as follows:

1. internal geometry profiles, native holes and optional Fusion CAM;
2. eligible bbox `BLADEX`/`BLADEY` trimming cuts, when enabled;
3. selected inclined `BLADEXY` fictive-face cuts.

This keeps internal machining ahead of the operations that release and finish
the outer panel.

## Machine profile

The active validated profile is `Tools/bladeGenerica.json`. It records tool
3000, blade diameter 300 mm, width 3.2 mm, usable penetration 85 mm, the
Busellato W95 reference, Beta sign, pass policy and clearances used by the
exporter. Null speed fields leave spindle and feeds to the machine/macro tool
technology.

The original CNC tool-table screen remains the evidence for diameter, kerf and
machine technology. Values such as LA, C-axis offset and wear belong to the CNC
tool record and are not reapplied as exporter geometry offsets.

## Validation

Run:

```powershell
python -m unittest discover -v
```

The blade tests cover the verified W95 depth projection, complementary Beta,
arbitrary Alpha, opposite directions, Z2 scoring, stock coverage, stale-plan
detection, quantized-plane reconstruction, bbox squaring, collinear silhouette
splits, partial bbox eligibility, retained curves, independent residual runs,
serialization fields and blade-phase ordering. Automated tests complement the
completed CNC validation; they do not replace normal operator checks for a new
part or stock setup.
