# TribuExporter

**Fusion 360 geometry exporter for TpaCAD and CNC panel work.**

[English guide](docs/USER_GUIDE.md) · [Guida italiana](docs/GUIDA_UTENTE_IT.md) · [Architecture](docs/ARCHITECTURE.md) · [Testing](docs/TESTING.md)

TribuExporter transfers the manufacturing geometry of a panel-like Fusion 360
solid into a profile-first `.tcn` file. The objective is simple: preserve the
modelled part and make its contours easy to select in TpaCAD. Native simple
blind holes, selected fictive-face blade analysis, and one explicitly selected
Fusion toolpath are opt-in features; all are disabled by default.

```text
Fusion 360 solid → TribuExporter → independent TPA profiles → TpaCAD CAM
```

Geometry profiles leave tool and setup assignment to TpaCAD. Optional native-hole
output never selects a tool. The explicit blade option uses an operator-supplied
machine profile for analysis. Executable blade output is blocked until the
custom PPC LAME/W95 reference mapping is verified against finished geometry.
See [blade setup and supported scope](docs/BLADE_CUTS.md).

![Independent outer, recessed and internal profiles in TpaCAD](docs/images/tpacad-independent-profiles.png)

## What works today

- An operator-defined panel frame using `SIDE#1`, `P0`, `PX`, and `PY`.
- Rectangular raw stock with configurable allowance or explicit dimensions.
- A mandatory finished outer silhouette on `SIDE1`, derived from the complete
  body rather than only the selected top face.
- Independent closed profiles owned by individual Fusion BRep faces.
- Multiple profiles and depths on the same TPA face.
- Real lateral geometry on `SIDE3` through `SIDE6`, transformed into each
  side's native TpaCAD coordinate system.
- Explicitly selected planar inclined faces as additional fictive faces
  `SIDE7+`.
- **Add Blade cut on fictive faces**: analysis of selected exterior inclined
  faces, with whole-body preservation checks and a local machine profile.
  Fictive geometry is retained; executable blade export is currently blocked.
- Exact lines and circular arcs/circles.
- Other bounded planar curves linearized at an explicit chordal tolerance.
- A persistent profile checklist, stored per Fusion body after export.
- Optional translation of native Fusion simple blind `HoleFeature` objects to
  TPA hole workings. Hole-looking BRep cylinders are never inferred.
- Optional export of exactly one generated 3-axis Fusion CAM operation as one
  independent SIDE1 trajectory. Native XY arcs remain A01; native XY helices
  use A01 helicoidal development. Post linearization, fallback arc fitting and
  optional 3D simplification have separate explicit tolerances. No TPA setup or
  tool is generated.

Fusion feature-pattern copies of a hole are not translated to W#81. For a
repeated drilling layout, pattern the sketch points first and create the native
HoleFeature from those points. Pattern-generated BRep boundaries may still
appear as optional profiles and should be unchecked when they are not wanted.

Lines are not tessellated, arcs remain arcs, and endpoints are never moved to
repair a contour. Unsupported or ambiguous geometry is reported instead of
being guessed.

## Install

1. Clone or download this repository.
2. In Fusion, open **Utilities → Add-Ins → Scripts and Add-Ins**.
3. Add the folder containing `TribuExporter.py` and
   `TribuExporter.manifest`.
4. Run **TribuExporter**.
5. Use **Utilities → Export TpaCAD Geometry**.

The command asks for the main face, three frame references, stock settings,
curve tolerance, and any intentionally selected inclined faces. After the
frame is complete, choose which detected profiles should be written and review
the pre-export report.

See the [English user guide](docs/USER_GUIDE.md) or the
[Italian user guide](docs/GUIDA_UTENTE_IT.md) for the complete first-export
workflow.

## Project boundary

This is a specialized bridge for panel components that are already modelled
with manufacturing in mind. It is not a generic CAD translator, a nesting
system, a CAM kernel, or an automatic feature-recognition engine.

The project is experimental. Opening successfully in TpaCAD proves file and
geometry compatibility; it does not by itself prove that a program is safe to
run on a CNC machine. Inspect every face, profile, coordinate, depth, stock
dimension, and assigned setup before execution.

## Development

The geometry model and TCN serializer can be tested without Fusion:

```powershell
python -m unittest discover -s tests -v
```

Tests use synthetic, redistributable fixtures. Proprietary TpaCAD manuals,
installed product samples, Busellato macros, and private machine programs are
development references only and are intentionally excluded from this public
repository.

A `.tribupath` manually generated in Fusion with the bundled post can also be
converted outside Fusion, keeping the Fusion UI completely out of parsing and
arc fitting:

```powershell
python tribu_cam_convert.py "C:\path\operation.tribupath"
```

The output is `<input>_TRIBU_CAM.tcn`. Use `--help` for output-path, separate
post/arc/3D tolerances, exact collinear merging, line-count warning and
overwrite options. This remains a geometry-only trajectory: no W#89 setup or
tool is added.

Contributions are welcome; read [CONTRIBUTING.md](CONTRIBUTING.md) first.

## License and names

Released under the [MIT License](LICENSE).

TribuExporter is an independent project and is not affiliated with or endorsed
by Autodesk, TPA, TpaCAD, or Busellato. Product names are trademarks of their
respective owners.
