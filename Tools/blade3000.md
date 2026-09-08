# CNC tool 3000 — lama 300 GENERICA

`blade3000.cnc-data.json` is the full transcription of the CNC screenshot.
`blade3000.json` is now a complete, loadable **analysis-only** exporter profile.
It does not establish a verified custom PPC LAME/W95 machine adapter.

## Values supplied by the CNC screen

- Tool ID 3000; blade diameter 300 mm.
- D / Spessore Lama 3.2 mm, entered in the existing profile width field.
- Maximum penetration 85 mm.
- Default spindle 5500 RPM (screen limits: 3000 to 5500 RPM).
- Default cutting feed 8 m/min and entry feed 1 m/min.

LA 57.9 mm, C-axis offset 0, rotation, wear and the other screen values remain
in the separate data record. LA and wear are not applied as normal offsets.

## Explicit analysis assumptions, not measured machine settings

- `contact_point_z`: use the existing synthetic contact-plane model. The real
  PPC XYZ reference remains unresolved. This setting does not reproduce or
  claim to explain the divider's Z=-31.74 machine record.
- `beta_sign=1`, Beta range 0 to 90: analysis convention and range only.
- Directed travel preference `[270,90,180,0]`: evaluate these directions
  independently. This is not proof of opposite-travel machine equivalence or
  of tool 3000's supported physical orientations.
- Normal offset 0 and breakthrough 0: no extra shift/penetration in analysis.
  Zero is not a calibration result.
- End clearance 30 mm beyond the radius: analysis coverage policy, motivated
  by the separate 780 mm longitudinal example; not calibrated for tool 3000.
- Single pass and intended finished-surface compensation are the existing
  planner's milestone, not automatic two-pass support.

These assumptions remove missing-setting validation errors and permit candidate
geometry planning. Executable blade export remains blocked by the earlier
`require_verified_adapter()` check. Editing this JSON cannot remove that check.
The latest independent-scanning refactor has been reverted as requested; the
previous extraction/planning flow and earlier checkbox fixes are restored.
