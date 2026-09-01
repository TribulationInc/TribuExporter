"""Autodesk Fusion BRep extraction for panel-like solids.

The selected SIDE#1 and P0/PX/PY define the only coordinate frame. Geometry is
assigned by supported outward/access direction, then projected into the actual
local coordinate system of SIDE1/3/4/5/6. Geometric Z alone never assigns a
machining side.
"""

from __future__ import annotations

from dataclasses import dataclass
import math

import adsk.core
import adsk.fusion

from .fusion_identity import (
    occurrence_path as _occurrence,
    same_contextual_entity as _same_contextual_entity,
)
from .model import (
    Arc2D, CurveChain2D, Line2D, PanelIR,
    FaceFactIR, FaceOwnershipIR, GeometricPlaneIR, MachiningFrameIR,
    MachiningFrameKind, MachiningSide, OwnershipState, PlanarProfileIR,
    ProfileZMode, StockAllowance, UnsupportedRegionIR, Vec2,
    classify_orthogonal_normal, same_geometric_depth,
    panel_to_side_coordinates,
)


CM_TO_MM = 10.0
EPS_CM = 1e-7
PLANE_ANGLE_TOLERANCE_DEG = 0.01
PLANE_LEVEL_TOLERANCE_MM = 0.01


@dataclass(frozen=True)
class V3:
    x: float
    y: float
    z: float

    def __sub__(self, other: "V3") -> "V3":
        return V3(self.x - other.x, self.y - other.y, self.z - other.z)

    def scaled(self, factor: float) -> "V3":
        return V3(self.x * factor, self.y * factor, self.z * factor)

    def dot(self, other: "V3") -> float:
        return self.x * other.x + self.y * other.y + self.z * other.z

    def cross(self, other: "V3") -> "V3":
        return V3(
            self.y * other.z - self.z * other.y,
            self.z * other.x - self.x * other.z,
            self.x * other.y - self.y * other.x,
        )

    @property
    def length(self) -> float:
        return math.sqrt(self.dot(self))

    def normalized(self) -> "V3":
        if self.length <= EPS_CM:
            raise ValueError("Cannot normalize a zero-length vector")
        return self.scaled(1.0 / self.length)


@dataclass(frozen=True)
class PanelFrame:
    origin: V3
    x_axis: V3
    y_axis: V3
    z_axis: V3

    def model_to_local_cm(self, point: V3) -> V3:
        delta = point - self.origin
        return V3(delta.dot(self.x_axis), delta.dot(self.y_axis),
                  delta.dot(self.z_axis))


def _v3_point(point) -> V3:
    return V3(point.x, point.y, point.z)


def _v3_vector(vector) -> V3:
    return V3(vector.x, vector.y, vector.z)


def _vector(value: V3):
    return adsk.core.Vector3D.create(value.x, value.y, value.z)


def _native_id(entity) -> str:
    native = getattr(entity, "nativeObject", None) or entity
    temp_id = getattr(native, "tempId", None)
    if temp_id is None:
        return f"runtime:{id(native)}"
    return str(temp_id)


def require_frame_selection(face, p0, px, py) -> None:
    context = _occurrence(face)
    if any(_occurrence(item) != context for item in (p0, px, py)):
        raise ValueError("SIDE#1 and P0/PX/PY must come from the same occurrence")
    face_vertex_ids = {_native_id(face.vertices.item(i))
                       for i in range(face.vertices.count)}
    for label, vertex in (("P0", p0), ("PX", px), ("PY", py)):
        if _native_id(vertex) not in face_vertex_ids:
            raise ValueError(f"{label} must be a boundary vertex of SIDE#1")


def make_panel_frame(face, p0, px, py) -> PanelFrame:
    require_frame_selection(face, p0, px, py)
    origin = _v3_point(p0.geometry)
    x_raw = _v3_point(px.geometry) - origin
    y_raw = _v3_point(py.geometry) - origin
    ok, normal = face.evaluator.getNormalAtPoint(face.centroid)
    if not ok:
        raise ValueError("Could not evaluate SIDE#1 normal")
    z_axis = _v3_vector(normal).normalized()
    x_projection = x_raw - z_axis.scaled(x_raw.dot(z_axis))
    if x_projection.length <= EPS_CM:
        raise ValueError("P0→PX is perpendicular to SIDE#1")
    x_axis = x_projection.normalized()
    y_candidate = z_axis.cross(x_axis).normalized()
    y_projection = y_raw - z_axis.scaled(y_raw.dot(z_axis))
    component = y_projection.dot(y_candidate)
    if abs(component) <= EPS_CM:
        raise ValueError("PY lies on the X axis; choose the desired +Y side")
    y_axis = y_candidate if component > 0 else y_candidate.scaled(-1.0)
    return PanelFrame(origin, x_axis, y_axis, z_axis)


def panel_extents(face, frame: PanelFrame) -> tuple[float, float, float, float, float]:
    """Return fixed-orientation bounds of the complete finished body.

    SIDE#1 defines orientation and Z=0, but it is not assumed to contain the
    body's largest XY footprint. Rebates and stepped joints can legitimately
    extend beyond the selected top face.
    """
    manager = adsk.core.Application.get().measureManager
    x_direction, y_direction = _vector(frame.x_axis), _vector(frame.y_axis)
    body_box = manager.getOrientedBoundingBox(face.body, x_direction, y_direction)
    if body_box is None:
        raise ValueError("Fusion could not compute prescribed-orientation bounds")
    center = frame.model_to_local_cm(_v3_point(body_box.centerPoint))
    half_x = body_box.length * CM_TO_MM / 2.0
    half_y = body_box.width * CM_TO_MM / 2.0
    return (
        center.x * CM_TO_MM - half_x,
        center.x * CM_TO_MM + half_x,
        center.y * CM_TO_MM - half_y,
        center.y * CM_TO_MM + half_y,
        body_box.height * CM_TO_MM,
    )


def _point2d(point, frame: PanelFrame, xmin: float, ymin: float,
             expected_depth_mm: float, side: MachiningSide,
             allowance: StockAllowance, stock_width: float,
             stock_height: float, stock_thickness: float) -> Vec2:
    local = frame.model_to_local_cm(_v3_point(point))
    xp = local.x * CM_TO_MM - xmin + allowance.x_minus
    yp = local.y * CM_TO_MM - ymin + allowance.y_minus
    zp = local.z * CM_TO_MM
    projected, depth = panel_to_side_coordinates(
        side, xp, yp, zp, stock_width, stock_height, stock_thickness,
    )
    if abs(depth - expected_depth_mm) > PLANE_LEVEL_TOLERANCE_MM:
        raise ValueError(
            f"Boundary depth={depth:.6f} differs from SIDE{int(side)} plane "
            f"depth={expected_depth_mm:.6f} mm"
        )
    return projected


def _edge_endpoints(coedge):
    edge = coedge.edge
    if edge.startVertex is None or edge.endVertex is None:
        return None
    if coedge.isOpposedToEdge:
        return edge.endVertex.geometry, edge.startVertex.geometry
    return edge.startVertex.geometry, edge.endVertex.geometry


def _clockwise(normal, coedge, frame: PanelFrame, side: MachiningSide) -> bool:
    curve_normal = _v3_vector(normal).normalized()
    if side == MachiningSide.SIDE1:
        basis_normal = frame.x_axis.cross(frame.y_axis).normalized()
    elif side in (MachiningSide.SIDE3, MachiningSide.SIDE5):
        basis_normal = frame.x_axis.cross(frame.z_axis).normalized()
    else:
        basis_normal = frame.y_axis.cross(frame.z_axis).normalized()
    if abs(curve_normal.dot(basis_normal)) < 0.999:
        raise ValueError(f"Circular boundary is not coplanar with SIDE{int(side)}")
    return (curve_normal.dot(basis_normal) > 0) == bool(coedge.isOpposedToEdge)


def coedge_segments(coedge, frame: PanelFrame, xmin: float, ymin: float,
                    depth_mm: float, curve_tolerance_mm: float,
                    side: MachiningSide, allowance: StockAllowance,
                    stock_width: float, stock_height: float,
                    stock_thickness: float,
                    reverse: bool = False) -> list[Line2D | Arc2D]:
    geometry = coedge.edge.geometry
    endpoints = _edge_endpoints(coedge)
    line = adsk.core.Line3D.cast(geometry)
    if line is not None and endpoints:
        result = [Line2D(
            _point2d(endpoints[0], frame, xmin, ymin, depth_mm, side, allowance, stock_width, stock_height, stock_thickness),
            _point2d(endpoints[1], frame, xmin, ymin, depth_mm, side, allowance, stock_width, stock_height, stock_thickness),
        )]
    else:
        arc = adsk.core.Arc3D.cast(geometry)
        circle = adsk.core.Circle3D.cast(geometry)
        if arc is not None and endpoints:
            result = [Arc2D(
                _point2d(endpoints[0], frame, xmin, ymin, depth_mm, side, allowance, stock_width, stock_height, stock_thickness),
                _point2d(endpoints[1], frame, xmin, ymin, depth_mm, side, allowance, stock_width, stock_height, stock_thickness),
                _point2d(arc.center, frame, xmin, ymin, depth_mm, side, allowance, stock_width, stock_height, stock_thickness),
                _clockwise(arc.normal, coedge, frame, side),
            )]
        elif circle is not None:
            center = _point2d(circle.center, frame, xmin, ymin, depth_mm, side, allowance, stock_width, stock_height, stock_thickness)
            start = Vec2(center.x + circle.radius * CM_TO_MM, center.y)
            result = [Arc2D(start, start, center,
                            _clockwise(circle.normal, coedge, frame, side), True)]
        else:
            evaluator = coedge.edge.evaluator
            ok, start_parameter, end_parameter = evaluator.getParameterExtents()
            if not ok:
                raise ValueError("Unbounded/unevaluable curve cannot be exported")
            ok, points = evaluator.getStrokes(
                start_parameter, end_parameter, curve_tolerance_mm / CM_TO_MM,
            )
            if not ok or points is None or len(points) < 2:
                raise ValueError(
                    f"Curve linearization failed at {curve_tolerance_mm:.6f} mm"
                )
            ordered = list(points)
            if coedge.isOpposedToEdge:
                ordered.reverse()
            if endpoints:
                ordered[0], ordered[-1] = endpoints
            points_2d = [_point2d(p, frame, xmin, ymin, depth_mm, side, allowance, stock_width, stock_height, stock_thickness) for p in ordered]
            result = [Line2D(a, b) for a, b in zip(points_2d, points_2d[1:])
                      if a.distance_to(b) > 1e-9]
            if not result:
                raise ValueError("Linearized curve collapsed to zero length")
    return [segment.reversed() for segment in reversed(result)] if reverse else result


def loop_chain(loop, frame: PanelFrame, xmin: float, ymin: float, z_mm: float,
               tolerance_mm: float, name: str, side: MachiningSide,
               allowance: StockAllowance, stock_width: float,
               stock_height: float, stock_thickness: float) -> CurveChain2D:
    segments = []
    source_ids = []
    for index in range(loop.coEdges.count):
        coedge = loop.coEdges.item(index)
        source_ids.append(_native_id(coedge.edge))
        segments.extend(coedge_segments(
            coedge, frame, xmin, ymin, z_mm, tolerance_mm, side, allowance,
            stock_width, stock_height, stock_thickness,
        ))
    chain = CurveChain2D(segments, True, tuple(source_ids), name)
    chain.validate()
    return chain


def _face_z(face, frame: PanelFrame) -> float:
    return frame.model_to_local_cm(_v3_point(face.centroid)).z * CM_TO_MM


def _face_local_normal(face, frame: PanelFrame) -> V3 | None:
    if adsk.core.Plane.cast(face.geometry) is None:
        return None
    ok, normal = face.evaluator.getNormalAtPoint(face.centroid)
    if not ok:
        return None
    world = _v3_vector(normal).normalized()
    return V3(world.dot(frame.x_axis), world.dot(frame.y_axis),
              world.dot(frame.z_axis))


def _orientation_side(face, frame: PanelFrame) -> MachiningSide | None:
    """Classify only orthogonal planar access directions; SIDE2 is ignored."""
    normal = _face_local_normal(face, frame)
    if normal is None:
        return None
    return classify_orthogonal_normal(
        normal.x, normal.y, normal.z, PLANE_ANGLE_TOLERANCE_DEG,
    )


def _finished_xyz(point, frame: PanelFrame, xmin: float, ymin: float) -> tuple[float, float, float]:
    local = frame.model_to_local_cm(_v3_point(point))
    return (local.x * CM_TO_MM - xmin, local.y * CM_TO_MM - ymin,
            local.z * CM_TO_MM)


def _face_plane(face, frame: PanelFrame, xmin: float, ymin: float) -> GeometricPlaneIR:
    normal = _face_local_normal(face, frame)
    if normal is None:
        raise ValueError("Face is not planar")
    x, y, z = _finished_xyz(face.centroid, frame, xmin, ymin)
    return GeometricPlaneIR(
        (normal.x, normal.y, normal.z), normal.x * x + normal.y * y + normal.z * z,
    )


def _face_depth(face, side: MachiningSide, frame: PanelFrame, xmin: float,
                ymin: float, allowance: StockAllowance, stock_width: float,
                stock_height: float, stock_thickness: float) -> float:
    local = frame.model_to_local_cm(_v3_point(face.centroid))
    xp = local.x * CM_TO_MM - xmin + allowance.x_minus
    yp = local.y * CM_TO_MM - ymin + allowance.y_minus
    _, depth = panel_to_side_coordinates(
        side, xp, yp, local.z * CM_TO_MM, stock_width, stock_height,
        stock_thickness,
    )
    return depth


def _adjacent_face_ids(face) -> tuple[str, ...]:
    adjacent = set()
    for edge_index in range(face.edges.count):
        edge = face.edges.item(edge_index)
        for face_index in range(edge.faces.count):
            other = edge.faces.item(face_index)
            if _native_id(other) != _native_id(face):
                adjacent.add(_native_id(other))
    return tuple(sorted(adjacent))


def _surface_type(face) -> str:
    geometry = getattr(face, "geometry", None)
    return str(getattr(geometry, "objectType", type(geometry).__name__))


def _side_outward_axis(side: MachiningSide, frame: PanelFrame) -> V3:
    if side == MachiningSide.SIDE1:
        return frame.z_axis
    if side == MachiningSide.SIDE3:
        return frame.y_axis.scaled(-1.0)
    if side == MachiningSide.SIDE4:
        return frame.x_axis
    if side == MachiningSide.SIDE5:
        return frame.y_axis
    if side == MachiningSide.SIDE6:
        return frame.x_axis.scaled(-1.0)
    raise ValueError(f"Unsupported real TPA side: {side}")


def _point_side_depth(point, side: MachiningSide, frame: PanelFrame,
                      xmin: float, ymin: float, allowance: StockAllowance,
                      stock_width: float, stock_height: float,
                      stock_thickness: float) -> float:
    local = frame.model_to_local_cm(_v3_point(point))
    xp = local.x * CM_TO_MM - xmin + allowance.x_minus
    yp = local.y * CM_TO_MM - ymin + allowance.y_minus
    _, depth = panel_to_side_coordinates(
        side, xp, yp, local.z * CM_TO_MM, stock_width, stock_height,
        stock_thickness,
    )
    return depth


def _directional_first_hit_is_face(
        candidate, frame: PanelFrame, side: MachiningSide,
        candidate_depth_mm: float, xmin: float, ymin: float,
        allowance: StockAllowance, stock_width: float, stock_height: float,
        stock_thickness: float,
        clearance_mm: float) -> tuple[bool, tuple[str, ...]]:
    """Prove access by requiring the exact face to be first on its body.

    The ray starts outside the complete body on the proposed machining side
    and travels along that side's ordinary tool-access direction.  Equal plane
    or depth is insufficient: the first selected-body hit must be the same
    Fusion BRepFace.
    """
    app = adsk.core.Application.get()
    design = adsk.fusion.Design.cast(app.activeProduct)
    if design is None:
        return False, ("no_active_design_for_access_test",)
    root = design.rootComponent
    # pointOnFace is guaranteed to lie in the trimmed face; a geometric
    # centroid can fall outside a deeply concave region.
    sample_point = getattr(candidate, "pointOnFace", None) or candidate.centroid
    centroid = _v3_point(sample_point)
    outward = _side_outward_axis(side, frame)
    clearance_cm = clearance_mm / CM_TO_MM
    origin_v = V3(
        centroid.x + outward.x * clearance_cm,
        centroid.y + outward.y * clearance_cm,
        centroid.z + outward.z * clearance_cm,
    )
    origin = adsk.core.Point3D.create(origin_v.x, origin_v.y, origin_v.z)
    inward = outward.scaled(-1.0)
    direction = _vector(inward)
    hit_points = adsk.core.ObjectCollection.create()
    hits = root.findBRepUsingRay(
        origin, direction,
        adsk.fusion.BRepEntityTypes.BRepFaceEntityType,
        # Inspect all bodies along the ray, then deliberately keep only the
        # body being exported. Other assembly parts are not part of this raw
        # panel and must neither grant nor deny ownership.
        1e-5, False, hit_points,
    )
    target_hits = []
    for index in range(hits.count):
        hit = adsk.fusion.BRepFace.cast(hits.item(index))
        if hit is None or not _same_contextual_entity(hit.body, candidate.body):
            continue
        hit_point = hit_points.item(index)
        hit_depth_mm = _point_side_depth(
            hit_point, side, frame, xmin, ymin, allowance,
            stock_width, stock_height, stock_thickness,
        )
        hit_v = _v3_point(hit_point)
        travel_cm = (hit_v - origin_v).dot(inward)
        if travel_cm >= -EPS_CM:
            target_hits.append((travel_cm, hit, hit_depth_mm))

    if target_hits:
        _, hit, hit_depth_mm = min(target_hits, key=lambda item: item[0])
        same_plane = same_geometric_depth(
            hit_depth_mm, candidate_depth_mm, PLANE_LEVEL_TOLERANCE_MM,
        )
        same_face = _same_contextual_entity(hit, candidate)
        if same_plane and same_face:
            return True, (
                f"side{int(side)}_first_hit_from_outside", "point_on_face_ray",
                f"candidate_depth={candidate_depth_mm:.6f}",
                f"first_hit_depth={hit_depth_mm:.6f}",
                f"first_face={_native_id(hit)}",
            )
        mismatch = (
            "different_coplanar_face_was_first"
            if same_plane else f"covered_from_side{int(side)}"
        )
        return False, (
            mismatch, "point_on_face_ray",
            f"candidate_depth={candidate_depth_mm:.6f}",
            f"first_hit_depth={hit_depth_mm:.6f}",
            f"first_face={_native_id(hit)}",
        )
    return False, (f"side{int(side)}_ray_missed_selected_body",)


def _real_machining_frames(stock_width: float, stock_height: float,
                            thickness: float) -> list[MachiningFrameIR]:
    """Return real-face frames in the panel/stock coordinate convention.

    These definitions are also the single source of truth for later access
    tests.  A future fictive face will use the same IR with operator-approved
    P0/P1/P2-derived axes.
    """
    return [
        MachiningFrameIR(
            "side1", MachiningFrameKind.REAL_FACE, 1,
            (0.0, 0.0, 0.0), (1.0, 0.0, 0.0), (0.0, 1.0, 0.0),
            (0.0, 0.0, 1.0), stock_width, stock_height, thickness,
            "operator-selected top face",
        ),
        MachiningFrameIR(
            "side3", MachiningFrameKind.REAL_FACE, 3,
            (0.0, 0.0, -thickness), (1.0, 0.0, 0.0), (0.0, 0.0, 1.0),
            (0.0, -1.0, 0.0), stock_width, thickness, stock_height,
        ),
        MachiningFrameIR(
            "side4", MachiningFrameKind.REAL_FACE, 4,
            (stock_width, 0.0, -thickness), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0),
            (1.0, 0.0, 0.0), stock_height, thickness, stock_width,
        ),
        MachiningFrameIR(
            "side5", MachiningFrameKind.REAL_FACE, 5,
            (0.0, stock_height, -thickness), (1.0, 0.0, 0.0), (0.0, 0.0, 1.0),
            (0.0, 1.0, 0.0), stock_width, thickness, stock_height,
        ),
        MachiningFrameIR(
            "side6", MachiningFrameKind.REAL_FACE, 6,
            (0.0, 0.0, -thickness), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0),
            (-1.0, 0.0, 0.0), stock_height, thickness, stock_width,
        ),
    ]


def _inventory_body(face, frame: PanelFrame, xmin: float, ymin: float,
                    allowance: StockAllowance, stock_width: float,
                    stock_height: float,
                    thickness_mm: float) -> tuple[list[FaceFactIR], list[FaceOwnershipIR]]:
    """Inventory the complete selected solid before constructing profiles.

    Orientation proposes a real machining side. The exact face must then be
    the first selected-body hit from that side before it owns export geometry.
    This replaces the former Z-first and edge-reaching guesses.
    """
    facts: list[FaceFactIR] = []
    ownership: list[FaceOwnershipIR] = []
    selected_id = _native_id(face)
    bottom_threshold = math.cos(math.radians(PLANE_ANGLE_TOLERANCE_DEG))
    clearance_mm = max(stock_width, stock_height, thickness_mm) + 10.0

    for index in range(face.body.faces.count):
        candidate = face.body.faces.item(index)
        source_id = _native_id(candidate)
        normal = _face_local_normal(candidate, frame)
        normal_tuple = None if normal is None else (normal.x, normal.y, normal.z)
        side = _orientation_side(candidate, frame)
        plane = (_face_plane(candidate, frame, xmin, ymin)
                 if normal is not None else None)
        is_selected = source_id == selected_id
        proposed_frame = f"side{int(side)}" if side is not None else None

        facts.append(FaceFactIR(
            source_id, _surface_type(candidate), normal_tuple,
            proposed_frame, side, plane, _adjacent_face_ids(candidate), is_selected,
        ))

        if is_selected:
            state = OwnershipState.EXPOSED
            evidence = ("operator_selected_side1",)
            owner_side = MachiningSide.SIDE1
            owner_frame = "side1"
            depth = 0.0
        elif normal is not None and normal.z <= -bottom_threshold:
            state = OwnershipState.EXCLUDED_BOTTOM
            evidence = ("normal_opposes_selected_side1", "side2_excluded")
            owner_side = None
            owner_frame = None
            depth = None
        elif normal is None:
            state = OwnershipState.UNSUPPORTED_SURFACE
            evidence = ("non_planar_surface",)
            owner_side = None
            owner_frame = None
            depth = None
        elif side is None:
            state = OwnershipState.UNSUPPORTED_ORIENTATION
            evidence = ("inclined_planar_face", "future_operator_selected_fictive_face")
            owner_side = None
            owner_frame = None
            depth = None
        else:
            depth = _face_depth(
                candidate, side, frame, xmin, ymin, allowance,
                stock_width, stock_height, thickness_mm,
            )
            exposed, evidence = _directional_first_hit_is_face(
                candidate, frame, side, depth, xmin, ymin, allowance,
                stock_width, stock_height, thickness_mm, clearance_mm,
            )
            state = OwnershipState.EXPOSED if exposed else OwnershipState.COVERED
            owner_side = side if exposed else None
            owner_frame = proposed_frame if exposed else None

        ownership.append(FaceOwnershipIR(
            source_id, state, owner_frame, owner_side, depth, evidence,
        ))

    return facts, ownership


def _body_silhouette_chain(face, frame: PanelFrame, xmin: float, ymin: float,
                           tolerance_mm: float, allowance: StockAllowance,
                           stock_width: float, stock_height: float,
                           stock_thickness: float,
                           logger=None) -> tuple[CurveChain2D, bool]:
    """Project the complete finished body onto SIDE1 and return its outer loop.

    The top face is intentionally not used here: edge rebates can make it
    smaller than the true top-view footprint.  Fusion computes the union
    silhouette of the whole BRep.  A disconnected projection is rejected
    because the production contract requires one finished outer profile.
    """
    manager = adsk.fusion.TemporaryBRepManager.get()
    if manager is None or not hasattr(manager, "createProjectedBodyOutline"):
        raise RuntimeError(
            "This Fusion build does not provide createProjectedBodyOutline; "
            "the mandatory whole-body outer profile cannot be exported safely"
        )

    plane = adsk.core.Plane.create(
        adsk.core.Point3D.create(frame.origin.x, frame.origin.y, frame.origin.z),
        _vector(frame.z_axis),
    )
    # Fusion API length units are centimetres.  A positive value is never
    # replaced with Fusion's looser bounding-box-relative default.
    outline_body, contains_approximation = manager.createProjectedBodyOutline(
        face.body, plane, tolerance_mm / CM_TO_MM,
    )
    if outline_body is None:
        raise ValueError("Fusion could not compute the complete body silhouette")

    outer_loops = []
    for face_index in range(outline_body.faces.count):
        projected_face = outline_body.faces.item(face_index)
        for loop_index in range(projected_face.loops.count):
            loop = projected_face.loops.item(loop_index)
            if loop.isOuter:
                outer_loops.append(loop)
    if len(outer_loops) != 1:
        raise ValueError(
            "The complete body projection produced "
            f"{len(outer_loops)} outer regions; exactly one closed finished "
            "panel contour is required"
        )

    chain = loop_chain(
        outer_loops[0], frame, xmin, ymin, 0.0, tolerance_mm,
        "body_silhouette_outer", MachiningSide.SIDE1, allowance,
        stock_width, stock_height, stock_thickness,
    )
    if logger:
        logger.info(
            "Whole-body SIDE1 silhouette segments=%d approximation=%s tolerance=%.6f mm",
            len(chain.segments), bool(contains_approximation), tolerance_mm,
        )
    return chain, bool(contains_approximation)


def extract_panel_ir(face, frame: PanelFrame, stock_margin_mm: float,
                     curve_tolerance_mm: float = 0.01,
                     explicit_stock_width_mm: float | None = None,
                     explicit_stock_height_mm: float | None = None,
                     logger=None) -> PanelIR:
    xmin, xmax, ymin, ymax, thickness = panel_extents(face, frame)
    width, height = xmax - xmin, ymax - ymin
    allowance = StockAllowance(stock_margin_mm, stock_margin_mm,
                               stock_margin_mm, stock_margin_mm)
    required_width = width + allowance.x_minus + allowance.x_plus
    required_height = height + allowance.y_minus + allowance.y_plus
    stock_width = explicit_stock_width_mm or required_width
    stock_height = explicit_stock_height_mm or required_height
    profiles: list[PlanarProfileIR] = []
    unsupported: list[UnsupportedRegionIR] = []

    machining_frames = _real_machining_frames(
        stock_width, stock_height, thickness,
    )
    face_facts, face_ownership = _inventory_body(
        face, frame, xmin, ymin, allowance, stock_width, stock_height,
        thickness,
    )

    silhouette, silhouette_approximated = _body_silhouette_chain(
        face, frame, xmin, ymin, curve_tolerance_mm, allowance,
        stock_width, stock_height, thickness, logger,
    )
    if silhouette_approximated:
        silhouette.diagnostics = (
            f"Fusion projected silhouette contains curve approximation at "
            f"declared tolerance {curve_tolerance_mm:.6f} mm",
        )
    profiles.append(PlanarProfileIR(
        chain=silhouette,
        z_mm=0.0,
        machining_side=MachiningSide.SIDE1,
        geometric_plane=_face_plane(face, frame, xmin, ymin),
        profile_id="body_silhouette_outer",
        # This is a deliberate whole-body projection, not a Fusion face.  Do
        # not claim that it owns or merges the body's source faces.
        source_face_ids=(),
        provenance="body_silhouette_outer",
        containment="finished_body_footprint",
        z_mode=ProfileZMode.UNSPECIFIED,
    ))
    # The selected SIDE1 face establishes the manufacturing frame and top
    # envelope.  Its outer loop is not an independent removal boundary: on a
    # stepped panel it is merely the top-surface/lateral-wall adjacency and can
    # partly duplicate the mandatory finished silhouette.  Emitting it caused
    # a second, misleading stock-profiling path.  The whole-body silhouette is
    # the only selected-face outer perimeter handed to TpaCAD.  Inner loops are
    # real openings/features and remain independent SIDE1 profiles.
    inner_loops = [
        face.loops.item(i) for i in range(face.loops.count)
        if not face.loops.item(i).isOuter
    ]
    for inner_index, loop in enumerate(inner_loops, 1):
        chain = loop_chain(
            loop, frame, xmin, ymin, 0.0, curve_tolerance_mm,
            f"side1_inner_{inner_index}", MachiningSide.SIDE1,
            allowance, stock_width, stock_height, thickness,
        )
        profiles.append(PlanarProfileIR(
            chain=chain, z_mm=0.0, machining_side=MachiningSide.SIDE1,
            geometric_plane=_face_plane(face, frame, xmin, ymin),
            profile_id=chain.name, source_face_ids=(_native_id(face),),
            provenance="side1_inner",
            containment=f"inner_{inner_index}",
        ))

    ownership_by_id = {item.source_face_id: item for item in face_ownership}
    owned_faces = []
    for face_index in range(face.body.faces.count):
        candidate = face.body.faces.item(face_index)
        if _native_id(candidate) == _native_id(face):
            continue
        owner = ownership_by_id[_native_id(candidate)]
        if (owner.state == OwnershipState.EXPOSED and
                owner.machining_side is not None):
            owned_faces.append(candidate)

    # A Fusion BRepFace is the manufacturing-region discriminant.  Never union
    # faces by equal depth, coplanarity, shared edges, or endpoint connectivity:
    # doing so creates a perimeter that can leave one recessed surface and walk
    # around neighbouring faces/the complete solid.  Each accepted face is
    # described exclusively by its own ordered BRepLoop/coedge boundaries.
    for source_face in sorted(
            owned_faces,
            key=lambda item: (
                int(ownership_by_id[_native_id(item)].machining_side),
                ownership_by_id[_native_id(item)].local_depth_mm,
                _native_id(item),
            )):
        source_id = _native_id(source_face)
        owner = ownership_by_id[source_id]
        depth = owner.local_depth_mm
        side = owner.machining_side
        if depth is None or side is None:
            unsupported.append(UnsupportedRegionIR(
                "Exposed face has no proven side/depth",
                (source_id,), None, ("face_owned_boundary_extraction",),
            ))
            continue
        loops = sorted(
            (source_face.loops.item(i) for i in range(source_face.loops.count)),
            key=lambda item: not item.isOuter,
        )
        inner_index = 0
        for loop_index, loop in enumerate(loops, 1):
            if loop.isOuter:
                loop_role = "outer"
            else:
                inner_index += 1
                loop_role = f"inner_{inner_index}"
            profile_id = (
                f"side{int(side)}_face_{source_id}_depth_"
                f"{abs(depth):.4f}_{loop_role}"
            )
            try:
                chain = loop_chain(
                    loop, frame, xmin, ymin, depth, curve_tolerance_mm,
                    profile_id, side, allowance,
                    stock_width, stock_height, thickness,
                )
            except ValueError as error:
                unsupported.append(UnsupportedRegionIR(
                    str(error), (source_id,), depth,
                    (
                        "face_owned_boundary_extraction",
                        f"loop={loop_index}",
                        f"role={loop_role}",
                    ),
                ))
                continue
            profiles.append(PlanarProfileIR(
                chain=chain,
                z_mm=depth,
                machining_side=side,
                geometric_plane=_face_plane(source_face, frame, xmin, ymin),
                profile_id=profile_id,
                source_face_ids=(source_id,),
                provenance="fusion_face_boundary",
                containment=loop_role,
            ))

    # Every face without proven directional access remains report-only. Do not
    # recreate the removed Z-first or edge-reaching inference here.
    for item in face_ownership:
        if item.state in (OwnershipState.EXPOSED, OwnershipState.EXCLUDED_BOTTOM):
            continue
        unsupported.append(UnsupportedRegionIR(
            "Face inventoried but not exported: " + item.state.value,
            (item.source_face_id,), item.local_depth_mm, item.evidence,
        ))

    panel = PanelIR(
        finished_width=width,
        finished_height=height,
        thickness=thickness,
        allowance=allowance,
        profiles=profiles,
        unsupported_regions=unsupported,
        machining_frames=machining_frames,
        face_facts=face_facts,
        face_ownership=face_ownership,
        explicit_stock_width=explicit_stock_width_mm,
        explicit_stock_height=explicit_stock_height_mm,
        comment="TRIBU Fusion geometry-only export V1 face-owned profiles",
        curve_tolerance_mm=curve_tolerance_mm,
    )
    panel.validate()
    if logger:
        logger.info(
            "Extracted profiles=%d inventoried_faces=%d unsupported=%d stock=%.4fx%.4f",
            len(profiles), len(face_facts), len(unsupported),
            panel.stock_width, panel.stock_height,
        )
    return panel
