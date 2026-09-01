"""Geometry-only TCN serialization.

Each PlanarProfileIR starts a new TPA profile with explicit XI/YI. Z is either
explicit geometric depth or deliberately omitted for setup-controlled geometry.
No setup, tool, compensation, feed, pass, spindle, or machine macro is emitted.
"""

from __future__ import annotations

from pathlib import Path

from .model import (
    Arc2D, Line2D, PanelIR, PlanarProfileIR, ProfileZMode,
    profile_sort_key, tcn_quantized,
)


def fmt(value: float) -> str:
    value = tcn_quantized(value)
    if value == 0:
        return "0"
    return f"{value:.4f}".rstrip("0").rstrip(".")


class TcnGeometryWriter:
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

    def render(self, panel: PanelIR) -> str:
        panel.validate()
        output = [
            r"TPA\ALBATROS\EDICAD\02.00",
            f"$={panel.comment}",
            "::SIDE=1;",
            (
                f"::UNm DL={fmt(panel.stock_width)} "
                f"DH={fmt(panel.stock_height)} DS={fmt(panel.thickness)}"
            ),
        ]
        profiles = sorted(panel.profiles, key=profile_sort_key)
        for side_number in range(1, 7):
            output.append(f"SIDE#{side_number}{{")
            for profile in profiles:
                if int(profile.machining_side) == side_number:
                    output.extend(self.profile_lines(panel, profile))
            output.append("}SIDE")
        return "\n".join(output) + "\n"

    def write(self, panel: PanelIR, path: str | Path) -> Path:
        target = Path(path)
        target.write_text(self.render(panel), encoding="ascii")
        return target
