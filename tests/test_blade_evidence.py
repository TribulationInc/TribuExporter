"""Golden machine observations, distinct from unverified geometry assumptions."""
from dataclasses import replace
import json
import math
from pathlib import Path
import re
import unittest

from tribu_exporter.blade import ppc_observed_depth, plan_blade_cuts
from tribu_exporter.tcn import TcnGeometryWriter
from tests.test_blade import blade_panel, software_test_machine, targets


RECORDS = json.loads((Path(__file__).parent / 'fixtures' / 'blade_observations.json').read_text())['records']
PRIVATE_DATA = Path(__file__).resolve().parents[3] / 'CustomBusellatoBlade' / 'Data'


def parameters(text):
    result = {}
    for key, value in re.findall(r'#(\d+)=([^\s}]+)', text):
        result[key] = value if key == '8098' else float(value)
    return result


class BladeGoldenEvidenceTests(unittest.TestCase):
    def test_a_divider_depth_observation_and_old_contract_disagreement(self):
        for fixture in RECORDS[:2]:
            p = fixture['parameters']
            depth = ppc_observed_depth(fixture['dimensions'][2], p['8521'])
            self.assertEqual(round(depth, 2), p['8512'])
            wrong = -fixture['dimensions'][2] / math.cos(math.radians(p['8521']))
            self.assertAlmostEqual(wrong, -40.57954, places=5)
            self.assertGreater(abs(wrong - p['8512']), 8)
            self.assertEqual((p['8519'], p['8521'], p['8525']), (270, 51.97, 1))

    def test_a_raw_serializer_preserves_observed_direction_without_plane_claim(self):
        # Supply observed machine values directly. This checks field encoding,
        # NOT derivation of them from a fictive face: that adapter is unverified.
        panel = blade_panel()
        cut, = plan_blade_cuts(panel, targets(panel), software_test_machine())
        for fixture in RECORDS[:2]:
            p = fixture['parameters']
            observed = replace(cut, start_xy=(p['8510'], p['8511']),
                z_mm=p['8512'], alpha_degrees=p['8519'], beta_degrees=p['8521'],
                length_mm=p['8520'], compensation=p['8525'],
                machine=replace(cut.machine, tool_id=p['8516']))
            self.assertEqual(parameters(TcnGeometryWriter()._blade(observed, 1)), p)
            self.assertAlmostEqual(observed.end_xy[1], 0)
            with self.assertRaisesRegex(ValueError, 'blocked'):
                observed.machine.require_verified_adapter()

    def test_b_radius_plus_30_reproduces_observed_longitudinal_coverage(self):
        p = RECORDS[2]['parameters']
        panel = blade_panel()
        panel.finished_height = RECORDS[2]['dimensions'][1]
        # Independent synthetic support plane: only compare longitudinal span.
        cut, = plan_blade_cuts(panel, targets(panel), software_test_machine(
            diameter_mm=300, end_clearance_mm=30, travel_angles_degrees=(90,)))
        self.assertEqual(cut.start_xy[1], p['8511'])
        self.assertEqual(cut.end_xy[1], 960)
        self.assertEqual(cut.length_mm, p['8520'])
        self.assertEqual(cut.length_mm, 150 + 30 + 780 + 150 + 30)
        # Dividers have a different observed lead policy; 30 is not universal.
        for divider in RECORDS[:2]:
            self.assertEqual(divider['parameters']['8520'], divider['dimensions'][1])

    def test_c_second_pass_is_evidenced_but_automatic_planning_rejects_it(self):
        p = RECORDS[2]['parameters']
        self.assertEqual((p['8512'], p['8513'], p['8526']), (-3, -28.07, 1))
        for policy in ('two_pass', 'chord', 'automatic_multipass'):
            with self.assertRaisesRegex(ValueError, 'supports Z2.*not implemented'):
                software_test_machine(pass_policy=policy).validate()

    @unittest.skipUnless(PRIVATE_DATA.is_dir(), 'Private source corpus is not distributed')
    def test_portable_observations_match_actual_user_programs(self):
        for fixture in RECORDS:
            text = (PRIVATE_DATA / fixture['source']).read_text(encoding='cp1252')
            dimensions = re.search(r'DL=([\d.]+) DH=([\d.]+) DS=([\d.]+)', text)
            self.assertEqual(list(map(float, dimensions.groups())), fixture['dimensions'])
            calls = re.findall(r'W#1052\{.*?\}W', text)
            self.assertEqual(len(calls), 1)
            self.assertEqual(parameters(calls[0]), fixture['parameters'])

    def test_observation_does_not_allow_singular_or_nonfinite_depth(self):
        for depth, beta in ((25, 0), (25, 91), (0, 45), (float('nan'), 45), (25, float('inf'))):
            with self.assertRaises(ValueError):
                ppc_observed_depth(depth, beta)
