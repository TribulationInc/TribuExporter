# Blade cuts for selected fictive faces: analysis only

**Automatic executable blade export is blocked.** The checkbox, profile picker,
extraction and analysis planner are implemented. The custom PPC LAME/W95 evidence
does not establish the complete mapping between programmed XYZ, compensation and
the finished cutting plane. No profile setting can bypass this check. Existing
geometry export remains available with the checkbox off.

Select inclined faces, enable **Add Blade cut on fictive faces**, and choose a
local profile JSON. Valid analysis settings show candidate cuts and the blocked
machine-export status. Incomplete settings show an error while preserving the
checkbox and controls. Fictive frames and their profiles remain unchanged.

## Actual machine evidence

Parameter-only golden observations in `tests/fixtures/blade_observations.json`
are compared with the original private files when available. They are distinct
from synthetic geometry tests and do not imply physical calibration.

| Source | Observation | Meaning and limit |
|---|---|---|
| 010 and 011 Divisorio verticale | DS=25, Alpha=270, Beta=51.97, Z=-31.74, U=870, correction=1 | Preserve these directed machine values. The files supply no fictive finished-plane reference. |
| 001 Fianco sinistro | DH=780, Alpha=90, start Y=-180, U=1140 | End Y=960, extending 180 mm each end. With diameter 300 this is radius 150 plus clearance 30. |
| Same left-side program | Z1=-3, Z2=-28.07, second pass enabled=1 | The actual macro supports an enabled second-depth pass. |

The former `top_xy_blade_distance` analysis divides vertical depth by the
horizontal length of the normal. Under its complementary-angle convention that
is division by cos(Beta): **-25/cos(51.97 degrees) = -40.57954**. The real divider
record instead matches **-25/sin(51.97 degrees) = -31.73844**, rounded to -31.74.
The former analysis is therefore not a verified adapter for these records.

The named `ppc_lame_beta_projection_observed` contract and `ppc_observed_depth()`
encode the observed scalar relationship. Full planning under that contract
fails because XY/contact placement remains unresolved. Simply exchanging sine
and cosine would not establish the reference point. The left-side program does
contain GSIDE geometry, but the macro X differs from its top-plane intersection.
No default zero offset or label Zp resolves this discrepancy.

Alpha reversal remains unproven for the machine. Analysis considers only
explicitly listed directed angles, in preference order, without reducing Alpha
modulo 180. Tests preserve Alpha 270 and cover both directions; their geometric
equivalence is not evidence that machine reversal is safe.

The 30 mm clearance is supported for the longitudinal example. The dividers'
U equals DH=870, with no explicit extension in that field. It is not a universal
lead policy. The custom macro forwards rotated cuts to W95 and W2202, including
Z2, its enable flag and second-pass feed. Its separate editor graphics do not
receive Z2. The newer generic TPA blade macro is not substituted.

## Analysis configuration

Copy [blade-profile.template.json](blade-profile.template.json) to a private
location. Null required values deliberately prevent accidental use. A complete
profile permits analysis only. The user's existing tool files are not silently
migrated or declared verified.

| Fields | Meaning |
|---|---|
| `tool_id`, `macro_path` | Explicit positive blade ID and exact custom .tmcr path. |
| `diameter_mm`, `kerf_mm`, `max_cutting_depth_mm` | Physical saw dimensions, with usable penetration at most its radius. End Mill-labelled library fields are not interpreted automatically as blade diameter or kerf. |
| `travel_angles_degrees` | Nonempty unique directed angles in [0,360), in preference order. Opposite travel must be listed independently. |
| `min_beta_degrees`, `max_abs_beta_degrees`, `beta_sign` | Explicit signed limits and analysis sign convention; not proof of machine behavior. |
| `z_reference` | `contact_point_z` and `top_xy_blade_distance` are synthetic assumptions. `ppc_lame_beta_projection_observed` refuses full planning. |
| `normal_offset_mm`, `compensation_reference` | Explicit residual reference shift after controller correction, and intended `finished_surface` boundary. No automatic second half-kerf offset. |
| `breakthrough_mm`, `end_clearance_mm` | Nonnegative vertical breakthrough and extra travel beyond radius at each end. |
| `pass_policy` | Only `single_pass`. Two-pass/chord requests fail explicitly; machine support does not imply automatic planning support. |
| `spindle_rpm`, `entry_feed`, `cutting_feed` | Positive explicit overrides in machine units, or null for macro/tool defaults. Generic 18000 RPM / 5000 feeds are not copied automatically. |

The synthetic assumptions mean respectively: XYZ lies on the compensated
contact plane; or XY lies on its top trace and Z measures inclined penetration.
Neither is a proven custom PPC contract.

The planner checks the whole-body supporting bound and separately checks that
the selected face coincides with the plane. Both must be within tolerance; large
negative support bounds fail. It covers stock thickness and waste-side kerf
bounds. Duplicate/re-entrant planes, insufficient reach, unsupported travel and
incomplete settings fail. Profiles and cut IR are immutable and independent of
Fusion. Future two-pass planning needs explicit Z2 enable/depth, chord mode and
optional second-pass feed rather than discarding these settings.

The serializer checks current stock/frames and quantized geometry, then requires
a verified adapter before writing any file. None currently exists. Raw field
encoding is tested separately against the single-pass observations; those tests
do not claim to derive their values from a finished face.

## UI state and verification

Checkbox, picker and manual path changes have dedicated event paths. Cancel
preserves path, visibility, status and checkbox. Success loads the profile and
refreshes explicitly even when nested Fusion events are suppressed. Stock,
tolerance and fictive-face changes do not restore body preferences. Only an
actual SIDE1 body/occurrence change disarms blade intent. New commands start off;
the saved profile path can be restored.

Run `python -m unittest discover -v`. Tests cover synthetic geometry, actual
records, production blocking, existing-file preservation and UI cases A-G using
the real handlers with fake Fusion controls. Live extraction has also been
checked on the user's test body. Mocked controls do not prove live picker behavior.

To unblock production, pair a known finished-plane reference with its exact
custom macro call and tool/reference configuration. Reproduce plane, depth,
travel, correction and coverage together; complete the required TpaCAD
open/inspect/save/diff round trip and user confirmation before enabling machine
output. No machine execution was performed during this work.
