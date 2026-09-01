# TribuExporter

**Fusion 360 manufacturing-geometry exporter for TpaCAD and CNC panel workflows.**

TribuExporter is a Fusion 360 add-in focused on a very specific problem: taking a panel that is already correctly modeled in CAD and transferring its **manufacturing-relevant geometry** into TpaCAD without rebuilding the part face by face, flattening it into a pile of DXFs.

---

## Why this exists

For panel-based CNC work, the CAD model often already contains the information we actually care about, moving that information into TpaCAD manually can involve repeated projection, DXF export/import, face reconstruction, and re-establishing geometry that already existed correctly in Fusion.

That works.

It is also a surprisingly effective way to spend time reproducing geometry instead of cutting wood.

TribuExporter aims to make the handoff more direct while deliberately leaving **tool choice, compensation, feeds, depths, passes, entry/exit strategy, ordering and machine-specific technology** where they belong: in TpaCAD.

---

## Current workflow

The intended workflow is:

```text
Fusion 360 BRep model
        |
        v
user-defined manufacturing frame
        |
        v
rectangular TPA stock
        |
        v
manufacturing-geometry decomposition
        |
        v
independent TPA geometric profiles
        |
        v
TpaCAD
        |
        +--> Apply Setup
        +--> tool compensation
        +--> Z advancement / depth passes
        +--> entry / exit
        +--> sequencing / optimization
        |
        v
machine
```

The important boundary is:

```text
TribuExporter = geometry
TpaCAD        = technology / CAM
```

---

## What currently works

The project is under active development, but the following concepts have already been validated in TpaCAD:

- user-defined manufacturing orientation;
- fixed-orientation stock calculation;
- corner-anchored placement with configurable stock allowance;
- rectangular TPA workpiece generation (`DL / DH / DS`);
- mandatory whole-body top-view silhouette as the finished SIDE1 outer contour;
- geometry-only export on `SIDE#1`;
- real SIDE#3–SIDE#6 coordinate transforms and exact-face directional access
  classification (awaiting golden-body TpaCAD acceptance);
- straight-line profiles;
- native planar arcs;
- spline / NURBS contours through tolerance-controlled linearization;
- multiple independent profiles on the same TPA face;
- independent profiles at different geometric Z levels;
- selected SIDE1 inner loops without duplicating the selected top face's outer
  reference boundary;
- face-owned recessed profiles: each exposed Fusion surface is emitted from
  only its own boundary loops and retains its source-face identity;
- separate later assignment of TpaCAD technology to those profiles.

A typical validated example is a loudspeaker baffle containing:

```text
Profile 1: outer panel contour
Profile 2: speaker cut-out
```

Both profiles exist in the same TCN program and remain independently selectable and machinable in TpaCAD.

---

## Near-term roadmap

### Face-owned multi-level BRep extraction — awaiting golden-body acceptance

Export additional safe planar contours from the Fusion body as independent profiles:

- multiple loops on `SIDE#1`;
- multiple profiles at the same Z level;
- profiles on planes parallel to `SIDE#1`;
- recessed geometry;
- ordered BRep loop/coedge traversal;
- explicit logging of profile source, Z, closure and curve count.

The manufacturing-region discriminator is the Fusion `BRepFace`. Coplanarity,
equal Z, shared edges and connected endpoints never merge faces. The mandatory
whole-body silhouette is a separately started synthetic stock-trim profile and
does not own any Fusion face. The selected SIDE1 outer loop is reference-only;
its inner loops and separately exposed recessed faces remain exportable.

The acceptance criterion is intentionally simple:

> Open the generated `.tcn` and click every exported contour independently in TpaCAD.

### Orthogonal lateral geometry — implemented, awaiting acceptance

Classify accessible planar geometry on the real TPA side families:

```text
-Y -> SIDE#3
+X -> SIDE#4
+Y -> SIDE#5
-X -> SIDE#6
```

The conservative implementation assigns a candidate side from the outward face
normal, then requires that exact Fusion face to be the first face of the
selected body hit from that machining direction. Proven faces are transformed
into the corresponding TPA side's actual local coordinates and emitted from
their own loops. It still requires golden-body validation in Fusion and TpaCAD.

### Inclined planar faces

Detect simple planar bevels and expose manufacturing choices without forcing one strategy.

Possible backends include:

- TPA fictive / inclined face;
- standard oriented milling;
- optional machine-specific saw adapters.

Machine-specific macros must remain adapters, not dependencies of the geometry core.

---

## Explicit non-goals

TribuExporter is **not** currently trying to become:

- a general STEP-to-TpaCAD converter;
- a generic 3D CAM system;
- a five-axis surface machining engine;
- a nesting optimizer;
- an automatic feature-recognition system;
- an automatic tool/feed/speed selector;
- a replacement for TpaCAD technology assignment.

The project is deliberately specialized around **CAD-ready panel components**.

That limited scope is a feature.

---

## TpaCAD philosophy

TpaCAD is very capable once geometry is expressed in the form it expects.

TribuExporter therefore avoids reproducing functionality that TpaCAD already handles well, including:


---

## Project status

**Experimental / active development.**

V1 now inventories every face of the selected solid before constructing any
body-derived profile. Orientation proposes a machining frame; directional
exposure must prove ownership. The selected SIDE1 face contributes its inner
loops, while its outer reference loop is replaced by the mandatory finished
silhouette. Other first-hit-proven faces are exported one face at a time from
their own loops, with explicit source face and depth diagnostics. Orthogonal
lateral faces use the same exact-face access proof and real-side transformation.
Fictive-face emission remains gated until that future path is validated in
Fusion and TpaCAD.

The generated output must be inspected in TpaCAD and validated with the machine workflow before production use.

This project deals with CNC machinery. Never assume that a geometrically valid file is automatically a safe machine program.

---

## Installation

1. Clone or download this repository.
2. In Fusion, open **Utilities → Add-Ins → Scripts and Add-Ins**.
3. Add/select the directory containing `TribuExporter.py` and
   `TribuExporter.manifest`.
4. Run the add-in.
5. Use **Utilities → Export TpaCAD Geometry**.

The command asks for SIDE#1, P0, PX and PY, stock allowance and the explicit
curve chordal tolerance. It presents a geometric report before allowing a file
to be written. Unsupported regions require an explicit decision to continue.

See [Architecture](docs/ARCHITECTURE.md) and [Testing](docs/TESTING.md) before
using output in a production workflow.

---

## Development

The core model and serializer run without Fusion:

```powershell
python -m unittest discover -v
```

The test fixtures are synthetic and redistributable. Proprietary manuals,
installed TPA samples, machine macros and private TCN programs are intentionally
excluded from this public repository.

---

## Contributing

Yes please.

---

## Disclaimer

TribuExporter is an independent project and is not affiliated with or endorsed by Autodesk, TPA, TpaCAD, or Busellato.

Fusion 360, TpaCAD and other product names are trademarks of their respective owners.
