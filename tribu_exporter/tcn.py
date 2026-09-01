"""TCN serialization for profiles and explicitly enabled native holes.

Each PlanarProfileIR starts a new TPA profile with explicit XI/YI. Z is either
explicit geometric depth or deliberately omitted for setup-controlled geometry.
Profiles remain geometry-only.  Native blind holes are the one deliberate CAM
exception and are emitted as minimal W#81 point workings without tool #205.
"""

from __future__ import annotations

from pathlib import Path

from .model import (
    Arc2D, EPS_MM, Line2D, MachiningFrameKind, MachiningSide,
    HoleIR, PanelIR, PlanarProfileIR,
    ProfileZMode, TCN_DECIMAL_PLACES, chain_signature, profile_sort_key,
    fictive_frame_points_absolute, profile_selection_key, tcn_quantized,
)


def fmt(value: float) -> str:
    value = tcn_quantized(value)
    if value == 0:
        return "0"
    return f"{value:.4f}".rstrip("0").rstrip(".")


class TcnGeometryWriter:
    def __init__(self, suppress_side1_z0_duplicates: bool = False,
                 selected_profile_keys: set[str] | None = None):
        self.suppress_side1_z0_duplicates = suppress_side1_z0_duplicates
        self.selected_profile_keys = selected_profile_keys

    def selected_profiles(self, panel: PanelIR) -> list[PlanarProfileIR]:
        """Apply only explicit export intent; FINAL_OUTER is always retained."""
        profiles = sorted(panel.profiles, key=profile_sort_key)
        if self.selected_profile_keys is None:
            return profiles
        return [
            profile for profile in profiles
            if profile.provenance == "body_silhouette_outer"
            or profile.provenance == "fictive_face_boundary"
            or profile_selection_key(panel, profile) in self.selected_profile_keys
        ]

    def z0_duplicate_pairs(
            self, panel: PanelIR,
    ) -> list[tuple[PlanarProfileIR, PlanarProfileIR]]:
        """Return selected-face Z0 loops duplicated by deeper SIDE1 loops.

        This is deliberately a serialization policy. The IR is unchanged, the
        mandatory finished silhouette is ineligible, and only a complete XY
        chain match at TCN precision can suppress a selected SIDE1 inner loop.
        """
        if not self.suppress_side1_z0_duplicates:
            return []
        signature_tolerance = 10 ** -TCN_DECIMAL_PLACES
        deeper_by_signature: dict[tuple, list[PlanarProfileIR]] = {}
        candidates = self.selected_profiles(panel)
        for profile in candidates:
            if (
                profile.machining_side == MachiningSide.SIDE1
                and profile.z_mode == ProfileZMode.EXPLICIT
                and profile.z_mm < -EPS_MM
            ):
                signature = chain_signature(profile.chain, signature_tolerance)
                deeper_by_signature.setdefault(signature, []).append(profile)

        pairs = []
        for profile in candidates:
            if not (
                profile.machining_side == MachiningSide.SIDE1
                and profile.z_mode == ProfileZMode.EXPLICIT
                and abs(profile.z_mm) <= EPS_MM
                and profile.provenance == "side1_inner"
            ):
                continue
            signature = chain_signature(profile.chain, signature_tolerance)
            matches = deeper_by_signature.get(signature, ())
            if matches:
                pairs.append((profile, matches[0]))
        return pairs

    def profiles_for_export(self, panel: PanelIR) -> list[PlanarProfileIR]:
        suppressed = {id(zero) for zero, _ in self.z0_duplicate_pairs(panel)}
        return [
            profile for profile in self.selected_profiles(panel)
            if id(profile) not in suppressed
        ]

    def _line(self, segment: Line2D, z_mm: float, first: bool,
              z_mode: ProfileZMode) -> str:
        fields = ["W#2201{ ::WTl", " #8015=0"]
        if first:
            fields.extend((
                f" #8121={fmt(segment.start.x)}",
                f" #8122={fmt(segment.start.y)}",
            ))
            if z_mode == ProfileZMode.EXPLICIT:
                fields.append(f" #8123={fmt(z_mm)}")
        fields.extend((
            f" #1={fmt(segment.end.x)}",
            f" #2={fmt(segment.end.y)}",
        ))
        if z_mode == ProfileZMode.EXPLICIT:
            fields.append(f" #3={fmt(z_mm)}")
        fields.append(" }W")
        return "".join(fields)

    def _arc(self, segment: Arc2D, z_mm: float, first: bool,
             z_mode: ProfileZMode) -> str:
        i = segment.center.x - segment.start.x
        j = segment.center.y - segment.start.y
        fields = ["W#2101{ ::WTa", " #8015=0"]
        if first:
            fields.extend((
                f" #8121={fmt(segment.start.x)}",
                f" #8122={fmt(segment.start.y)}",
            ))
            if z_mode == ProfileZMode.EXPLICIT:
                fields.append(f" #8123={fmt(z_mm)}")
        fields.extend((
            f" #1={fmt(segment.end.x)}",
            f" #2={fmt(segment.end.y)}",
            f" #34={0 if segment.clockwise else 1}",
            f" #31={fmt(i)}",
            f" #32={fmt(j)}",
        ))
        if z_mode == ProfileZMode.EXPLICIT:
            fields.append(f" #3={fmt(z_mm)}")
        fields.append(" }W")
        return "".join(fields)

    def profile_lines(self, panel: PanelIR, profile: PlanarProfileIR) -> list[str]:
        result = []
        for index, source in enumerate(profile.chain.segments):
            if isinstance(source, Line2D):
                result.append(self._line(
                    source, profile.z_mm, index == 0, profile.z_mode,
                ))
            elif isinstance(source, Arc2D):
                result.append(self._arc(
                    source, profile.z_mm, index == 0, profile.z_mode,
                ))
            else:
                raise TypeError(f"Unsupported segment type: {type(source).__name__}")
        return result

    def _hole(self, hole: HoleIR) -> str:
        """Serialize one native simple blind hole in its assigned SIDE frame."""
        return "".join((
            "W#81{ ::WTp",
            " #8015=0",
            " #201=1",
            " #203=1",
            f" #1={fmt(hole.center.x)}",
            f" #2={fmt(hole.center.y)}",
            f" #3={fmt(-hole.depth_mm)}",
            f" #1002={fmt(hole.diameter_mm)}",
            " #1001=1",
            " }W",
        ))

    def render(self, panel: PanelIR) -> str:
        panel.validate()
        profiles = self.profiles_for_export(panel)
        emitted_sides = sorted(
            {int(profile.machining_side) for profile in profiles}
            | {int(hole.machining_side) for hole in panel.holes}
        )
        fictive_sides = set(side for side in emitted_sides if side >= 7)
        fictive_frames = sorted(
            (
                frame for frame in panel.machining_frames
                if frame.kind == MachiningFrameKind.FICTIVE_FACE
                and frame.tpa_face_number in fictive_sides
            ),
            key=lambda frame: frame.tpa_face_number,
        )
        output = [
            r"TPA\ALBATROS\EDICAD\02.00",
            f"$={panel.comment}",
            "::SIDE=" + ";".join(str(side) for side in emitted_sides) + ";",
            (
                f"::UNm DL={fmt(panel.stock_width)} "
                f"DH={fmt(panel.stock_height)} DS={fmt(panel.thickness)}"
            ),
        ]
        if fictive_frames:
            output.append(f"GEO{{ ::NF={len(fictive_frames)}")
            for frame in fictive_frames:
                p0, p1, p2 = fictive_frame_points_absolute(panel, frame)
                output.extend((
                    f"GSIDE#{frame.tpa_face_number}{{",
                    "#1=" + "|".join(fmt(value) for value in p0),
                    "#2=" + "|".join(fmt(value) for value in p1),
                    "#3=" + "|".join(fmt(value) for value in p2),
                    f"#Z={fmt(frame.thickness_mm)}",
                    "}GSIDE",
                ))
            output.append("}GEO")
        max_side = max([6] + emitted_sides)
        for side_number in range(1, max_side + 1):
            output.append(f"SIDE#{side_number}{{")
            for profile in profiles:
                if int(profile.machining_side) == side_number:
                    output.extend(self.profile_lines(panel, profile))
            for hole in panel.holes:
                if int(hole.machining_side) == side_number:
                    output.append(self._hole(hole))
            output.append("}SIDE")
        return "\n".join(output) + "\n"

    def write(self, panel: PanelIR, path: str | Path) -> Path:
        target = Path(path)
        target.write_text(self.render(panel), encoding="ascii")
        return target
