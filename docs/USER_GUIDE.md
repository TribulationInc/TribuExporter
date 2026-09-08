# TribuExporter user guide

[README](../README.md) · [Guida italiana](GUIDA_UTENTE_IT.md)

TribuExporter exports geometry from one Fusion 360 solid to a TpaCAD `.tcn`
piece. It is designed for plywood and panel components whose removed material
is already represented in the final BRep body.

The exporter creates selectable geometry. TpaCAD remains responsible for
setups, tools, compensation, depth passes, entry and exit, sequencing, and all
other CAM decisions. Native simple blind holes are one explicit, disabled-by-
default exception: when enabled, they are emitted as TPA hole workings.
One selected, already-generated Fusion 3-axis CAM operation can also be added
as an independent trajectory when its separate checkbox is enabled.

## Before exporting

- Work from a solid body, not from sketches or an STL mesh.
- Make sure the body represents the finished part.
- Decide which physical surface will be TPA `SIDE1`.
- Know which edge should define the stock's positive X direction.
- Leave unsupported or unwanted machining regions unchecked.

## Define the panel frame

Run **Utilities → Export TpaCAD Geometry**, then select:

1. **SIDE#1** — the main planar face and the body to export. Its outward normal
   defines panel `+Z`.
2. **P0** — the reference point for the manufacturing frame.
3. **PX** — a point defining the direction `P0 → PX`. This becomes panel `+X`
   after projection onto SIDE1.
4. **PY** — a point that only chooses the positive side of `Y`. It does not
   force Y to follow a possibly non-square model edge.

The exporter always constructs an orthogonal, right-handed frame. Fusion world
XYZ and component placement do not define the TPA orientation.

## Export options

### Fictive faces (SIDE7+)

Select only planar inclined BRep faces that are intentionally useful as TpaCAD
machining coordinate systems. Each selected face receives its own additional
TPA side, starting at `SIDE7`, with an exact right-handed P0/P1/P2 frame and
its own trimmed boundary loops at local `Z=0`.

Do not select every inclined face automatically. A sloped surface made by a
saw cut or an oriented operation may be better represented later by its actual
manufacturing operation.

### Stock

**Stock allowance each side** expands the fixed-orientation body footprint.
The finished geometry is translated inside that stock; it is never stretched,
rotated, or corrected to fit.

Use **Actual stock width/height** when a larger known sheet must be declared.
Zero means the minimum calculated stock size.

### Curve chordal tolerance

The default `0.01 mm` chordal tolerance applies only when a bounded planar
curve cannot be emitted as a native TPA primitive and must be linearized.

- Lines remain exact lines.
- Circular arcs and circles remain exact arcs/circles.
- Existing endpoints are never moved.
- The tolerance is never silently relaxed.

Coordinates are calculated in millimetres and written to four decimal places;
the maximum coordinate quantization is below `0.00005 mm`.

### Duplicate SIDE1 loop filter

**Suppress SIDE1 Z=0 loop when identical deeper loop exists** prevents an
identical reference loop at the top surface from being written twice when a
selected deeper SIDE1 profile owns the same complete XY boundary. It affects
only TCN output. It never changes the geometry model or removes the mandatory
finished outer contour.

### Native Fusion blind holes

**Export native Fusion simple blind holes (W#81 CAM)** is off by default. When
enabled, the exporter scans only native `HoleFeature` timeline objects that
modify the selected body. A hole-looking cylinder, imported body, DXF circle,
extruded cut, or ordinary BRep face is not inferred as a hole.

The first supported case is an untapped, simple, distance-defined blind hole.
Its modelled entry BRep face determines the real or selected fictive TPA side;
the center is transformed into that side's local XY coordinates, and depth is
written as negative inward Z. Through All, counterbore, countersink, tapped,
clearance, ambiguous-entry, SIDE2, and otherwise unsupported HoleFeatures are
reported and omitted.

This option writes executable TPA `W#81` point workings. It specifies diameter,
but deliberately emits no `#205` tool choice. Always verify SIDE, center,
diameter, and negative depth in TpaCAD before machine execution. The checkbox
state is saved on the Fusion body after a successful export.

#### Repeated holes: use a sketch-point pattern

Do not pattern the completed HoleFeature with Fusion's Rectangular, Circular,
or Path Pattern feature when W#81 export is required. Fusion keeps the original
hole as the only native HoleFeature and represents the copies as PatternFeature
elements. TribuExporter deliberately does not expand those copies into hole
workings.

Instead:

1. Create the hole-position sketch point.
2. Pattern the sketch point or points in the sketch.
3. Create one native HoleFeature using all resulting sketch points.

TribuExporter reads every position owned by that native HoleFeature and can
emit one W#81 working per point. Boundaries produced by an unsupported feature
pattern are still ordinary final-body BRep geometry, so some may appear in the
optional profile checklist—often on a lateral face. Leave those profiles
unchecked when they are not intended as contour geometry.

### One optional Fusion CAM toolpath

**Export CAM toolpath** is off by default. While it remains off, TribuExporter
does not query Manufacture and the geometry-only TCN output is unchanged.
When enabled, choose exactly one generated, current, non-suppressed milling
operation from **CAM toolpath**. The geometry command lists 3-axis Fusion 3D
Contour and Parallel operations whose setup axes and stock agree with the
Tribu panel frame. The standalone Manufacture command can export other
generated 3-axis milling operations for careful experimentation.

The resolved tool-center motions are appended to `SIDE1` as one independent
open L01/A01 profile. Fusion-native constant-Z XY arcs are preserved as A01.
Full circles are four continuous 90° A01 records. Fusion-native constant-radius
XY helices are retained and emitted as one helicoidal A01 per native circular
record. XY spirals are retained in `.tribupath` before explicit
tolerance-controlled linearization. Other unsupported circular planes are
linearized by Fusion's post engine at the selected post tolerance.

The CAM window exposes every approximation separately:

- **Post linearization tolerance** controls unsupported post circular moves and
  retained spiral fallback. It is never changed automatically.
- **Fit fallback XY line chains to A01** and its tolerance control optional arc
  recovery from eligible constant-Z cutting lines.
- **Merge exactly collinear motions** removes only redundant points with the
  same feed and movement class; it introduces no geometric approximation.
- **Simplify 3D line chains** is optional and off by default. Its tolerance is
  a maximum measured point-to-chord deviation. Pass/movement boundaries, Z
  extrema and turns of 15 degrees or more are preserved.
- **Warn above complete TCN lines** is warning-only. Export remains available
  and no tolerance is silently relaxed.
- **Reset CAM parameters to safe defaults** restores all values in this CAM
  section. It does not regenerate or modify the selected Fusion operation.

The window also reports Fusion's smoothing mode. When it says
`redistribute`/Evenly spaced points, regenerate the Fusion operation with
**Fit arcs** when suitable; otherwise Fusion has already converted eligible
curves into dense point sequences.

No W#89, tool, compensation, feed, or strategy is written to TCN. Fusion feed
and movement classes are retained internally for continuity decisions and
diagnostics. Apply one normal setup to the complete trajectory in TpaCAD. The
intermediate `.tribupath` exists only in a temporary directory during export
and is deleted automatically.

For long paths, Fusion may instead post the bundled Tribu post manually to a
persistent `.tribupath`. Convert that file outside Fusion from the repository
directory:

```powershell
python tribu_cam_convert.py "C:\path\operation.tribupath"
```

This produces `operation_TRIBU_CAM.tcn` beside the input. It performs the same
native-primitive preservation and configurable compression without occupying
Fusion's Python/UI process. Run `python tribu_cam_convert.py --help` for all
controls. It never adds W#89 or a tool.

For the original standalone workflow, select exactly one generated operation
in the Manufacture browser and run **Export Selected CAM Toolpath to TCN** from
Manufacture → Utilities → Add-Ins. This writes only that trajectory, without
the BRep geometry profiles; it also retains support for the previously tested
3-axis milling operations such as Adaptive.

## Choose profiles

After SIDE1, P0, PX, and PY are complete, the **Profiles to export** checklist
is populated.

- The finished whole-body outer contour is mandatory.
- New SIDE1 candidates default on.
- New lateral SIDE3–SIDE6 candidates default off.
- Selected inclined faces are emitted on SIDE7+.
- Successful export stores numeric settings and profile choices on the Fusion
  body so the next export can restore them.

Accessibility decides which TPA side can own a face; the checklist decides
whether you want that geometry in the current program. One Fusion BRep face is
never merged with another merely because both have the same depth or touching
endpoints.

## What to expect in TpaCAD

![Independent profiles exported to TpaCAD](images/tpacad-independent-profiles.png)

The example shows a finished outer contour, a recessed T-shaped contour, and a
handle opening. Each accepted boundary starts as an independent TPA profile,
so an operator can select it and assign its own setup without bridging into a
neighbouring contour.

Real lateral sides use ordinary TpaCAD local coordinates:

```text
-Y → SIDE3    +X → SIDE4    +Y → SIDE5    -X → SIDE6
```

On lateral sides, local Y runs from the stock bottom (`0`) to the top (`DS`),
and negative local Z is inward machining depth. Fictive faces appear in
addition to the six standard sides as `SIDE7+`.

## Mandatory review

Before creating executable CAM:

1. Check `DL`, `DH`, and `DS` against the real stock.
2. Open every populated SIDE and inspect its local orientation.
3. Verify the mandatory outer contour against the complete Fusion body.
4. Click every contour independently and confirm no unrelated profile is
   selected with it.
5. Verify every geometric Z/depth.
6. Inspect linearized curves at the declared tolerance.
7. Confirm no unexpected or unsupported region was exported.
8. If native holes were enabled, verify every W#81 SIDE, X, Y, negative depth,
   and diameter; confirm no unwanted hole-looking geometry became a working.
9. Apply technology in TpaCAD and run the normal machine-side simulation and
   safety checks.
10. If CAM export was enabled, verify the selected operation, the complete
    ordered trajectory, every Z transition, L01/A01 counts, warning threshold,
    selected tolerances and reported maximum deviation before applying one
    setup to it. Helicoidal A01 remains subject to TpaCAD round-trip and actual
    Busellato validation before production use.

Stop if any stock dimension, side assignment, contour, depth, or orientation
does not match the Fusion model.
# Optional blade cuts

Enable **Add Blade cut on fictive faces** after selecting the desired inclined
faces to inspect an analysis plan using a local blade profile. Executable blade
export is currently blocked pending verification of the custom PPC LAME/W95
reference mapping. The checkbox stays armed during profile and geometry changes;
only a new body or command resets it. Disable it to export ordinary geometry.
See [Blade cuts](BLADE_CUTS.md) for evidence, configuration and remaining limits.
