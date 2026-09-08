"""TCN serialization for geometry, holes and explicit Busellato blade workings.

Each PlanarProfileIR starts a new TPA profile with explicit XI/YI. Z is either
explicit geometric depth or deliberately omitted for setup-controlled geometry.
A fully converted rectangular ``body_silhouette_outer`` is consumed by four
BLADEX/BLADEY calls and is therefore not emitted again as mill geometry.
Native blind holes are emitted as minimal W#81 point workings without tool #205.
"""

from __future__ import annotations

from pathlib import Path

from .blade import (
    BLADE_X, BLADE_Y, BLADE_XY, SOURCE_PROFILE_OUTER,
    profile_blade_consumed_profile_ids,
)
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
        consumed = profile_blade_consumed_profile_ids(panel)
        return [
            profile for profile in self.selected_profiles(panel)
            if id(profile) not in suppressed
            and (profile.profile_id or profile.provenance) not in consumed
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

    def _blade(self, cut, number: int) -> str:
        """Serialize one official custom Busellato LAME branch.

        r9=0 -> BLADEX:  r10/r11 start, r17 X final.  Native W95 Beta=90.
        r9=1 -> BLADEY:  r10/r11 start, r18 Y final.  Native W95 Beta=90.
        r9=2 -> BLADEXY: r10/r11 start, r19 Alpha, r20 U, r21 Beta.
        """
        machine = cut.machine
        machine.require_verified_adapter()
        if cut.mode not in (BLADE_X, BLADE_Y, BLADE_XY):
            raise ValueError(f"Unsupported blade mode {cut.mode}")

        label = (
            "Outer~profile~trim"
            if cut.source_kind == SOURCE_PROFILE_OUTER
            else f"Blade~cut~for~SIDE{cut.target_side}"
        )
        fields = [
            f"W#1052{{ ::WT2 WS={number} W$={label}",
            f" #8098={machine.macro_path}",
            " #6=1",
            f" #8509={cut.mode}",
            f" #8510={fmt(cut.start_xy[0])}",
            f" #8511={fmt(cut.start_xy[1])}",
            f" #8512={fmt(cut.z_mm)}",
        ]

        if cut.z2_enabled:
            if cut.z2_mm is None:
                raise ValueError(
                    f"Blade SIDE{cut.target_side}: Z2 enabled without Z2 depth"
                )
            if not cut.z2_mm < cut.z_mm < 0:
                raise ValueError(
                    f"Blade SIDE{cut.target_side}: expected 0 > Zp > Z2"
                )
            fields.append(f" #8513={fmt(cut.z2_mm)}")
        elif cut.z2_mm is not None or cut.z2_feed is not None:
            raise ValueError(
                f"Blade SIDE{cut.target_side}: Z2 data present while Z2 is disabled"
            )

        # Mode-specific geometry comes directly from the official LAME.TMCR.
        if cut.mode == BLADE_X:
            if cut.end_axis_mm is None:
                raise ValueError("BLADEX requires X final / r17")
            fields.append(f" #8517={fmt(cut.end_axis_mm)}")
        elif cut.mode == BLADE_Y:
            if cut.end_axis_mm is None:
                raise ValueError("BLADEY requires Y final / r18")
            fields.append(f" #8518={fmt(cut.end_axis_mm)}")
        else:
            if cut.end_axis_mm is not None:
                raise ValueError("BLADEXY must not carry X/Y final-axis data")
            fields.extend((
                f" #8519={fmt(cut.alpha_degrees)}",
                f" #8520={fmt(cut.length_mm)}",
                f" #8521={fmt(cut.beta_degrees)}",
            ))

        fields.extend((
            f" #8516={machine.tool_id}",
            f" #8525={cut.compensation}",
            f" #8526={1 if cut.z2_enabled else 0}",
            " #8527=0",  # chord calculation OFF
        ))

        for parameter, value in (
            (8522, machine.spindle_rpm),
            (8523, machine.cutting_feed),
            (8524, machine.entry_feed),
        ):
            if value is not None:
                fields.append(f" #{parameter}={fmt(value)}")

        if cut.z2_enabled and cut.z2_feed is not None:
            fields.append(f" #8529={fmt(cut.z2_feed)}")

        fields.append(" }W")
        return "".join(fields)

    def blade_lines(self, panel: PanelIR) -> list[str]:
        """Serialize all planned blade workings in deterministic panel order."""
        return [self._blade(cut, number) for number, cut in enumerate(panel.blade_cuts, 1)]

    def append_blades_to_tcn(self, tcn_text: str, panel: PanelIR) -> str:
        """Insert blade workings at the END of SIDE#1 in an existing TCN.

        This is used after CAM append: milling/drilling/CAM stay upstream and the
        saw trim remains the final SIDE1 operation, so the finished rectangle is
        not released before later machining.
        """
        if not panel.blade_cuts:
            return tcn_text
        panel.validate()
        for cut in panel.blade_cuts:
            cut.machine.require_verified_adapter()
        marker = "SIDE#1{"
        start = tcn_text.find(marker)
        if start < 0:
            raise ValueError("Cannot append blade workings: SIDE#1 block is missing")
        close = tcn_text.find("\n}SIDE", start + len(marker))
        if close < 0:
            raise ValueError("Cannot append blade workings: SIDE#1 closing block is missing")
        blade_text = "\n".join(self.blade_lines(panel))
        return tcn_text[:close] + "\n" + blade_text + tcn_text[close:]

    def render(self, panel: PanelIR, include_blades: bool = True) -> str:
        panel.validate()
        for cut in panel.blade_cuts:
            cut.machine.require_verified_adapter()
        profiles = self.profiles_for_export(panel)
        emitted_sides = sorted(
            {int(profile.machining_side) for profile in profiles}
            | {int(hole.machining_side) for hole in panel.holes}
            | ({1} | {cut.target_side for cut in panel.blade_cuts}
               if panel.blade_cuts else set())
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
            # Keep internal/profile geometry and native holes before saw trimming.
            # Rectangular profile blade cuts can release the finished panel from
            # surrounding stock, so all blade workings are deliberately emitted last.
            for profile in profiles:
                if int(profile.machining_side) == side_number:
                    output.extend(self.profile_lines(panel, profile))
            for hole in panel.holes:
                if int(hole.machining_side) == side_number:
                    output.append(self._hole(hole))
            if include_blades and side_number == 1:
                output.extend(self.blade_lines(panel))
            output.append("}SIDE")
        return "\n".join(output) + "\n"

    def write(self, panel: PanelIR, path: str | Path) -> Path:
        target = Path(path)
        target.write_text(self.render(panel), encoding="ascii")
        return target
