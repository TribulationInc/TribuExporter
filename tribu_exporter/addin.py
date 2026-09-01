"""Fusion command UI for the TribuExporter geometry bridge."""

from __future__ import annotations

import logging
from pathlib import Path
import re
import tempfile
import traceback
from collections import Counter

import adsk.core
import adsk.fusion

from .fusion_extract import extract_panel_ir, make_panel_frame
from .model import ProfileZMode, profile_sort_key
from .tcn import TcnGeometryWriter


COMMAND_ID = "TribuExporterV1GeometryCommand"
COMMAND_NAME = "Export TpaCAD Geometry"
COMMAND_DESCRIPTION = "Export body-derived closed profiles to geometry-only TCN"
WORKSPACE_ID = "FusionSolidEnvironment"
PANEL_ID = "SolidScriptsAddinsPanel"
LOG_PATH = Path(tempfile.gettempdir()) / "tribu_tpa_debug.log"
BUILD_ID = "2026-09-01.12-selected-top-reference-loop"

_handlers = []


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


def _report(panel) -> str:
    depths_by_side = {}
    for profile in panel.profiles:
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
        f"Profiles: {len(panel.profiles)}",
        f"Local profile depths by assigned face: {'; '.join(depth_summary)}",
        f"Stock: {panel.stock_width:.3f} × {panel.stock_height:.3f} × {panel.thickness:.3f} mm",
        f"Curve chordal tolerance: {panel.curve_tolerance_mm:.4f} mm",
        f"Body faces inventoried: {len(panel.face_facts)}",
        "",
    ]
    for index, profile in enumerate(sorted(panel.profiles, key=profile_sort_key), 1):
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
        "No setup, tool, compensation, passes, feeds, spindle data, or machine macros will be exported.",
        "", "Continue with export?",
    ))
    return "\n".join(lines)


class ExecuteHandler(adsk.core.CommandEventHandler):
    def __init__(self, inputs):
        super().__init__()
        self.inputs = inputs

    def notify(self, args):
        ui = adsk.core.Application.get().userInterface
        logger = get_logger()
        try:
            face = adsk.fusion.BRepFace.cast(self.inputs.itemById("side1").selection(0).entity)
            p0 = adsk.fusion.BRepVertex.cast(self.inputs.itemById("p0").selection(0).entity)
            px = adsk.fusion.BRepVertex.cast(self.inputs.itemById("px").selection(0).entity)
            py = adsk.fusion.BRepVertex.cast(self.inputs.itemById("py").selection(0).entity)
            # Fusion ValueCommandInput values use internal centimetres.
            margin = self.inputs.itemById("margin").value * 10.0
            tolerance = self.inputs.itemById("tolerance").value * 10.0
            stock_width = self.inputs.itemById("stock_width").value * 10.0
            stock_height = self.inputs.itemById("stock_height").value * 10.0
            if not all((face, p0, px, py)):
                raise ValueError("Select SIDE#1 and exactly one P0, PX, and PY vertex")
            logger.info("Starting extraction body=%s margin=%.6f tolerance=%.6f",
                        getattr(face.body, "name", "<unnamed>"), margin, tolerance)
            frame = make_panel_frame(face, p0, px, py)
            panel = extract_panel_ir(
                face, frame, margin, tolerance,
                stock_width if stock_width > 1e-5 else None,
                stock_height if stock_height > 1e-5 else None,
                logger,
            )
            answer = ui.messageBox(
                _report(panel), "TribuExporter V1",
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
            TcnGeometryWriter().write(panel, output)
            logger.info("Wrote geometry-only TCN: %s", output)
            ui.messageBox(
                f"Exported {len(panel.profiles)} independent profiles.\n\n{output}\n\n"
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


class ValidateHandler(adsk.core.ValidateInputsEventHandler):
    def notify(self, args):
        try:
            inputs = args.inputs
            selections_ok = all(
                inputs.itemById(item_id).selectionCount == 1
                for item_id in ("side1", "p0", "px", "py")
            )
            args.areInputsValid = (
                selections_ok and inputs.itemById("margin").value >= 0 and
                inputs.itemById("tolerance").value > 0 and
                inputs.itemById("stock_width").value >= 0 and
                inputs.itemById("stock_height").value >= 0
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
        execute = ExecuteHandler(inputs)
        validate = ValidateHandler()
        command.execute.add(execute)
        command.validateInputs.add(validate)
        _handlers.extend((execute, validate))


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
