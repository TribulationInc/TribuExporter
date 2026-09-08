"""Small isolated bridge from one resolved Fusion CAM operation to TPA.

The dump retains Fusion's native XY arcs, helices, and spirals before any
fallback approximation.  This module deliberately contains no Fusion objects,
BRep profiles, tools, setups, or compensation.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from math import acos, atan2, cos, hypot, isfinite, pi, sin, sqrt
from pathlib import Path
from time import monotonic
from typing import Callable, Iterable, Iterator

from .tcn import fmt


CAM_LINEARIZATION_TOLERANCE_MM = 0.01
CAM_ARC_FIT_TOLERANCE_MM = 0.05
CAM_ARC_Z_EPSILON_MM = 0.001
CAM_CONNECTIVITY_EPSILON_MM = 0.0001
CAM_ARC_MIN_SWEEP_RAD = pi / 12.0
CAM_ARC_MAX_FIT_POINTS = 1024
CAM_MAX_OUTPUT_SEGMENTS = 2_000_000
CAM_TCN_LINE_WARNING_LIMIT = 9_999
CAM_SIMPLIFICATION_TOLERANCE_MM = 0.05
CAM_EXACT_COLLINEAR_EPSILON_MM = 1e-9
_TAU = 2.0 * pi
_CUTTING_MOVEMENTS = {"cutting", "finish_cutting"}
YieldCallback = Callable[[], None]


class _CooperativePulse:
    """Throttle a caller-provided UI/event-loop yield callback."""

    def __init__(self, callback: YieldCallback | None,
                 interval_seconds: float = 0.05):
        self.callback = callback
        self.interval_seconds = interval_seconds
        self.last = monotonic()

    def tick(self, force: bool = False) -> None:
        if self.callback is None:
            return
        now = monotonic()
        if force or now - self.last >= self.interval_seconds:
            self.callback()
            self.last = monotonic()


@dataclass(frozen=True)
class Point3:
    x: float
    y: float
    z: float

    def distance_to(self, other: "Point3") -> float:
        return sqrt((self.x - other.x) ** 2 +
                    (self.y - other.y) ** 2 +
                    (self.z - other.z) ** 2)


@dataclass(frozen=True)
class PostedMove:
    kind: str
    end: Point3
    feed_mm_min: float | None
    fusion_movement: int
    fusion_movement_name: str = "unknown"

    def __post_init__(self) -> None:
        if self.kind not in {"rapid", "linear"}:
            raise ValueError(f"Unsupported posted move kind: {self.kind!r}")
        _validate_point(self.end)
        _validate_feed(self.feed_mm_min)


@dataclass(frozen=True)
class PostedArcXY:
    end: Point3
    center: Point3
    clockwise: bool
    feed_mm_min: float | None
    fusion_movement: int
    fusion_movement_name: str = "unknown"
    full_circle: bool = False
    sweep_radians: float | None = None
    fitted: bool = False
    max_residual_mm: float = 0.0

    def __post_init__(self) -> None:
        _validate_point(self.end)
        _validate_point(self.center)
        _validate_feed(self.feed_mm_min)
        if self.sweep_radians is not None and (
                not isfinite(self.sweep_radians) or self.sweep_radians <= 0):
            raise ValueError("Arc sweep must be finite and positive")
        if self.max_residual_mm < 0 or not isfinite(self.max_residual_mm):
            raise ValueError("Arc residual must be finite and non-negative")


@dataclass(frozen=True)
class PostedHelixXY:
    """One Fusion-native constant-radius XY helix, emitted exactly as A01."""

    end: Point3
    center: Point3
    clockwise: bool
    feed_mm_min: float | None
    fusion_movement: int
    fusion_movement_name: str = "unknown"
    full_circle: bool = False
    sweep_radians: float | None = None

    def __post_init__(self) -> None:
        _validate_point(self.end)
        _validate_point(self.center)
        _validate_feed(self.feed_mm_min)
        if self.sweep_radians is None or (
                not isfinite(self.sweep_radians) or self.sweep_radians <= 0):
            raise ValueError("Helix sweep must be finite and positive")


@dataclass(frozen=True)
class PostedSpiralXY:
    """Fusion-native XY spiral retained until explicit bounded linearization."""

    end: Point3
    center: Point3
    clockwise: bool
    feed_mm_min: float | None
    fusion_movement: int
    fusion_movement_name: str = "unknown"
    sweep_radians: float | None = None
    start_radius_mm: float | None = None
    end_radius_mm: float | None = None

    def __post_init__(self) -> None:
        _validate_point(self.end)
        _validate_point(self.center)
        _validate_feed(self.feed_mm_min)
        if self.sweep_radians is None or (
                not isfinite(self.sweep_radians) or self.sweep_radians <= 0):
            raise ValueError("Spiral sweep must be finite and positive")
        for radius in (self.start_radius_mm, self.end_radius_mm):
            if radius is not None and (not isfinite(radius) or radius < 0):
                raise ValueError("Spiral radius must be finite and non-negative")


CamMotion = PostedMove | PostedArcXY | PostedHelixXY | PostedSpiralXY


@dataclass(frozen=True)
class CamExportOptions:
    """All CAM approximations are explicit operator-facing settings."""

    post_linearization_tolerance_mm: float = CAM_LINEARIZATION_TOLERANCE_MM
    fit_arcs: bool = True
    arc_fit_tolerance_mm: float = CAM_ARC_FIT_TOLERANCE_MM
    merge_exact_collinear: bool = True
    simplify_3d: bool = False
    simplification_tolerance_mm: float = CAM_SIMPLIFICATION_TOLERANCE_MM
    tcn_line_warning_limit: int = CAM_TCN_LINE_WARNING_LIMIT

    def validate(self) -> None:
        for label, value in (
            ("Post linearization tolerance", self.post_linearization_tolerance_mm),
            ("Arc-fit tolerance", self.arc_fit_tolerance_mm),
            ("3D simplification tolerance", self.simplification_tolerance_mm),
        ):
            if not isfinite(value) or value <= 0:
                raise ValueError(f"{label} must be finite and positive")
        if self.tcn_line_warning_limit <= 0:
            raise ValueError("TCN line warning limit must be positive")


@dataclass(frozen=True)
class StockBox:
    lower: Point3
    upper: Point3

    @property
    def width(self) -> float:
        return self.upper.x - self.lower.x

    @property
    def height(self) -> float:
        return self.upper.y - self.lower.y

    @property
    def thickness(self) -> float:
        return self.upper.z - self.lower.z

    def validate(self) -> None:
        _validate_point(self.lower)
        _validate_point(self.upper)
        if min(self.width, self.height, self.thickness) <= 0:
            raise ValueError("Fusion CAM stock must have positive X, Y, and Z dimensions")


@dataclass(frozen=True)
class PostedToolpath:
    operation_name: str
    stock: StockBox
    start: Point3
    moves: tuple[CamMotion, ...]
    section_count: int = 1

    def validate(self) -> None:
        self.stock.validate()
        _validate_point(self.start)
        if self.section_count != 1:
            raise ValueError(
                f"The selected Fusion operation produced {self.section_count} "
                "post sections; V1 supports exactly one"
            )
        if not self.moves:
            raise ValueError("The selected Fusion operation has no posted moves")

    def to_tpa_point(self, point: Point3) -> Point3:
        return Point3(point.x - self.stock.lower.x,
                      point.y - self.stock.lower.y,
                      point.z - self.stock.upper.z)

    @property
    def rapid_count(self) -> int:
        return sum(isinstance(m, PostedMove) and m.kind == "rapid" for m in self.moves)

    @property
    def linear_count(self) -> int:
        return sum(isinstance(m, PostedMove) and m.kind == "linear" for m in self.moves)

    @property
    def native_arc_count(self) -> int:
        return sum(isinstance(m, PostedArcXY) and not m.fitted for m in self.moves)

    @property
    def native_helix_count(self) -> int:
        return sum(isinstance(m, PostedHelixXY) for m in self.moves)

    @property
    def native_spiral_count(self) -> int:
        return sum(isinstance(m, PostedSpiralXY) for m in self.moves)

    def movement_counts(self) -> dict[str, int]:
        result: dict[str, int] = {}
        for move in self.moves:
            result[move.fusion_movement_name] = result.get(move.fusion_movement_name, 0) + 1
        return result

    def tpa_bounds(self) -> tuple[Point3, Point3]:
        points = [self.start]
        current = self.start
        for move in self.moves:
            points.append(move.end)
            if isinstance(move, (PostedArcXY, PostedHelixXY)):
                radius = hypot(current.x - move.center.x,
                               current.y - move.center.y)
                start_angle = atan2(current.y - move.center.y,
                                    current.x - move.center.x)
                sweep = move.sweep_radians or _directed_delta(
                    start_angle,
                    atan2(move.end.y - move.center.y,
                          move.end.x - move.center.x),
                    move.clockwise,
                )
                for angle in (0.0, pi / 2, pi, 3 * pi / 2):
                    if _directed_delta(start_angle, angle, move.clockwise) <= sweep + 1e-9:
                        points.append(Point3(
                            move.center.x + radius * cos(angle),
                            move.center.y + radius * sin(angle), current.z,
                        ))
            current = move.end
        return _bounds([self.to_tpa_point(point) for point in points])


@dataclass(frozen=True)
class ArcFitResult:
    toolpath: PostedToolpath
    original_motion_count: int
    original_linear_count: int
    original_rapid_count: int
    original_native_arc_count: int
    candidate_count: int
    fitted_arc_count: int
    remaining_line_count: int
    maximum_residual_mm: float
    original_native_helix_count: int = 0
    original_native_spiral_count: int = 0
    spiral_linearized_segment_count: int = 0
    exact_collinear_removed_count: int = 0
    simplified_removed_count: int = 0
    simplification_max_deviation_mm: float = 0.0


def _validate_point(point: Point3) -> None:
    if not all(isfinite(v) for v in (point.x, point.y, point.z)):
        raise ValueError("Toolpath coordinates must be finite")


def _validate_feed(feed: float | None) -> None:
    if feed is not None and (not isfinite(feed) or feed < 0):
        raise ValueError("Toolpath feed must be finite and non-negative")


def _point(values: Iterable[str], context: str) -> Point3:
    fields = tuple(values)
    if len(fields) != 3:
        raise ValueError(f"Malformed {context} record")
    try:
        return Point3(*(float(value) for value in fields))
    except ValueError as error:
        raise ValueError(f"Malformed numeric value in {context} record") from error


def parse_toolpath_dump(text: str,
                        operation_name: str = "Fusion CAM operation",
                        yield_callback: YieldCallback | None = None) -> PostedToolpath:
    pulse = _CooperativePulse(yield_callback)
    pulse.tick(force=True)
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if not lines or lines[0] not in {
            "TRIBU_TOOLPATH_DUMP|1", "TRIBU_TOOLPATH_DUMP|2"}:
        raise ValueError("Not a TribuExporter resolved-toolpath dump")
    stock = None
    start = None
    moves: list[CamMotion] = []
    sections = 0
    for line in lines[1:]:
        pulse.tick()
        fields = line.split("|")
        record = fields[0]
        if record == "UNITS":
            if fields[1:] != ["MM"]:
                raise ValueError("Toolpath dump must use millimetres")
        elif record == "STOCK":
            if len(fields) != 7:
                raise ValueError("Malformed STOCK record")
            stock = StockBox(_point(fields[1:4], "STOCK lower"),
                             _point(fields[4:7], "STOCK upper"))
        elif record == "SECTION":
            sections += 1
        elif record == "START":
            if start is None:
                start = _point(fields[1:4], "START")
        elif record in {"RAPID", "LINEAR"}:
            if len(fields) not in {6, 7}:
                raise ValueError(f"Malformed {record} record")
            moves.append(PostedMove(
                record.lower(), _point(fields[1:4], record),
                float(fields[4]) if fields[4] else None, int(fields[5]),
                fields[6] if len(fields) == 7 else f"movement_{fields[5]}",
            ))
        elif record == "ARC_XY":
            if len(fields) != 14:
                raise ValueError("Malformed ARC_XY record")
            moves.append(PostedArcXY(
                end=_point(fields[5:8], record),
                center=_point(fields[2:5], record),
                clockwise=fields[1] == "1",
                feed_mm_min=float(fields[8]) if fields[8] else None,
                fusion_movement=int(fields[9]),
                fusion_movement_name=fields[10],
                full_circle=fields[11] == "1",
                sweep_radians=float(fields[12]),
            ))
            if fields[13] != "native":
                raise ValueError("Unknown ARC_XY provenance")
        elif record == "HELIX_XY":
            if len(fields) != 14:
                raise ValueError("Malformed HELIX_XY record")
            moves.append(PostedHelixXY(
                end=_point(fields[5:8], record),
                center=_point(fields[2:5], record),
                clockwise=fields[1] == "1",
                feed_mm_min=float(fields[8]) if fields[8] else None,
                fusion_movement=int(fields[9]),
                fusion_movement_name=fields[10],
                full_circle=fields[11] == "1",
                sweep_radians=float(fields[12]),
            ))
            if fields[13] != "native":
                raise ValueError("Unknown HELIX_XY provenance")
        elif record == "SPIRAL_XY":
            if len(fields) != 15:
                raise ValueError("Malformed SPIRAL_XY record")
            moves.append(PostedSpiralXY(
                end=_point(fields[5:8], record),
                center=_point(fields[2:5], record),
                clockwise=fields[1] == "1",
                feed_mm_min=float(fields[8]) if fields[8] else None,
                fusion_movement=int(fields[9]),
                fusion_movement_name=fields[10],
                sweep_radians=float(fields[11]),
                start_radius_mm=float(fields[12]),
                end_radius_mm=float(fields[13]),
            ))
            if fields[14] != "native":
                raise ValueError("Unknown SPIRAL_XY provenance")
        else:
            raise ValueError(f"Unknown toolpath dump record: {record}")
    if stock is None or start is None:
        raise ValueError("Toolpath dump is missing STOCK or START")
    result = PostedToolpath(operation_name, stock, start, tuple(moves), sections)
    result.validate()
    pulse.tick(force=True)
    return result


def read_toolpath_dump(path: str | Path,
                       operation_name: str = "Fusion CAM operation",
                       yield_callback: YieldCallback | None = None) -> PostedToolpath:
    return parse_toolpath_dump(
        Path(path).read_text(encoding="ascii"), operation_name, yield_callback,
    )


def _bounds(points: list[Point3]) -> tuple[Point3, Point3]:
    return (
        Point3(min(p.x for p in points), min(p.y for p in points), min(p.z for p in points)),
        Point3(max(p.x for p in points), max(p.y for p in points), max(p.z for p in points)),
    )


def _circle_through(a: Point3, b: Point3, c: Point3) -> tuple[float, float] | None:
    d = 2.0 * (a.x * (b.y - c.y) + b.x * (c.y - a.y) + c.x * (a.y - b.y))
    if abs(d) <= 1e-12:
        return None
    aa, bb, cc = a.x * a.x + a.y * a.y, b.x * b.x + b.y * b.y, c.x * c.x + c.y * c.y
    return (
        (aa * (b.y - c.y) + bb * (c.y - a.y) + cc * (a.y - b.y)) / d,
        (aa * (c.x - b.x) + bb * (a.x - c.x) + cc * (b.x - a.x)) / d,
    )


def _turn(a: Point3, b: Point3, c: Point3) -> float:
    ux, uy = b.x - a.x, b.y - a.y
    vx, vy = c.x - b.x, c.y - b.y
    return atan2(ux * vy - uy * vx, ux * vx + uy * vy)


def _directed_delta(a: float, b: float, clockwise: bool) -> float:
    return (a - b) % _TAU if clockwise else (b - a) % _TAU


def _fit_window(points: list[Point3], tolerance_mm: float) -> PostedArcXY | None:
    if len(points) < 3 or any(abs(p.z - points[0].z) > CAM_ARC_Z_EPSILON_MM for p in points):
        return None
    turns = [_turn(a, b, c) for a, b, c in zip(points, points[1:], points[2:])]
    useful = [turn for turn in turns if abs(turn) > 1e-7]
    if not useful:
        return None
    clockwise = useful[0] < 0
    if any((turn < 0) != clockwise for turn in useful):
        return None
    closed = points[0].distance_to(points[-1]) <= CAM_CONNECTIVITY_EPSILON_MM
    middle = points[len(points) // 3] if closed else points[len(points) // 2]
    tail = points[(2 * len(points)) // 3] if closed else points[-1]
    center_xy = _circle_through(points[0], middle, tail)
    if center_xy is None:
        return None
    cx, cy = center_xy
    radii = [hypot(p.x - cx, p.y - cy) for p in points]
    radius = sum(radii) / len(radii)
    if radius <= CAM_CONNECTIVITY_EPSILON_MM:
        return None
    residual = max(abs(value - radius) for value in radii)
    if residual > tolerance_mm:
        return None
    sagitta = 0.0
    for left, right in zip(points, points[1:]):
        chord = hypot(right.x - left.x, right.y - left.y)
        if chord > 2.0 * radius + tolerance_mm:
            return None
        sagitta = max(sagitta, radius - sqrt(max(
            0.0, radius * radius - min(chord, 2 * radius) ** 2 / 4,
        )))
    deviation = residual + sagitta
    if deviation > tolerance_mm:
        return None
    angles = [atan2(p.y - cy, p.x - cx) for p in points]
    sweep = sum(_directed_delta(a, b, clockwise) for a, b in zip(angles, angles[1:]))
    if sweep <= 1e-7 or sweep > _TAU + 1e-5:
        return None
    direct = _directed_delta(angles[0], angles[-1], clockwise)
    if not closed and abs(direct - sweep) > 1e-5:
        return None
    return PostedArcXY(
        end=points[-1], center=Point3(cx, cy, points[0].z),
        clockwise=clockwise, feed_mm_min=None, fusion_movement=0,
        full_circle=closed and sweep > _TAU - 1e-3,
        sweep_radians=sweep, fitted=True, max_residual_mm=deviation,
    )


def _compatible(left: PostedMove, right: PostedMove) -> bool:
    return (left.kind == right.kind == "linear" and
            left.fusion_movement_name in _CUTTING_MOVEMENTS and
            right.fusion_movement_name == left.fusion_movement_name and
            left.fusion_movement == right.fusion_movement and
            left.feed_mm_min == right.feed_mm_min)


def _same_motion_class(left: PostedMove, right: PostedMove) -> bool:
    return (
        left.kind == right.kind
        and left.fusion_movement_name == right.fusion_movement_name
        and left.fusion_movement == right.fusion_movement
        and left.feed_mm_min == right.feed_mm_min
    )


def _point_segment_distance(point: Point3, start: Point3, end: Point3) -> float:
    vx, vy, vz = end.x - start.x, end.y - start.y, end.z - start.z
    length2 = vx * vx + vy * vy + vz * vz
    if length2 <= 1e-30:
        return point.distance_to(start)
    wx, wy, wz = point.x - start.x, point.y - start.y, point.z - start.z
    factor = max(0.0, min(1.0, (wx * vx + wy * vy + wz * vz) / length2))
    projection = Point3(
        start.x + factor * vx, start.y + factor * vy, start.z + factor * vz,
    )
    return point.distance_to(projection)


def _spiral_point(start: Point3, spiral: PostedSpiralXY, factor: float) -> Point3:
    if factor <= 0:
        return start
    if factor >= 1:
        return spiral.end
    start_radius = spiral.start_radius_mm
    if start_radius is None:
        start_radius = hypot(start.x - spiral.center.x, start.y - spiral.center.y)
    end_radius = spiral.end_radius_mm
    if end_radius is None:
        end_radius = hypot(
            spiral.end.x - spiral.center.x, spiral.end.y - spiral.center.y,
        )
    start_angle = atan2(start.y - spiral.center.y, start.x - spiral.center.x)
    direction = -1.0 if spiral.clockwise else 1.0
    angle = start_angle + direction * (spiral.sweep_radians or 0.0) * factor
    radius = start_radius + (end_radius - start_radius) * factor
    return Point3(
        spiral.center.x + radius * cos(angle),
        spiral.center.y + radius * sin(angle),
        start.z + (spiral.end.z - start.z) * factor,
    )


def _linearize_spiral(start: Point3, spiral: PostedSpiralXY,
                      tolerance_mm: float, pulse: _CooperativePulse) -> list[PostedMove]:
    """Linearize one retained Archimedean circular record without relaxing tolerance."""
    endpoints: list[Point3] = []

    def subdivide(t0: float, p0: Point3, t1: float, p1: Point3,
                  depth: int) -> None:
        pulse.tick()
        probes = []
        for fraction in (0.25, 0.5, 0.75):
            t = t0 + (t1 - t0) * fraction
            point = _spiral_point(start, spiral, t)
            probes.append((t, point, _point_segment_distance(point, p0, p1)))
        if max(item[2] for item in probes) <= tolerance_mm:
            if len(endpoints) >= CAM_MAX_OUTPUT_SEGMENTS:
                raise ValueError(
                    "Spiral linearization exceeded the internal safety limit; "
                    "the tolerance was not relaxed"
                )
            endpoints.append(p1)
            return
        if depth >= 30:
            raise ValueError(
                "Spiral could not be linearized at the declared tolerance; "
                "the tolerance was not relaxed"
            )
        midpoint_t, midpoint = probes[1][0], probes[1][1]
        subdivide(t0, p0, midpoint_t, midpoint, depth + 1)
        subdivide(midpoint_t, midpoint, t1, p1, depth + 1)

    subdivide(0.0, start, 1.0, spiral.end, 0)
    if len(endpoints) > CAM_MAX_OUTPUT_SEGMENTS:
        raise ValueError("Spiral linearization exceeded the internal safety limit")
    return [
        PostedMove(
            "linear", point, spiral.feed_mm_min, spiral.fusion_movement,
            spiral.fusion_movement_name,
        )
        for point in endpoints
    ]


def linearize_spirals(toolpath: PostedToolpath, tolerance_mm: float,
                      yield_callback: YieldCallback | None = None,
                      ) -> tuple[PostedToolpath, int]:
    """Resolve retained spirals only; native arcs and helices stay untouched."""
    if tolerance_mm <= 0 or not isfinite(tolerance_mm):
        raise ValueError("Post linearization tolerance must be finite and positive")
    pulse = _CooperativePulse(yield_callback)
    output: list[CamMotion] = []
    current = toolpath.start
    produced = 0
    for motion in toolpath.moves:
        pulse.tick()
        if isinstance(motion, PostedSpiralXY):
            lines = _linearize_spiral(current, motion, tolerance_mm, pulse)
            output.extend(lines)
            produced += len(lines)
        else:
            output.append(motion)
        current = motion.end
    result = replace(toolpath, moves=tuple(output))
    result.validate()
    pulse.tick(force=True)
    return result, produced


def _exactly_collinear_forward(a: Point3, b: Point3, c: Point3) -> bool:
    # Decide on the same four-decimal coordinates that TCN will contain. This
    # prevents removal of a point that was collinear in full precision but is
    # significant after serialization quantization.
    a, b, c = (
        Point3(float(fmt(point.x)), float(fmt(point.y)), float(fmt(point.z)))
        for point in (a, b, c)
    )
    ab = (b.x - a.x, b.y - a.y, b.z - a.z)
    bc = (c.x - b.x, c.y - b.y, c.z - b.z)
    lab = sqrt(sum(value * value for value in ab))
    lbc = sqrt(sum(value * value for value in bc))
    if lab <= CAM_EXACT_COLLINEAR_EPSILON_MM or lbc <= CAM_EXACT_COLLINEAR_EPSILON_MM:
        return True
    cross = (
        ab[1] * bc[2] - ab[2] * bc[1],
        ab[2] * bc[0] - ab[0] * bc[2],
        ab[0] * bc[1] - ab[1] * bc[0],
    )
    cross_length = sqrt(sum(value * value for value in cross))
    return (
        cross_length <= 1e-12 * max(lab, lbc)
        and sum(left * right for left, right in zip(ab, bc)) >= 0
    )


def merge_exact_collinear_moves(toolpath: PostedToolpath) -> tuple[PostedToolpath, int]:
    """Remove only mathematically redundant same-class 3D line endpoints."""
    output: list[CamMotion] = []
    removed = 0
    for motion in toolpath.moves:
        if output and isinstance(output[-1], PostedMove) and isinstance(motion, PostedMove):
            previous = output[-1]
            start = output[-2].end if len(output) > 1 else toolpath.start
            if (_same_motion_class(previous, motion)
                    and _exactly_collinear_forward(start, previous.end, motion.end)):
                output[-1] = motion
                removed += 1
                continue
        output.append(motion)
    result = replace(toolpath, moves=tuple(output))
    result.validate()
    return result, removed


def _turn_angle_3d(a: Point3, b: Point3, c: Point3) -> float:
    left = (b.x - a.x, b.y - a.y, b.z - a.z)
    right = (c.x - b.x, c.y - b.y, c.z - b.z)
    left_length = sqrt(sum(value * value for value in left))
    right_length = sqrt(sum(value * value for value in right))
    if left_length <= 1e-15 or right_length <= 1e-15:
        return 0.0
    cosine = sum(x * y for x, y in zip(left, right)) / (left_length * right_length)
    return acos(max(-1.0, min(1.0, cosine)))


def _rdp_indices(points: list[Point3], tolerance_mm: float,
                 mandatory: set[int]) -> tuple[list[int], float]:
    kept = set(mandatory) | {0, len(points) - 1}
    quantized = [
        Point3(float(fmt(point.x)), float(fmt(point.y)), float(fmt(point.z)))
        for point in points
    ]

    def emitted_distance(point_index: int, left: int, right: int) -> float:
        return _point_segment_distance(
            points[point_index], quantized[left], quantized[right],
        )

    fixed = sorted(kept)
    pending = list(zip(fixed, fixed[1:]))
    while pending:
        left, right = pending.pop()
        maximum = 0.0
        selected = None
        for index in range(left + 1, right):
            distance = emitted_distance(index, left, right)
            if distance > maximum:
                maximum, selected = distance, index
        if selected is not None and maximum > tolerance_mm:
            kept.add(selected)
            pending.append((left, selected))
            pending.append((selected, right))
    ordered = sorted(kept)
    maximum_error = 0.0
    for left, right in zip(ordered, ordered[1:]):
        maximum_error = max(maximum_error, *(
            emitted_distance(index, left, right)
            for index in range(left + 1, right)
        ), 0.0)
    return ordered, maximum_error


def simplify_3d_moves(toolpath: PostedToolpath, tolerance_mm: float,
                      yield_callback: YieldCallback | None = None,
                      ) -> tuple[PostedToolpath, int, float]:
    """Simplify same-class line runs with a measured 3D point-to-chord bound."""
    if tolerance_mm <= 0 or not isfinite(tolerance_mm):
        raise ValueError("3D simplification tolerance must be finite and positive")
    pulse = _CooperativePulse(yield_callback)
    output: list[CamMotion] = []
    removed = 0
    maximum_error = 0.0
    current = toolpath.start
    index = 0
    while index < len(toolpath.moves):
        pulse.tick()
        first = toolpath.moves[index]
        if not isinstance(first, PostedMove):
            output.append(first)
            current, index = first.end, index + 1
            continue
        run = [first]
        scan = index + 1
        while (scan < len(toolpath.moves)
               and isinstance(toolpath.moves[scan], PostedMove)
               and _same_motion_class(run[-1], toolpath.moves[scan])):
            run.append(toolpath.moves[scan])
            scan += 1
        points = [current] + [motion.end for motion in run]
        mandatory = {0, len(points) - 1}
        for point_index in range(1, len(points) - 1):
            z_before = points[point_index].z - points[point_index - 1].z
            z_after = points[point_index + 1].z - points[point_index].z
            if z_before * z_after < 0 or _turn_angle_3d(
                    points[point_index - 1], points[point_index],
                    points[point_index + 1]) >= pi / 12.0:
                mandatory.add(point_index)
        kept, error = _rdp_indices(points, tolerance_mm, mandatory)
        for point_index in kept[1:]:
            output.append(run[point_index - 1])
        removed += len(run) - (len(kept) - 1)
        maximum_error = max(maximum_error, error)
        current, index = run[-1].end, scan
    result = replace(toolpath, moves=tuple(output))
    result.validate()
    pulse.tick(force=True)
    return result, removed, maximum_error


def _split_full_circle(start: Point3, arc: PostedArcXY) -> list[PostedArcXY]:
    cx, cy = arc.center.x, arc.center.y
    radius = hypot(start.x - cx, start.y - cy)
    angle = atan2(start.y - cy, start.x - cx)
    direction = -1.0 if arc.clockwise else 1.0
    result = []
    for quarter in range(1, 5):
        target = arc.end if quarter == 4 else Point3(
            cx + radius * cos(angle + direction * quarter * pi / 2),
            cy + radius * sin(angle + direction * quarter * pi / 2), start.z,
        )
        result.append(PostedArcXY(
            target, arc.center, arc.clockwise, arc.feed_mm_min,
            arc.fusion_movement, arc.fusion_movement_name, False, pi / 2,
            arc.fitted, arc.max_residual_mm,
        ))
    return result


def fit_xy_arcs(toolpath: PostedToolpath,
                tolerance_mm: float = CAM_ARC_FIT_TOLERANCE_MM,
                yield_callback: YieldCallback | None = None) -> ArcFitResult:
    """Greedily replace only proven constant-Z cutting polylines with arcs."""
    if tolerance_mm <= 0 or not isfinite(tolerance_mm):
        raise ValueError("Arc-fit tolerance must be finite and positive")
    toolpath.validate()
    pulse = _CooperativePulse(yield_callback)
    pulse.tick(force=True)
    output: list[CamMotion] = []
    candidate_count = fitted_count = 0
    maximum_residual = 0.0
    current = toolpath.start
    index = 0
    while index < len(toolpath.moves):
        pulse.tick()
        move = toolpath.moves[index]
        if isinstance(move, PostedArcXY):
            output.extend(_split_full_circle(current, move) if move.full_circle else [move])
            current, index = move.end, index + 1
            continue
        if not isinstance(move, PostedMove):
            output.append(move)
            current, index = move.end, index + 1
            continue
        if move.kind != "linear" or move.fusion_movement_name not in _CUTTING_MOVEMENTS:
            output.append(move)
            current, index = move.end, index + 1
            continue
        run_start = current
        run: list[PostedMove] = [move]
        scan = index + 1
        while (scan < len(toolpath.moves) and
               isinstance(toolpath.moves[scan], PostedMove) and
               _compatible(run[-1], toolpath.moves[scan])):
            run.append(toolpath.moves[scan])
            scan += 1
            pulse.tick()

        # Dense circles are a common result when a Fusion strategy/post does
        # not retain circular interpolation.  Validate the complete closed run
        # once before entering the incremental fallback.  This changes no fit
        # criterion and avoids quadratic rescanning of thousands of points.
        if (len(run) >= 3 and
                run_start.distance_to(run[-1].end) <= CAM_CONNECTIVITY_EPSILON_MM):
            candidate_count += 1
            closed_arc = _fit_window(
                [run_start] + [item.end for item in run], tolerance_mm,
            )
            pulse.tick()
            if (closed_arc is not None and closed_arc.full_circle and
                    (closed_arc.sweep_radians or 0.0) >= CAM_ARC_MIN_SWEEP_RAD):
                closed_arc = PostedArcXY(
                    closed_arc.end, closed_arc.center, closed_arc.clockwise,
                    run[0].feed_mm_min, run[0].fusion_movement,
                    run[0].fusion_movement_name, True,
                    closed_arc.sweep_radians, True,
                    closed_arc.max_residual_mm,
                )
                parts = _split_full_circle(run_start, closed_arc)
                output.extend(parts)
                fitted_count += len(parts)
                maximum_residual = max(
                    maximum_residual, closed_arc.max_residual_mm,
                )
                current, index = run[-1].end, scan
                continue
        position, local_start = 0, run_start
        run_points = [item.end for item in run]
        while position < len(run):
            pulse.tick()
            best_end = None
            best_arc = None
            maximum = min(len(run), position + CAM_ARC_MAX_FIT_POINTS)
            if position + 2 >= len(run) or abs(_turn(
                    local_start, run[position].end, run[position + 1].end,
            )) <= 1e-7 or any(
                abs(point.z - local_start.z) > CAM_ARC_Z_EPSILON_MM
                for point in (run[position].end, run[position + 1].end)
            ):
                output.append(run[position])
                local_start, position = run[position].end, position + 1
                continue

            def candidate(end: int) -> PostedArcXY | None:
                nonlocal candidate_count
                pulse.tick()
                candidate_count += 1
                return _fit_window(
                    [local_start] + run_points[position:end],
                    tolerance_mm,
                )

            # Three points are the smallest possible local proof of a circle.
            # If that proof already violates the tolerance, retain the first
            # original line.  Searching thousands of larger windows from every
            # adaptive-path point was both speculative and pathologically slow.
            last_valid_end = position + 2
            last_valid_arc = candidate(last_valid_end)
            if last_valid_arc is None:
                output.append(run[position])
                local_start, position = run[position].end, position + 1
                continue
            if ((last_valid_arc.sweep_radians or 0.0) >=
                    CAM_ARC_MIN_SWEEP_RAD):
                best_end, best_arc = last_valid_end, last_valid_arc

            # Grow geometrically: a proven circular run is scanned in linear
            # total work instead of rebuilding every intermediate window.
            failed_end = None
            window_size = 4
            while last_valid_end < maximum:
                end = min(maximum, position + window_size)
                if end <= last_valid_end:
                    end = min(maximum, last_valid_end + 1)
                arc = candidate(end)
                if arc is None:
                    failed_end = end
                    break
                last_valid_end, last_valid_arc = end, arc
                if ((arc.sweep_radians or 0.0) >=
                        CAM_ARC_MIN_SWEEP_RAD):
                    best_end, best_arc = end, arc
                if end == maximum:
                    break
                window_size *= 2

            # Refine only the boundary between the longest proven window and
            # the first failed one.  Each accepted result is still validated
            # against every original point in that complete window.
            if failed_end is not None:
                low, high = last_valid_end + 1, failed_end - 1
                while low <= high:
                    end = (low + high) // 2
                    arc = candidate(end)
                    if arc is None:
                        high = end - 1
                    else:
                        last_valid_end, last_valid_arc = end, arc
                        if ((arc.sweep_radians or 0.0) >=
                                CAM_ARC_MIN_SWEEP_RAD):
                            best_end, best_arc = end, arc
                        low = end + 1
            if best_arc is None:
                output.append(run[position])
                local_start, position = run[position].end, position + 1
                continue
            best_arc = PostedArcXY(
                best_arc.end, best_arc.center, best_arc.clockwise,
                run[position].feed_mm_min, run[position].fusion_movement,
                run[position].fusion_movement_name, best_arc.full_circle,
                best_arc.sweep_radians, True, best_arc.max_residual_mm,
            )
            parts = _split_full_circle(local_start, best_arc) if best_arc.full_circle else [best_arc]
            output.extend(parts)
            fitted_count += len(parts)
            maximum_residual = max(maximum_residual, best_arc.max_residual_mm)
            local_start, position = best_arc.end, best_end
        current, index = run[-1].end, scan
    fitted_path = PostedToolpath(toolpath.operation_name, toolpath.stock,
                                 toolpath.start, tuple(output), toolpath.section_count)
    if len(output) > CAM_MAX_OUTPUT_SEGMENTS:
        raise ValueError(
            f"CAM output has {len(output)} segments; the V1 safety limit is "
            f"{CAM_MAX_OUTPUT_SEGMENTS}"
        )
    fitted_path.validate()
    _validate_arcs(
        toolpath.start, fitted_path.moves, tolerance_mm, yield_callback,
    )
    pulse.tick(force=True)
    return ArcFitResult(
        fitted_path, len(toolpath.moves), toolpath.linear_count,
        toolpath.rapid_count, toolpath.native_arc_count,
        candidate_count, fitted_count,
        sum(isinstance(move, PostedMove) for move in output), maximum_residual,
        toolpath.native_helix_count, toolpath.native_spiral_count,
    )


def _validate_arcs(start: Point3, motions: tuple[CamMotion, ...],
                   tolerance_mm: float,
                   yield_callback: YieldCallback | None = None) -> None:
    pulse = _CooperativePulse(yield_callback)
    current = start
    for motion in motions:
        pulse.tick()
        if isinstance(motion, PostedArcXY):
            if abs(current.z - motion.end.z) > CAM_ARC_Z_EPSILON_MM:
                raise ValueError("A01 arc has Z drift")
            r0 = hypot(current.x - motion.center.x, current.y - motion.center.y)
            r1 = hypot(motion.end.x - motion.center.x, motion.end.y - motion.center.y)
            allowed = tolerance_mm + 1e-6 if motion.fitted else 1e-5
            if abs(r0 - r1) > allowed:
                raise ValueError("A01 arc endpoints do not share a circle")
        elif isinstance(motion, PostedHelixXY):
            r0 = hypot(current.x - motion.center.x, current.y - motion.center.y)
            r1 = hypot(motion.end.x - motion.center.x, motion.end.y - motion.center.y)
            if abs(r0 - r1) > 1e-5:
                raise ValueError("Helical A01 endpoints do not share a circle")
        current = motion.end


def optimize_toolpath(toolpath: PostedToolpath,
                      options: CamExportOptions | None = None,
                      yield_callback: YieldCallback | None = None) -> ArcFitResult:
    """Apply the explicit, deterministic CAM compression pipeline."""
    options = options or CamExportOptions()
    options.validate()
    original = toolpath
    resolved, spiral_segments = linearize_spirals(
        toolpath, options.post_linearization_tolerance_mm, yield_callback,
    )
    if options.fit_arcs:
        result = fit_xy_arcs(
            resolved, options.arc_fit_tolerance_mm, yield_callback,
        )
    else:
        _validate_arcs(
            resolved.start, resolved.moves, options.arc_fit_tolerance_mm,
            yield_callback,
        )
        result = ArcFitResult(
            resolved, len(original.moves), original.linear_count,
            original.rapid_count, original.native_arc_count,
            0, 0, sum(isinstance(move, PostedMove) for move in resolved.moves), 0.0,
            original.native_helix_count, original.native_spiral_count,
        )
    path = result.toolpath
    collinear_removed = 0
    if options.merge_exact_collinear:
        path, collinear_removed = merge_exact_collinear_moves(path)
    simplified_removed = 0
    simplification_error = 0.0
    if options.simplify_3d:
        path, simplified_removed, simplification_error = simplify_3d_moves(
            path, options.simplification_tolerance_mm, yield_callback,
        )
    if len(path.moves) > CAM_MAX_OUTPUT_SEGMENTS:
        raise ValueError(
            f"CAM output has {len(path.moves)} segments; the internal safety "
            f"limit is {CAM_MAX_OUTPUT_SEGMENTS}"
        )
    _validate_arcs(
        path.start, path.moves, options.arc_fit_tolerance_mm, yield_callback,
    )
    return replace(
        result,
        toolpath=path,
        original_motion_count=len(original.moves),
        original_linear_count=original.linear_count,
        original_rapid_count=original.rapid_count,
        original_native_arc_count=original.native_arc_count,
        original_native_helix_count=original.native_helix_count,
        original_native_spiral_count=original.native_spiral_count,
        spiral_linearized_segment_count=spiral_segments,
        exact_collinear_removed_count=collinear_removed,
        simplified_removed_count=simplified_removed,
        remaining_line_count=sum(
            isinstance(move, PostedMove) for move in path.moves
        ),
        simplification_max_deviation_mm=simplification_error,
    )


def _translated(toolpath: PostedToolpath, point: Point3,
                xy_shift: tuple[float, float]) -> Point3:
    base = toolpath.to_tpa_point(point)
    return Point3(base.x + xy_shift[0], base.y + xy_shift[1], base.z)


def iter_cam_profile_records(result: ArcFitResult,
                             xy_shift: tuple[float, float] = (0.0, 0.0),
                             yield_callback: YieldCallback | None = None) -> Iterator[str]:
    path = result.toolpath
    pulse = _CooperativePulse(yield_callback)
    pulse.tick(force=True)
    start = _translated(path, path.start, xy_shift)
    current = start
    emitted = 0
    for move in path.moves:
        pulse.tick()
        end = _translated(path, move.end, xy_shift)
        if (fmt(end.x), fmt(end.y), fmt(end.z)) == (fmt(current.x), fmt(current.y), fmt(current.z)):
            continue
        if isinstance(move, PostedMove):
            fields = ["W#2201{ ::WTl #8015=0"]
        elif isinstance(move, (PostedArcXY, PostedHelixXY)):
            fields = ["W#2101{ ::WTa #8015=0"]
        else:
            raise ValueError(
                "A retained spiral reached TCN serialization without explicit "
                "tolerance-controlled linearization"
            )
        if emitted == 0:
            fields.append(f" #8121={fmt(start.x)} #8122={fmt(start.y)} #8123={fmt(start.z)}")
        fields.append(f" #1={fmt(end.x)} #2={fmt(end.y)}")
        if isinstance(move, (PostedArcXY, PostedHelixXY)):
            center = _translated(path, move.center, xy_shift)
            fields.append(f" #34={0 if move.clockwise else 1}")
            fields.append(f" #31={fmt(center.x-current.x)} #32={fmt(center.y-current.y)}")
        fields.append(f" #3={fmt(end.z)} }}W")
        yield "".join(fields)
        current, emitted = end, emitted + 1
    if emitted == 0:
        raise ValueError("Selected CAM operation contains no non-zero output motion")
    pulse.tick(force=True)


def target_stock_xy_shift(panel, toolpath: PostedToolpath) -> tuple[float, float]:
    """Require Fusion stock to match either Tribu stock or finished body."""
    epsilon = 0.01
    if abs(toolpath.stock.thickness - panel.thickness) > epsilon:
        raise ValueError("Fusion CAM stock thickness does not match Tribu panel thickness")
    if (abs(toolpath.stock.width - panel.stock_width) <= epsilon and
            abs(toolpath.stock.height - panel.stock_height) <= epsilon):
        return 0.0, 0.0
    if (abs(toolpath.stock.width - panel.finished_width) <= epsilon and
            abs(toolpath.stock.height - panel.finished_height) <= epsilon):
        return panel.allowance.x_minus, panel.allowance.y_minus
    raise ValueError(
        "Fusion CAM stock XY dimensions match neither the Tribu raw stock nor "
        "the finished panel; align the CAM setup before exporting"
    )


def append_cam_profile_to_tcn(geometry_tcn: str, result: ArcFitResult | None,
                              xy_shift: tuple[float, float] = (0.0, 0.0),
                              yield_callback: YieldCallback | None = None) -> str:
    if result is None:
        return geometry_tcn
    side_start = geometry_tcn.find("SIDE#1{")
    position = geometry_tcn.find("\n}SIDE", side_start)
    if side_start < 0 or position < 0:
        raise ValueError("Generated geometry TCN has no SIDE#1 block")
    records = "\n" + "\n".join(iter_cam_profile_records(
        result, xy_shift, yield_callback,
    ))
    return geometry_tcn[:position] + records + geometry_tcn[position:]


def render_experimental_tcn(
        toolpath: PostedToolpath, fitted: ArcFitResult | None = None,
        yield_callback: YieldCallback | None = None) -> str:
    fitted = fitted or optimize_toolpath(
        toolpath, yield_callback=yield_callback,
    )
    lines = [
        r"TPA\ALBATROS\EDICAD\02.00",
        "$=EXPERIMENTAL TRIBU FUSION CAM TRAJECTORY - NOT MACHINE APPROVED",
        "::SIDE=1;",
        f"::UNm DL={fmt(toolpath.stock.width)} DH={fmt(toolpath.stock.height)} DS={fmt(toolpath.stock.thickness)}",
        "SIDE#1{", *iter_cam_profile_records(
            fitted, yield_callback=yield_callback,
        ),
        "}SIDE", "SIDE#2{", "}SIDE", "SIDE#3{", "}SIDE",
        "SIDE#4{", "}SIDE", "SIDE#5{", "}SIDE", "SIDE#6{", "}SIDE",
    ]
    return "\n".join(lines) + "\n"


def write_experimental_tcn(toolpath: PostedToolpath, path: str | Path,
                           fitted: ArcFitResult | None = None,
                           yield_callback: YieldCallback | None = None) -> Path:
    target = Path(path)
    target.write_text(
        render_experimental_tcn(toolpath, fitted, yield_callback),
        encoding="ascii",
    )
    return target


def tcn_physical_line_count(text: str) -> int:
    """Count the complete file, including header and empty SIDE blocks."""
    return len(text.splitlines())
