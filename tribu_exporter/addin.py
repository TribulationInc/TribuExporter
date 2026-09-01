"""Fusion command UI for the TribuExporter geometry bridge."""

from __future__ import annotations

import json
import logging
from pathlib import Path
import re
import tempfile
import traceback
from collections import Counter

import adsk.core
import adsk.fusion

from .fusion_extract import extract_panel_ir, make_panel_frame
from .model import (
    MachiningFrameKind, MachiningSide, ProfileZMode, profile_selection_key,
)
from .tcn import TcnGeometryWriter


COMMAND_ID = "TribuExporterV1GeometryCommand"
COMMAND_NAME = "Export TpaCAD Geometry"
COMMAND_DESCRIPTION = "Export body-derived closed profiles to geometry-only TCN"
WORKSPACE_ID = "FusionSolidEnvironment"
PANEL_ID = "SolidScriptsAddinsPanel"
LOG_PATH = Path(tempfile.gettempdir()) / "tribu_tpa_debug.log"
BUILD_ID = "2026-09-01.16-fictive-face-geometry"
ATTRIBUTE_GROUP = "TribuExporterV1"
PROFILE_SELECTION_ATTRIBUTE = "profile_export_selection"

_handlers = []


class CommandState:
    def __init__(self):
        self.panel = None
        self.profile_keys: list[str] = []
        self.preferences: dict = {}
        self.body_runtime_key = None
        self.updating = False


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
    dialog.title = "Export geometry-only TpaCAD TCN"
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


def _base_inputs_valid(inputs) -> bool:
    return (
        all(inputs.itemById(item_id).selectionCount == 1
            for item_id in ("side1", "p0", "px", "py"))
        and inputs.itemById("margin").value >= 0
        and inputs.itemById("tolerance").value > 0
        and inputs.itemById("stock_width").value >= 0
        and inputs.itemById("stock_height").value >= 0
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
    panel = extract_panel_ir(
        face, frame, margin, tolerance,
        stock_width if stock_width > 1e-5 else None,
        stock_height if stock_height > 1e-5 else None,
        logger, inclined_faces=inclined_faces,
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
        "FINAL_OUTER_CONTOUR and selected fictive-face loops are always exported."
    )
    state.panel = panel


def _report(panel, writer: TcnGeometryWriter | None = None) -> str:
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
        f"Geometry-only pre-export report — build {BUILD_ID}",
        "",
        f"Profiles to write: {len(exported_profiles)} (IR profiles: {len(panel.profiles)})",
        f"Selected/mandatory profiles before suppression: {len(writer.selected_profiles(panel))}",
        f"Serializer-only SIDE1 Z0 duplicates suppressed: {len(suppressed_pairs)}",
        f"Local profile depths by assigned face: {'; '.join(depth_summary)}",
        f"Stock: {panel.stock_width:.3f} × {panel.stock_height:.3f} × {panel.thickness:.3f} mm",
        f"Curve chordal tolerance: {panel.curve_tolerance_mm:.4f} mm",
        f"Body faces inventoried: {len(panel.face_facts)}",
        f"Fictive faces emitted: {sum(1 for frame in panel.machining_frames if frame.kind == MachiningFrameKind.FICTIVE_FACE)}",
        "",
    ]
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
                f"size={machining_frame.length_mm:.4f} × "
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
        "No setup, tool, compensation, passes, feeds, spindle data, or machine macros will be exported.",
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
            selected_keys = _selected_profile_keys(self.inputs, self.state)
            writer = TcnGeometryWriter(
                suppress_side1_z0_duplicates=(
                    self.inputs.itemById("suppress_z0_duplicates").value
                ),
                selected_profile_keys=selected_keys,
            )
            answer = ui.messageBox(
                _report(panel, writer), "TribuExporter V1",
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
            writer.write(panel, output)
            _save_preferences(
                face.body, self.inputs, self.state.profile_keys,
                selected_keys, logger,
            )
            logger.info("Wrote geometry-only TCN: %s", output)
            written_count = len(writer.profiles_for_export(panel))
            ui.messageBox(
                f"Exported {written_count} independent profiles.\n\n{output}\n\n"
                "Inspect every contour and its Z in TpaCAD before assigning technology.",
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
        "stock_width", "stock_height", "inclined_faces",
    }

    def __init__(self, inputs, state: CommandState):
        super().__init__()
        self.inputs = inputs
        self.state = state

    def notify(self, args):
        if self.state.updating or args.input.id not in self._GEOMETRY_INPUTS:
            return
        logger = get_logger()
        try:
            self.state.updating = True
            side1 = self.inputs.itemById("side1")
            if side1.selectionCount == 1:
                face = adsk.fusion.BRepFace.cast(side1.selection(0).entity)
                native = _native_body(face.body)
                runtime_key = getattr(native, "tempId", None) or id(native)
                if runtime_key != self.state.body_runtime_key:
                    self.state.body_runtime_key = runtime_key
                    self.state.panel = None
                    self.state.profile_keys = []
                    self.inputs.itemById("profile_selection").listItems.clear()
                    self.state.preferences = _load_preferences(face.body, logger)
                    _restore_settings(self.inputs, self.state.preferences)

            if not _base_inputs_valid(self.inputs):
                self.state.panel = None
                self.state.profile_keys = []
                dropdown = self.inputs.itemById("profile_selection")
                dropdown.listItems.clear()
                self.inputs.itemById("profile_status").text = (
                    "Complete SIDE#1, P0, PX, and PY to scan profiles."
                )
                return

            _, panel = _extract_from_inputs(self.inputs, logger)
            _populate_profile_choices(self.inputs, self.state, panel)
        except Exception as error:
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
            args.areInputsValid = (
                _base_inputs_valid(inputs) and self.state.panel is not None
            )
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
        workspace = ui.workspaces.itemById(WORKSPACE_ID)
        panel = workspace.toolbarPanels.itemById(PANEL_ID)
        control = panel.controls.itemById(COMMAND_ID)
        if control is None:
            control = panel.controls.addCommand(definition)
        control.isPromoted = True
        get_logger().info("TribuExporter V1 add-in started")
    except Exception:
        ui.messageBox(traceback.format_exc(), "TribuExporter V1 start failed")


def stop(context):
    ui = adsk.core.Application.get().userInterface
    try:
        workspace = ui.workspaces.itemById(WORKSPACE_ID)
        panel = workspace.toolbarPanels.itemById(PANEL_ID) if workspace else None
        control = panel.controls.itemById(COMMAND_ID) if panel else None
        if control:
            control.deleteMe()
        definition = ui.commandDefinitions.itemById(COMMAND_ID)
        if definition:
            definition.deleteMe()
        _handlers.clear()
    except Exception:
        ui.messageBox(traceback.format_exc(), "TribuExporter V1 stop failed")
