/**
  TribuExporter experimental resolved-toolpath dump post.

  This is intentionally not a machine post.  It exposes the ordered motion
  records produced by Fusion's post engine so the Python add-in can serialize
  the proven subset to TCN.  Coordinates are millimetres in the CAM setup WCS.
*/

description = "TribuExporter resolved toolpath dump";
vendor = "TribuExporter";
vendorUrl = "https://github.com/";
legal = "Experimental geometry transfer only; not machine code.";
certificationLevel = 2;
minimumRevision = 45991;

extension = "tribupath";
setCodePage("ascii");
capabilities = CAPABILITY_MILLING | CAPABILITY_INTERMEDIATE;
unit = MM;

// Fusion's linearize() accepts a chordal tolerance in millimetres.  The API
// caller also sets builtin_tolerance to the operator-entered value.
tolerance = spatial(0.01, MM);
minimumChordLength = spatial(0, MM);
minimumCircularRadius = spatial(0, MM);
maximumCircularRadius = spatial(1000000, MM);
minimumCircularSweep = toRad(0.001);
maximumCircularSweep = toRad(360);
allowHelicalMoves = true;
allowSpiralMoves = true;

var xyzFormat = createFormat({decimals:9, trim: true});
var feedFormat = createFormat({decimals:6, trim: true});
var sectionCount = 0;

function number(value) {
  return xyzFormat.format(value);
}

function movementName(value) {
  if (value == MOVEMENT_RAPID) { return "rapid"; }
  if (value == MOVEMENT_CUTTING) { return "cutting"; }
  if (value == MOVEMENT_FINISH_CUTTING) { return "finish_cutting"; }
  if (value == MOVEMENT_PLUNGE) { return "plunge"; }
  if (value == MOVEMENT_RAMP) { return "ramp"; }
  if (value == MOVEMENT_RAMP_HELIX) { return "ramp_helix"; }
  if (value == MOVEMENT_RAMP_PROFILE) { return "ramp_profile"; }
  if (value == MOVEMENT_RAMP_ZIG_ZAG) { return "ramp_zig_zag"; }
  if (value == MOVEMENT_LEAD_IN) { return "lead_in"; }
  if (value == MOVEMENT_LEAD_OUT) { return "lead_out"; }
  if (value == MOVEMENT_LINK_DIRECT) { return "link_direct"; }
  if (value == MOVEMENT_LINK_TRANSITION) { return "link_transition"; }
  if (value == MOVEMENT_HIGH_FEED) { return "high_feed"; }
  if (value == MOVEMENT_REDUCED) { return "reduced"; }
  return "movement_" + value;
}

function writePointRecord(kind, x, y, z, feed) {
  var movementValue = getMovement();
  writeln(
    kind + "|" + number(x) + "|" + number(y) + "|" + number(z) + "|" +
    ((feed === undefined) ? "" : feedFormat.format(feed)) + "|" + movementValue +
    "|" + movementName(movementValue)
  );
}

function onOpen() {
  writeln("TRIBU_TOOLPATH_DUMP|2");
  writeln("UNITS|MM");
  var workpiece = getWorkpiece();
  writeln(
    "STOCK|" + number(workpiece.lower.x) + "|" + number(workpiece.lower.y) + "|" +
    number(workpiece.lower.z) + "|" + number(workpiece.upper.x) + "|" +
    number(workpiece.upper.y) + "|" + number(workpiece.upper.z)
  );
}

function onSection() {
  ++sectionCount;
  if (currentSection.isMultiAxis()) {
    error("TribuExporter V1 rejects simultaneous multi-axis toolpaths.");
  }
  var forward = currentSection.workPlane.forward;
  if ((Math.abs(forward.x) > 1e-7) || (Math.abs(forward.y) > 1e-7) ||
      (Math.abs(forward.z - 1) > 1e-7)) {
    error("TribuExporter V1 supports only a 3-axis setup with tool direction +Z.");
  }

  // This makes the callback coordinates use the section/setup frame, matching
  // the same convention a normal milling post uses for XYZ output.
  setRotation(currentSection.workPlane);
  var initial = getFramePosition(currentSection.getInitialPosition());
  writeln("SECTION|" + sectionCount);
  writeln("START|" + number(initial.x) + "|" + number(initial.y) + "|" + number(initial.z));
}

function onRapid(x, y, z) {
  writePointRecord("RAPID", x, y, z, undefined);
}

function onLinear(x, y, z, feed) {
  writePointRecord("LINEAR", x, y, z, feed);
}

function onCircular(clockwise, cx, cy, cz, x, y, z, feed) {
  // Retain all XY circular semantics before approximation. Python emits
  // constant-radius helices as the A01 helicoidal form documented by TpaCAD;
  // spirals stay typed until explicit tolerance-controlled linearization.
  if (getCircularPlane() == PLANE_XY) {
    var movementValue = getMovement();
    var common = (clockwise ? "1" : "0") + "|" +
      number(cx) + "|" + number(cy) + "|" + number(cz) + "|" +
      number(x) + "|" + number(y) + "|" + number(z) + "|" +
      feedFormat.format(feed) + "|" + movementValue + "|" +
      movementName(movementValue) + "|";
    if (isSpiral()) {
      writeln(
        "SPIRAL_XY|" + common + number(Math.abs(getCircularSweep())) + "|" +
        number(getCircularStartRadius()) + "|" + number(getCircularRadius()) +
        "|native"
      );
      return;
    }
    if (isHelical()) {
      writeln(
        "HELIX_XY|" + common + (isFullCircle() ? "1" : "0") + "|" +
        number(Math.abs(getCircularSweep())) + "|native"
      );
      return;
    }
    writeln(
      "ARC_XY|" + common + (isFullCircle() ? "1" : "0") +
      "|" + number(Math.abs(getCircularSweep())) + "|native"
    );
    return;
  }
  linearize(tolerance);
}

function onRapid5D(x, y, z, dx, dy, dz) {
  error("TribuExporter V1 rejects 5-axis rapid motion.");
}

function onLinear5D(x, y, z, dx, dy, dz, feed) {
  error("TribuExporter V1 rejects 5-axis linear motion.");
}

function onCycle() {
  error("TribuExporter V1 CAM export does not support canned cycles.");
}

function onCyclePoint(x, y, z) {
  error("TribuExporter V1 CAM export does not support canned-cycle points.");
}
