"""Manufacturing-neutral geometry and validation model.

This module deliberately has no dependency on Autodesk Fusion or TpaCAD.
Coordinates are millimetres in the operator-defined panel frame. SIDE#1 is
Z=0 and geometry recessed into the finished body has negative Z.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum, IntEnum
import hashlib
import math
from typing import Iterable, Sequence


EPS_MM = 1e-5
CONNECTIVITY_TOLERANCE_MM = 0.001
TCN_DECIMAL_PLACES = 4


def tcn_quantized(value: float) -> float:
    """Value as it will be emitted by the geometry-only TCN serializer."""
    return float(f"{value:.{TCN_DECIMAL_PLACES}f}")


def same_geometric_depth(left_mm: float, right_mm: float,
                         tolerance_mm: float = 0.01) -> bool:
    """True only when two coordinates belong to the same depth plane."""
    return abs(left_mm - right_mm) <= tolerance_mm


@dataclass(frozen=True, order=True)
class Vec2:
    x: float
    y: float

    def distance_to(self, other: "Vec2") -> float:
        return math.hypot(self.x - other.x, self.y - other.y)


@dataclass(frozen=True)
class Line2D:
    start: Vec2
    end: Vec2

    def reversed(self) -> "Line2D":
        return Line2D(self.end, self.start)


@dataclass(frozen=True)
class Arc2D:
    start: Vec2
    end: Vec2
    center: Vec2
    clockwise: bool
    full_circle: bool = False

    def reversed(self) -> "Arc2D":
        return Arc2D(
            self.end, self.start, self.center, not self.clockwise,
            self.full_circle,
        )


CurveSegment2D = Line2D | Arc2D


class MachiningSide(IntEnum):
    SIDE1 = 1
    SIDE3 = 3
    SIDE4 = 4
    SIDE5 = 5
    SIDE6 = 6


class MachiningFrameKind(str, Enum):
    """How a TPA-local machining coordinate system is established."""

    REAL_FACE = "real_face"
    FICTIVE_FACE = "fictive_face"


class OwnershipState(str, Enum):
    """Final, mutually exclusive classification of one source BRep face."""

    EXPOSED = "exposed"
    COVERED = "covered"
    PARTIALLY_EXPOSED = "partially_exposed"
    AMBIGUOUS = "ambiguous"
    EXCLUDED_BOTTOM = "excluded_bottom"
    UNSUPPORTED_ORIENTATION = "unsupported_orientation"
    UNSUPPORTED_SURFACE = "unsupported_surface"


class ProfileZMode(str, Enum):
    """Whether geometry assigns local Z or leaves it to later TpaCAD setup."""

    EXPLICIT = "explicit"
    UNSPECIFIED = "unspecified"


@dataclass(frozen=True)
class MachiningFrameIR:
    """A real or future fictive TPA face expressed in panel coordinates.

    V1 only serializes real faces.  Keeping arbitrary orthonormal axes in the
    IR lets an operator-approved inclined plane later use the same projection
    and profile pipeline without pretending that every inclined BRep face is a
    machining face.
    """

    frame_id: str
    kind: MachiningFrameKind
    tpa_face_number: int | None
    origin: tuple[float, float, float]
    x_axis: tuple[float, float, float]
    y_axis: tuple[float, float, float]
    outward_axis: tuple[float, float, float]
    length_mm: float
    height_mm: float
    thickness_mm: float
    provenance: str = ""

    def validate(self) -> None:
        if self.kind == MachiningFrameKind.FICTIVE_FACE:
            if (self.tpa_face_number is not None and
                    not 7 <= self.tpa_face_number <= 99):
                raise ValueError("Fictive TPA face number must be between 7 and 99")
        for name, value in (
            ("length_mm", self.length_mm),
            ("height_mm", self.height_mm),
            ("thickness_mm", self.thickness_mm),
        ):
            if value <= 0 or not math.isfinite(value):
                raise ValueError(f"Machining frame {name} must be finite and > 0")
        vectors = (self.x_axis, self.y_axis, self.outward_axis)
        if any(len(vector) != 3 or not all(map(math.isfinite, vector))
               for vector in vectors):
            raise ValueError("Machining frame axes must be finite 3D vectors")
        x, y, z = vectors

        def dot(a, b):
            return sum(left * right for left, right in zip(a, b))

        if any(abs(dot(vector, vector) - 1.0) > 1e-8 for vector in vectors):
            raise ValueError("Machining frame axes must be unit length")
        if any(abs(dot(left, right)) > 1e-8 for left, right in (
                (x, y), (x, z), (y, z))):
            raise ValueError("Machining frame axes must be orthogonal")
        cross = (
            x[1] * y[2] - x[2] * y[1],
            x[2] * y[0] - x[0] * y[2],
            x[0] * y[1] - x[1] * y[0],
        )
        if (self.kind == MachiningFrameKind.FICTIVE_FACE and
                dot(cross, z) < 1.0 - 1e-8):
            raise ValueError("Machining frame must be right-handed (X x Y = +Z)")


@dataclass(frozen=True)
class FaceFactIR:
    """Read-only facts collected before any profile is constructed."""

    source_face_id: str
    surface_type: str
    normal: tuple[float, float, float] | None
    proposed_frame_id: str | None
    proposed_side: int | MachiningSide | None
    plane: "GeometricPlaneIR | None"
    adjacent_face_ids: tuple[str, ...] = ()
    is_selected_side1: bool = False


@dataclass(frozen=True)
class FaceOwnershipIR:
    """Exactly one access decision for exactly one source BRep face."""

    source_face_id: str
    state: OwnershipState
    machining_frame_id: str | None
    machining_side: int | MachiningSide | None
    local_depth_mm: float | None
    evidence: tuple[str, ...] = ()


@dataclass(frozen=True)
class GeometricPlaneIR:
    """A plane in panel coordinates, expressed as n·p = offset_mm."""

    normal: tuple[float, float, float]
    offset_mm: float


@dataclass
class CurveChain2D:
    """One ordered geometric chain; never implicitly connected to another."""

    segments: list[CurveSegment2D]
    closed: bool
    source_ids: tuple[str, ...] = ()
    name: str = ""
    diagnostics: tuple[str, ...] = ()

    @property
    def start(self) -> Vec2:
        if not self.segments:
            raise ValueError(f"Chain '{self.name}' has no segments")
        return self.segments[0].start

    @property
    def end(self) -> Vec2:
        if not self.segments:
            raise ValueError(f"Chain '{self.name}' has no segments")
        return self.segments[-1].end

    def validate(self, tolerance_mm: float = EPS_MM) -> None:
        if not self.segments:
            raise ValueError(f"Chain '{self.name}' has no segments")
        for index, (left, right) in enumerate(zip(self.segments, self.segments[1:])):
            gap = left.end.distance_to(right.start)
            if gap > tolerance_mm:
                raise ValueError(
                    f"Chain '{self.name}' is discontinuous after segment {index}: "
                    f"gap={gap:.6f} mm"
                )
        gap = self.end.distance_to(self.start)
        if self.closed and gap > tolerance_mm:
            raise ValueError(
                f"Chain '{self.name}' is marked closed but has a "
                f"{gap:.6f} mm closure gap"
            )
        if not self.closed:
            raise ValueError(f"Open chain '{self.name}' is not exportable")


@dataclass
class PlanarProfileIR:
    chain: CurveChain2D
    z_mm: float
    machining_side: int | MachiningSide = MachiningSide.SIDE1
    geometric_plane: GeometricPlaneIR | None = None
    profile_id: str = ""
    source_face_ids: tuple[str, ...] = ()
    provenance: str = ""
    containment: str = "unknown"
    z_mode: ProfileZMode = ProfileZMode.EXPLICIT

    @property
    def source_face_id(self) -> str | None:
        """The one Fusion face that owns this profile, if face-derived.

        Whole-body silhouettes are synthetic and intentionally return None.
        A face-derived profile may never span more than one BRepFace.
        """
        return self.source_face_ids[0] if len(self.source_face_ids) == 1 else None

    def validate(self) -> None:
        self.chain.validate()
        if not math.isfinite(self.z_mm):
            raise ValueError("Profile Z must be finite")
        if self.provenance == "side1_top_boundary":
            raise ValueError(
                "The selected SIDE1 outer loop is a reference boundary; "
                "the mandatory whole-body silhouette owns the finished perimeter"
            )
        if len(self.source_face_ids) != len(set(self.source_face_ids)):
            raise ValueError(
                f"Profile '{self.profile_id or self.chain.name}' repeats a source face"
            )
        if (self.provenance != "body_silhouette_outer" and
                len(self.source_face_ids) > 1):
            raise ValueError(
                f"Profile '{self.profile_id or self.chain.name}' merges multiple "
                "Fusion BRepFaces; face-derived profiles must have exactly one owner"
            )


@dataclass(frozen=True)
class HoleIR:
    """One explicitly enabled native Fusion blind-hole working.

    ``depth_mm`` is the positive distance from the TPA side's local Z=0 plane
    to the hole endpoint.  The serializer is responsible for emitting it as a
    negative TPA depth.  Keeping holes separate from PlanarProfileIR prevents a
    point working from ever participating in contour chaining.
    """

    hole_id: str
    center: Vec2
    depth_mm: float
    diameter_mm: float
    machining_side: int | MachiningSide
    source_feature_id: str
    source_entry_face_id: str
    feature_depth_mm: float
    entry_local_z_mm: float = 0.0
    provenance: str = "fusion_native_hole_feature"

    def validate(self) -> None:
        for name, value in (
            ("center.x", self.center.x),
            ("center.y", self.center.y),
            ("depth_mm", self.depth_mm),
            ("diameter_mm", self.diameter_mm),
            ("feature_depth_mm", self.feature_depth_mm),
            ("entry_local_z_mm", self.entry_local_z_mm),
        ):
            if not math.isfinite(value):
                raise ValueError(f"Hole '{self.hole_id}' {name} must be finite")
        if self.depth_mm <= 0 or self.feature_depth_mm <= 0:
            raise ValueError(f"Hole '{self.hole_id}' depth must be > 0")
        if self.diameter_mm <= 0:
            raise ValueError(f"Hole '{self.hole_id}' diameter must be > 0")
        if not self.source_feature_id:
            raise ValueError(f"Hole '{self.hole_id}' has no Fusion HoleFeature ID")
        if not self.source_entry_face_id:
            raise ValueError(f"Hole '{self.hole_id}' has no entry BRepFace ID")
        if self.provenance != "fusion_native_hole_feature":
            raise ValueError(
                f"Hole '{self.hole_id}' is not owned by a native Fusion HoleFeature"
            )


@dataclass(frozen=True)
class UnsupportedRegionIR:
    reason: str
    source_face_ids: tuple[str, ...] = ()
    z_mm: float | None = None
    diagnostics: tuple[str, ...] = ()


@dataclass(frozen=True)
class UnsupportedHoleIR:
    """A native Fusion HoleFeature deliberately not translated to W#81."""

    reason: str
    source_feature_id: str
    diagnostics: tuple[str, ...] = ()


@dataclass(frozen=True)
class StockAllowance:
    x_minus: float = 0.0
    x_plus: float = 0.0
    y_minus: float = 0.0
    y_plus: float = 0.0

    def validate(self) -> None:
        for name, value in vars(self).items():
            if value < 0 or not math.isfinite(value):
                raise ValueError(f"{name} must be a finite value >= 0")


@dataclass
class PanelIR:
    finished_width: float
    finished_height: float
    thickness: float
    allowance: StockAllowance
    profiles: list[PlanarProfileIR] = field(default_factory=list)
    unsupported_regions: list[UnsupportedRegionIR] = field(default_factory=list)
    machining_frames: list[MachiningFrameIR] = field(default_factory=list)
    face_facts: list[FaceFactIR] = field(default_factory=list)
    face_ownership: list[FaceOwnershipIR] = field(default_factory=list)
    holes: list[HoleIR] = field(default_factory=list)
    unsupported_holes: list[UnsupportedHoleIR] = field(default_factory=list)
    explicit_stock_width: float | None = None
    explicit_stock_height: float | None = None
    comment: str = "TRIBU Fusion geometry export V1"
    curve_tolerance_mm: float = 0.01

    @property
    def required_stock_width(self) -> float:
        return self.finished_width + self.allowance.x_minus + self.allowance.x_plus

    @property
    def required_stock_height(self) -> float:
        return self.finished_height + self.allowance.y_minus + self.allowance.y_plus

    @property
    def stock_width(self) -> float:
        return self.explicit_stock_width or self.required_stock_width

    @property
    def stock_height(self) -> float:
        return self.explicit_stock_height or self.required_stock_height

    def finished_to_stock(self, point: Vec2) -> Vec2:
        return Vec2(
            point.x + self.allowance.x_minus,
            point.y + self.allowance.y_minus,
        )

    def validate(self) -> None:
        for name, value in (
            ("finished_width", self.finished_width),
            ("finished_height", self.finished_height),
            ("thickness", self.thickness),
            ("curve_tolerance_mm", self.curve_tolerance_mm),
        ):
            if value <= 0 or not math.isfinite(value):
                raise ValueError(f"{name} must be a finite value > 0")
        self.allowance.validate()
        frame_ids = [frame.frame_id for frame in self.machining_frames]
        frame_sides = [frame.tpa_face_number for frame in self.machining_frames
                       if frame.tpa_face_number is not None]
        if len(frame_ids) != len(set(frame_ids)):
            raise ValueError("Machining frame IDs must be unique")
        if len(frame_sides) != len(set(frame_sides)):
            raise ValueError("A TPA face number has more than one machining frame")
        for machining_frame in self.machining_frames:
            machining_frame.validate()
        frame_by_side = {
            frame.tpa_face_number: frame for frame in self.machining_frames
            if frame.tpa_face_number is not None
        }
        fact_ids = [fact.source_face_id for fact in self.face_facts]
        ownership_ids = [item.source_face_id for item in self.face_ownership]
        if len(fact_ids) != len(set(fact_ids)):
            raise ValueError("A source BRep face was inventoried more than once")
        if len(ownership_ids) != len(set(ownership_ids)):
            raise ValueError("A source BRep face received more than one ownership state")
        if self.face_facts and set(fact_ids) != set(ownership_ids):
            raise ValueError("Every inventoried BRep face must receive one ownership state")
        hole_ids = [hole.hole_id for hole in self.holes]
        if len(hole_ids) != len(set(hole_ids)):
            raise ValueError("Every exported native hole must have a unique hole_id")
        if self.stock_width + EPS_MM < self.required_stock_width:
            raise ValueError("Explicit stock width is smaller than required footprint")
        if self.stock_height + EPS_MM < self.required_stock_height:
            raise ValueError("Explicit stock height is smaller than required footprint")

        if self.face_facts:
            profile_ids = [profile.profile_id for profile in self.profiles]
            if any(not profile_id for profile_id in profile_ids):
                raise ValueError("Every extracted profile must have a stable profile_id")
            if len(profile_ids) != len(set(profile_ids)):
                raise ValueError("Every extracted profile must have a unique profile_id")
            owner_by_id = {item.source_face_id: item for item in self.face_ownership}
            for profile in self.profiles:
                if profile.provenance == "body_silhouette_outer":
                    if profile.source_face_ids:
                        raise ValueError(
                            "The whole-body silhouette is synthetic and must not claim "
                            "ownership of Fusion BRepFaces"
                        )
                    continue
                if len(profile.source_face_ids) != 1:
                    raise ValueError(
                        f"Extracted profile '{profile.profile_id}' must be owned by "
                        "exactly one Fusion BRepFace"
                    )
                source_id = profile.source_face_ids[0]
                if source_id not in set(fact_ids):
                    raise ValueError(
                        f"Extracted profile '{profile.profile_id}' references an "
                        "uninventoried Fusion BRepFace"
                    )
                ownership = owner_by_id[source_id]
                if (ownership.state != OwnershipState.EXPOSED or
                        ownership.machining_side != profile.machining_side):
                    raise ValueError(
                        f"Extracted profile '{profile.profile_id}' is not owned by "
                        "an exposed face on the same TPA side"
                    )
            for hole in self.holes:
                if hole.source_entry_face_id not in set(fact_ids):
                    raise ValueError(
                        f"Hole '{hole.hole_id}' references an uninventoried entry face"
                    )
                ownership = owner_by_id[hole.source_entry_face_id]
                if (ownership.state != OwnershipState.EXPOSED or
                        ownership.machining_side != hole.machining_side):
                    raise ValueError(
                        f"Hole '{hole.hole_id}' entry face is not exposed on the "
                        "same TPA side"
                    )

        for profile in self.profiles:
            profile.validate()
            if profile.z_mm > EPS_MM:
                raise ValueError(
                    f"Profile '{profile.chain.name}' has positive access depth "
                    f"Z={profile.z_mm:.6f} mm"
                )
            side_number = int(profile.machining_side)
            if side_number == MachiningSide.SIDE1:
                x_limit, y_min, y_max = self.stock_width, 0.0, self.stock_height
                depth_min = -self.thickness
            elif side_number in (MachiningSide.SIDE3, MachiningSide.SIDE5):
                x_limit, y_min, y_max = self.stock_width, 0.0, self.thickness
                depth_min = -self.stock_height
            elif side_number in (MachiningSide.SIDE4, MachiningSide.SIDE6):
                x_limit, y_min, y_max = self.stock_height, 0.0, self.thickness
                depth_min = -self.stock_width
            elif 7 <= side_number <= 99:
                machining_frame = frame_by_side.get(side_number)
                if (machining_frame is None or
                        machining_frame.kind != MachiningFrameKind.FICTIVE_FACE):
                    raise ValueError(
                        f"Profile on SIDE{side_number} has no fictive machining frame"
                    )
                x_limit = machining_frame.length_mm
                y_min, y_max = 0.0, machining_frame.height_mm
                depth_min = -machining_frame.thickness_mm
            else:
                raise ValueError(f"Unsupported TPA side number: {side_number}")
            if profile.z_mm < depth_min - EPS_MM:
                raise ValueError(
                    f"Profile '{profile.chain.name}' access depth "
                    f"Z={profile.z_mm:.6f} is outside SIDE{int(profile.machining_side)}"
                )
            for segment in profile.chain.segments:
                points = [segment.start, segment.end]
                if isinstance(segment, Arc2D):
                    points.append(segment.center)
                for point in points[:2]:
                    qx = tcn_quantized(point.x)
                    qy = tcn_quantized(point.y)
                    qx_limit = tcn_quantized(x_limit)
                    qy_min = tcn_quantized(y_min)
                    qy_max = tcn_quantized(y_max)
                    if not (0.0 <= qx <= qx_limit):
                        raise ValueError(
                            f"Profile '{profile.chain.name}' SIDE{int(profile.machining_side)} "
                            f"X={point.x:.12f} (TCN {qx:.4f}) is outside local "
                            f"[0, {x_limit:.12f}] (TCN {qx_limit:.4f})"
                        )
                    if not (qy_min <= qy <= qy_max):
                        raise ValueError(
                            f"Profile '{profile.chain.name}' SIDE{int(profile.machining_side)} "
                            f"Y={point.y:.12f} (TCN {qy:.4f}) is outside local "
                            f"[{y_min:.12f}, {y_max:.12f}] "
                            f"(TCN [{qy_min:.4f}, {qy_max:.4f}])"
                        )

        for hole in self.holes:
            hole.validate()
            side_number = int(hole.machining_side)
            if side_number == MachiningSide.SIDE1:
                x_limit, y_limit, depth_limit = (
                    self.stock_width, self.stock_height, self.thickness,
                )
            elif side_number in (MachiningSide.SIDE3, MachiningSide.SIDE5):
                x_limit, y_limit, depth_limit = (
                    self.stock_width, self.thickness, self.stock_height,
                )
            elif side_number in (MachiningSide.SIDE4, MachiningSide.SIDE6):
                x_limit, y_limit, depth_limit = (
                    self.stock_height, self.thickness, self.stock_width,
                )
            elif 7 <= side_number <= 99:
                machining_frame = frame_by_side.get(side_number)
                if (machining_frame is None or
                        machining_frame.kind != MachiningFrameKind.FICTIVE_FACE):
                    raise ValueError(
                        f"Hole '{hole.hole_id}' on SIDE{side_number} has no "
                        "fictive machining frame"
                    )
                x_limit = machining_frame.length_mm
                y_limit = machining_frame.height_mm
                depth_limit = machining_frame.thickness_mm
            else:
                raise ValueError(
                    f"Hole '{hole.hole_id}' uses unsupported SIDE{side_number}"
                )
            qx = tcn_quantized(hole.center.x)
            qy = tcn_quantized(hole.center.y)
            if not 0.0 <= qx <= tcn_quantized(x_limit):
                raise ValueError(
                    f"Hole '{hole.hole_id}' X={hole.center.x:.12f} is outside "
                    f"SIDE{side_number} [0, {x_limit:.12f}]"
                )
            if not 0.0 <= qy <= tcn_quantized(y_limit):
                raise ValueError(
                    f"Hole '{hole.hole_id}' Y={hole.center.y:.12f} is outside "
                    f"SIDE{side_number} [0, {y_limit:.12f}]"
                )
            if tcn_quantized(hole.depth_mm) > tcn_quantized(depth_limit):
                raise ValueError(
                    f"Hole '{hole.hole_id}' depth {hole.depth_mm:.6f} exceeds "
                    f"SIDE{side_number} thickness {depth_limit:.6f}"
                )


def profile_sort_key(profile: PlanarProfileIR) -> tuple:
    """Deterministic TPA order: top outer, top inner, then deeper layers."""
    role_order = {
        "body_silhouette_outer": 0,
        "side1_inner": 1,
    }.get(profile.provenance, 3)
    return (
        int(profile.machining_side),
        role_order,
        0.0 if abs(profile.z_mm) <= EPS_MM else -profile.z_mm,
        profile.chain.name,
        profile.source_face_ids,
    )


def panel_to_side_coordinates(
    side: MachiningSide,
    xp: float,
    yp: float,
    zp: float,
    stock_width: float,
    stock_height: float,
    stock_thickness: float,
) -> tuple[Vec2, float]:
    """Apply the panel-space → real TPA-side coordinate contract.

    Panel Z is top-relative (top=0, bottom=-DS), whereas TpaCAD lateral-face Y
    is absolute through the piece thickness (bottom=0, top=DS).
    """
    if side == MachiningSide.SIDE1:
        return Vec2(xp, yp), zp
    z_abs = zp + stock_thickness
    if side == MachiningSide.SIDE3:
        return Vec2(xp, z_abs), -yp
    if side == MachiningSide.SIDE5:
        return Vec2(xp, z_abs), yp - stock_height
    if side == MachiningSide.SIDE4:
        return Vec2(yp, z_abs), xp - stock_width
    if side == MachiningSide.SIDE6:
        return Vec2(yp, z_abs), -xp
    raise ValueError(f"Unsupported TPA machining side: {side}")


def fictive_local_to_panel(
    frame: MachiningFrameIR, point: Vec2, z_mm: float = 0.0,
) -> tuple[float, float, float]:
    """Reconstruct stock/panel coordinates from one fictive-face coordinate.

    The returned Z uses Tribu's top-relative convention (top=0, bottom=-DS).
    TCN GSIDE point records add DS to this Z to use TpaCAD's absolute piece Z.
    """
    if frame.kind != MachiningFrameKind.FICTIVE_FACE:
        raise ValueError("Local fictive transform requires a fictive frame")
    return tuple(
        frame.origin[index]
        + point.x * frame.x_axis[index]
        + point.y * frame.y_axis[index]
        + z_mm * frame.outward_axis[index]
        for index in range(3)
    )


def fictive_frame_points_absolute(
    panel: PanelIR, frame: MachiningFrameIR,
) -> tuple[tuple[float, float, float], ...]:
    """Return TpaCAD P0/P1/P2 in absolute piece coordinates."""
    points = (
        fictive_local_to_panel(frame, Vec2(0.0, 0.0)),
        fictive_local_to_panel(frame, Vec2(frame.length_mm, 0.0)),
        fictive_local_to_panel(frame, Vec2(0.0, frame.height_mm)),
    )
    return tuple((x, y, z + panel.thickness) for x, y, z in points)


def classify_orthogonal_normal(
    nx: float, ny: float, nz: float, angular_tolerance_degrees: float = 0.01,
) -> MachiningSide | None:
    """Map an outward panel-space normal to a supported machining direction."""
    length = math.sqrt(nx * nx + ny * ny + nz * nz)
    if length <= 1e-12:
        return None
    nx, ny, nz = nx / length, ny / length, nz / length
    threshold = math.cos(math.radians(angular_tolerance_degrees))
    candidates = (
        (nz, MachiningSide.SIDE1),
        (-ny, MachiningSide.SIDE3),
        (nx, MachiningSide.SIDE4),
        (ny, MachiningSide.SIDE5),
        (-nx, MachiningSide.SIDE6),
    )
    alignment, side = max(candidates, key=lambda item: item[0])
    return side if alignment >= threshold else None


def quantized_point(point: Vec2, tolerance_mm: float) -> tuple[int, int]:
    return (round(point.x / tolerance_mm), round(point.y / tolerance_mm))


def chain_signature(
    chain: CurveChain2D,
    tolerance_mm: float = CONNECTIVITY_TOLERANCE_MM,
) -> tuple:
    """Direction/start-independent exact-primitive signature for deduplication."""
    tokens = []
    for segment in chain.segments:
        if isinstance(segment, Line2D):
            token = ("L", quantized_point(segment.start, tolerance_mm),
                     quantized_point(segment.end, tolerance_mm))
        else:
            token = (
                "A", quantized_point(segment.start, tolerance_mm),
                quantized_point(segment.end, tolerance_mm),
                quantized_point(segment.center, tolerance_mm),
                segment.clockwise, segment.full_circle,
            )
        tokens.append(token)

    def rotations(items: Sequence[tuple]) -> Iterable[tuple]:
        for index in range(len(items)):
            yield tuple(items[index:]) + tuple(items[:index])

    forward = list(rotations(tokens))
    reversed_tokens = []
    for segment in reversed(chain.segments):
        reverse = segment.reversed()
        if isinstance(reverse, Line2D):
            reversed_tokens.append((
                "L", quantized_point(reverse.start, tolerance_mm),
                quantized_point(reverse.end, tolerance_mm),
            ))
        else:
            reversed_tokens.append((
                "A", quantized_point(reverse.start, tolerance_mm),
                quantized_point(reverse.end, tolerance_mm),
                quantized_point(reverse.center, tolerance_mm),
                reverse.clockwise, reverse.full_circle,
            ))
    return min(forward + list(rotations(reversed_tokens)))


def profile_selection_key(panel: PanelIR, profile: PlanarProfileIR) -> str:
    """Return a stock-allowance-independent key for operator export choices.

    Fusion ``tempId`` values are intentionally absent: they can change after a
    document is reopened.  The key describes the complete TPA-side geometry in
    finished-part coordinates, including side and access depth.  Changing the
    actual geometry therefore creates a new candidate instead of silently
    reusing an obsolete manufacturing choice.
    """
    if profile.provenance == "body_silhouette_outer":
        return "v1:FINAL_OUTER_CONTOUR"

    side = profile.machining_side
    if side == MachiningSide.SIDE1:
        dx = -panel.allowance.x_minus
        dy = -panel.allowance.y_minus
        depth = profile.z_mm
    elif side == MachiningSide.SIDE3:
        dx = -panel.allowance.x_minus
        dy = 0.0
        depth = profile.z_mm + panel.allowance.y_minus
    elif side == MachiningSide.SIDE5:
        dx = -panel.allowance.x_minus
        dy = 0.0
        depth = profile.z_mm + panel.allowance.y_plus
    elif side == MachiningSide.SIDE4:
        dx = -panel.allowance.y_minus
        dy = 0.0
        depth = profile.z_mm + panel.allowance.x_plus
    elif side == MachiningSide.SIDE6:
        dx = -panel.allowance.y_minus
        dy = 0.0
        depth = profile.z_mm + panel.allowance.x_minus
    elif 7 <= int(side) <= 99:
        dx = dy = 0.0
        depth = profile.z_mm
    else:
        raise ValueError(f"Unsupported profile side: {side}")

    translated = []
    for segment in profile.chain.segments:
        start = Vec2(segment.start.x + dx, segment.start.y + dy)
        end = Vec2(segment.end.x + dx, segment.end.y + dy)
        if isinstance(segment, Line2D):
            translated.append(Line2D(start, end))
        else:
            translated.append(Arc2D(
                start, end,
                Vec2(segment.center.x + dx, segment.center.y + dy),
                segment.clockwise, segment.full_circle,
            ))
    normalized_chain = CurveChain2D(
        translated, profile.chain.closed, name="selection_fingerprint",
    )
    signature = chain_signature(
        normalized_chain, CONNECTIVITY_TOLERANCE_MM,
    )
    digest = hashlib.sha256(repr(signature).encode("ascii")).hexdigest()[:20]
    depth_token = round(depth / CONNECTIVITY_TOLERANCE_MM)
    return (
        f"v1:side={int(side)}:zmode={profile.z_mode.value}:"
        f"depth={depth_token}:shape={digest}"
    )
