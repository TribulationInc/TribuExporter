"""Small semantic reader for TribuExporter's geometry-only TCN subset."""

from __future__ import annotations

from dataclasses import dataclass
import re


PARAMETER = re.compile(r"#(\d+)=([^\s}]+)")
DIMENSION = re.compile(r"\b(DL|DH|DS)=([^\s]+)")


@dataclass
class SemanticProfile:
    side: int
    initial: tuple[float, float, float | None]
    operations: list[tuple[str, dict[int, float]]]


def read_tcn(text: str) -> tuple[dict[str, float], list[SemanticProfile]]:
    dimensions = {}
    profiles = []
    active = None
    active_side = None
    for line in text.splitlines():
        if line.startswith("::UNm"):
            dimensions = {name: float(value) for name, value in DIMENSION.findall(line)}
        side_match = re.fullmatch(r"SIDE#(\d+)\{", line)
        if side_match:
            active_side = int(side_match.group(1))
            active = None
        if not line.startswith("W#"):
            continue
        values = {int(number): float(value) for number, value in PARAMETER.findall(line)}
        operation = "L01" if line.startswith("W#2201") else "A01"
        if 8121 in values:
            active = SemanticProfile(
                active_side, (values[8121], values[8122], values.get(8123)), [],
            )
            profiles.append(active)
        if active is None:
            raise ValueError("TCN operation appeared before an explicit profile start")
        active.operations.append((operation, values))
    return dimensions, profiles
