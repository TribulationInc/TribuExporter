"""Synthetic geometry/serialization checks, not a machine calibration."""
from dataclasses import asdict, replace
import json
import math
from pathlib import Path
import re
import tempfile
import unittest

from tribu_exporter.blade import (
    BladeMachineProfile, BladeTargetIR, dot, plane_stock_section,
    plan_blade_cuts, validate_blade_cuts,
)
from tribu_exporter.model import (
    MachiningFrameIR, MachiningFrameKind, PanelIR, StockAllowance,
    CurveChain2D, Line2D, PlanarProfileIR, Vec2,
)
from tribu_exporter.tcn import TcnGeometryWriter
from tests.tcn_reader import read_fictive_faces, read_tcn


def software_test_machine(**changes):
    # Tool 123 and dimensions are synthetic, not taken from a CNC tool table.
    result = BladeMachineProfile(
        'SYNTHETIC SOFTWARE TEST ONLY', 'lame.tmcr', 123, 300, 3, 100,
        'contact_point_z', 1, 89, 0, 1, 5, 'finished_surface',
        travel_angles_degrees=(90, 0, 270, 180), min_beta_degrees=-89,
    )
    return replace(result, **changes)


def blade_panel():
    frame = MachiningFrameIR(
        'side7', MachiningFrameKind.FICTIVE_FACE, 7,
        (30, 0, 0), (0, 1, 0), (-0.8, 0, -0.6), (-0.6, 0, 0.8),
        80, 30, 18,
    )
    corners = [Vec2(0, 0), Vec2(80, 0), Vec2(80, 30), Vec2(0, 30)]
    profile = PlanarProfileIR(
        CurveChain2D([Line2D(corners[i], corners[(i + 1) % 4])
                      for i in range(4)], True),
        0, machining_side=7, profile_id='bevel',
        source_face_ids=('face-7',), provenance='fictive_face_boundary',
    )
    return PanelIR(100, 80, 18, StockAllowance(), profiles=[profile],
                   machining_frames=[frame])


def targets(panel, outside=0.0):
    return [BladeTargetIR('face-' + str(f.tpa_face_number), f.frame_id, outside, 0.0)
            for f in panel.machining_frames]


class BladeTests(unittest.TestCase):
    def test_stock_section_uses_full_depth_and_stock_height(self):
        points = plane_stock_section((-0.6, 0, 0.8), -18, (100, 80, 18))
        self.assertEqual(len(points), 4)
        self.assertEqual({round(p[0], 5) for p in points}, {6, 30})
        self.assertEqual({p[1] for p in points}, {0, 80})
        self.assertEqual({p[2] for p in points}, {-18, 0})

    def test_plane_through_box_vertices_has_no_duplicate_points(self):
        points = plane_stock_section((1, -1, 0), 0, (80, 80, 18))
        self.assertEqual(len(points), 4)
        self.assertEqual(len(set(points)), 4)

    def test_left_correction_and_nominal_contact_plane(self):
        panel = blade_panel()
        cut, = plan_blade_cuts(panel, targets(panel), software_test_machine())
        self.assertEqual(cut.alpha_degrees, 90)
        self.assertAlmostEqual(cut.beta_degrees, 53.13010235415598)
        self.assertEqual(cut.compensation, 1)
        self.assertAlmostEqual(cut.z_mm, -19)
        self.assertAlmostEqual(cut.start_xy[0], 4.666666666666667)
        self.assertAlmostEqual(cut.start_xy[1], -155)
        self.assertAlmostEqual(cut.end_xy[1], 235)
        self.assertAlmostEqual(dot(cut.target_normal, (*cut.start_xy, cut.z_mm)), cut.target_offset_mm)

    def test_normal_reversal_preserves_plane_and_beta_but_changes_correction(self):
        panel = blade_panel()
        machine = software_test_machine()
        first, = plan_blade_cuts(panel, targets(panel), machine)
        frame = panel.machining_frames[0]
        panel.machining_frames[0] = replace(frame,
            outward_axis=tuple(-v for v in frame.outward_axis),
            y_axis=tuple(-v for v in frame.y_axis))
        second, = plan_blade_cuts(panel, targets(panel), machine)
        self.assertEqual(first.beta_degrees, second.beta_degrees)
        self.assertEqual(first.alpha_degrees, second.alpha_degrees)
        self.assertEqual((first.compensation, second.compensation), (1, 2))

    def test_opposite_bevel_slopes_get_opposite_signed_beta(self):
        panel = blade_panel()
        frame = panel.machining_frames[0]
        panel.machining_frames[0] = replace(frame, origin=(70, 0, 0),
            outward_axis=(0.6, 0, 0.8), y_axis=(-0.8, 0, 0.6))
        cut, = plan_blade_cuts(panel, targets(panel), software_test_machine())
        self.assertEqual(cut.compensation, 2)
        self.assertAlmostEqual(cut.beta_degrees, -53.13010235415598)

    def test_inclined_distance_reference_is_explicit_and_invertible(self):
        panel = blade_panel()
        machine = software_test_machine(z_reference='top_xy_blade_distance')
        cut, = plan_blade_cuts(panel, targets(panel), machine)
        self.assertAlmostEqual(cut.start_xy[0], 30)
        self.assertAlmostEqual(cut.z_mm, -19 / 0.6)
        panel.blade_cuts = [cut]
        panel.validate()

    def test_rotated_xy_planes_keep_cut_on_the_same_plane(self):
        for degrees in (0, 37, 90, 137, 180, 270):
            with self.subTest(degrees=degrees):
                panel = blade_panel()
                panel.finished_width = panel.finished_height = 400
                a = math.radians(degrees)
                def rotate(v):
                    return (v[0]*math.cos(a)-v[1]*math.sin(a),
                            v[0]*math.sin(a)+v[1]*math.cos(a), v[2])
                f = panel.machining_frames[0]
                panel.machining_frames[0] = replace(f, origin=(200, 200, 0),
                    x_axis=rotate(f.x_axis), y_axis=rotate(f.y_axis),
                    outward_axis=rotate(f.outward_axis))
                cut, = plan_blade_cuts(panel, targets(panel), software_test_machine(
                    travel_angles_degrees=((90 + degrees) % 360,)))
                self.assertAlmostEqual(dot(cut.target_normal, (*cut.start_xy, cut.z_mm)), cut.target_offset_mm)
                self.assertAlmostEqual(dot(cut.target_normal, (*cut.end_xy, cut.z_mm)), cut.target_offset_mm)
                panel.blade_cuts = [cut]
                validate_blade_cuts(panel)

    def test_allowance_already_in_frame_is_not_added_twice(self):
        panel = blade_panel()
        cut, = plan_blade_cuts(panel, targets(panel), software_test_machine())
        panel.allowance = StockAllowance(5, 7, 3, 9)
        f = panel.machining_frames[0]
        panel.machining_frames[0] = replace(f, origin=(35, 3, 0))
        shifted, = plan_blade_cuts(panel, targets(panel), software_test_machine())
        self.assertAlmostEqual(shifted.start_xy[0] - cut.start_xy[0], 5)
        self.assertAlmostEqual(shifted.length_mm - cut.length_mm, 12)

    def test_explicit_larger_stock_extends_the_cut(self):
        panel = blade_panel()
        cut, = plan_blade_cuts(panel, targets(panel), software_test_machine())
        panel.explicit_stock_height = 150
        longer, = plan_blade_cuts(panel, targets(panel), software_test_machine())
        self.assertAlmostEqual(longer.length_mm - cut.length_mm, 70)

    def test_whole_body_intrusion_is_rejected(self):
        panel = blade_panel()
        for outside in (0.1, -100.0, float('nan'), float('inf')):
            with self.assertRaisesRegex(ValueError, 'finished material|unproven'):
                plan_blade_cuts(panel, targets(panel, outside), software_test_machine())

    def test_insufficient_tool_reach_and_beta_limits_are_rejected(self):
        panel = blade_panel()
        for machine, message in ((software_test_machine(max_cutting_depth_mm=20), 'penetration'),
                                 (software_test_machine(max_abs_beta_degrees=45), 'Beta')):
            with self.assertRaisesRegex(ValueError, message):
                plan_blade_cuts(panel, targets(panel), machine)

    def test_missing_duplicate_or_extra_target_fails_atomically(self):
        panel = blade_panel()
        for value in ([], targets(panel)*2, [BladeTargetIR('other', 'side8', 0)]):
            with self.assertRaisesRegex(ValueError, 'every selected'):
                plan_blade_cuts(panel, value, software_test_machine())
        self.assertEqual(panel.blade_cuts, [])

    def test_no_fictive_faces_rejected(self):
        panel = blade_panel()
        panel.machining_frames.clear()
        with self.assertRaisesRegex(ValueError, 'at least one'):
            plan_blade_cuts(panel, [], software_test_machine())

    def test_duplicate_planes_rejected(self):
        panel = blade_panel()
        panel.machining_frames.append(replace(panel.machining_frames[0], frame_id='side8', tpa_face_number=8))
        with self.assertRaisesRegex(ValueError, 'same blade plane'):
            plan_blade_cuts(panel, targets(panel), software_test_machine())

    def test_changed_stock_invalidates_resolved_plan(self):
        panel = blade_panel()
        panel.blade_cuts = plan_blade_cuts(panel, targets(panel), software_test_machine())
        panel.explicit_stock_height = 100
        with self.assertRaisesRegex(ValueError, 'stale'):
            TcnGeometryWriter().render(panel)

    def test_tampered_cut_rejected_before_writing(self):
        panel = blade_panel()
        cut, = plan_blade_cuts(panel, targets(panel), software_test_machine())
        panel.blade_cuts = [replace(cut, compensation=2)]
        with self.assertRaisesRegex(ValueError, 'stale'):
            TcnGeometryWriter().render(panel)

    def test_macro_is_on_side1_and_fictive_geometry_is_unchanged(self):
        panel = blade_panel()
        writer = TcnGeometryWriter()
        before = writer.render(panel)
        panel.blade_cuts = plan_blade_cuts(panel, targets(panel), software_test_machine())
        with self.assertRaisesRegex(ValueError, 'machine export is blocked'):
            writer.render(panel)
        # Test raw field encoding without treating the assumed adapter as a
        # production program. Full render/write must remain blocked above.
        record = writer._blade(panel.blade_cuts[0], 1)
        self.assertIn('#8525=1 #8526=0 #8527=0', record)
        self.assertIn('#8516=123', record)
        self.assertNotIn('#8513=', record)
        self.assertNotIn('#8522=', record)
        panel.blade_cuts = []
        self.assertEqual(writer.render(panel), before)

    def test_explicit_feeds_are_preserved(self):
        panel = blade_panel()
        panel.blade_cuts = plan_blade_cuts(panel, targets(panel), software_test_machine(
            spindle_rpm=4500, entry_feed=2, cutting_feed=4.5))
        text = TcnGeometryWriter()._blade(panel.blade_cuts[0], 1)
        self.assertIn('#8522=4500', text)
        self.assertIn('#8524=2', text)
        self.assertIn('#8523=4.5', text)

    def test_kerf_not_added_twice_to_the_reference(self):
        panel = blade_panel()
        first, = plan_blade_cuts(panel, targets(panel), software_test_machine(kerf_mm=2))
        second, = plan_blade_cuts(panel, targets(panel), software_test_machine(kerf_mm=5))
        self.assertEqual(first.start_xy, second.start_xy)
        self.assertEqual(first.z_mm, second.z_mm)

    def test_profile_requires_explicit_conventions_and_positive_tool(self):
        for change in ({'tool_id': 0}, {'diameter_mm': 0}, {'kerf_mm': float('nan')},
                       {'z_reference': 'guess'}, {'beta_sign': 0},
                       {'compensation_reference': 'unknown'}, {'normal_offset_mm': None},
                       {'macro_path': 'lame.tmcr #8521=0'}, {'cutting_feed': -1}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                software_test_machine(**change).validate()

    def test_profile_json_round_trip_and_unknown_fields(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'blade.json'
            data = {'schema': 1, **asdict(software_test_machine())}
            path.write_text(json.dumps(data), encoding='utf-8')
            self.assertEqual(BladeMachineProfile.load(path), software_test_machine())
            data['typod_feed'] = 100
            path.write_text(json.dumps(data), encoding='utf-8')
            with self.assertRaisesRegex(ValueError, 'Unknown'):
                BladeMachineProfile.load(path)
        with self.assertRaisesRegex(ValueError, 'Choose'):
            BladeMachineProfile.load('')

    def test_selected_face_contact_is_required_separately(self):
        panel = blade_panel()
        for error in (float('inf'), -1, 0.1):
            with self.assertRaisesRegex(ValueError, 'selected face'):
                plan_blade_cuts(panel, [replace(targets(panel)[0],
                    selected_face_plane_error_mm=error)], software_test_machine())

    def test_explicit_opposite_travel_is_preserved_not_canonicalized(self):
        panel = blade_panel()
        forward, = plan_blade_cuts(panel, targets(panel), software_test_machine(
            travel_angles_degrees=(90,)))
        reverse, = plan_blade_cuts(panel, targets(panel), software_test_machine(
            travel_angles_degrees=(270,)))
        self.assertEqual((forward.alpha_degrees, reverse.alpha_degrees), (90, 270))
        self.assertEqual((forward.compensation, reverse.compensation), (1, 2))
        self.assertGreater(reverse.start_xy[1], reverse.end_xy[1])
        # Geometric equivalence is deliberately NOT a machine verification.
        for cut in (forward, reverse):
            with self.assertRaisesRegex(ValueError, 'blocked'):
                cut.machine.require_verified_adapter()

    def test_unlisted_travel_cannot_be_substituted(self):
        panel = blade_panel()
        with self.assertRaisesRegex(ValueError, 'unproven'):
            plan_blade_cuts(panel, targets(panel), software_test_machine(
                travel_angles_degrees=(0,)))

    def test_depth_observation_cannot_become_an_xy_adapter(self):
        panel = blade_panel()
        with self.assertRaisesRegex(ValueError, 'no verified XY'):
            plan_blade_cuts(panel, targets(panel), software_test_machine(
                z_reference='ppc_lame_beta_projection_observed'))

    def test_unverified_adapter_cannot_write_a_partial_file(self):
        panel = blade_panel()
        panel.blade_cuts = plan_blade_cuts(panel, targets(panel), software_test_machine())
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'existing.tcn'
            path.write_text('keep this file', encoding='ascii')
            with self.assertRaisesRegex(ValueError, 'blocked'):
                TcnGeometryWriter().write(panel, path)
            self.assertEqual(path.read_text(encoding='ascii'), 'keep this file')
