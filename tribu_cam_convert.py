"""Convert a Tribu Fusion post dump to a standalone TpaCAD TCN.

This command deliberately runs outside Fusion. Fusion remains responsible
only for generating the resolved .tribupath with the bundled post processor.
"""

from __future__ import annotations

import argparse
from pathlib import Path
import sys
from time import monotonic

from tribu_exporter.cam_export import (
    CAM_ARC_FIT_TOLERANCE_MM, CAM_LINEARIZATION_TOLERANCE_MM,
    CAM_SIMPLIFICATION_TOLERANCE_MM, CAM_TCN_LINE_WARNING_LIMIT,
    CamExportOptions,
    optimize_toolpath,
    read_toolpath_dump,
    render_experimental_tcn, tcn_physical_line_count,
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Convert one TribuExporter .tribupath into a geometry-only TCN "
            "without running Python inside Fusion."
        ),
    )
    parser.add_argument("input", type=Path, help="Fusion-posted .tribupath")
    parser.add_argument(
        "-o", "--output", type=Path,
        help="Output TCN (default: <input>_TRIBU_CAM.tcn)",
    )
    parser.add_argument(
        "--arc-tolerance", type=float, default=CAM_ARC_FIT_TOLERANCE_MM,
        help=(
            "Maximum deviation in mm for fallback line-to-arc fitting "
            f"(default: {CAM_ARC_FIT_TOLERANCE_MM:g})"
        ),
    )
    parser.add_argument(
        "--post-tolerance", type=float,
        default=CAM_LINEARIZATION_TOLERANCE_MM,
        help="Chordal tolerance in mm for post/spiral fallback linearization",
    )
    parser.add_argument(
        "--no-arc-fit", action="store_true",
        help="Disable fallback fitting of linear XY cutting chains to A01",
    )
    parser.add_argument(
        "--no-collinear-merge", action="store_true",
        help="Disable exact collinear motion merging",
    )
    parser.add_argument(
        "--simplify-3d", action="store_true",
        help="Enable tolerance-controlled 3D line-chain simplification",
    )
    parser.add_argument(
        "--simplify-tolerance", type=float,
        default=CAM_SIMPLIFICATION_TOLERANCE_MM,
        help="Maximum measured point-to-chord deviation for 3D simplification",
    )
    parser.add_argument(
        "--line-warning", type=int, default=CAM_TCN_LINE_WARNING_LIMIT,
        help="Warn when the complete generated TCN exceeds this many lines",
    )
    parser.add_argument(
        "--force", action="store_true",
        help="Replace an existing output file",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    source = args.input.resolve()
    if not source.is_file():
        raise FileNotFoundError(f"Input toolpath does not exist: {source}")
    target = (args.output or source.with_name(
        source.stem + "_TRIBU_CAM.tcn"
    )).resolve()
    if target == source:
        raise ValueError("Input and output paths must be different")
    if target.exists() and not args.force:
        raise FileExistsError(
            f"Output already exists: {target}\nUse --force to replace it."
        )

    started = monotonic()
    raw = read_toolpath_dump(source, source.stem)
    parsed = monotonic()
    print(
        f"Parsed {len(raw.moves)} motions in {parsed - started:.3f} s; "
        "fitting proven constant-Z XY arcs...",
        flush=True,
    )
    last_report = [parsed]

    def progress() -> None:
        now = monotonic()
        if now - last_report[0] >= 1.0:
            print(f"  fitting: {now - parsed:.1f} s elapsed", flush=True)
            last_report[0] = now

    options = CamExportOptions(
        post_linearization_tolerance_mm=args.post_tolerance,
        fit_arcs=not args.no_arc_fit,
        arc_fit_tolerance_mm=args.arc_tolerance,
        merge_exact_collinear=not args.no_collinear_merge,
        simplify_3d=args.simplify_3d,
        simplification_tolerance_mm=args.simplify_tolerance,
        tcn_line_warning_limit=args.line_warning,
    )
    fitted = optimize_toolpath(raw, options, progress)
    fit_complete = monotonic()
    rendered = render_experimental_tcn(raw, fitted)
    line_count = tcn_physical_line_count(rendered)
    target.write_text(rendered, encoding="ascii")
    finished = monotonic()
    warning = (
        "WARNING ONLY: line-count level exceeded; tolerances were not changed.\n"
        if line_count > args.line_warning else ""
    )
    print(
        f"Wrote {target}\n"
        f"Native Fusion A01: {fitted.original_native_arc_count}\n"
        f"Native helical A01: {fitted.original_native_helix_count}\n"
        f"Retained spirals: {fitted.original_native_spiral_count}; "
        f"linearized segments: {fitted.spiral_linearized_segment_count}\n"
        f"Fallback fitted A01: {fitted.fitted_arc_count}\n"
        f"Exactly collinear removed: {fitted.exact_collinear_removed_count}\n"
        f"3D simplified: {fitted.simplified_removed_count}; measured max "
        f"deviation: {fitted.simplification_max_deviation_mm:.6f} mm\n"
        f"Remaining L01: {fitted.remaining_line_count}\n"
        f"Complete TCN lines: {line_count} (warning level {args.line_warning})\n"
        f"{warning}"
        f"Fit: {fit_complete - parsed:.3f} s; write: "
        f"{finished - fit_complete:.3f} s; total: {finished - started:.3f} s",
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:
        print(f"Tribu conversion failed: {error}", file=sys.stderr)
        raise SystemExit(1)
