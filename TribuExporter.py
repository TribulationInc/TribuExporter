"""Thin Autodesk Fusion entry point for TribuExporter."""

from pathlib import Path
import sys

# Fusion can load an add-in without placing its directory on sys.path. Make the
# sibling package resolvable before importing it; this is intentionally the only
# bootstrap logic in the entrypoint.
_ADDIN_DIRECTORY = str(Path(__file__).resolve().parent)
if _ADDIN_DIRECTORY not in sys.path:
    sys.path.insert(0, _ADDIN_DIRECTORY)

def _report_bootstrap_failure(action: str) -> None:
    import tempfile
    import traceback

    details = traceback.format_exc()
    log_path = Path(tempfile.gettempdir()) / "tribu_tpa_debug.log"
    with log_path.open("a", encoding="utf-8") as stream:
        stream.write(f"\nV1 bootstrap {action} failed\n{details}\n")
    try:
        import adsk.core
        adsk.core.Application.get().userInterface.messageBox(
            f"TribuExporter V1 could not {action}:\n\n{details}\n\nLog:\n{log_path}",
            "TribuExporter V1",
        )
    except Exception:
        pass


def run(context):
    try:
        # Fusion keeps imported modules alive after an add-in is stopped. Reload
        # dependencies in order so a normal Stop → Run cycle uses current files.
        import importlib
        from tribu_exporter import model, tcn, fusion_extract, addin
        for module in (model, tcn, fusion_extract, addin):
            importlib.reload(module)
        addin.run(context)
    except BaseException:
        _report_bootstrap_failure("start")


def stop(context):
    try:
        from tribu_exporter import addin
        addin.stop(context)
    except BaseException:
        _report_bootstrap_failure("stop")


__all__ = ["run", "stop"]
