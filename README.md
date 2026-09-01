# TribuExporter

**Fusion 360 geometry exporter for TpaCAD and CNC panel work.**

[English guide](docs/USER_GUIDE.md) · [Guida italiana](docs/GUIDA_UTENTE_IT.md) · [Architecture](docs/ARCHITECTURE.md) · [Testing](docs/TESTING.md)

TribuExporter transfers the manufacturing geometry of a panel-like Fusion 360
solid into a geometry-only `.tcn` file. The objective is simple: preserve the
modelled part, make its contours easy to select in TpaCAD, and leave CAM to the
operator.

```text
Fusion 360 solid → TribuExporter → independent TPA profiles → TpaCAD CAM
```

TribuExporter does **not** choose tools, compensation, feeds, passes, entry
moves, setup technology, or machining order.

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
- Exact lines and circular arcs/circles.
- Other bounded planar curves linearized at an explicit chordal tolerance.
- A persistent profile checklist, stored per Fusion body after export.

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

Contributions are welcome; read [CONTRIBUTING.md](CONTRIBUTING.md) first.

## License and names

Released under the [MIT License](LICENSE).

TribuExporter is an independent project and is not affiliated with or endorsed
by Autodesk, TPA, TpaCAD, or Busellato. Product names are trademarks of their
respective owners.
