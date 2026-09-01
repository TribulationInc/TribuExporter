import unittest

from tribu_exporter.model import (
    Arc2D, CurveChain2D, Line2D, PanelIR, PlanarProfileIR,
    MachiningSide, ProfileZMode, StockAllowance, Vec2,
)
from tribu_exporter.tcn import TcnGeometryWriter
from tests.tcn_reader import read_tcn


class TcnTests(unittest.TestCase):
    def test_profiles_are_independent_and_keep_z(self):
        outer_points = [Vec2(5, 5), Vec2(105, 5), Vec2(105, 65), Vec2(5, 65)]
        outer = CurveChain2D([
            Line2D(outer_points[i], outer_points[(i + 1) % 4]) for i in range(4)
        ], True, name="outer")
        circle = CurveChain2D([
            Arc2D(Vec2(45, 35), Vec2(45, 35), Vec2(35, 35), False, True)
        ], True, name="recess")
        panel = PanelIR(
            100, 60, 18, StockAllowance(5, 5, 5, 5),
            [PlanarProfileIR(outer, 0, provenance="side1_outer"),
             PlanarProfileIR(circle, -1.25, provenance="parallel_depth")],
        )
        dimensions, profiles = read_tcn(TcnGeometryWriter().render(panel))
        self.assertEqual(dimensions, {"DL": 110.0, "DH": 70.0, "DS": 18.0})
        self.assertEqual(len(profiles), 2)
        self.assertEqual(profiles[0].initial, (5.0, 5.0, 0.0))
        self.assertEqual(profiles[1].initial, (45.0, 35.0, -1.25))
        self.assertEqual([profile.side for profile in profiles], [1, 1])
        self.assertEqual([len(p.operations) for p in profiles], [4, 1])
        self.assertEqual(profiles[1].operations[0][0], "A01")

    def test_subsequent_segments_do_not_start_new_profile(self):
        a, b, c = Vec2(0, 0), Vec2(10, 0), Vec2(0, 0)
        chain = CurveChain2D([Line2D(a, b), Line2D(b, c)], True, name="two")
        panel = PanelIR(10, 1, 18, StockAllowance(),
                        [PlanarProfileIR(chain, -2)])
        _, profiles = read_tcn(TcnGeometryWriter().render(panel))
        self.assertEqual(len(profiles), 1)
        self.assertEqual(len(profiles[0].operations), 2)

    def test_lateral_profile_is_written_in_real_side_block(self):
        a, b, c, d = Vec2(10, -18), Vec2(40, -18), Vec2(40, 0), Vec2(10, 0)
        chain = CurveChain2D([
            Line2D(a, b), Line2D(b, c), Line2D(c, d), Line2D(d, a),
        ], True, name="side6_joint")
        panel = PanelIR(90, 50, 18, StockAllowance(5, 5, 5, 5), [
            PlanarProfileIR(
                chain=chain, z_mm=-5, machining_side=MachiningSide.SIDE6,
                profile_id="side6_joint",
            )
        ])
        _, profiles = read_tcn(TcnGeometryWriter().render(panel))
        self.assertEqual(len(profiles), 1)
        self.assertEqual(profiles[0].side, 6)
        self.assertEqual(profiles[0].initial, (10, -18, -5))

    def test_setup_controlled_outer_profile_omits_all_z_fields(self):
        points = [Vec2(0, 0), Vec2(100, 0), Vec2(100, 50), Vec2(0, 50)]
        chain = CurveChain2D([
            Line2D(points[i], points[(i + 1) % 4]) for i in range(4)
        ], True, name="body_silhouette_outer")
        panel = PanelIR(100, 50, 18, StockAllowance(), [
            PlanarProfileIR(
                chain, 0, provenance="body_silhouette_outer",
                z_mode=ProfileZMode.UNSPECIFIED,
            )
        ])
        text = TcnGeometryWriter().render(panel)
        side1 = text.split("SIDE#1{", 1)[1].split("}SIDE", 1)[0]
        self.assertNotIn("#8123=", side1)
        self.assertNotRegex(side1, r"(?:^|\s)#3=")
        _, profiles = read_tcn(text)
        self.assertEqual(profiles[0].initial, (0, 0, None))

    def test_touching_coplanar_faces_remain_independent_profiles(self):
        a0, a1, a2, a3 = (
            Vec2(0, 0), Vec2(50, 0), Vec2(50, 20), Vec2(0, 20),
        )
        b0, b1, b2, b3 = (
            Vec2(50, 0), Vec2(100, 0), Vec2(100, 20), Vec2(50, 20),
        )
        face_a = CurveChain2D([
            Line2D(a0, a1), Line2D(a1, a2),
            Line2D(a2, a3), Line2D(a3, a0),
        ], True, name="face-a")
        face_b = CurveChain2D([
            Line2D(b0, b1), Line2D(b1, b2),
            Line2D(b2, b3), Line2D(b3, b0),
        ], True, name="face-b")
        panel = PanelIR(100, 20, 18, StockAllowance(), [
            PlanarProfileIR(
                face_a, -2.0, profile_id="face-a",
                source_face_ids=("fusion-face-a",),
                provenance="fusion_face_boundary",
            ),
            PlanarProfileIR(
                face_b, -2.0, profile_id="face-b",
                source_face_ids=("fusion-face-b",),
                provenance="fusion_face_boundary",
            ),
        ])
        _, profiles = read_tcn(TcnGeometryWriter().render(panel))
        self.assertEqual(len(profiles), 2)
        self.assertEqual([item.initial for item in profiles], [
            (0, 0, -2), (50, 0, -2),
        ])
        self.assertEqual([len(item.operations) for item in profiles], [4, 4])

    def test_same_xy_boundary_at_different_depth_is_never_chained(self):
        points = [Vec2(10, 10), Vec2(210, 10), Vec2(210, 16.3), Vec2(10, 16.3)]

        def face_chain(name):
            return CurveChain2D([
                Line2D(points[i], points[(i + 1) % 4]) for i in range(4)
            ], True, name=name)

        panel = PanelIR(220, 30, 18, StockAllowance(), [
            PlanarProfileIR(
                face_chain("z-minus-1"), -1.0, profile_id="z-minus-1",
                source_face_ids=("fusion-face-z1",),
                provenance="fusion_face_boundary",
            ),
            PlanarProfileIR(
                face_chain("z-minus-2"), -2.0, profile_id="z-minus-2",
                source_face_ids=("fusion-face-z2",),
                provenance="fusion_face_boundary",
            ),
        ])
        text = TcnGeometryWriter().render(panel)
        _, profiles = read_tcn(text)
        self.assertEqual(len(profiles), 2)
        self.assertEqual([item.initial[2] for item in profiles], [-1, -2])
        self.assertEqual(text.count("#8121="), 2)


if __name__ == "__main__":
    unittest.main()
