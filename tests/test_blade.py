"""Synthetic geometry/serialization checks, not a machine calibration."""
from dataclasses import asdict, replace
import json
import math
from pathlib import Path
import re
import tempfile
import unittest

from tribu_exporter.blade import (
    BLADE_X, BLADE_XY, BLADE_Y, BUSELLATO_Z_REFERENCE,
    SOURCE_FICTIVE_FACE, BladeMachineProfile, BladeTargetIR, dot,
    plane_stock_section, plan_blade_cuts, plan_rectangular_profile_blade_cuts,
    profile_blade_consumed_profile_ids, profile_blade_residual_profiles,
    validate_blade_cuts,
)
from tribu_exporter.model import (
    Arc2D, MachiningFrameIR, MachiningFrameKind, PanelIR, StockAllowance,
    CurveChain2D, Line2D, PlanarProfileIR, Vec2,
)
from tribu_exporter.tcn import TcnGeometryWriter
from tests.tcn_reader import read_fictive_faces, read_tcn


def software_test_machine(**changes):
    # Tool 123 and dimensions are synthetic, not taken from a CNC tool table.
    result = BladeMachineProfile(
        'SYNTHETIC SOFTWARE TEST ONLY', 'lame.tmcr', 123, 300, 3, 100,
        BUSELLATO_Z_REFERENCE, -1, 89, 0, 1, 5, 'finished_surface',
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


def outer_panel(points=None):
    points = points or [Vec2(0, 0), Vec2(100, 0), Vec2(100, 80), Vec2(0, 80)]
    chain = CurveChain2D([
        Line2D(points[index], points[(index + 1) % len(points)])
        for index in range(len(points))
    ], True, name='FINAL_OUTER_CONTOUR')
    outer = PlanarProfileIR(
        chain, 0, profile_id='body_silhouette_outer',
        provenance='body_silhouette_outer', containment='finished_body_footprint',
    )
    return PanelIR(100, 80, 18, StockAllowance(), profiles=[outer])


class BladeTests(unittest.TestCase):
    def test_bbox_squaring_is_four_native_axis_workings(self):
        panel = outer_panel()
        cuts = plan_rectangular_profile_blade_cuts(panel, software_test_machine())
        self.assertEqual([cut.mode for cut in cuts], [BLADE_X, BLADE_Y, BLADE_X, BLADE_Y])
        self.assertEqual([cut.alpha_degrees for cut in cuts], [0, 90, 180, 270])
        self.assertEqual([cut.compensation for cut in cuts], [2, 2, 2, 2])
        self.assertEqual([cut.source_segment_index for cut in cuts], [0, 1, 2, 3])
        self.assertEqual(cuts[0].start_xy, (-155, 0))
        self.assertEqual(cuts[0].end_xy, (255, 0))
        self.assertEqual(cuts[1].start_xy, (100, -155))
        self.assertEqual(cuts[1].end_xy, (100, 235))

    def test_bbox_squaring_ignores_collinear_silhouette_splits(self):
        panel = outer_panel([
            Vec2(0, 0), Vec2(40, 0), Vec2(100, 0), Vec2(100, 30),
            Vec2(100, 80), Vec2(55, 80), Vec2(0, 80), Vec2(0, 25),
        ])
        panel.blade_cuts = plan_rectangular_profile_blade_cuts(panel, software_test_machine())
        self.assertEqual(profile_blade_consumed_profile_ids(panel), {'body_silhouette_outer'})

    def test_uncovered_chamfer_becomes_residual_until_fictive_cut_covers_trace(self):
        panel = outer_panel([
            Vec2(0, 0), Vec2(100, 0), Vec2(100, 60), Vec2(80, 80), Vec2(0, 80),
        ])
        squaring = plan_rectangular_profile_blade_cuts(panel, software_test_machine())
        panel.blade_cuts = squaring
        self.assertEqual(profile_blade_consumed_profile_ids(panel), set())
        residual, = profile_blade_residual_profiles(panel)
        self.assertFalse(residual.chain.closed)
        self.assertEqual(residual.chain.segments, [panel.profiles[0].chain.segments[2]])
        # The chamfer is x+y=160 on SIDE1. Only its target-plane trace matters
        # for the complete-perimeter coverage proof.
        witness = replace(squaring[0], mode=BLADE_XY,
            source_kind=SOURCE_FICTIVE_FACE, source_profile_id=None,
            source_segment_index=None, end_axis_mm=None,
            target_normal=(0.6, 0.6, math.sqrt(0.28)), target_offset_mm=96)
        panel.blade_cuts = squaring + [witness]
        self.assertEqual(profile_blade_consumed_profile_ids(panel), {'body_silhouette_outer'})

    def test_curved_outer_perimeter_is_retained_as_residual(self):
        panel = outer_panel()
        panel.profiles[0].chain.segments[0] = Arc2D(
            Vec2(0, 0), Vec2(100, 0), Vec2(50, 0), False)
        panel.blade_cuts = plan_rectangular_profile_blade_cuts(panel, software_test_machine())
        self.assertEqual(len(panel.blade_cuts), 3)
        residual, = profile_blade_residual_profiles(panel)
        self.assertEqual(residual.chain.segments, [panel.profiles[0].chain.segments[0]])
        rendered = TcnGeometryWriter().render(panel)
        self.assertIn('W#2101', rendered)

    def test_only_bbox_sides_present_in_outline_receive_blades(self):
        panel = outer_panel([
            Vec2(0, 0), Vec2(100, 0), Vec2(80, 20), Vec2(80, 60),
            Vec2(100, 80), Vec2(0, 80),
        ])
        panel.blade_cuts = plan_rectangular_profile_blade_cuts(
            panel, software_test_machine())
        self.assertEqual(
            [cut.source_segment_index for cut in panel.blade_cuts], [0, 2, 3])
        residuals = profile_blade_residual_profiles(panel)
        self.assertEqual([len(item.chain.segments) for item in residuals], [3])

    def test_outline_without_bbox_aligned_lines_keeps_original_profile(self):
        panel = outer_panel([
            Vec2(50, 0), Vec2(100, 40), Vec2(50, 80), Vec2(0, 40),
        ])
        panel.blade_cuts = plan_rectangular_profile_blade_cuts(
            panel, software_test_machine())
        self.assertEqual(panel.blade_cuts, [])
        self.assertEqual(profile_blade_residual_profiles(panel), panel.profiles)
        rendered = TcnGeometryWriter().render(panel)
        self.assertNotIn('W#105', rendered)
        self.assertEqual(rendered.count('#8121='), 1)

    def test_disconnected_uncovered_runs_start_independent_open_profiles(self):
        panel = outer_panel([
            Vec2(0, 0), Vec2(100, 0), Vec2(90, 10), Vec2(100, 20),
            Vec2(100, 60), Vec2(90, 70), Vec2(100, 80), Vec2(0, 80),
        ])
        panel.blade_cuts = plan_rectangular_profile_blade_cuts(
            panel, software_test_machine())
        residuals = profile_blade_residual_profiles(panel)
        self.assertEqual([len(item.chain.segments) for item in residuals], [2, 2])
        rendered = TcnGeometryWriter().render(panel)
        starts = sum(line.count('#8121=') for line in rendered.splitlines()
                     if line.startswith('W#2201'))
        self.assertEqual(starts, 2)

    def test_serializer_orders_bbox_before_fictive_inside_blade_phase(self):
        panel = outer_panel()
        bbox = plan_rectangular_profile_blade_cuts(panel, software_test_machine())
        fictive = replace(bbox[0], mode=BLADE_XY,
            source_kind=SOURCE_FICTIVE_FACE, source_profile_id=None,
            source_segment_index=None, end_axis_mm=None,
            alpha_degrees=37, beta_degrees=45, length_mm=200)
        panel.blade_cuts = [fictive] + bbox
        lines = TcnGeometryWriter().blade_lines(panel)
        self.assertEqual([parameters.group(1) for line in lines
                          if (parameters := re.search(r'#8509=(\d)', line))],
                         ['0', '1', '0', '1', '2'])

    def test_combined_program_keeps_internal_hole_and_cam_before_all_blades(self):
        panel = blade_panel()
        panel.profiles.insert(0, outer_panel().profiles[0])
        machine = software_test_machine()
        bbox = plan_rectangular_profile_blade_cuts(panel, machine)
        fictive = plan_blade_cuts(panel, targets(panel), machine)
        panel.blade_cuts = bbox + fictive
        writer = TcnGeometryWriter()
        base = writer.render(panel, include_blades=False)
        close = base.index('\n}SIDE', base.index('SIDE#1{'))
        prior_work = (
            '\nW#2201{ ::WTl W$=internal_geometry }W'
            '\nW#81{ ::WTp W$=native_hole }W'
            '\nW#2201{ ::WTl W$=fusion_cam }W'
        )
        combined = writer.append_blades_to_tcn(
            base[:close] + prior_work + base[close:], panel)
        first_blade = min(
            index for token in ('W#1050', 'W#1051', 'W#1052')
            if (index := combined.find(token)) >= 0
        )
        self.assertLess(combined.index('W$=internal_geometry'), first_blade)
        self.assertLess(combined.index('W$=native_hole'), first_blade)
        self.assertLess(combined.index('W$=fusion_cam'), first_blade)
        modes = re.findall(r'W#105[012][^\n]*#8509=(\d)', combined)
        self.assertEqual(modes, ['0', '1', '0', '1', '2'])

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

    def test_left_correction_complementary_beta_and_top_trace(self):
        panel = blade_panel()
        cut, = plan_blade_cuts(panel, targets(panel), software_test_machine())
        self.assertEqual(cut.alpha_degrees, 90)
        self.assertAlmostEqual(cut.beta_degrees, -36.86989764584402)
        self.assertEqual(cut.compensation, 1)
        self.assertAlmostEqual(cut.z_mm, -31)
        self.assertAlmostEqual(cut.start_xy[0], 30)
        self.assertAlmostEqual(cut.start_xy[1], -155)
        self.assertAlmostEqual(cut.end_xy[1], 235)
        self.assertAlmostEqual(dot(cut.target_normal, (*cut.start_xy, 0)), cut.target_offset_mm)

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
        self.assertAlmostEqual(cut.beta_degrees, 36.86989764584402)

    def test_busellato_depth_reference_is_explicit_and_invertible(self):
        panel = blade_panel()
        machine = software_test_machine()
        cut, = plan_blade_cuts(panel, targets(panel), machine)
        self.assertAlmostEqual(cut.start_xy[0], 30)
        self.assertAlmostEqual(cut.z_mm, -(18 / 0.6 + 1))
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
                self.assertAlmostEqual(dot(cut.target_normal, (*cut.start_xy, 0)), cut.target_offset_mm)
                self.assertAlmostEqual(dot(cut.target_normal, (*cut.end_xy, 0)), cut.target_offset_mm)
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
        rendered = writer.render(panel)
        self.assertIn('W#1052', rendered)
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
        for cut in (forward, reverse):
            cut.machine.require_verified_adapter()

    def test_arbitrary_alpha_is_supported_when_no_preference_matches(self):
        panel = blade_panel()
        cut, = plan_blade_cuts(panel, targets(panel), software_test_machine(
            travel_angles_degrees=(0,)))
        self.assertEqual(cut.alpha_degrees, 270)

    def test_other_depth_contracts_are_rejected(self):
        with self.assertRaisesRegex(ValueError, 'busellato_lame_w95'):
            software_test_machine(z_reference='contact_point_z').validate()

    def test_verified_adapter_writes_complete_file(self):
        panel = blade_panel()
        panel.blade_cuts = plan_blade_cuts(panel, targets(panel), software_test_machine())
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'existing.tcn'
            TcnGeometryWriter().write(panel, path)
            self.assertIn('W#1052', path.read_text(encoding='ascii'))
