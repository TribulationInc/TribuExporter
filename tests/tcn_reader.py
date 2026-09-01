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


@dataclass
class SemanticFictiveFace:
    side: int
    p0: tuple[float, float, float]
    p1: tuple[float, float, float]
    p2: tuple[float, float, float]
    thickness: float


@dataclass
class SemanticHole:
    side: int
    values: dict[int, float]


def read_holes(text: str) -> list[SemanticHole]:
    holes = []
    active_side = None
    for line in text.splitlines():
        side_match = re.fullmatch(r"SIDE#(\d+)\{", line)
        if side_match:
            active_side = int(side_match.group(1))
        if line.startswith("W#81"):
            values = {
                int(number): float(value)
                for number, value in PARAMETER.findall(line)
            }
            holes.append(SemanticHole(active_side, values))
    return holes


def read_fictive_faces(text: str) -> list[SemanticFictiveFace]:
    faces = []
    active_side = None
    points = {}
    thickness = None
    for line in text.splitlines():
        match = re.fullmatch(r"GSIDE#(\d+)\{", line)
        if match:
            active_side = int(match.group(1))
            points = {}
            thickness = None
            continue
        if active_side is None:
            continue
        point_match = re.fullmatch(r"#([123])=([^|]+)\|([^|]+)\|([^|]+)", line)
        if point_match:
            points[int(point_match.group(1))] = tuple(
                float(point_match.group(index)) for index in (2, 3, 4)
            )
        elif line.startswith("#Z="):
            thickness = float(line[3:])
        elif line == "}GSIDE":
            if set(points) != {1, 2, 3} or thickness is None:
                raise ValueError(f"Incomplete GSIDE#{active_side} definition")
            faces.append(SemanticFictiveFace(
                active_side, points[1], points[2], points[3], thickness,
            ))
            active_side = None
    return faces


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
        if line.startswith("W#81"):
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
