"""Fusion command UI for the TribuExporter geometry bridge."""

from __future__ import annotations

import json
import logging
from pathlib import Path
import re
import tempfile
import time
import traceback
from collections import Counter

import adsk.core
import adsk.fusion

from .cam_addin import (
    add_cam_option_inputs, available_cam_operations, cam_options_from_inputs,
    operation_label, operation_problem, operation_smoothing_info, post_operation,
    reset_cam_option_inputs,
)
from .cam_export import (
    PostedArcXY, append_cam_profile_to_tcn, optimize_toolpath,
    target_stock_xy_shift, tcn_physical_line_count,
)
from .fusion_extract import extract_panel_ir, make_panel_frame
from .blade import (
    BLADE_X, BLADE_Y, BLADE_XY, SOURCE_PROFILE_OUTER,
    BladeMachineProfile, plan_rectangular_profile_blade_cuts,
    validate_blade_cuts,
)
from .fusion_identity import same_contextual_entity, occurrence_path
from .model import (
    MachiningFrameKind, MachiningSide, ProfileZMode, profile_selection_key,
)
from .tcn import TcnGeometryWriter


COMMAND_ID = "TribuExporterV1GeometryCommand"
COMMAND_NAME = "Export TpaCAD Geometry"
COMMAND_DESCRIPTION = (
    "Export Fusion geometry, native holes, CAM, and Busellato blade cuts to TCN"
)
TOOLBAR_TARGETS = (
    ("FusionSolidEnvironment", "SolidScriptsAddinsPanel"),
    ("CAMEnvironment", "CAMScriptsAddinsPanel"),
)
LOG_PATH = Path(tempfile.gettempdir()) / "tribu_tpa_debug.log"
BUILD_ID = "2026-09-08.3-rectangular-profile-blade"
ATTRIBUTE_GROUP = "TribuExporterV1"
PROFILE_SELECTION_ATTRIBUTE = "profile_export_selection"

_handlers = []


class CommandState:
    def __init__(self):
        self.panel = None
        self.profile_keys: list[str] = []
        self.preferences: dict = {}
        self.body_entity = None
        self.updating = False
        self.cam_operations: list = []


def get_logger() -> logging.Logger:
    logger = logging.getLogger("tribu_tpa_exporter_v1")
    logger.setLevel(logging.DEBUG)
    expected = str(LOG_PATH).lower()
    if not any(isinstance(h, logging.FileHandler) and
               str(Path(h.baseFilename)).lower() == expected for h in logger.handlers):
        handler = logging.FileHandler(LOG_PATH, mode="a", encoding="utf-8")
        handler.setFormatter(logging.Formatter(
            "%(asctime)s %(levelname)s %(message)s", "%Y-%m-%d %H:%M:%S",
        ))
        logger.addHandler(handler)
    logger.propagate = False
    return logger


def safe_filename(name: str) -> str:
    cleaned = re.sub(r'[<>:"/\\|?*]+', "_", name or "TRIBU_EXPORT")
    return cleaned.strip().rstrip(".") or "TRIBU_EXPORT"


def _body_name(face, fallback: str) -> str:
    body = face.body
    name = getattr(body, "name", None)
    return safe_filename(name or fallback)


def _save_path(ui, suggested_name: str) -> str | None:
    dialog = ui.createFileDialog()
    dialog.isMultiSelectEnabled = False
    dialog.title = "Export TpaCAD TCN"
    dialog.filter = "TpaCAD programs (*.tcn);;All files (*.*)"
    dialog.initialFilename = suggested_name + ".tcn"
    if dialog.showSave() != adsk.core.DialogResults.DialogOK:
        return None
    return dialog.filename if dialog.filename.lower().endswith(".tcn") else dialog.filename + ".tcn"


def _native_body(body):
    return getattr(body, "nativeObject", None) or body


def _load_preferences(body, logger) -> dict:
    try:
        attribute = _native_body(body).attributes.itemByName(
            ATTRIBUTE_GROUP, PROFILE_SELECTION_ATTRIBUTE,
        )
        if attribute is None:
            return {}
        value = json.loads(attribute.value)
        return value if value.get("schema") == 1 else {}
    except Exception:
        logger.warning("Could not read saved profile choices\n%s", traceback.format_exc())
        return {}


def _save_preferences(body, inputs, known_keys: list[str],
                      selected_keys: set[str], logger) -> None:
    payload = {
        "schema": 1,
        "known_profile_keys": sorted(set(known_keys)),
        "selected_profile_keys": sorted(selected_keys),
        "settings": {
            "margin_mm": inputs.itemById("margin").value * 10.0,
            "tolerance_mm": inputs.itemById("tolerance").value * 10.0,
            "stock_width_mm": inputs.itemById("stock_width").value * 10.0,
            "stock_height_mm": inputs.itemById("stock_height").value * 10.0,
            "suppress_z0_duplicates": inputs.itemById(
                "suppress_z0_duplicates",
            ).value,
            "export_native_holes": inputs.itemById(
                "export_native_holes",
            ).value,
            "blade_profile_path": inputs.itemById("blade_profile_path").value,
        },
    }
    attribute = _native_body(body).attributes.add(
        ATTRIBUTE_GROUP, PROFILE_SELECTION_ATTRIBUTE,
        json.dumps(payload, sort_keys=True, separators=(",", ":")),
    )
    if attribute is None:
        raise ValueError("Fusion could not persist TribuExporter profile choices")
    logger.info(
        "Saved profile choices selected=%d known=%d",
        len(selected_keys), len(set(known_keys)),
    )


def _restore_settings(inputs, preferences: dict) -> None:
    settings = preferences.get("settings", {})
    # This only restores settings. The SIDE1 body transition owns disarming.
    inputs.itemById("blade_profile_path").value = settings.get("blade_profile_path", "")
    for input_id, setting_name in (
        ("margin", "margin_mm"),
        ("tolerance", "tolerance_mm"),
        ("stock_width", "stock_width_mm"),
        ("stock_height", "stock_height_mm"),
    ):
        if setting_name in settings:
            # Fusion command values are centimetres internally.
            inputs.itemById(input_id).value = float(settings[setting_name]) / 10.0
    if "suppress_z0_duplicates" in settings:
        inputs.itemById("suppress_z0_duplicates").value = bool(
            settings["suppress_z0_duplicates"],
        )
    if "export_native_holes" in settings:
        inputs.itemById("export_native_holes").value = bool(
            settings["export_native_holes"],
        )


def _base_inputs_valid(inputs) -> bool:
    return (
        all(inputs.itemById(item_id).selectionCount == 1
            for item_id in ("side1", "p0", "px", "py"))
        and inputs.itemById("margin").value >= 0
        and inputs.itemById("tolerance").value > 0
        and inputs.itemById("stock_width").value >= 0
        and inputs.itemById("stock_height").value >= 0
    )


def _fictive_blade_enabled(inputs) -> bool:
    return bool(inputs.itemById("export_blade_cuts").value)


def _profile_blade_enabled(inputs) -> bool:
    return bool(inputs.itemById("use_blade_profile_cuts").value)


def _blade_requested(inputs) -> bool:
    return _fictive_blade_enabled(inputs) or _profile_blade_enabled(inputs)


def _show_blade_inputs(inputs) -> None:
    enabled = _blade_requested(inputs)
    for name in ("blade_profile_path", "choose_blade_profile", "blade_status"):
        inputs.itemById(name).isVisible = enabled


def _blade_machine_from_inputs(inputs):
    if not _blade_requested(inputs):
        return None
    return BladeMachineProfile.load(
        inputs.itemById("blade_profile_path").value.strip().strip('"'),
    )


def _extract_from_inputs(inputs, logger):
    face = adsk.fusion.BRepFace.cast(inputs.itemById("side1").selection(0).entity)
    p0 = adsk.fusion.BRepVertex.cast(inputs.itemById("p0").selection(0).entity)
    px = adsk.fusion.BRepVertex.cast(inputs.itemById("px").selection(0).entity)
    py = adsk.fusion.BRepVertex.cast(inputs.itemById("py").selection(0).entity)
    margin = inputs.itemById("margin").value * 10.0
    tolerance = inputs.itemById("tolerance").value * 10.0
    stock_width = inputs.itemById("stock_width").value * 10.0
    stock_height = inputs.itemById("stock_height").value * 10.0
    export_native_holes = inputs.itemById("export_native_holes").value
    inclined_input = inputs.itemById("inclined_faces")
    inclined_faces = [
        inclined_input.selection(index).entity
        for index in range(inclined_input.selectionCount)
    ]
    if not all((face, p0, px, py)):
        raise ValueError("Select SIDE#1 and exactly one P0, PX, and PY vertex")
    logger.info(
        "Starting extraction body=%s margin=%.6f tolerance=%.6f",
        getattr(face.body, "name", "<unnamed>"), margin, tolerance,
    )
    frame = make_panel_frame(face, p0, px, py)
    machine = _blade_machine_from_inputs(inputs)
    panel = extract_panel_ir(
        face, frame, margin, tolerance,
        stock_width if stock_width > 1e-5 else None,
        stock_height if stock_height > 1e-5 else None,
        logger, inclined_faces=inclined_faces,
        export_native_holes=export_native_holes,
        # The extractor owns only operator-selected fictive-face BLADEXY cuts.
        blade_machine=machine if _fictive_blade_enabled(inputs) else None,
    )

    if _profile_blade_enabled(inputs):
        assert machine is not None
        profile_cuts = plan_rectangular_profile_blade_cuts(panel, machine)
        panel.blade_cuts.extend(profile_cuts)

    if panel.blade_cuts:
        validate_blade_cuts(panel)
        first = panel.blade_cuts[0]
        profile_count = sum(
            1 for cut in panel.blade_cuts
            if cut.source_kind == SOURCE_PROFILE_OUTER
        )
        fictive_count = len(panel.blade_cuts) - profile_count
        pass_text = (
            f"Zp={first.z_mm:.2f}, Z2={first.z2_mm:.2f}"
            if first.z2_enabled and first.z2_mm is not None
            else f"Zp={first.z_mm:.2f}"
        )
        inputs.itemById("blade_status").text = (
            f"READY: {len(panel.blade_cuts)} blade cut(s) "
            f"({profile_count} rectangular profile, {fictive_count} inclined); "
            f"tool {first.machine.tool_id}; {first.machine.diameter_mm:g} mm blade; "
            f"{pass_text}."
        )
    elif _blade_requested(inputs):
        inputs.itemById("blade_status").text = (
            "Blade enabled, but no executable blade cuts were generated."
        )
    return face, panel


def _profile_bounds(profile) -> tuple[float, float, float, float]:
    points = []
    for segment in profile.chain.segments:
        points.extend((segment.start, segment.end))
    return (
        min(point.x for point in points), max(point.x for point in points),
        min(point.y for point in points), max(point.y for point in points),
    )


def _profile_label(profile) -> str:
    xmin, xmax, ymin, ymax = _profile_bounds(profile)
    z_text = "unspecified" if profile.z_mode == ProfileZMode.UNSPECIFIED else f"{profile.z_mm:g}"
    return (
        f"SIDE{int(profile.machining_side)} | Z {z_text} | "
        f"{profile.containment} | {len(profile.chain.segments)} seg | "
        f"X {xmin:g}..{xmax:g}, Y {ymin:g}..{ymax:g}"
    )


def _selected_profile_keys(inputs, state: CommandState) -> set[str]:
    items = inputs.itemById("profile_selection").listItems
    return {
        key for index, key in enumerate(state.profile_keys)
        if index < items.count and items.item(index).isSelected
    }


def _cam_enabled(inputs) -> bool:
    return bool(inputs.itemById("export_cam_toolpath").value)


def _selected_cam_operation(inputs, state: CommandState):
    items = inputs.itemById("cam_toolpath").listItems
    for index in range(min(items.count, len(state.cam_operations))):
        if items.item(index).isSelected:
            return state.cam_operations[index]
    return None


def _populate_cam_choices(inputs, state: CommandState) -> None:
    dropdown = inputs.itemById("cam_toolpath")
    dropdown.listItems.clear()
    state.cam_operations = available_cam_operations()
    for index, operation in enumerate(state.cam_operations):
        dropdown.listItems.add(operation_label(operation), index == 0)
    if state.cam_operations:
        smoothing, warning = operation_smoothing_info(state.cam_operations[0])
        inputs.itemById("cam_status").text = (
            f"{len(state.cam_operations)} generated, valid 3D Contour/Parallel "
            f"operation(s) available.\n{smoothing}"
            + ("\nWARNING: regenerate with Fusion Fit arcs; redistribute emits "
               "many linear points." if warning else "")
        )
    else:
        inputs.itemById("cam_status").text = (
            "No generated, valid, non-suppressed 3D Contour or Parallel "
            "operation is available."
        )


def _populate_profile_choices(inputs, state: CommandState, panel) -> None:
    dropdown = inputs.itemById("profile_selection")
    old_known = set(state.profile_keys)
    old_choices = _selected_profile_keys(inputs, state)
    known = set(state.preferences.get("known_profile_keys", ()))
    saved = set(state.preferences.get("selected_profile_keys", ()))
    dropdown.listItems.clear()
    state.profile_keys = []
    for profile in sorted(
            (item for item in panel.profiles
             if item.provenance not in (
                 "body_silhouette_outer", "fictive_face_boundary",
             )),
            key=lambda item: (
                int(item.machining_side), -item.z_mm, item.chain.name,
            )):
        key = profile_selection_key(panel, profile)
        state.profile_keys.append(key)
        if key in old_known:
            selected = key in old_choices
        elif key in known:
            selected = key in saved
        else:
            # Main-face geometry is useful by default. Lateral geometry can
            # create unwanted/unexecutable workings and requires explicit intent.
            selected = profile.machining_side == MachiningSide.SIDE1
        dropdown.listItems.add(_profile_label(profile), selected)
    inputs.itemById("profile_status").text = (
        f"Detected {len(state.profile_keys)} optional profiles. "
        "FINAL_OUTER_CONTOUR is exported unless completely consumed by rectangular blade trim; "
        "selected fictive-face loops remain geometric inventory."
    )
    state.panel = panel


def _report(panel, writer: TcnGeometryWriter | None = None,
            cam_operation=None, cam_result=None, cam_options=None,
            complete_tcn_lines: int | None = None) -> str:
    writer = writer or TcnGeometryWriter()
    exported_profiles = writer.profiles_for_export(panel)
    suppressed_pairs = writer.z0_duplicate_pairs(panel)
    depths_by_side = {}
    for profile in exported_profiles:
        side = int(profile.machining_side)
        depths_by_side.setdefault(side, {"explicit": set(), "unspecified": False})
        if profile.z_mode == ProfileZMode.UNSPECIFIED:
            depths_by_side[side]["unspecified"] = True
        else:
            depths_by_side[side]["explicit"].add(round(profile.z_mm, 4))
    depth_summary = []
    for side, values in sorted(depths_by_side.items()):
        labels = []
        if values["unspecified"]:
            labels.append("unspecified")
        labels.extend(f"{depth:g}" for depth in sorted(
            values["explicit"], reverse=True,
        ))
        depth_summary.append(f"SIDE{side}={', '.join(labels)} mm")
    ownership_counts = Counter(item.state.value for item in panel.face_ownership)
    lines = [
        f"Pre-export report - build {BUILD_ID}",
        "",
        f"Profiles to write: {len(exported_profiles)} (IR profiles: {len(panel.profiles)})",
        f"Selected/mandatory profiles before suppression: {len(writer.selected_profiles(panel))}",
        f"Serializer-only SIDE1 Z0 duplicates suppressed: {len(suppressed_pairs)}",
        f"Local profile depths by assigned face: {'; '.join(depth_summary)}",
        f"Stock: {panel.stock_width:.3f} x {panel.stock_height:.3f} x {panel.thickness:.3f} mm",
        f"Curve chordal tolerance: {panel.curve_tolerance_mm:.4f} mm",
        f"Body faces inventoried: {len(panel.face_facts)}",
        f"Fictive faces emitted: {sum(1 for frame in panel.machining_frames if frame.kind == MachiningFrameKind.FICTIVE_FACE)}",
        f"Native simple blind holes to write: {len(panel.holes)}",
        "",
    ]
    if cam_result is not None:
        raw = cam_result.toolpath
        lower, upper = raw.tpa_bounds()
        smoothing, smoothing_warning = operation_smoothing_info(cam_operation)
        lines.extend((
            "CAM EXPORT ENABLED",
            f"Selected: {operation_label(cam_operation)}",
            smoothing,
            *( ["WARNING: Fusion redistribute/evenly-spaced smoothing is active; Fit arcs can reduce L01 count."]
               if smoothing_warning else [] ),
            "Generated and current: yes",
            f"Post linearization tolerance: {cam_options.post_linearization_tolerance_mm:.6f} mm",
            f"Fallback arc fitting: {'on' if cam_options.fit_arcs else 'off'}"
            + (f", tolerance={cam_options.arc_fit_tolerance_mm:.6f} mm"
               if cam_options.fit_arcs else ""),
            f"Exact collinear merge: {'on' if cam_options.merge_exact_collinear else 'off'}",
            f"3D simplification: {'on' if cam_options.simplify_3d else 'off'}"
            + (f", tolerance={cam_options.simplification_tolerance_mm:.6f} mm"
               if cam_options.simplify_3d else ""),
            f"Raw motions: {cam_result.original_motion_count}",
            f"Raw linear motions: {cam_result.original_linear_count}",
            f"Raw rapid motions: {cam_result.original_rapid_count}",
            f"Native Fusion XY arcs: {cam_result.original_native_arc_count}",
            f"Native Fusion XY helices -> exact A01: {cam_result.original_native_helix_count}",
            f"Native Fusion XY spirals retained then linearized: "
            f"{cam_result.original_native_spiral_count} -> "
            f"{cam_result.spiral_linearized_segment_count} L01",
            f"Arc-fit candidates checked: {cam_result.candidate_count}",
            f"Fitted A01 segments: {cam_result.fitted_arc_count}",
            f"Exactly collinear motions removed: {cam_result.exact_collinear_removed_count}",
            f"3D simplification motions removed: {cam_result.simplified_removed_count}",
            f"Measured 3D simplification deviation: "
            f"{cam_result.simplification_max_deviation_mm:.6f} mm",
            f"Remaining L01 motions: {cam_result.remaining_line_count}",
            f"Output motions: {len(raw.moves)}",
            f"Complete generated TCN lines: {complete_tcn_lines} "
            f"(warning level {cam_options.tcn_line_warning_limit})",
            *( ["WARNING ONLY: complete TCN line count exceeds the configured level. Export remains available; no tolerance was relaxed."]
               if complete_tcn_lines > cam_options.tcn_line_warning_limit else [] ),
            f"Maximum fitted radial deviation: {cam_result.maximum_residual_mm:.6f} mm",
            f"CAM bounds: X {lower.x:.4f}..{upper.x:.4f}, "
            f"Y {lower.y:.4f}..{upper.y:.4f}, Z {lower.z:.4f}..{upper.z:.4f} mm",
            "CAM is appended as one independent SIDE1 L01/A01 profile; blade cuts, when enabled, are inserted after CAM as the final SIDE1 operations.",
            "",
        ))
    if panel.blade_cuts:
        lines.extend((
            "Executable Busellato blade cuts on SIDE1:",
            f"Machine profile: {panel.blade_cuts[0].machine.name}",
        ))
        mode_names = {BLADE_X: "BLADEX", BLADE_Y: "BLADEY", BLADE_XY: "BLADEXY"}
        for cut in panel.blade_cuts:
            z2_text = (
                f", Z2={cut.z2_mm:.4f}"
                if cut.z2_enabled and cut.z2_mm is not None
                else ""
            )
            if cut.source_kind == SOURCE_PROFILE_OUTER:
                source_text = (
                    f"outer profile={cut.source_profile_id}, "
                    f"segment={cut.source_segment_index + 1}"
                )
            else:
                source_text = f"Fusion face={cut.source_face_id}"
            lines.append(
                f"- {mode_names.get(cut.mode, str(cut.mode))}, {source_text}: "
                f"tool={cut.machine.tool_id}, "
                f"start=({cut.start_xy[0]:.4f}, {cut.start_xy[1]:.4f}), "
                f"end=({cut.end_xy[0]:.4f}, {cut.end_xy[1]:.4f}), "
                f"A={cut.alpha_degrees:.4f}, Beta={cut.beta_degrees:.4f}, "
                f"Zp={cut.z_mm:.4f}{z2_text}, "
                f"Z2EN={1 if cut.z2_enabled else 0}, "
                f"correction={'Left' if cut.compensation == 1 else 'Right'}, "
                f"final penetration={cut.final_depth_mm:.4f} mm"
            )
        lines.extend((
            "Busellato LAME/W95 mapping enabled; chord calculation is Off.",
            "BLADEX/BLADEY are used only for a proven four-line XY rectangle; "
            "BLADEXY remains reserved for inclined fictive faces.",
            "Zp/Z2 breakthrough is measured along the blade-depth coordinate.",
            "Blade width is compensated into waste; finished geometry owns the target line/plane.",
            "Blade workings are emitted after profiles and native holes.",
            "",
        ))
    fictive_frames = [
        frame for frame in panel.machining_frames
        if frame.kind == MachiningFrameKind.FICTIVE_FACE
    ]
    if fictive_frames:
        lines.append("Fictive face frames (panel coordinates, top Z=0):")
        for machining_frame in sorted(
                fictive_frames, key=lambda item: item.tpa_face_number):
            origin = ", ".join(f"{value:.4f}" for value in machining_frame.origin)
            x_axis = ", ".join(f"{value:.6f}" for value in machining_frame.x_axis)
            y_axis = ", ".join(f"{value:.6f}" for value in machining_frame.y_axis)
            z_axis = ", ".join(f"{value:.6f}" for value in machining_frame.outward_axis)
            lines.append(
                f"- SIDE{machining_frame.tpa_face_number}: P0=({origin}), "
                f"X=({x_axis}), Y=({y_axis}), Z=({z_axis}), "
                f"size={machining_frame.length_mm:.4f} x "
                f"{machining_frame.height_mm:.4f} mm"
            )
        lines.append("")
    for index, profile in enumerate(exported_profiles, 1):
        role = " [MANDATORY FINISHED OUTER CONTOUR]" if (
            profile.provenance == "body_silhouette_outer"
        ) else ""
        z_text = ("unspecified (TpaCAD setup-controlled)"
                  if profile.z_mode == ProfileZMode.UNSPECIFIED
                  else f"{profile.z_mm:.4f} mm")
        source_text = (
            "synthetic whole-body projection"
            if profile.provenance == "body_silhouette_outer"
            else f"Fusion face={profile.source_face_id}, loop={profile.containment}"
        )
        lines.append(
            f"{index}. {profile.chain.name}{role}: SIDE{int(profile.machining_side)}, "
            f"local Z={z_text}, "
            f"segments={len(profile.chain.segments)}, closed=yes, "
            f"{source_text}"
        )
    if suppressed_pairs:
        lines.extend(("", "Suppressed only in generated TCN:"))
        for zero, deeper in suppressed_pairs:
            lines.append(
                f"- {zero.profile_id or zero.chain.name} at SIDE1 Z=0 matches "
                f"{deeper.profile_id or deeper.chain.name} at Z={deeper.z_mm:.4f} mm"
            )
    if ownership_counts:
        lines.append("")
        lines.append("Face ownership (classification before profiles):")
        for state, count in sorted(ownership_counts.items()):
            lines.append(f"- {state}: {count}")
    if panel.holes:
        lines.extend(("", "Native Fusion HoleFeature workings (W#81):"))
        for hole in panel.holes:
            lines.append(
                f"- {hole.hole_id}: SIDE{int(hole.machining_side)}, "
                f"X={hole.center.x:.4f}, Y={hole.center.y:.4f}, "
                f"Z={-hole.depth_mm:.4f}, diameter={hole.diameter_mm:.4f} mm, "
                f"feature={hole.source_feature_id}, "
                f"entry face={hole.source_entry_face_id}"
            )
    lines.extend(("", f"Unsupported native HoleFeatures: {len(panel.unsupported_holes)}"))
    for hole in panel.unsupported_holes:
        detail = f" ({', '.join(hole.diagnostics)})" if hole.diagnostics else ""
        lines.append(f"- {hole.source_feature_id}: {hole.reason}{detail}")
    lines.extend(("", f"Report-only / unsupported regions: {len(panel.unsupported_regions)}"))
    for region in panel.unsupported_regions:
        z_text = "unknown" if region.z_mm is None else f"{region.z_mm:.4f} mm"
        detail = f" ({', '.join(region.diagnostics)})" if region.diagnostics else ""
        source = (", ".join(region.source_face_ids)
                  if region.source_face_ids else "unknown")
        lines.append(
            f"- Fusion face={source}, candidate local depth={z_text}: "
            f"{region.reason}{detail}"
        )
    lines.extend((
        "", "Each accepted Fusion BRepFace is exported only from its own boundary loops.",
        "The selected SIDE1 outer face loop is reference-only; the whole-body silhouette owns the finished perimeter.",
        "Equal depth, coplanarity, shared edges, and connected endpoints never merge faces.",
        "SIDE1 and orthogonal lateral faces export only after exact-face directional first-hit proof.",
        "Unchecked profiles remain in the geometric inventory but are not written to TCN.",
        "Profiles remain independently started geometry; rectangular FINAL_OUTER may be fully consumed by four executable blade workings.",
        "When enabled, native simple blind holes are executable W#81 point workings; #205 tool selection is never emitted.",
        "Verify every hole's SIDE, center, negative depth, and diameter before CNC execution.",
        "", "Continue with export?",
    ))
    return "\n".join(lines)


class ExecuteHandler(adsk.core.CommandEventHandler):
    def __init__(self, inputs, state: CommandState):
        super().__init__()
        self.inputs = inputs
        self.state = state

    def notify(self, args):
        ui = adsk.core.Application.get().userInterface
        logger = get_logger()
        try:
            face, panel = _extract_from_inputs(self.inputs, logger)
            if panel.blade_cuts:
                panel.blade_cuts[0].machine.require_verified_adapter()
            selected_keys = _selected_profile_keys(self.inputs, self.state)
            writer = TcnGeometryWriter(
                suppress_side1_z0_duplicates=(
                    self.inputs.itemById("suppress_z0_duplicates").value
                ),
                selected_profile_keys=selected_keys,
            )
            cam_operation = None
            cam_result = None
            cam_options = None
            combined_preview = None
            complete_tcn_lines = None
            cam_shift = (0.0, 0.0)
            if _cam_enabled(self.inputs):
                cam_operation = _selected_cam_operation(self.inputs, self.state)
                problem = operation_problem(cam_operation)
                if problem:
                    raise ValueError(problem)
                cam_options = cam_options_from_inputs(self.inputs)
                logger.info("CAM EXPORT ENABLED: %s", operation_label(cam_operation))
                started = time.monotonic()
                logger.info("CAM stage=post start operation=%s", cam_operation.name)
                raw_cam = post_operation(
                    cam_operation, cam_options.post_linearization_tolerance_mm,
                    adsk.doEvents,
                )
                posted = time.monotonic()
                logger.info(
                    "CAM stage=post complete seconds=%.3f motions=%d",
                    posted - started, len(raw_cam.moves),
                )
                cam_result = optimize_toolpath(
                    raw_cam, cam_options, adsk.doEvents,
                )
                logger.info(
                    "CAM stage=fit complete seconds=%.3f candidates=%d output=%d",
                    time.monotonic() - posted, cam_result.candidate_count,
                    len(cam_result.toolpath.moves),
                )
                cam_shift = target_stock_xy_shift(panel, cam_result.toolpath)
                logger.info(
                    "CAM raw=%d linear=%d native_arcs=%d native_helices=%d "
                    "native_spirals=%d candidates=%d fitted_A01=%d "
                    "collinear_removed=%d simplified_removed=%d remaining_L01=%d "
                    "output=%d max_residual=%.6f simplify_deviation=%.6f "
                    "stock_shift=(%.4f, %.4f)",
                    cam_result.original_motion_count, raw_cam.linear_count,
                    raw_cam.native_arc_count, raw_cam.native_helix_count,
                    raw_cam.native_spiral_count, cam_result.candidate_count,
                    cam_result.fitted_arc_count,
                    cam_result.exact_collinear_removed_count,
                    cam_result.simplified_removed_count,
                    cam_result.remaining_line_count, len(cam_result.toolpath.moves),
                    cam_result.maximum_residual_mm,
                    cam_result.simplification_max_deviation_mm, *cam_shift,
                )
                for index, motion in enumerate(cam_result.toolpath.moves):
                    if isinstance(motion, PostedArcXY) and motion.fitted:
                        logger.debug(
                            "ARC FIT index=%d Z=%.6f center=(%.6f,%.6f) "
                            "sweep=%.6f direction=%s residual=%.6f",
                            index, motion.end.z, motion.center.x, motion.center.y,
                            motion.sweep_radians or 0.0,
                            "CW" if motion.clockwise else "CCW",
                            motion.max_residual_mm,
                        )
                # Render geometry/holes without saw release cuts, append CAM,
                # then insert all blade workings LAST in SIDE1.
                combined_preview = append_cam_profile_to_tcn(
                    writer.render(panel, include_blades=False),
                    cam_result, cam_shift, adsk.doEvents,
                )
                combined_preview = writer.append_blades_to_tcn(
                    combined_preview, panel,
                )
                complete_tcn_lines = tcn_physical_line_count(combined_preview)
            answer = ui.messageBox(
                _report(
                    panel, writer, cam_operation, cam_result, cam_options,
                    complete_tcn_lines,
                ),
                "TribuExporter V1",
                adsk.core.MessageBoxButtonTypes.YesNoButtonType,
                adsk.core.MessageBoxIconTypes.QuestionIconType,
            )
            if answer != adsk.core.DialogResults.DialogYes:
                logger.info("Operator cancelled after pre-export report")
                return
            app = adsk.core.Application.get()
            document_name = getattr(app.activeDocument, "name", "TRIBU_EXPORT")
            output = _save_path(ui, _body_name(face, document_name) + "_TPA")
            if output is None:
                return
            if cam_result is None:
                # Deliberately retain the exact pre-CAM geometry-only path.
                writer.write(panel, output)
            else:
                Path(output).write_text(combined_preview, encoding="ascii")
            _save_preferences(
                face.body, self.inputs, self.state.profile_keys,
                selected_keys, logger,
            )
            logger.info("Wrote profile/hole TCN: %s", output)
            written_count = len(writer.profiles_for_export(panel))
            cam_text = ""
            if cam_result is not None:
                cam_text = (
                    f"\nCAM operation: {operation_label(cam_operation)}\n"
                    f"CAM output: {cam_result.remaining_line_count} L01 + "
                    f"{cam_result.toolpath.native_arc_count + cam_result.toolpath.native_helix_count + cam_result.fitted_arc_count} A01.\n"
                    f"Complete TCN: {complete_tcn_lines} lines "
                    f"(warning level {cam_options.tcn_line_warning_limit}).\n"
                    "It is one independent SIDE1 profile with no W#89 setup.\n"
                )
            ui.messageBox(
                f"Exported {written_count} independent profiles and "
                f"{len(panel.holes)} native blind holes.\n"
                f"Blade cuts: {len(panel.blade_cuts)}.\n"
                f"{cam_text}\n{output}\n\n"
                "Inspect every contour, hole, SIDE, coordinate, and depth in "
                "TpaCAD before assigning technology or executing the program.",
                "TribuExporter V1",
            )
        except Exception:
            details = traceback.format_exc()
            logger.error("Export failed\n%s", details)
            ui.messageBox(
                f"TribuExporter failed:\n\n{details}\n\nDiagnostic log:\n{LOG_PATH}",
                "TribuExporter V1",
            )


class InputChangedHandler(adsk.core.InputChangedEventHandler):
    _GEOMETRY_INPUTS = {
        "side1", "p0", "px", "py", "margin", "tolerance",
        "stock_width", "stock_height", "inclined_faces", "export_native_holes",
    }

    def __init__(self, inputs, state: CommandState):
        super().__init__()
        self.inputs = inputs
        self.state = state

    def _sync_selected_body(self, logger):
        """Only a SIDE1 event may restore preferences or reset blade intent.

        Keep the contextual BRep entity itself and use native API equality plus
        occurrence, as elsewhere in the extractor. Tokens can change and Python
        wrapper id() is not a body identity. An invalid old entity is treated as
        a new body only here, never after a modal file dialog.
        """
        side1 = self.inputs.itemById("side1")
        if side1.selectionCount != 1:
            return
        face = adsk.fusion.BRepFace.cast(side1.selection(0).entity)
        body = face.body
        old = self.state.body_entity
        changed = (old is None or not getattr(old, 'isValid', True) or
                   not same_contextual_entity(old, body))
        logger.info("SIDE1 body old=%s@%s new=%s@%s restore_preferences=%s",
                    getattr(old, 'name', None), occurrence_path(old),
                    getattr(body, 'name', None), occurrence_path(body), changed)
        if not changed:
            return
        self.state.body_entity = body
        self.state.panel = None
        self.state.profile_keys = []
        self.inputs.itemById("profile_selection").listItems.clear()
        self.state.preferences = _load_preferences(body, logger)
        _restore_settings(self.inputs, self.state.preferences)
        self.inputs.itemById("export_blade_cuts").value = False
        self.inputs.itemById("use_blade_profile_cuts").value = False
        _show_blade_inputs(self.inputs)

    def _refresh_profiles(self, logger):
        """Refresh explicitly; nested input events may be suppressed by Fusion."""
        if not _base_inputs_valid(self.inputs):
            self.state.panel = None
            self.state.profile_keys = []
            self.inputs.itemById("profile_selection").listItems.clear()
            self.inputs.itemById("profile_status").text = (
                "Complete SIDE#1, P0, PX, and PY to scan profiles."
            )
            return
        _, panel = _extract_from_inputs(self.inputs, logger)
        _populate_profile_choices(self.inputs, self.state, panel)

    def notify(self, args):
        if self.state.updating:
            return
        logger = get_logger()
        logger.info(
            "Input event=%s blade_fictive=%s blade_profile_trim=%s blade_profile=%r",
            args.input.id, _fictive_blade_enabled(self.inputs),
            _profile_blade_enabled(self.inputs),
            self.inputs.itemById("blade_profile_path").value,
        )
        try:
            self.state.updating = True
            if args.input.id in ("export_blade_cuts", "use_blade_profile_cuts"):
                _show_blade_inputs(self.inputs)
                if _blade_requested(self.inputs):
                    self.inputs.itemById("blade_status").text = (
                        "Choose the configured blade machine profile. "
                        "Profile cuts currently require a four-line XY rectangle."
                    )
                else:
                    self.inputs.itemById("blade_status").text = "Blade export is off."
                self._refresh_profiles(logger)
                return
            if args.input.id == "choose_blade_profile":
                dialog = adsk.core.Application.get().userInterface.createFileDialog()
                dialog.title = "Select Busellato blade machine profile"
                dialog.filter = "Blade machine profile (*.json)"
                if dialog.showOpen() != adsk.core.DialogResults.DialogOK:
                    logger.info("Blade profile picker cancelled; intent and path unchanged")
                    return
                self.inputs.itemById("blade_profile_path").value = dialog.filename
                logger.info("Blade profile selected=%r", dialog.filename)
                _show_blade_inputs(self.inputs)
                _blade_machine_from_inputs(self.inputs)
                self._refresh_profiles(logger)
                return
            if args.input.id == "blade_profile_path":
                if _blade_requested(self.inputs):
                    _blade_machine_from_inputs(self.inputs)
                    self._refresh_profiles(logger)
                return
            if args.input.id == "export_cam_toolpath":
                enabled = _cam_enabled(self.inputs)
                dropdown = self.inputs.itemById("cam_toolpath")
                status = self.inputs.itemById("cam_status")
                dropdown.isVisible = dropdown.isEnabled = enabled
                status.isVisible = enabled
                for input_id in (
                    "cam_post_tolerance", "cam_fit_arcs", "cam_arc_tolerance",
                    "cam_merge_collinear", "cam_simplify_3d",
                    "cam_simplify_tolerance", "cam_line_warning",
                    "cam_reset_options",
                ):
                    self.inputs.itemById(input_id).isVisible = enabled
                if enabled:
                    _populate_cam_choices(self.inputs, self.state)
                else:
                    dropdown.listItems.clear()
                    self.state.cam_operations = []
                    status.text = "CAM export is off. Manufacture data was not queried."
                return
            if args.input.id == "cam_toolpath":
                operation = _selected_cam_operation(self.inputs, self.state)
                if operation is not None:
                    smoothing, warning = operation_smoothing_info(operation)
                    self.inputs.itemById("cam_status").text = (
                        smoothing + (
                            "\nWARNING: regenerate with Fusion Fit arcs; "
                            "redistribute emits many linear points."
                            if warning else ""
                        )
                    )
                return
            if args.input.id == "cam_fit_arcs":
                self.inputs.itemById("cam_arc_tolerance").isEnabled = bool(
                    self.inputs.itemById("cam_fit_arcs").value
                )
                return
            if args.input.id == "cam_simplify_3d":
                self.inputs.itemById("cam_simplify_tolerance").isEnabled = bool(
                    self.inputs.itemById("cam_simplify_3d").value
                )
                return
            if args.input.id == "cam_reset_options":
                reset_cam_option_inputs(self.inputs)
                return
            if args.input.id not in self._GEOMETRY_INPUTS:
                return
            if args.input.id == "side1":
                self._sync_selected_body(logger)
            self._refresh_profiles(logger)
        except Exception as error:
            if _blade_requested(self.inputs):
                self.inputs.itemById("blade_status").text = str(error)
            self.state.panel = None
            self.state.profile_keys = []
            self.inputs.itemById("profile_selection").listItems.clear()
            self.inputs.itemById("profile_status").text = (
                f"Profile scan failed: {error}"
            )
            logger.error("Profile scan failed\n%s", traceback.format_exc())
        finally:
            self.state.updating = False


class ValidateHandler(adsk.core.ValidateInputsEventHandler):
    def __init__(self, state: CommandState):
        super().__init__()
        self.state = state

    def notify(self, args):
        try:
            inputs = args.inputs
            valid = _base_inputs_valid(inputs) and self.state.panel is not None
            if valid and _blade_requested(inputs):
                _blade_machine_from_inputs(inputs)
                profile_cuts = [
                    cut for cut in self.state.panel.blade_cuts
                    if cut.source_kind == SOURCE_PROFILE_OUTER
                ]
                fictive_cuts = [
                    cut for cut in self.state.panel.blade_cuts
                    if cut.source_kind != SOURCE_PROFILE_OUTER
                ]
                if _fictive_blade_enabled(inputs):
                    valid = bool(fictive_cuts)
                if valid and _profile_blade_enabled(inputs):
                    valid = len(profile_cuts) == 4
                if valid and self.state.panel.blade_cuts:
                    self.state.panel.blade_cuts[0].machine.require_verified_adapter()
            if valid and _cam_enabled(inputs):
                cam_options_from_inputs(inputs)
                valid = operation_problem(
                    _selected_cam_operation(inputs, self.state),
                ) is None
            args.areInputsValid = valid
        except Exception:
            args.areInputsValid = False


class CreatedHandler(adsk.core.CommandCreatedEventHandler):
    def notify(self, args):
        command = args.command
        inputs = command.commandInputs
        side1 = inputs.addSelectionInput("side1", "SIDE#1", "Select main planar top face")
        side1.addSelectionFilter("PlanarFaces")
        side1.setSelectionLimits(1, 1)
        for input_id, label, prompt in (
            ("p0", "P0", "Select stock-orientation origin vertex"),
            ("px", "PX", "Select vertex defining +X from P0"),
            ("py", "PY", "Select vertex choosing the positive Y side"),
        ):
            selection = inputs.addSelectionInput(input_id, label, prompt)
            selection.addSelectionFilter("Vertices")
            selection.setSelectionLimits(1, 1)
        inclined = inputs.addSelectionInput(
            "inclined_faces", "Fictive faces (SIDE7+)",
            "Select zero or more planar inclined faces on this body",
        )
        inclined.addSelectionFilter("PlanarFaces")
        inclined.setSelectionLimits(0, 0)
        inputs.addBoolValueInput(
            "export_blade_cuts", "Add Blade cut on fictive faces", True, "", False,
        )
        inputs.addBoolValueInput(
            "use_blade_profile_cuts", "Use blade for profile cuts", True, "", False,
        )
        inputs.addStringValueInput("blade_profile_path", "Blade machine profile", "")
        inputs.addBoolValueInput("choose_blade_profile", "Choose blade profile...", False, "", False)
        inputs.addTextBoxCommandInput(
            "blade_status", "", "Blade export is off. Rectangular profile mode requires 4 straight X/Y sides.", 3, True,
        )
        _show_blade_inputs(inputs)
        inputs.addValueInput(
            "margin", "Stock allowance each side", "mm",
            adsk.core.ValueInput.createByString("5 mm"),
        )
        inputs.addValueInput(
            "tolerance", "Curve chordal tolerance", "mm",
            adsk.core.ValueInput.createByString("0.01 mm"),
        )
        inputs.addValueInput(
            "stock_width", "Actual stock width (0 = minimum)", "mm",
            adsk.core.ValueInput.createByString("0 mm"),
        )
        inputs.addValueInput(
            "stock_height", "Actual stock height (0 = minimum)", "mm",
            adsk.core.ValueInput.createByString("0 mm"),
        )
        inputs.addBoolValueInput(
            "suppress_z0_duplicates",
            "Suppress SIDE1 Z=0 loop when identical deeper loop exists",
            True, "", True,
        )
        inputs.addBoolValueInput(
            "export_native_holes",
            "Export native Fusion simple blind holes (W#81 CAM)",
            True, "", False,
        )
        inputs.addBoolValueInput(
            "export_cam_toolpath", "Export CAM toolpath", True, "", False,
        )
        cam_toolpath = inputs.addDropDownCommandInput(
            "cam_toolpath", "CAM toolpath",
            adsk.core.DropDownStyles.TextListDropDownStyle,
        )
        cam_toolpath.isVisible = False
        cam_toolpath.isEnabled = False
        cam_status = inputs.addTextBoxCommandInput(
            "cam_status", "", "CAM export is off. Manufacture data was not queried.",
            2, True,
        )
        cam_status.isVisible = False
        add_cam_option_inputs(inputs, visible=False)
        profile_selection = inputs.addDropDownCommandInput(
            "profile_selection", "Profiles to export",
            adsk.core.DropDownStyles.CheckBoxDropDownStyle,
        )
        profile_selection.maxVisibleItems = 20
        inputs.addTextBoxCommandInput(
            "profile_status", "",
            "Complete SIDE#1, P0, PX, and PY to scan profiles.",
            2, True,
        )
        state = CommandState()
        execute = ExecuteHandler(inputs, state)
        changed = InputChangedHandler(inputs, state)
        validate = ValidateHandler(state)
        command.execute.add(execute)
        command.inputChanged.add(changed)
        command.validateInputs.add(validate)
        _handlers.extend((execute, changed, validate))


def run(context):
    ui = adsk.core.Application.get().userInterface
    get_logger().info("Loading TribuExporter V1 build=%s context=%r", BUILD_ID, context)
    try:
        definition = ui.commandDefinitions.itemById(COMMAND_ID)
        if definition is None:
            definition = ui.commandDefinitions.addButtonDefinition(
                COMMAND_ID, COMMAND_NAME, COMMAND_DESCRIPTION,
            )
        created = CreatedHandler()
        definition.commandCreated.add(created)
        _handlers.append(created)
        installed = 0
        for workspace_id, panel_id in TOOLBAR_TARGETS:
            workspace = ui.workspaces.itemById(workspace_id)
            panel = workspace.toolbarPanels.itemById(panel_id) if workspace else None
            if panel is None:
                get_logger().warning(
                    "Toolbar panel unavailable workspace=%s panel=%s",
                    workspace_id, panel_id,
                )
                continue
            control = panel.controls.itemById(COMMAND_ID)
            if control is None:
                control = panel.controls.addCommand(definition)
            control.isPromoted = True
            installed += 1
        if installed == 0:
            raise ValueError("No supported Fusion toolbar panel is available")
        get_logger().info("TribuExporter V1 add-in started")
    except Exception:
        ui.messageBox(traceback.format_exc(), "TribuExporter V1 start failed")


def stop(context):
    ui = adsk.core.Application.get().userInterface
    try:
        for workspace_id, panel_id in TOOLBAR_TARGETS:
            workspace = ui.workspaces.itemById(workspace_id)
            panel = workspace.toolbarPanels.itemById(panel_id) if workspace else None
            control = panel.controls.itemById(COMMAND_ID) if panel else None
            if control:
                control.deleteMe()
        definition = ui.commandDefinitions.itemById(COMMAND_ID)
        if definition:
            definition.deleteMe()
        _handlers.clear()
    except Exception:
        ui.messageBox(traceback.format_exc(), "TribuExporter V1 stop failed")
