# Architecture

TribuExporter is a narrow geometry bridge:

```text
Fusion BRep extraction → manufacturing-neutral IR → geometry-only TCN
```

The layers are deliberately separated:

- `tribu_exporter/model.py` contains millimetre-based geometry and validation.
- `tribu_exporter/fusion_extract.py` is the only BRep interpretation layer.
- `tribu_exporter/tcn.py` knows the supported TCN geometric primitives, but
  never receives Fusion objects.
- `tribu_exporter/addin.py` owns Fusion command inputs, reporting and file UI.
- `TribuExporter.py` is only Fusion's add-in entry point.

## Coordinate contract

The operator selects SIDE#1, P0, PX and PY. P0→PX fixes stock X. SIDE#1 fixes
Z. PY only selects the sign of the orthogonal Y axis. World XYZ is irrelevant.

SIDE#1 is `Z=0`. Every profile explicitly records its machining side and its
geometric source plane. TPA-side assignment is an access-direction decision,
not a Z-level decision.

Panel Z is top-relative: the top is `zp=0` and the bottom is `zp=-DS`.
TpaCAD's ordinary lateral faces instead use absolute thickness as their local
Y coordinate, so `Ys=zp+DS`: bottom is `Y=0`, top is `Y=DS`. SIDE3/SIDE5 use
`Xs=xp`; SIDE4/SIDE6 use `Xs=yp`. Local profile Z remains the negative inward
depth from the assigned stock side.

Fusion's fixed-direction oriented bounding box of the complete finished body
supplies spline-safe directional extrema. SIDE1 prescribes the orientation but
does not restrict the footprint: a rebate or stepped level may extend beyond
the selected top face. Fusion does not optimize or rotate the operator's
orientation.

## Profile contract

For body-derived machining geometry, the Fusion `BRepFace` is the ownership
unit. Each of that face's accepted closed boundary loops becomes one
independent TPA profile. Its first operation always declares XI/YI/ZI. No
operation chains across profile boundaries.

The operator-selected SIDE1 face has one deliberate manufacturing exception:
its outer loop establishes the top-surface envelope but is not emitted as a
second machining profile. On stepped panels that loop partly duplicates the
finished outer contour and would invite a redundant stock-profiling operation.
The mandatory whole-body silhouette owns the finished perimeter. Selected
SIDE1 inner loops and every separately exposed recessed face retain their own
independent profiles.

Faces are never merged because they are coplanar, have equal depth, share an
edge, or have connecting endpoints. Those facts may describe BRep adjacency;
they do not mean that the operator wants one machining region. A narrow rebate
floor and a large surrounding floor at the same depth remain separately
selectable because they are different Fusion faces.

- Lines stay exact L01 operations.
- Circular arcs and circles stay exact A01 operations.
- Other bounded planar curves are linearized at the declared chordal tolerance.
- Endpoints are never moved to repair a chain.
- Open, branched, non-manifold or ambiguous boundaries are not exported.

### Mandatory finished outer contour

SIDE1 always receives one independent `body_silhouette_outer` profile. It is
the non-occluded top-view projection of the complete finished BRep, not merely
the selected top face's outer loop. Consequently, rebates and stepped joints
at other geometric Z values still contribute to the contour used to trim raw
stock to the finished footprint.

Fusion's projected-body-outline operation receives the explicit curve
tolerance. The exporter records when Fusion reports that the silhouette
contains an approximated smooth curve. Multiple disconnected outer regions are
rejected instead of being guessed into one profile.

The silhouette is geometric SIDE1 data with deliberately unspecified Z; TpaCAD
setup technology defines the actual stepped depth strategy. Its first operation
still declares XI/YI so it cannot chain onto a previous profile. Selected-face
inner boundaries and later proven depth boundaries remain separate SIDE1
profiles with explicit geometric Z. Lateral joint geometry never replaces or
fragments the mandatory silhouette.

Connection is permitted only between the ordered coedges of one `BRepLoop` on
one source `BRepFace`. Coincident projected endpoints never connect profiles
across loops, source faces, sides, or depths. Connectivity checks diagnose a
loop; they never discover or assemble a larger region.

`body_silhouette_outer` is the one explicit exception to face ownership. It is
a synthetic whole-body projection used only as the mandatory stock-trim
contour. It claims no `source_face_id`, always starts a separate TPA profile,
and never supplies boundaries for any face-derived machining region.

## Access classification before profile extraction

The complete solid selected through `SIDE#1.body` is inventoried first. No
profile is constructed during this pass. Every BRep face receives exactly one
state: exposed, covered, partially exposed, ambiguous, excluded bottom,
unsupported orientation, or unsupported surface.

A planar face normal may propose a real machining face, but orientation alone
does not prove access. Geometric Z never assigns a TPA face. Directional
first-hit exposure from the proposed machining direction must prove ownership
before body-derived geometry can be emitted.

The current milestone applies that proof to SIDE1 and the four orthogonal real
lateral sides. A ray starts outside the complete body on the proposed machining
side and travels in that side's access direction. The candidate face itself
must be the first surface of the selected body hit; a different coplanar face
does not prove ownership. Other assembly bodies are ignored because bodies are
exported separately. Every proven face is then extracted alone from its own
`BRepLoop`/coedge topology, transformed into the real TPA side coordinate
system, and retains its actual local depth and `source_face_id`. The bottom is
explicitly excluded. No face is accepted by a simple edge-reaching heuristic.

Fusion ray queries can return new proxy objects for an already-known face or
body. Selected-body filtering and exact-face comparison therefore use the
native BRep entity together with its assembly occurrence path. Python wrapper
identity and runtime object IDs are never used to decide physical ownership.

## TpaCAD profile/CAM handoff

An independently started closed profile on a real TPA face is the required
handoff unit. TpaCAD can apply setup technology to the current or selected
profile, and its area-emptying workflow consumes a closed profile on the active
face. Therefore two coincident or partly coincident boundaries remain useful
when they are emitted as separate profiles: the operator may assign different
CAM treatment to each one.

A fictive face is a machining coordinate system, not a separator for two
profiles that already belong on the same real face. It is not introduced merely
to resolve coincident boundaries.

### Operator export selection

Exposure proves that a face *can* be worked from a TPA side; it does not prove
that the operator wants that geometry in this program. Extraction therefore
always builds the complete geometry IR first, after which a separate selection
policy decides which profiles reach the serializer. The mandatory whole-body
silhouette cannot be disabled. New SIDE1 candidates default enabled and new
lateral candidates default disabled.

Each choice is keyed by side, normalized access depth and the complete closed
curve signature in finished-part coordinates. The key excludes Fusion
`tempId` values and stock allowance, so it survives a document reopen and a
change of raw-stock margin. Changed or newly discovered geometry does not
silently inherit an unrelated lateral machining choice. The known/selected key
sets and numeric command settings are stored in a namespaced Fusion body
attribute only after a successful export. This selection layer neither removes
profiles from the IR nor changes endpoints, curves, side assignment or depth.

### Optional Z0 duplicate serialization filter

The Fusion-derived IR retains selected SIDE1 inner loops even when a deeper
SIDE1 face has the same complete XY boundary. A visible export checkbox may
suppress that redundant Z0 loop only while writing TCN. Matching is performed
on the complete closed chain at TCN coordinate precision; partial overlap does
not qualify. The deeper profile remains unchanged at its geometric Z.

This policy never changes the IR, never suppresses lateral profiles, and never
applies to the mandatory `body_silhouette_outer`. TpaCAD construct geometry is
not used as a substitute: construct elements remain programmed geometry and can
still participate in profile tooling even though they are excluded from piece
execution.

## Real and future fictive machining frames

Profiles reference a machining coordinate frame. SIDE1 and SIDE3-SIDE6 use
predefined real-face frames. Explicitly operator-selected inclined planar
BRepFaces use the same IR as fictive frames. They are numbered deterministically
from SIDE7 after sorting by their panel-space plane and position; selection
click order does not affect numbering.

For each selected face:

- the outward Fusion BRep normal becomes local +Z;
- projected panel +X becomes local +X, with panel +Y as the near-parallel
  fallback;
- local +Y is `Z × X`, so `X × Y = Z` and the frame is right-handed;
- P0 is the minimum local XY corner of the complete emitted trimmed boundary;
- P1 and P2 define the exact local length and height;
- P0/P1/P2 are serialized in absolute TPA piece coordinates (`Zbottom=0`);
- every loop remains owned by that one source BRepFace and is emitted at local
  Z=0 on its assigned SIDE7+.

The exact selected face must also be the first selected-body hit when approached
along its outward normal. Covered or non-planar selections fail instead of
creating plausible but unworkable coordinate systems. Detecting an inclined
face never creates a fictive TPA face by itself; explicit selection is required.

Assembly proxies are normalized deliberately: temporary whole-body silhouette
projection occurs in native component coordinates and is transformed back into
the selected occurrence context before it is combined with occurrence-space
face geometry.

This follows the TpaCAD fictive-face model (`GEO` / `GSIDE#7+` with P0, P1,
P2 and face thickness) while preserving the project rule
that machining intent, rather than arbitrary BRep topology, decides whether an
inclined plane deserves a machining coordinate system.

## Explicit boundary

The IR contains no tool, compensation, feed, spindle, pass, setup, pocket,
rabbet, engraving or drilling instruction. Manufacturing interpretation and CAM
remain in TpaCAD.
