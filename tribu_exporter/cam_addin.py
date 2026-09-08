"""Fusion-only helpers for optional one-operation CAM export.

The UI remains in :mod:`addin`; this module is intentionally just the adapter
from Fusion's Manufacturing API to the private, temporary .tribupath dump.
"""

from __future__ import annotations

from pathlib import Path
import logging
import re
import shutil
import tempfile
import time
import traceback
import uuid

import adsk.cam
import adsk.core

from .cam_export import (
    CAM_ARC_FIT_TOLERANCE_MM, CAM_LINEARIZATION_TOLERANCE_MM,
    CAM_SIMPLIFICATION_TOLERANCE_MM, CAM_TCN_LINE_WARNING_LIMIT,
    CamExportOptions, optimize_toolpath, read_toolpath_dump,
    render_experimental_tcn, tcn_physical_line_count,
)


POST_PATH = Path(__file__).resolve().parent.parent / "posts" / "tribu_toolpath_dump.cps"
LEGACY_COMMAND_ID = "TribuExporterV1SelectedCamToolpathCommand"
LEGACY_WORKSPACE_ID = "CAMEnvironment"
LEGACY_PANEL_ID = "CAMScriptsAddinsPanel"
COMMAND_NAME = "Export Selected CAM Toolpath to TCN"
COMMAND_DESCRIPTION = "Export one resolved Fusion CAM toolpath as one TPA profile"
LOG_PATH = Path(tempfile.gettempdir()) / "tribu_tpa_debug.log"
_handlers = []


def cam_product():
    app = adsk.core.Application.get()
    cam = adsk.cam.CAM.cast(app.activeProduct)
    if cam is None and app.activeDocument is not None:
        cam = adsk.cam.CAM.cast(
            app.activeDocument.products.itemByProductType("CAMProductType")
        )
    return cam


def operation_label(operation) -> str:
    setup = getattr(getattr(operation, "parentSetup", None), "name", "Setup")
    strategy = getattr(operation, "strategy", "unknown")
    return f"{setup} / {operation.name} / {strategy}"


def operation_problem(operation, require_contour: bool = True) -> str | None:
    if operation is None:
        return "No Fusion CAM operation is selected"
    setup = getattr(operation, "parentSetup", None)
    if setup is None or setup.operationType != adsk.cam.OperationTypes.MillingOperation:
        return "Only Fusion milling operations are supported"
    if require_contour and getattr(operation, "strategy", None) not in {
            "contour", "parallel"}:
        return "V1 CAM export supports Fusion 3D Contour and Parallel operations"
    if bool(getattr(operation, "isSuppressed", False)):
        return "The selected Fusion CAM operation is suppressed"
    if not operation.hasToolpath:
        return "The selected Fusion CAM operation has no generated toolpath"
    if not operation.isToolpathValid:
        return "The selected Fusion CAM toolpath is invalid or out of date"
    if operation.hasError:
        return f"The selected Fusion CAM operation has an error: {operation.error}"
    return None


def _parameter_value(operation, name: str):
    try:
        parameter = operation.parameters.itemByName(name)
        return None if parameter is None else parameter.value.value
    except Exception:
        return None


def _parameter_expression(operation, name: str) -> str | None:
    try:
        parameter = operation.parameters.itemByName(name)
        expression = None if parameter is None else parameter.expression
        return str(expression) if expression not in (None, "") else None
    except Exception:
        return None


def operation_smoothing_info(operation) -> tuple[str, bool]:
    """Return operator-readable Fusion smoothing state and redistribute warning."""
    enabled_value = _parameter_value(operation, "smoothingFilter")
    enabled = (
        str(enabled_value).strip().lower() in {"true", "1", "yes", "on"}
        if isinstance(enabled_value, str) else bool(enabled_value)
    )
    mode_value = _parameter_value(operation, "smoothingFilterMode")
    mode = str(mode_value) if mode_value is not None else "unknown"
    tolerance = _parameter_expression(operation, "smoothingFilterTolerance")
    machining = _parameter_expression(operation, "tolerance")
    summary = (
        f"Fusion smoothing: {'on' if enabled else 'off'}, mode={mode}"
        + (f", smoothing tolerance={tolerance}" if tolerance else "")
        + (f", machining tolerance={machining}" if machining else "")
    )
    warning = enabled and mode.lower() in {
        "redistribute", "evenlyspacedpoints", "evenly spaced points",
    }
    return summary, warning


def cam_options_from_inputs(inputs, prefix: str = "cam_") -> CamExportOptions:
    options = CamExportOptions(
        post_linearization_tolerance_mm=(
            inputs.itemById(prefix + "post_tolerance").value * 10.0
        ),
        fit_arcs=bool(inputs.itemById(prefix + "fit_arcs").value),
        arc_fit_tolerance_mm=(
            inputs.itemById(prefix + "arc_tolerance").value * 10.0
        ),
        merge_exact_collinear=bool(
            inputs.itemById(prefix + "merge_collinear").value
        ),
        simplify_3d=bool(inputs.itemById(prefix + "simplify_3d").value),
        simplification_tolerance_mm=(
            inputs.itemById(prefix + "simplify_tolerance").value * 10.0
        ),
        tcn_line_warning_limit=int(
            inputs.itemById(prefix + "line_warning").value
        ),
    )
    options.validate()
    return options


def reset_cam_option_inputs(inputs, prefix: str = "cam_") -> None:
    """Restore documented conservative defaults without touching Fusion CAM."""
    # Fusion ValueCommandInput values use centimetres internally.
    inputs.itemById(prefix + "post_tolerance").value = (
        CAM_LINEARIZATION_TOLERANCE_MM / 10.0
    )
    inputs.itemById(prefix + "fit_arcs").value = True
    inputs.itemById(prefix + "arc_tolerance").value = (
        CAM_ARC_FIT_TOLERANCE_MM / 10.0
    )
    inputs.itemById(prefix + "merge_collinear").value = True
    inputs.itemById(prefix + "simplify_3d").value = False
    inputs.itemById(prefix + "simplify_tolerance").value = (
        CAM_SIMPLIFICATION_TOLERANCE_MM / 10.0
    )
    inputs.itemById(prefix + "line_warning").value = CAM_TCN_LINE_WARNING_LIMIT
    inputs.itemById(prefix + "arc_tolerance").isEnabled = True
    inputs.itemById(prefix + "simplify_tolerance").isEnabled = False
    inputs.itemById(prefix + "reset_options").value = False


def add_cam_option_inputs(inputs, prefix: str = "cam_",
                          visible: bool = True) -> list:
    created = []
    created.append(inputs.addValueInput(
        prefix + "post_tolerance", "Post linearization tolerance", "mm",
        adsk.core.ValueInput.createByString(
            f"{CAM_LINEARIZATION_TOLERANCE_MM:g} mm"
        ),
    ))
    created.append(inputs.addBoolValueInput(
        prefix + "fit_arcs", "Fit fallback XY line chains to A01",
        True, "", True,
    ))
    created.append(inputs.addValueInput(
        prefix + "arc_tolerance", "Fallback arc-fit tolerance", "mm",
        adsk.core.ValueInput.createByString(f"{CAM_ARC_FIT_TOLERANCE_MM:g} mm"),
    ))
    created.append(inputs.addBoolValueInput(
        prefix + "merge_collinear", "Merge exactly collinear motions",
        True, "", True,
    ))
    created.append(inputs.addBoolValueInput(
        prefix + "simplify_3d", "Simplify 3D line chains (approximation)",
        True, "", False,
    ))
    created.append(inputs.addValueInput(
        prefix + "simplify_tolerance", "3D simplification tolerance", "mm",
        adsk.core.ValueInput.createByString(
            f"{CAM_SIMPLIFICATION_TOLERANCE_MM:g} mm"
        ),
    ))
    created.append(inputs.addIntegerSpinnerCommandInput(
        prefix + "line_warning", "Warn above complete TCN lines",
        1, 2_000_000, 100, CAM_TCN_LINE_WARNING_LIMIT,
    ))
    reset = inputs.addBoolValueInput(
        prefix + "reset_options", "Reset CAM parameters to safe defaults",
        False, "", False,
    )
    reset.tooltip = (
        "Restore post tolerance, arc fitting, exact merging, 3D simplification "
        "and the line-warning threshold. This does not modify the Fusion toolpath."
    )
    created.append(reset)
    for item in created:
        item.isVisible = visible
    created[5].isEnabled = False
    return created


def available_cam_operations() -> list:
    cam = cam_product()
    if cam is None:
        raise ValueError(
            "This document has no Fusion Manufacture product. Create and generate "
            "a 3-axis milling operation first."
        )
    cam.checkValidity()
    result = []
    for index in range(cam.allOperations.count):
        operation = adsk.cam.Operation.cast(cam.allOperations.item(index))
        if operation is not None and operation_problem(operation) is None:
            result.append(operation)
    return result


def _set_parameter(parameters, name: str, value) -> None:
    parameter = parameters.itemByName(name)
    if parameter is None:
        raise ValueError(f"Fusion NC Program parameter is unavailable: {name}")
    parameter.value.value = value


def _local_post_configuration(cam):
    if not POST_PATH.is_file():
        raise FileNotFoundError(f"Missing Tribu toolpath post: {POST_PATH}")
    installed_name = "TribuExporterResolvedToolpath.cps"
    installed_path = Path(cam.personalPostFolder) / installed_name
    installed_path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(POST_PATH, installed_path)
    post_library = adsk.cam.CAMManager.get().libraryManager.postLibrary
    configuration = post_library.postConfigurationAtURL(
        adsk.core.URL.create("user://" + installed_name)
    )
    if configuration is None:
        raise ValueError(f"Fusion could not load Tribu toolpath post: {POST_PATH}")
    return configuration


def _post_operation(cam, operation, output_directory: Path,
                    tolerance_mm: float) -> Path:
    cam.checkValidity()
    problem = operation_problem(operation, require_contour=False)
    if problem:
        raise ValueError(problem)
    token = "tribu_" + uuid.uuid4().hex
    program_input = cam.ncPrograms.createInput()
    program_input.operations = [operation]
    parameters = program_input.parameters
    _set_parameter(parameters, "nc_program_name", token)
    _set_parameter(parameters, "nc_program_filename", token)
    _set_parameter(parameters, "nc_program_output_folder", output_directory.as_posix())
    _set_parameter(parameters, "nc_program_openInEditor", False)
    _set_parameter(parameters, "nc_program_createInBrowser", False)
    program = cam.ncPrograms.add(program_input)
    if program is None:
        raise ValueError("Fusion could not create the transient NC Program")
    program.postConfiguration = _local_post_configuration(cam)
    post_parameters = program.postParameters
    tolerance = post_parameters.itemByName("builtin_tolerance")
    if tolerance is not None:
        tolerance.value.value = tolerance_mm
    minimum_chord = post_parameters.itemByName("builtin_minimumChordLength")
    if minimum_chord is not None:
        minimum_chord.value.value = 0.0
    if not program.updatePostParameters(post_parameters):
        raise ValueError("Fusion rejected the Tribu post parameters")
    if not program.postProcess(adsk.cam.NCProgramPostProcessOptions.create()):
        raise ValueError("Fusion post processing failed")
    app = adsk.core.Application.get()
    deadline = time.monotonic() + 180.0
    while app.hasActiveJobs and time.monotonic() < deadline:
        adsk.doEvents()
        time.sleep(0.05)
    if app.hasActiveJobs:
        raise TimeoutError("Fusion post processing did not finish within 180 seconds")
    expected = output_directory / f"{token}.tribupath"
    if expected.is_file():
        return expected
    candidates = list(output_directory.glob(f"{token}.*"))
    if len(candidates) == 1:
        return candidates[0]
    raise FileNotFoundError(
        "Fusion reported success but the resolved-toolpath dump was not created"
    )


def post_operation(operation,
                   tolerance_mm: float = CAM_LINEARIZATION_TOLERANCE_MM,
                   yield_callback=None):
    cam = cam_product()
    if cam is None:
        raise ValueError("Fusion Manufacture is unavailable in the active document")
    with tempfile.TemporaryDirectory(prefix="tribu_cam_") as directory:
        dump = _post_operation(cam, operation, Path(directory), tolerance_mm)
        return read_toolpath_dump(dump, operation.name, yield_callback)


def _logger() -> logging.Logger:
    logger = logging.getLogger("tribu_tpa_exporter_v1")
    logger.setLevel(logging.DEBUG)
    expected = str(LOG_PATH).lower()
    if not any(isinstance(handler, logging.FileHandler) and
               str(Path(handler.baseFilename)).lower() == expected
               for handler in logger.handlers):
        handler = logging.FileHandler(LOG_PATH, mode="a", encoding="utf-8")
        handler.setFormatter(logging.Formatter(
            "%(asctime)s %(levelname)s %(message)s", "%Y-%m-%d %H:%M:%S",
        ))
        logger.addHandler(handler)
    logger.propagate = False
    return logger


def _selected_operation():
    cam = cam_product()
    if cam is None:
        return None
    selected = []
    for index in range(cam.allOperations.count):
        operation = adsk.cam.Operation.cast(cam.allOperations.item(index))
        if operation is not None and operation.isSelected:
            selected.append(operation)
    return selected[0] if len(selected) == 1 else None


def _safe_filename(name: str) -> str:
    cleaned = re.sub(r'[<>:"/\\|?*]+', "_", name or "TRIBU_CAM")
    return cleaned.strip().rstrip(".") or "TRIBU_CAM"


def _save_path(ui, operation_name: str) -> str | None:
    dialog = ui.createFileDialog()
    dialog.isMultiSelectEnabled = False
    dialog.title = "Export Selected Fusion CAM Toolpath"
    dialog.filter = "TpaCAD programs (*.tcn);;All files (*.*)"
    dialog.initialFilename = _safe_filename(operation_name) + "_TRIBU_CAM.tcn"
    if dialog.showSave() != adsk.core.DialogResults.DialogOK:
        return None
    return dialog.filename if dialog.filename.lower().endswith(".tcn") else dialog.filename + ".tcn"


class _ExecuteHandler(adsk.core.CommandEventHandler):
    def __init__(self, operation, inputs):
        super().__init__()
        self.operation = operation
        self.inputs = inputs

    def notify(self, args):
        ui = adsk.core.Application.get().userInterface
        logger = _logger()
        try:
            problem = operation_problem(self.operation, require_contour=False)
            if problem:
                raise ValueError(problem)
            target = _save_path(ui, self.operation.name)
            if target is None:
                return
            options = cam_options_from_inputs(self.inputs)
            started = time.monotonic()
            logger.info("CAM stage=post start operation=%s", self.operation.name)
            raw = post_operation(
                self.operation, options.post_linearization_tolerance_mm,
                adsk.doEvents,
            )
            posted = time.monotonic()
            logger.info(
                "CAM stage=post complete seconds=%.3f motions=%d",
                posted - started, len(raw.moves),
            )
            fitted = optimize_toolpath(raw, options, adsk.doEvents)
            fitted_at = time.monotonic()
            logger.info(
                "CAM stage=fit complete seconds=%.3f candidates=%d output=%d",
                fitted_at - posted, fitted.candidate_count,
                len(fitted.toolpath.moves),
            )
            rendered = render_experimental_tcn(raw, fitted, adsk.doEvents)
            complete_lines = tcn_physical_line_count(rendered)
            over_budget = complete_lines > options.tcn_line_warning_limit
            if over_budget:
                ui.messageBox(
                    f"WARNING ONLY: the complete TCN contains {complete_lines} "
                    f"lines, above your warning level of "
                    f"{options.tcn_line_warning_limit}.\n\n"
                    "Export will continue. The tolerance was not changed.",
                    "TribuExporter CAM line-count warning",
                    adsk.core.MessageBoxButtonTypes.OKButtonType,
                    adsk.core.MessageBoxIconTypes.WarningIconType,
                )
            Path(target).write_text(rendered, encoding="ascii")
            output = Path(target)
            logger.info(
                "CAM stage=write complete seconds=%.3f",
                time.monotonic() - fitted_at,
            )
            lower, upper = fitted.toolpath.tpa_bounds()
            a01_count = (
                fitted.toolpath.native_arc_count
                + fitted.toolpath.native_helix_count
                + fitted.fitted_arc_count
            )
            smoothing, smoothing_warning = operation_smoothing_info(self.operation)
            logger.info(
                "Standalone CAM export operation=%s strategy=%s raw=%d "
                "native_arcs=%d native_helices=%d native_spirals=%d "
                "fitted_A01=%d collinear_removed=%d simplified_removed=%d "
                "remaining_L01=%d output=%d tcn_lines=%d path=%s",
                self.operation.name, self.operation.strategy,
                fitted.original_motion_count, fitted.original_native_arc_count,
                fitted.original_native_helix_count,
                fitted.original_native_spiral_count,
                fitted.fitted_arc_count,
                fitted.exact_collinear_removed_count,
                fitted.simplified_removed_count,
                fitted.remaining_line_count, len(fitted.toolpath.moves),
                complete_lines, output,
            )
            smoothing_note = (
                "WARNING: use Fusion 'Fit arcs' instead of 'Evenly spaced points'.\n"
                if smoothing_warning else ""
            )
            ui.messageBox(
                "Fusion CAM toolpath exported.\n\n"
                f"Operation: {operation_label(self.operation)}\n"
                f"{smoothing}\n"
                f"{smoothing_note}"
                f"Raw motions: {fitted.original_motion_count}\n"
                f"Native Fusion XY arcs: {fitted.original_native_arc_count}\n"
                f"Native helical A01: {fitted.original_native_helix_count}\n"
                f"Retained Fusion spirals: {fitted.original_native_spiral_count} "
                f"(linearized to {fitted.spiral_linearized_segment_count} L01)\n"
                f"Fitted A01: {fitted.fitted_arc_count}\n"
                f"Exactly collinear motions removed: "
                f"{fitted.exact_collinear_removed_count}\n"
                f"3D simplified motions removed: {fitted.simplified_removed_count}; "
                f"measured max deviation: "
                f"{fitted.simplification_max_deviation_mm:.6f} mm\n"
                f"Output: {fitted.remaining_line_count} L01 + {a01_count} A01\n"
                f"Complete TCN lines: {complete_lines} "
                f"(warning level {options.tcn_line_warning_limit})\n"
                f"Bounds: X {lower.x:.4f}..{upper.x:.4f}, "
                f"Y {lower.y:.4f}..{upper.y:.4f}, Z {lower.z:.4f}..{upper.z:.4f} mm\n\n"
                "One independent profile was written. No W#89 setup or tool "
                "was added. Inspect the complete trajectory in TpaCAD.\n\n"
                f"{output}",
                "TribuExporter CAM toolpath",
            )
        except Exception:
            details = traceback.format_exc()
            logger.error("Standalone CAM export failed\n%s", details)
            ui.messageBox(
                f"Tribu CAM export failed:\n\n{details}\n\nLog:\n{LOG_PATH}",
                "TribuExporter CAM toolpath",
            )


class _ValidateHandler(adsk.core.ValidateInputsEventHandler):
    def __init__(self, operation, inputs):
        super().__init__()
        self.operation = operation
        self.inputs = inputs

    def notify(self, args):
        try:
            cam_options_from_inputs(self.inputs)
            args.areInputsValid = (
                operation_problem(self.operation, require_contour=False) is None
            )
        except Exception:
            args.areInputsValid = False


class _InputChangedHandler(adsk.core.InputChangedEventHandler):
    def __init__(self, inputs):
        super().__init__()
        self.inputs = inputs
        self.updating = False

    def notify(self, args):
        if self.updating:
            return
        try:
            self.updating = True
            if args.input.id == "cam_fit_arcs":
                self.inputs.itemById("cam_arc_tolerance").isEnabled = bool(
                    self.inputs.itemById("cam_fit_arcs").value
                )
            elif args.input.id == "cam_simplify_3d":
                self.inputs.itemById("cam_simplify_tolerance").isEnabled = bool(
                    self.inputs.itemById("cam_simplify_3d").value
                )
            elif args.input.id == "cam_reset_options":
                reset_cam_option_inputs(self.inputs)
        finally:
            self.updating = False


class _CreatedHandler(adsk.core.CommandCreatedEventHandler):
    def notify(self, args):
        operation = _selected_operation()
        inputs = args.command.commandInputs
        status = (
            f"Selected: {operation_label(operation)}"
            if operation is not None else
            "Select exactly one generated CAM operation in the Manufacture browser, "
            "then run this command again."
        )
        inputs.addTextBoxCommandInput(
            "cam_selection_status", "", status, 2, True,
        )
        if operation is not None:
            smoothing, warning = operation_smoothing_info(operation)
            inputs.addTextBoxCommandInput(
                "cam_smoothing_status", "",
                smoothing + (
                    "\nWARNING: Evenly spaced points creates more L01 records; "
                    "regenerate with Fusion Fit arcs when suitable."
                    if warning else ""
                ), 3, True,
            )
        add_cam_option_inputs(inputs)
        execute = _ExecuteHandler(operation, inputs)
        validate = _ValidateHandler(operation, inputs)
        changed = _InputChangedHandler(inputs)
        args.command.execute.add(execute)
        args.command.validateInputs.add(validate)
        args.command.inputChanged.add(changed)
        _handlers.extend((execute, validate, changed))


def run(context) -> None:
    ui = adsk.core.Application.get().userInterface
    remove_legacy_command()
    definition = ui.commandDefinitions.addButtonDefinition(
        LEGACY_COMMAND_ID, COMMAND_NAME, COMMAND_DESCRIPTION,
    )
    created = _CreatedHandler()
    definition.commandCreated.add(created)
    _handlers.append(created)
    workspace = ui.workspaces.itemById(LEGACY_WORKSPACE_ID)
    panel = workspace.toolbarPanels.itemById(LEGACY_PANEL_ID) if workspace else None
    if panel is None:
        raise ValueError("Fusion Manufacture Add-Ins panel is unavailable")
    control = panel.controls.addCommand(definition)
    control.isPromoted = True
    _logger().info("Standalone CAM toolpath command started")


def stop(context) -> None:
    remove_legacy_command()
    _handlers.clear()


def remove_legacy_command() -> None:
    """Remove the superseded separate Manufacture toolbar command."""
    ui = adsk.core.Application.get().userInterface
    workspace = ui.workspaces.itemById(LEGACY_WORKSPACE_ID)
    panel = workspace.toolbarPanels.itemById(LEGACY_PANEL_ID) if workspace else None
    control = panel.controls.itemById(LEGACY_COMMAND_ID) if panel else None
    if control:
        control.deleteMe()
    definition = ui.commandDefinitions.itemById(LEGACY_COMMAND_ID)
    if definition:
        definition.deleteMe()
