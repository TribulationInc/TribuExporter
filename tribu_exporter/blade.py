"""Busellato/TpaCAD straight exterior blade-cut planning.

This module plans straight Busellato blade workings for two proven cases:

* BLADEXY for operator-selected inclined exterior fictive faces;
* BLADEX/BLADEY for the four sides of a proven axis-aligned rectangular
  ``body_silhouette_outer`` profile when the operator explicitly requests
  ``Use blade for profile cuts``.

Fusion objects and TCN strings do not enter this module.  Inner profiles,
pockets, arcs, generic polygons and non-rectangular outer contours are never
converted to saw cuts by this milestone.

Machine contract implemented here
---------------------------------
The production mapping is the custom Busellato ``LAME.TMCR`` from the Jet
Master T PPC.  Its selector ``r9`` dispatches the three official branches:

    r9 = 0 -> BLADEX   (r10/r11 start, r17 X final, W95 Beta fixed at 90)
    r9 = 1 -> BLADEY   (r10/r11 start, r18 Y final, W95 Beta fixed at 90)
    r9 = 2 -> BLADEXY  (r10/r11 start, r19 Alpha, r20 U, r21 Beta)

The shared custom-macro parameters are:

    r10 -> X start
    r11 -> Y start
    r12 -> Zp (first blade-depth coordinate)
    r13 -> Z2 (second blade-depth coordinate)
    r16 -> tool
    r19 -> Alpha
    r20 -> U / cut length
    r21 -> Beta
    r25 -> compensation side
    r26 -> double-pass enable
    r27 -> chord calculation
    r29 -> second-pass feed

For this Busellato contract:

* For BLADEXY, X/Y locate the programmed blade-plane trace on SIDE1 Z=0.
* For BLADEX/BLADEY, X/Y plus X-final/Y-final locate the finished rectangular
  trim line.  The custom macro derives Alpha automatically and fixes W95 Beta
  at 90 degrees.
* The declared controller compensation contract places blade width in waste;
  ``normal_offset_mm`` is only a calibrated residual normal shift.
* Zp/Z2 are signed penetration distances along the blade-depth coordinate.
* Their vertical projection is ``Z * sin(abs(Beta))``. Therefore the blade-axis
  depth required to reach a vertical stock depth H is ``H/sin(abs(Beta))``.
* ``breakthrough_mm`` is deliberately defined ALONG THE BLADE DEPTH COORDINATE,
  not vertically. A value of 10 means the final pass travels 10 mm farther
  along the blade after reaching the deepest required stock point.
* In ``score_then_full`` mode, Zp is the shallow first/scoring pass and Z2 is
  the full-depth second pass, matching the labels in the custom Busellato MCR
  (``quota z(prima)``, ``seconda quota z``).

The finished-side boundary of the kerf is the target plane. The configured
controller compensation must place blade width into waste; this module does
not add half-kerf a second time.
"""
from __future__ import annotations

from dataclasses import dataclass, fields
import json
import math
from pathlib import Path
import re

from .model import Line2D, MachiningFrameIR, MachiningFrameKind, PanelIR, tcn_quantized

V3 = tuple[float, float, float]

BUSELLATO_Z_REFERENCE = "busellato_lame_w95"
PASS_SINGLE = "single_pass"
PASS_SCORE_THEN_FULL = "score_then_full"

BLADE_X = 0
BLADE_Y = 1
BLADE_XY = 2

SOURCE_FICTIVE_FACE = "fictive_face"
SOURCE_PROFILE_OUTER = "profile_outer_rectangle"
OUTER_PROFILE_PROVENANCE = "body_silhouette_outer"


def dot(a: V3, b: V3) -> float:
    return sum(x * y for x, y in zip(a, b))


def add(a: V3, b: V3) -> V3:
    return tuple(x + y for x, y in zip(a, b))


def scale(a: V3, value: float) -> V3:
    return tuple(x * value for x in a)


def length(a: V3) -> float:
    return math.sqrt(dot(a, a))


def normalized(a: V3) -> V3:
    norm = length(a)
    if not math.isfinite(norm) or norm < 1e-12:
        raise ValueError("Cannot normalize a null/non-finite vector")
    return scale(a, 1.0 / norm)


def _angle_error_degrees(a: float, b: float) -> float:
    """Smallest absolute angular distance between two degree values."""
    return abs((a - b + 180.0) % 360.0 - 180.0)


def busellato_w95_depth_for_vertical_depth(
    vertical_depth_mm: float,
    beta_degrees: float,
) -> float:
    """Return the positive blade-axis depth needed for a vertical depth.

    Official Busellato GRAPHICLAMATA geometry projects the programmed blade
    depth by ``sin(abs(Beta))`` onto SIDE1 Z. This helper returns a positive
    magnitude. The TCN Zp/Z2 value is its negative when cutting into the stock.
    """
    if (
        isinstance(vertical_depth_mm, bool)
        or not isinstance(vertical_depth_mm, (int, float))
        or not math.isfinite(vertical_depth_mm)
        or vertical_depth_mm < 0
    ):
        raise ValueError("Vertical blade depth must be a finite non-negative number")
    if (
        isinstance(beta_degrees, bool)
        or not isinstance(beta_degrees, (int, float))
        or not math.isfinite(beta_degrees)
        or not 0 < abs(beta_degrees) <= 90
    ):
        raise ValueError("Busellato blade depth needs non-zero Beta within 90 degrees")

    vertical_component = abs(math.sin(math.radians(beta_degrees)))
    if vertical_component < 1e-12:
        raise ValueError("Busellato blade depth is undefined for Beta near zero")
    return vertical_depth_mm / vertical_component


@dataclass(frozen=True)
class BladeMachineProfile:
    """Machine/tool/process data required by the Busellato blade planner.

    Physical blade values used by this module are only:
      * diameter_mm: conservative disc overtravel at each end;
      * kerf_mm: waste-side stock coverage / plane check;
      * max_cutting_depth_mm: usable W95 blade-axis penetration.

    The CNC/tool technology owns pivot geometry and other head kinematics; they
    are intentionally not duplicated here.

    ``score_depth_mm`` and ``breakthrough_mm`` are blade-axis distances, not
    vertical Z distances. ``second_pass_feed`` is optional; null preserves the
    machine/macro default. ``travel_angles_degrees`` is a BLADEXY travel
    preference list only: arbitrary Alpha is supported by the custom Busellato
    BLADEXY branch. Exact listed directions are preferred when available; a
    generic plane-intersection direction is used otherwise. The official
    BLADEX/BLADEY branches derive 0/180 or 90/270 from their start/final
    coordinates themselves.
    """

    name: str
    macro_path: str
    tool_id: int
    diameter_mm: float
    kerf_mm: float
    max_cutting_depth_mm: float
    z_reference: str
    beta_sign: int
    max_abs_beta_degrees: float
    normal_offset_mm: float
    breakthrough_mm: float
    end_clearance_mm: float
    compensation_reference: str

    spindle_rpm: float | None = None
    entry_feed: float | None = None
    cutting_feed: float | None = None
    second_pass_feed: float | None = None

    travel_angles_degrees: tuple[float, ...] = ()
    min_beta_degrees: float = 0.0

    pass_policy: str = PASS_SINGLE
    score_depth_mm: float | None = None

    def validate(self) -> None:
        if not isinstance(self.name, str) or not self.name.strip():
            raise ValueError("Blade profile needs a name")

        if (
            not isinstance(self.macro_path, str)
            or not self.macro_path
            or not self.macro_path.isascii()
            or re.search(r"[\s#{}\[\]=;]", self.macro_path)
            or not self.macro_path.lower().endswith(".tmcr")
        ):
            raise ValueError(
                "Blade macro_path must be an ASCII .tmcr path without spaces or TCN delimiters"
            )

        if type(self.tool_id) is not int or self.tool_id <= 0:
            raise ValueError("Select an explicit positive blade tool_id")

        if type(self.beta_sign) is not int or self.beta_sign not in (-1, 1):
            raise ValueError("Blade beta_sign must explicitly be 1 or -1")

        for field_name in (
            "diameter_mm",
            "kerf_mm",
            "max_cutting_depth_mm",
            "max_abs_beta_degrees",
            "normal_offset_mm",
            "breakthrough_mm",
            "end_clearance_mm",
        ):
            value = getattr(self, field_name)
            if (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not math.isfinite(value)
            ):
                raise ValueError(f"Blade {field_name} must be a finite number")

        if not 0 < self.kerf_mm < self.diameter_mm:
            raise ValueError("Blade kerf must be positive and smaller than diameter")
        if not 0 < self.max_cutting_depth_mm <= self.diameter_mm / 2:
            raise ValueError(
                "Blade usable cutting depth must be positive and no greater than radius"
            )
        if not 0 < self.max_abs_beta_degrees <= 90:
            raise ValueError("Blade maximum absolute Beta must be in (0, 90]")
        if self.breakthrough_mm < 0 or self.end_clearance_mm < 0:
            raise ValueError("Blade clearances cannot be negative")

        if self.z_reference != BUSELLATO_Z_REFERENCE:
            raise ValueError(
                f"Executable Busellato blade export requires "
                f"z_reference={BUSELLATO_Z_REFERENCE!r}"
            )

        if self.compensation_reference != "finished_surface":
            raise ValueError(
                "Blade profile must specify controller compensation referenced to finished_surface"
            )

        if (
            not isinstance(self.travel_angles_degrees, tuple)
            or not self.travel_angles_degrees
            or any(
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not math.isfinite(value)
                or not 0 <= value < 360
                for value in self.travel_angles_degrees
            )
            or len(set(self.travel_angles_degrees)) != len(self.travel_angles_degrees)
        ):
            raise ValueError(
                "Set finite travel_angles_degrees in preference order; "
                "they are direction preferences, not a BLADEXY Alpha whitelist"
            )

        if (
            isinstance(self.min_beta_degrees, bool)
            or not isinstance(self.min_beta_degrees, (int, float))
            or not math.isfinite(self.min_beta_degrees)
            or not -self.max_abs_beta_degrees
            <= self.min_beta_degrees
            <= self.max_abs_beta_degrees
        ):
            raise ValueError("Blade minimum Beta must lie inside its configured limits")

        if self.pass_policy not in (PASS_SINGLE, PASS_SCORE_THEN_FULL):
            raise ValueError(
                "Blade pass_policy must be 'single_pass' or 'score_then_full'"
            )

        if self.pass_policy == PASS_SCORE_THEN_FULL:
            if (
                self.score_depth_mm is None
                or isinstance(self.score_depth_mm, bool)
                or not isinstance(self.score_depth_mm, (int, float))
                or not math.isfinite(self.score_depth_mm)
                or self.score_depth_mm <= 0
            ):
                raise ValueError(
                    "score_then_full requires an explicit positive score_depth_mm "
                    "measured along the blade depth coordinate"
                )
            if self.score_depth_mm > self.max_cutting_depth_mm:
                raise ValueError(
                    "Blade score_depth_mm cannot exceed configured usable cutting depth"
                )
        elif self.score_depth_mm is not None:
            raise ValueError(
                "score_depth_mm must be null/omitted when pass_policy is single_pass"
            )

        for field_name in (
            "spindle_rpm",
            "entry_feed",
            "cutting_feed",
            "second_pass_feed",
        ):
            value = getattr(self, field_name)
            if value is not None and (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not math.isfinite(value)
                or value <= 0
            ):
                raise ValueError(
                    f"Blade {field_name} must be positive, or null to use macro/tool defaults"
                )

    @classmethod
    def load(cls, filename: str | Path) -> "BladeMachineProfile":
        if not str(filename).strip():
            raise ValueError(
                "Choose a blade machine profile JSON file before enabling blade export"
            )
        try:
            data = json.loads(Path(filename).read_text(encoding="utf-8-sig"))
        except (OSError, ValueError) as error:
            raise ValueError(f"Cannot read blade machine profile: {error}") from error

        if not isinstance(data, dict) or data.pop("schema", None) != 1:
            raise ValueError("Blade machine profile requires schema 1")

        unknown = set(data) - {item.name for item in fields(cls)}
        if unknown:
            raise ValueError(
                f"Unknown blade profile fields: {', '.join(sorted(unknown))}"
            )

        try:
            if "travel_angles_degrees" in data:
                data["travel_angles_degrees"] = tuple(data["travel_angles_degrees"])
            profile = cls(**data)
        except TypeError as error:
            raise ValueError(f"Incomplete blade machine profile: {error}") from error

        profile.validate()
        return profile

    def require_verified_adapter(self) -> None:
        """Production gate for the now-identified custom Busellato contract."""
        self.validate()
        if self.z_reference != BUSELLATO_Z_REFERENCE:
            raise ValueError(
                "Blade machine export is blocked: unsupported blade reference contract"
            )


@dataclass(frozen=True)
class BladeTargetIR:
    source_face_id: str
    frame_id: str
    # Maximum signed distance of the COMPLETE retained body outside the target plane.
    body_outside_mm: float
    # Geometric mismatch between selected Fusion face and requested cutting plane.
    selected_face_plane_error_mm: float = math.inf


@dataclass(frozen=True)
class BladeCutIR:
    source_face_id: str
    target_frame_id: str
    target_side: int
    target_normal: V3
    target_offset_mm: float
    stock_dimensions: V3
    body_outside_mm: float
    selected_face_plane_error_mm: float
    stock_section: tuple[V3, ...]

    start_xy: tuple[float, float]
    alpha_degrees: float
    beta_degrees: float
    length_mm: float

    # Custom Busellato LAME fields:
    # z_mm -> r12/#8512 (prima quota Z)
    # z2_mm -> r13/#8513 (seconda quota Z)
    z_mm: float
    z2_enabled: bool
    z2_mm: float | None
    z2_feed: float | None

    compensation: int
    required_depth_mm: float
    machine: BladeMachineProfile

    # Working family / source.  Defaults preserve existing BLADEXY callers.
    mode: int = BLADE_XY
    source_kind: str = SOURCE_FICTIVE_FACE
    source_profile_id: str | None = None
    source_segment_index: int | None = None
    # For BLADEX/BLADEY this is r17 (X final) or r18 (Y final).
    end_axis_mm: float | None = None

    @property
    def end_xy(self) -> tuple[float, float]:
        if self.mode == BLADE_X:
            if self.end_axis_mm is None:
                raise ValueError("BLADEX cut is missing X final")
            return (self.end_axis_mm, self.start_xy[1])
        if self.mode == BLADE_Y:
            if self.end_axis_mm is None:
                raise ValueError("BLADEY cut is missing Y final")
            return (self.start_xy[0], self.end_axis_mm)
        if self.mode != BLADE_XY:
            raise ValueError(f"Unsupported blade mode {self.mode}")
        alpha = math.radians(self.alpha_degrees)
        return (
            self.start_xy[0] + self.length_mm * math.cos(alpha),
            self.start_xy[1] + self.length_mm * math.sin(alpha),
        )

    @property
    def final_depth_mm(self) -> float:
        """Positive final blade-axis penetration, including breakthrough."""
        return abs(self.z2_mm if self.z2_enabled and self.z2_mm is not None else self.z_mm)


def plane_stock_section(
    normal: V3,
    offset: float,
    dimensions: V3,
) -> tuple[V3, ...]:
    """Intersect a plane with all 12 stock-box edges, including Z=0/-DS."""
    width, height, thickness = dimensions
    points: list[V3] = []
    corners = [
        (x, y, z)
        for x in (0.0, width)
        for y in (0.0, height)
        for z in (-thickness, 0.0)
    ]

    for index, first in enumerate(corners):
        for axis_bit in (1, 2, 4):
            other = index ^ axis_bit
            if other <= index:
                continue
            second = corners[other]
            a = dot(normal, first) - offset
            b = dot(normal, second) - offset
            candidates: list[V3] = []

            if abs(a) < 1e-9:
                candidates.append(first)
            if abs(b) < 1e-9:
                candidates.append(second)
            if a * b < 0:
                fraction = a / (a - b)
                candidates.append(
                    tuple(
                        x + fraction * (y - x)
                        for x, y in zip(first, second)
                    )
                )

            for point in candidates:
                if not any(math.dist(point, prior) < 1e-8 for prior in points):
                    points.append(point)

    return tuple(sorted(points))


def _select_machine_orientation(
    n: V3,
    h: float,
    machine: BladeMachineProfile,
    label: str,
) -> tuple[V3, float, float, int]:
    """Select Alpha/Beta/compensation for an arbitrary straight BLADEXY plane.

    The cutting travel is the intersection of the target plane with SIDE1
    (Z=0). Therefore a face inclined in both panel X and Y simply requires an
    arbitrary Alpha plus the corresponding Beta. The custom Busellato BLADEXY
    macro accepts float Alpha; ``travel_angles_degrees`` only preserves a
    preferred travel direction when one of the two equivalent directions
    exactly matches a configured preference. It is not an Alpha whitelist.

    MACHINE-VERIFIED BETA CONVENTION:
    the former ``atan2(n_z, n_left)`` produced the complementary angle
    (for example +/-53.1297 deg where W95 needs +/-36.8703 deg). W95 Beta is
    obtained from ``atan2(n_left, n_z)`` and then mapped through beta_sign.
    Mirrored Fusion bevels verified both signs on the Jet Master T.
    """
    base = (-n[1] / h, n[0] / h, 0.0)
    travels = (base, scale(base, -1.0))

    # Preserve established cardinal travel choices when a candidate exactly
    # matches the configured preference list. Otherwise arbitrary Alpha is
    # valid and we deterministically keep the canonical plane-intersection
    # direction (``base``).
    chosen = None
    for allowed in machine.travel_angles_degrees:
        for travel in travels:
            alpha = math.degrees(math.atan2(travel[1], travel[0])) % 360.0
            if _angle_error_degrees(alpha, allowed) <= 1e-7:
                chosen = travel
                break
        if chosen is not None:
            break
    if chosen is None:
        chosen = base

    travel = chosen
    alpha = math.degrees(math.atan2(travel[1], travel[0])) % 360.0
    left_normal = (-travel[1], travel[0], 0.0)
    n_left = dot(n, left_normal)
    if abs(n_left) < 1e-10:
        raise ValueError(f"{label}: target plane does not define a stable BLADEXY travel")

    # W95 Beta is the signed angle from SIDE1 +Z toward the plane's horizontal
    # normal measured on the left/right section perpendicular to travel. The
    # wrap keeps the same unoriented plane in the programmable [-90, +90]
    # interval. beta_sign is the machine/tool calibration only.
    beta_geometry = (
        (math.degrees(math.atan2(n_left, n[2])) + 90.0) % 180.0
        - 90.0
    )
    beta = beta_geometry * machine.beta_sign

    if not (
        machine.min_beta_degrees - 1e-8
        <= beta
        <= machine.max_abs_beta_degrees + 1e-8
    ):
        raise ValueError(
            f"{label}: BLADEXY Beta {beta:.5f} exceeds configured limits "
            f"[{machine.min_beta_degrees:.5f}, "
            f"{machine.max_abs_beta_degrees:.5f}]"
        )

    compensation = 1 if n_left > 0 else 2  # TPA: Sx / Dx
    return travel, alpha, beta, compensation


def _plan_pass_depths(
    *,
    machine: BladeMachineProfile,
    beta_degrees: float,
    deepest_stock_z: float,
    label: str,
) -> tuple[float, bool, float | None, float | None, float]:
    """Return Zp, Z2 enable, Z2, Z2 feed and final required blade depth.

    ``deepest_stock_z`` is a SIDE1 vertical coordinate (normally <= 0).
    ``breakthrough_mm`` is added after projection, so it means extra travel
    ALONG THE BLADE, exactly as declared by the machine profile.
    """
    vertical_depth = max(0.0, -deepest_stock_z)
    stock_blade_depth = busellato_w95_depth_for_vertical_depth(
        vertical_depth, beta_degrees
    )
    final_depth = stock_blade_depth + machine.breakthrough_mm

    if final_depth > machine.max_cutting_depth_mm + 1e-8:
        raise ValueError(
            f"{label}: requires {final_depth:.4f} mm blade-axis penetration "
            f"({stock_blade_depth:.4f} mm to stock + "
            f"{machine.breakthrough_mm:.4f} mm breakthrough); configured usable "
            f"depth is {machine.max_cutting_depth_mm:.4f} mm"
        )

    if machine.pass_policy == PASS_SINGLE:
        return -final_depth, False, None, None, final_depth

    assert machine.pass_policy == PASS_SCORE_THEN_FULL
    assert machine.score_depth_mm is not None

    if machine.score_depth_mm >= final_depth - 1e-8:
        raise ValueError(
            f"{label}: configured score depth {machine.score_depth_mm:.4f} mm "
            f"must be shallower than final depth {final_depth:.4f} mm"
        )

    # Official custom Busellato LAME.TMCR names r12 "quota z(prima)" and r13
    # "seconda quota z". Known-good PPC records use e.g. Zp=-3, Z2=-28.07.
    z_first = -machine.score_depth_mm
    z_second = -final_depth
    return z_first, True, z_second, machine.second_pass_feed, final_depth


def _plan_one(
    panel: PanelIR,
    frame: MachiningFrameIR,
    target: BladeTargetIR,
    machine: BladeMachineProfile,
) -> BladeCutIR:
    frame.validate()
    label = (
        f"Blade SIDE{frame.tpa_face_number} "
        f"(Fusion face {target.source_face_id})"
    )

    if not target.source_face_id:
        raise ValueError(f"{label}: missing face ownership")

    owners = {
        owner
        for profile in panel.profiles
        if int(profile.machining_side) == frame.tpa_face_number
        and profile.provenance == "fictive_face_boundary"
        for owner in profile.source_face_ids
    }
    if owners and owners != {target.source_face_id}:
        raise ValueError(
            f"{label}: target does not own the selected fictive face"
        )

    if (
        not math.isfinite(target.body_outside_mm)
        or abs(target.body_outside_mm) > panel.curve_tolerance_mm
    ):
        raise ValueError(
            f"{label}: straight cut crosses finished material, or whole-body "
            f"support is unproven ({target.body_outside_mm} mm)"
        )

    if (
        not math.isfinite(target.selected_face_plane_error_mm)
        or target.selected_face_plane_error_mm < 0
        or target.selected_face_plane_error_mm > panel.curve_tolerance_mm
    ):
        raise ValueError(
            f"{label}: selected face does not coincide with the requested cutting plane"
        )

    # Normalize explicitly: plane offsets and kerf distances below are in mm.
    n = normalized(frame.outward_axis)
    h = math.hypot(n[0], n[1])
    vertical_component = abs(n[2])

    if h < 1e-6 or vertical_component < 1e-6:
        raise ValueError(
            f"{label}: first blade milestone requires an inclined exterior plane"
        )

    d = dot(n, frame.origin)
    dimensions = (panel.stock_width, panel.stock_height, panel.thickness)

    section = plane_stock_section(n, d, dimensions)
    if len(section) < 3:
        raise ValueError(f"{label}: target plane has no area inside the stock")

    travel, alpha, beta, compensation = _select_machine_orientation(
        n, h, machine, label
    )

    if abs(beta) < 1e-6:
        raise ValueError(
            f"{label}: Busellato W95 blade-depth projection is undefined at Beta=0"
        )

    # The target plane is the FINISHED side of the kerf. The blade width must
    # remain in waste (+ outward normal). Include the outward kerf boundary when
    # calculating required stock coverage / lowest stock point.
    outer_section = plane_stock_section(n, d + machine.kerf_mm, dimensions)
    coverage = section + outer_section
    if not coverage:
        raise ValueError(f"{label}: blade/kerf has no stock intersection")

    deepest_stock_z = min(point[2] for point in coverage)
    z_program, z2_enabled, z2_program, z2_feed, required_depth = _plan_pass_depths(
        machine=machine,
        beta_degrees=beta,
        deepest_stock_z=deepest_stock_z,
        label=label,
    )

    # The custom W95 blade plane is located by its SIDE1 Z=0 trace. Zp/Z2 move
    # the disc within that plane; they do not define the plane-normal position.
    # normal_offset_mm is only a calibrated residual after controller comp.
    programmed_plane_offset = d + machine.normal_offset_mm
    xy_offset = programmed_plane_offset / h
    nh = (n[0] / h, n[1] / h, 0.0)

    # Cover the complete target + waste-side kerf section. One full blade radius
    # is included at each end, plus explicit machine clearance.
    lead = machine.diameter_mm / 2.0 + machine.end_clearance_mm
    lo = min(dot(travel, point) for point in coverage) - lead
    hi = max(dot(travel, point) for point in coverage) + lead

    start = add(scale(nh, xy_offset), scale(travel, lo))

    return BladeCutIR(
        source_face_id=target.source_face_id,
        target_frame_id=frame.frame_id,
        target_side=int(frame.tpa_face_number),
        target_normal=n,
        target_offset_mm=d,
        stock_dimensions=dimensions,
        body_outside_mm=target.body_outside_mm,
        selected_face_plane_error_mm=target.selected_face_plane_error_mm,
        stock_section=section,
        start_xy=start[:2],
        alpha_degrees=alpha,
        beta_degrees=beta,
        length_mm=hi - lo,
        z_mm=z_program,
        z2_enabled=z2_enabled,
        z2_mm=z2_program,
        z2_feed=z2_feed,
        compensation=compensation,
        required_depth_mm=required_depth,
        machine=machine,
        mode=BLADE_XY,
        source_kind=SOURCE_FICTIVE_FACE,
    )


def _outer_profile(panel: PanelIR):
    profiles = [
        profile for profile in panel.profiles
        if profile.provenance == OUTER_PROFILE_PROVENANCE
    ]
    if len(profiles) != 1:
        raise ValueError(
            "Use blade for profile cuts requires exactly one body_silhouette_outer"
        )
    return profiles[0]


def _rectangle_profile_geometry(panel: PanelIR):
    """Return (profile, xmin, xmax, ymin, ymax) for an exact 4-line XY rectangle.

    This milestone deliberately refuses generic linear polygons.  The complete
    mandatory outer profile must be a four-segment, axis-aligned rectangle at
    the declared TCN geometry tolerance.
    """
    profile = _outer_profile(panel)
    segments = tuple(profile.chain.segments)
    tolerance = panel.curve_tolerance_mm

    if not profile.chain.closed or len(segments) != 4:
        raise ValueError(
            "Use blade for profile cuts currently requires a closed outer "
            "rectangle made of exactly four segments"
        )
    if any(not isinstance(segment, Line2D) for segment in segments):
        raise ValueError(
            "Use blade for profile cuts currently supports rectangles made only "
            "of four straight Line2D segments"
        )

    points = [point for segment in segments for point in (segment.start, segment.end)]
    xmin = min(point.x for point in points)
    xmax = max(point.x for point in points)
    ymin = min(point.y for point in points)
    ymax = max(point.y for point in points)
    if xmax - xmin <= tolerance or ymax - ymin <= tolerance:
        raise ValueError("Outer rectangle collapsed below geometric tolerance")

    seen_sides: set[str] = set()
    for index, segment in enumerate(segments, 1):
        dx = segment.end.x - segment.start.x
        dy = segment.end.y - segment.start.y
        horizontal = abs(dy) <= tolerance and abs(dx) > tolerance
        vertical = abs(dx) <= tolerance and abs(dy) > tolerance
        if horizontal == vertical:
            raise ValueError(
                f"Outer rectangle segment {index} is not a unique X/Y straight edge"
            )

        if horizontal:
            y = (segment.start.y + segment.end.y) / 2.0
            if abs(y - ymin) <= tolerance:
                side = "ymin"
            elif abs(y - ymax) <= tolerance:
                side = "ymax"
            else:
                raise ValueError(
                    f"Outer rectangle segment {index} is horizontal but not on a rectangle bound"
                )
            for point in (segment.start, segment.end):
                if not (abs(point.x - xmin) <= tolerance or abs(point.x - xmax) <= tolerance):
                    raise ValueError(
                        f"Outer rectangle segment {index} endpoint is not a rectangle corner"
                    )
        else:
            x = (segment.start.x + segment.end.x) / 2.0
            if abs(x - xmin) <= tolerance:
                side = "xmin"
            elif abs(x - xmax) <= tolerance:
                side = "xmax"
            else:
                raise ValueError(
                    f"Outer rectangle segment {index} is vertical but not on a rectangle bound"
                )
            for point in (segment.start, segment.end):
                if not (abs(point.y - ymin) <= tolerance or abs(point.y - ymax) <= tolerance):
                    raise ValueError(
                        f"Outer rectangle segment {index} endpoint is not a rectangle corner"
                    )

        if side in seen_sides:
            raise ValueError(f"Outer rectangle repeats side {side}")
        seen_sides.add(side)

    if seen_sides != {"xmin", "xmax", "ymin", "ymax"}:
        raise ValueError("Outer profile is not a complete four-sided rectangle")

    return profile, xmin, xmax, ymin, ymax


def _profile_edge_outward_normal(segment: Line2D, bounds, tolerance: float) -> V3:
    xmin, xmax, ymin, ymax = bounds
    dx = segment.end.x - segment.start.x
    dy = segment.end.y - segment.start.y
    if abs(dy) <= tolerance and abs(dx) > tolerance:
        y = (segment.start.y + segment.end.y) / 2.0
        if abs(y - ymin) <= tolerance:
            return (0.0, -1.0, 0.0)
        if abs(y - ymax) <= tolerance:
            return (0.0, 1.0, 0.0)
    elif abs(dx) <= tolerance and abs(dy) > tolerance:
        x = (segment.start.x + segment.end.x) / 2.0
        if abs(x - xmin) <= tolerance:
            return (-1.0, 0.0, 0.0)
        if abs(x - xmax) <= tolerance:
            return (1.0, 0.0, 0.0)
    raise ValueError("Rectangle edge does not lie on a proven exterior bound")


def plan_rectangular_profile_blade_cuts(
    panel: PanelIR,
    machine: BladeMachineProfile,
) -> list[BladeCutIR]:
    """Replace the complete rectangular FINAL_OUTER_CONTOUR with four saw cuts.

    All-or-nothing by design.  Every side must be a straight axis-aligned edge;
    BLADEX/BLADEY put the complete blade kerf on the rectangle's waste side.
    The official custom macro fixes W95 Beta=90 for these two branches.
    """
    machine.validate()
    machine.require_verified_adapter()
    profile, xmin, xmax, ymin, ymax = _rectangle_profile_geometry(panel)
    dimensions = (panel.stock_width, panel.stock_height, panel.thickness)
    tolerance = panel.curve_tolerance_mm
    bounds = (xmin, xmax, ymin, ymax)
    lead = machine.diameter_mm / 2.0 + machine.end_clearance_mm

    z_program, z2_enabled, z2_program, z2_feed, required_depth = _plan_pass_depths(
        machine=machine,
        beta_degrees=90.0,
        deepest_stock_z=-panel.thickness,
        label="Blade rectangular outer profile",
    )

    cuts: list[BladeCutIR] = []
    for index, segment in enumerate(profile.chain.segments):
        assert isinstance(segment, Line2D)
        dx = segment.end.x - segment.start.x
        dy = segment.end.y - segment.start.y
        if abs(dy) <= tolerance and abs(dx) > tolerance:
            mode = BLADE_X
            travel = (1.0 if dx > 0 else -1.0, 0.0, 0.0)
            alpha = 0.0 if dx > 0 else 180.0
        elif abs(dx) <= tolerance and abs(dy) > tolerance:
            mode = BLADE_Y
            travel = (0.0, 1.0 if dy > 0 else -1.0, 0.0)
            alpha = 90.0 if dy > 0 else 270.0
        else:
            raise ValueError(
                f"Outer profile segment {index + 1} is not axis aligned"
            )

        outward = _profile_edge_outward_normal(segment, bounds, tolerance)
        left_normal = (-travel[1], travel[0], 0.0)
        n_left = dot(outward, left_normal)
        if abs(n_left) < 0.5:
            raise ValueError(
                f"Outer profile segment {index + 1}: cannot resolve waste-side compensation"
            )
        compensation = 1 if n_left > 0 else 2

        # Program the finished line (plus calibrated residual only); controller
        # compensation owns the saw width and must send it into +outward waste.
        shift = scale(outward, machine.normal_offset_mm)
        start_point = add(
            add((segment.start.x, segment.start.y, 0.0), shift),
            scale(travel, -lead),
        )
        end_point = add(
            add((segment.end.x, segment.end.y, 0.0), shift),
            scale(travel, lead),
        )
        end_axis = end_point[0] if mode == BLADE_X else end_point[1]
        cut_length = math.dist(start_point[:2], end_point[:2])

        d = dot(outward, (segment.start.x, segment.start.y, 0.0))
        section = plane_stock_section(outward, d, dimensions)
        if len(section) < 4:
            raise ValueError(
                f"Outer profile segment {index + 1}: trim plane does not cross stock as expected"
            )

        cuts.append(BladeCutIR(
            source_face_id="",
            target_frame_id="side1",
            target_side=1,
            target_normal=outward,
            target_offset_mm=d,
            stock_dimensions=dimensions,
            body_outside_mm=0.0,
            selected_face_plane_error_mm=0.0,
            stock_section=section,
            start_xy=start_point[:2],
            alpha_degrees=alpha,
            beta_degrees=90.0,
            length_mm=cut_length,
            z_mm=z_program,
            z2_enabled=z2_enabled,
            z2_mm=z2_program,
            z2_feed=z2_feed,
            compensation=compensation,
            required_depth_mm=required_depth,
            machine=machine,
            mode=mode,
            source_kind=SOURCE_PROFILE_OUTER,
            source_profile_id=profile.profile_id or OUTER_PROFILE_PROVENANCE,
            source_segment_index=index,
            end_axis_mm=end_axis,
        ))

    if len(cuts) != 4 or {cut.mode for cut in cuts} != {BLADE_X, BLADE_Y}:
        raise ValueError("Rectangular profile did not resolve to four BLADEX/BLADEY cuts")
    return cuts


def profile_blade_consumed_profile_ids(panel: PanelIR) -> set[str]:
    """Profiles completely replaced by validated profile-blade cuts."""
    cuts = [cut for cut in panel.blade_cuts if cut.source_kind == SOURCE_PROFILE_OUTER]
    if not cuts:
        return set()
    ids = {cut.source_profile_id for cut in cuts}
    if len(cuts) != 4 or None in ids or len(ids) != 1:
        raise ValueError(
            "Profile-blade conversion must contain exactly four cuts for one outer profile"
        )
    if {cut.source_segment_index for cut in cuts} != {0, 1, 2, 3}:
        raise ValueError("Profile-blade conversion does not own all four rectangle segments")
    return {next(iter(ids))}


def plan_blade_cuts(
    panel: PanelIR,
    targets: list[BladeTargetIR],
    machine: BladeMachineProfile,
) -> list[BladeCutIR]:
    """Plan all requested blade cuts or return no partial result."""
    machine.validate()
    machine.require_verified_adapter()

    frames = {
        frame.frame_id: frame
        for frame in panel.machining_frames
        if frame.kind == MachiningFrameKind.FICTIVE_FACE
    }
    if not frames:
        raise ValueError("Select at least one fictive face for blade cutting")

    if (
        len(targets) != len(frames)
        or {target.frame_id for target in targets} != set(frames)
        or len({target.source_face_id for target in targets}) != len(targets)
    ):
        raise ValueError(
            "Blade intent must identify every selected fictive face exactly once"
        )

    cuts: list[BladeCutIR] = []
    for target in sorted(
        targets,
        key=lambda item: frames[item.frame_id].tpa_face_number,
    ):
        cut = _plan_one(panel, frames[target.frame_id], target, machine)

        for prior in cuts:
            alignment = dot(cut.target_normal, prior.target_normal)
            if (
                abs(abs(alignment) - 1.0) < 1e-10
                and abs(
                    cut.target_offset_mm
                    - math.copysign(1.0, alignment) * prior.target_offset_mm
                )
                < 1e-7
            ):
                raise ValueError(
                    f"SIDE{cut.target_side} and SIDE{prior.target_side} request "
                    "the same blade plane; resolve duplicate selections"
                )

        cuts.append(cut)

    return cuts


def _reconstruct_finished_plane_from_tcn(
    cut: BladeCutIR,
) -> tuple[V3, float]:
    """Reconstruct the finished plane from rounded serialized A/Beta/X/Y/DN.

    Zp/Z2 intentionally do not enter this plane reconstruction: in the custom
    Busellato LAME/W95 contract they are penetration coordinates within the
    blade plane. This check therefore validates the XY/Alpha/Beta/compensation
    placement independently of depth planning.
    """
    machine = cut.machine
    x, y = map(tcn_quantized, cut.start_xy)

    if cut.mode in (BLADE_X, BLADE_Y):
        end_x, end_y = map(tcn_quantized, cut.end_xy)
        dx, dy = end_x - x, end_y - y
        distance = math.hypot(dx, dy)
        if distance < 1e-9:
            raise ValueError("Rounded BLADEX/BLADEY start/final coordinates collapse")
        travel = (dx / distance, dy / distance, 0.0)
        left_normal = (-travel[1], travel[0], 0.0)
        normal = left_normal if cut.compensation == 1 else scale(left_normal, -1.0)
        normal = normalized(normal)
        offset = dot(normal, (x, y, 0.0)) - machine.normal_offset_mm
        return normal, offset

    if cut.mode != BLADE_XY:
        raise ValueError(f"Unsupported blade mode {cut.mode}")

    alpha = math.radians(tcn_quantized(cut.alpha_degrees))
    beta = math.radians(tcn_quantized(cut.beta_degrees) / machine.beta_sign)

    # W95 convention verified on-machine:
    #   Beta=90 deg -> vertical blade plane -> horizontal plane normal
    #   Beta=0 deg  -> horizontal blade plane -> vertical plane normal
    # Therefore the horizontal normal component is sin(Beta), while the
    # vertical component is cos(Beta). The previous cos/sin reconstruction was
    # the same complementary-angle bug as the planner.
    normal = normalized(
        (
            -math.sin(alpha) * math.sin(beta),
            math.cos(alpha) * math.sin(beta),
            math.cos(beta),
        )
    )

    # Plane orientation is unoriented. Align the reconstructed normal to the
    # target outward normal so a non-zero calibrated normal_offset_mm keeps its
    # intended sign. Compensation is validated as waste-side policy separately;
    # it does not change the geometric plane angle.
    if dot(normal, cut.target_normal) < 0.0:
        normal = scale(normal, -1.0)

    # X/Y anchor the plane trace at SIDE1 Z=0. The calibrated residual normal
    # shift belongs to the machine profile, not to kerf compensation.
    offset = dot(normal, (x, y, 0.0)) - machine.normal_offset_mm
    return normal, offset


def validate_blade_cuts(panel: PanelIR) -> None:
    """Recompute both blade families and validate serialization precision."""
    cuts = panel.blade_cuts
    if not cuts:
        return

    if any(cut.machine != cuts[0].machine for cut in cuts):
        raise ValueError("Blade cuts must use the selected shared machine profile")

    machine = cuts[0].machine
    machine.require_verified_adapter()

    fictive = [cut for cut in cuts if cut.source_kind == SOURCE_FICTIVE_FACE]
    profile = [cut for cut in cuts if cut.source_kind == SOURCE_PROFILE_OUTER]
    unknown = [
        cut for cut in cuts
        if cut.source_kind not in (SOURCE_FICTIVE_FACE, SOURCE_PROFILE_OUTER)
    ]
    if unknown:
        raise ValueError("Blade plan contains an unsupported source_kind")

    rebuilt: list[BladeCutIR] = []
    if fictive:
        rebuilt.extend(plan_blade_cuts(
            panel,
            [
                BladeTargetIR(
                    cut.source_face_id,
                    cut.target_frame_id,
                    cut.body_outside_mm,
                    cut.selected_face_plane_error_mm,
                )
                for cut in fictive
            ],
            machine,
        ))
    if profile:
        rebuilt.extend(plan_rectangular_profile_blade_cuts(panel, machine))

    if cuts != rebuilt:
        raise ValueError(
            "Blade plan is stale or modified; recompute from current stock and faces/profiles"
        )

    if profile:
        profile_blade_consumed_profile_ids(panel)

    for cut in cuts:
        if cut.mode == BLADE_X and cut.end_axis_mm is None:
            raise ValueError("BLADEX cut is missing X final")
        if cut.mode == BLADE_Y and cut.end_axis_mm is None:
            raise ValueError("BLADEY cut is missing Y final")
        if cut.mode == BLADE_XY and cut.end_axis_mm is not None:
            raise ValueError("BLADEXY cut must not serialize X/Y final-axis data")

        normal, offset = _reconstruct_finished_plane_from_tcn(cut)
        plane_error = max(
            abs(dot(normal, point) - offset)
            for point in cut.stock_section
        )
        if plane_error > panel.curve_tolerance_mm:
            raise ValueError(
                f"Blade SIDE{cut.target_side}: rounded TCN placement reconstructs "
                f"the finished plane with {plane_error:.6f} mm error, exceeding "
                f"tolerance {panel.curve_tolerance_mm:.6f} mm"
            )

        # BLADEX/BLADEY fix native W95 Beta to 90.  BLADEXY serializes Beta.
        beta_q = 90.0 if cut.mode in (BLADE_X, BLADE_Y) else tcn_quantized(cut.beta_degrees)
        vertical_component = abs(math.sin(math.radians(beta_q)))
        if vertical_component < 1e-12:
            raise ValueError(
                f"Blade SIDE{cut.target_side}: rounded Beta makes W95 depth invalid"
            )

        final_z = (
            cut.z2_mm
            if cut.z2_enabled and cut.z2_mm is not None
            else cut.z_mm
        )
        final_z_q = tcn_quantized(final_z)
        vertical_reach_q = abs(final_z_q) * vertical_component

        breakthrough_vertical_q = machine.breakthrough_mm * vertical_component
        stock_vertical_reach_q = vertical_reach_q - breakthrough_vertical_q

        if cut.source_kind == SOURCE_PROFILE_OUTER:
            deepest_required_vertical = panel.thickness
        else:
            deepest_required_vertical = max(
                0.0,
                -min(point[2] for point in (
                    cut.stock_section
                    + plane_stock_section(
                        cut.target_normal,
                        cut.target_offset_mm + machine.kerf_mm,
                        cut.stock_dimensions,
                    )
                )),
            )

        depth_error = abs(stock_vertical_reach_q - deepest_required_vertical)
        if depth_error > panel.curve_tolerance_mm:
            raise ValueError(
                f"Blade SIDE{cut.target_side}: rounded Z/Beta depth projection "
                f"misses required stock depth by {depth_error:.6f} mm"
            )

        if cut.z2_enabled:
            if cut.z2_mm is None:
                raise ValueError(
                    f"Blade SIDE{cut.target_side}: Z2 enabled without second depth"
                )
            if not cut.z2_mm < cut.z_mm < 0:
                raise ValueError(
                    f"Blade SIDE{cut.target_side}: score_then_full requires "
                    "0 > Zp > Z2 (first pass shallower than second)"
                )
        elif cut.z2_mm is not None or cut.z2_feed is not None:
            raise ValueError(
                f"Blade SIDE{cut.target_side}: single pass must not serialize Z2 data"
            )
