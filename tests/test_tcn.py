import unittest

from tribu_exporter.model import (
    Arc2D, CurveChain2D, HoleIR, Line2D, PanelIR, PlanarProfileIR,
    MachiningFrameIR, MachiningFrameKind, MachiningSide, ProfileZMode,
    StockAllowance, Vec2, fictive_local_to_panel, profile_selection_key,
)
from tribu_exporter.tcn import TcnGeometryWriter
from tests.tcn_reader import read_fictive_faces, read_holes, read_tcn


class TcnTests(unittest.TestCase):
    @staticmethod
    def _fictive_panel():
        frame = MachiningFrameIR(
            "side7", MachiningFrameKind.FICTIVE_FACE, 7,
            (10, 20, -2),
            (1, 0, 0),
            (0, 0.8, 0.6),
            (0, -0.6, 0.8),
            100, 50, 18,
            "synthetic inclined face",
        )
        points = [Vec2(0, 0), Vec2(100, 0), Vec2(100, 50), Vec2(0, 50)]
        chain = CurveChain2D([
            Line2D(points[index], points[(index + 1) % 4])
            for index in range(4)
        ], True, name="inclined-boundary")
        profile = PlanarProfileIR(
            chain, 0, machining_side=7, profile_id="inclined-boundary",
            source_face_ids=("fusion-face-42",),
            provenance="fictive_face_boundary", containment="outer",
        )
        return PanelIR(
            120, 100, 18, StockAllowance(), [profile],
            machining_frames=[frame],
        ), frame

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

    def test_native_blind_hole_uses_minimal_w81_without_tool_205(self):
        hole = HoleIR(
            "hole-1", Vec2(25.12567, 9.5), 12.25, 8.0,
            MachiningSide.SIDE4, "Hole1@7", "face-12", 12.25,
        )
        panel = PanelIR(
            100, 50, 18, StockAllowance(), holes=[hole],
        )
        text = TcnGeometryWriter().render(panel)
        holes = read_holes(text)
        self.assertEqual(len(holes), 1)
        self.assertEqual(holes[0].side, 4)
        self.assertEqual(holes[0].values, {
            8015: 0, 201: 1, 203: 1, 1: 25.1257, 2: 9.5,
            3: -12.25, 1002: 8, 1001: 1,
        })
        self.assertNotIn("#205=", text)
        self.assertNotIn("#204=", text)
        self.assertNotIn(" WS=", text)
        _, profiles = read_tcn(text)
        self.assertEqual(profiles, [])

    def test_hole_only_fictive_side_emits_its_gside_definition(self):
        panel, frame = self._fictive_panel()
        panel.profiles = []
        panel.holes = [HoleIR(
            "inclined-hole", Vec2(30, 20), 10, 6, 7,
            "Hole9@12", "fusion-face-42", 10,
        )]
        text = TcnGeometryWriter(selected_profile_keys=set()).render(panel)
        self.assertEqual([face.side for face in read_fictive_faces(text)], [7])
        self.assertEqual([hole.side for hole in read_holes(text)], [7])
        self.assertIn("::SIDE=7;", text)

    def test_subsequent_segments_do_not_start_new_profile(self):
        a, b, c = Vec2(0, 0), Vec2(10, 0), Vec2(0, 0)
        chain = CurveChain2D([Line2D(a, b), Line2D(b, c)], True, name="two")
        panel = PanelIR(10, 1, 18, StockAllowance(),
                        [PlanarProfileIR(chain, -2)])
        _, profiles = read_tcn(TcnGeometryWriter().render(panel))
        self.assertEqual(len(profiles), 1)
        self.assertEqual(len(profiles[0].operations), 2)

    def test_lateral_profile_is_written_in_real_side_block(self):
        a, b, c, d = Vec2(10, 0), Vec2(40, 0), Vec2(40, 18), Vec2(10, 18)
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
        self.assertEqual(profiles[0].initial, (10, 0, -5))

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

    def test_optional_serializer_filter_suppresses_only_matching_side1_z0_inner(self):
        points = [Vec2(20, 20), Vec2(80, 20), Vec2(80, 50), Vec2(20, 50)]

        def chain(name):
            return CurveChain2D([
                Line2D(points[i], points[(i + 1) % 4]) for i in range(4)
            ], True, name=name)

        # Deliberately use the same XY chain as the deeper profile: the
        # mandatory outer contour is still never eligible for suppression.
        silhouette = chain("body_silhouette_outer")
        panel = PanelIR(100, 70, 18, StockAllowance(), [
            PlanarProfileIR(
                silhouette, 0, profile_id="body_silhouette_outer",
                provenance="body_silhouette_outer",
                z_mode=ProfileZMode.UNSPECIFIED,
            ),
            PlanarProfileIR(
                chain("top-t"), 0, profile_id="top-t",
                provenance="side1_inner",
            ),
            PlanarProfileIR(
                chain("floor-t"), -2, profile_id="floor-t",
                provenance="fusion_face_boundary",
            ),
        ])
        writer = TcnGeometryWriter(suppress_side1_z0_duplicates=True)
        pairs = writer.z0_duplicate_pairs(panel)
        self.assertEqual([(a.profile_id, b.profile_id) for a, b in pairs], [
            ("top-t", "floor-t"),
        ])
        _, profiles = read_tcn(writer.render(panel))
        self.assertEqual([profile.initial[2] for profile in profiles], [None, -2])

    def test_optional_filter_does_not_suppress_a_nonidentical_z0_loop(self):
        top = CurveChain2D([
            Line2D(Vec2(0, 0), Vec2(10, 0)),
            Line2D(Vec2(10, 0), Vec2(10, 10)),
            Line2D(Vec2(10, 10), Vec2(0, 10)),
            Line2D(Vec2(0, 10), Vec2(0, 0)),
        ], True, name="top")
        deeper = CurveChain2D([
            Line2D(Vec2(0, 0), Vec2(11, 0)),
            Line2D(Vec2(11, 0), Vec2(11, 10)),
            Line2D(Vec2(11, 10), Vec2(0, 10)),
            Line2D(Vec2(0, 10), Vec2(0, 0)),
        ], True, name="deeper")
        panel = PanelIR(20, 20, 18, StockAllowance(), [
            PlanarProfileIR(top, 0, profile_id="top", provenance="side1_inner"),
            PlanarProfileIR(
                deeper, -2, profile_id="deeper",
                provenance="fusion_face_boundary",
            ),
        ])
        writer = TcnGeometryWriter(suppress_side1_z0_duplicates=True)
        self.assertEqual(writer.z0_duplicate_pairs(panel), [])
        _, profiles = read_tcn(writer.render(panel))
        self.assertEqual(len(profiles), 2)

    def test_operator_selection_keeps_mandatory_outer_and_selected_profile_only(self):
        outer = CurveChain2D([
            Line2D(Vec2(0, 0), Vec2(100, 0)),
            Line2D(Vec2(100, 0), Vec2(100, 50)),
            Line2D(Vec2(100, 50), Vec2(0, 50)),
            Line2D(Vec2(0, 50), Vec2(0, 0)),
        ], True, name="outer")
        side3 = CurveChain2D([
            Line2D(Vec2(0, 0), Vec2(100, 0)),
            Line2D(Vec2(100, 0), Vec2(100, 18)),
            Line2D(Vec2(100, 18), Vec2(0, 18)),
            Line2D(Vec2(0, 18), Vec2(0, 0)),
        ], True, name="side3")
        side5 = CurveChain2D(list(side3.segments), True, name="side5")
        panel = PanelIR(100, 50, 18, StockAllowance(), [
            PlanarProfileIR(
                outer, 0, provenance="body_silhouette_outer",
                z_mode=ProfileZMode.UNSPECIFIED,
            ),
            PlanarProfileIR(side3, 0, machining_side=MachiningSide.SIDE3),
            PlanarProfileIR(side5, 0, machining_side=MachiningSide.SIDE5),
        ])
        selected = {profile_selection_key(panel, panel.profiles[1])}
        writer = TcnGeometryWriter(selected_profile_keys=selected)
        _, profiles = read_tcn(writer.render(panel))
        self.assertEqual([profile.side for profile in profiles], [1, 3])

    def test_z0_filter_never_suppresses_selected_top_when_deeper_is_unchecked(self):
        points = [Vec2(10, 10), Vec2(40, 10), Vec2(40, 30), Vec2(10, 30)]

        def chain(name):
            return CurveChain2D([
                Line2D(points[i], points[(i + 1) % 4]) for i in range(4)
            ], True, name=name)

        top = PlanarProfileIR(
            chain("top"), 0, provenance="side1_inner",
        )
        deeper = PlanarProfileIR(
            chain("deeper"), -2, provenance="fusion_face_boundary",
        )
        panel = PanelIR(50, 40, 18, StockAllowance(), [top, deeper])
        writer = TcnGeometryWriter(
            suppress_side1_z0_duplicates=True,
            selected_profile_keys={profile_selection_key(panel, top)},
        )
        self.assertEqual(writer.z0_duplicate_pairs(panel), [])
        _, profiles = read_tcn(writer.render(panel))
        self.assertEqual([profile.initial[2] for profile in profiles], [0])

    def test_fictive_face_serializes_as_additional_gside_and_side7(self):
        panel, _ = self._fictive_panel()
        text = TcnGeometryWriter(selected_profile_keys=set()).render(panel)
        faces = read_fictive_faces(text)
        _, profiles = read_tcn(text)
        self.assertIn("::SIDE=7;", text)
        self.assertEqual(len(faces), 1)
        self.assertEqual(faces[0].side, 7)
        self.assertEqual(faces[0].p0, (10, 20, 16))
        self.assertEqual(faces[0].p1, (110, 20, 16))
        self.assertEqual(faces[0].p2, (10, 60, 46))
        self.assertEqual(faces[0].thickness, 18)
        self.assertEqual([profile.side for profile in profiles], [7])
        self.assertEqual(profiles[0].initial, (0, 0, 0))

    def test_serialized_fictive_face_reconstructs_plane_and_boundary(self):
        panel, original_frame = self._fictive_panel()
        text = TcnGeometryWriter().render(panel)
        face = read_fictive_faces(text)[0]
        _, profiles = read_tcn(text)

        def subtract(left, right):
            return tuple(a - b for a, b in zip(left, right))

        def length(vector):
            return sum(value * value for value in vector) ** 0.5

        def normalized(vector):
            magnitude = length(vector)
            return tuple(value / magnitude for value in vector)

        x_axis = normalized(subtract(face.p1, face.p0))
        p2_vector = subtract(face.p2, face.p0)
        projection = sum(a * b for a, b in zip(p2_vector, x_axis))
        y_axis = normalized(tuple(
            value - projection * axis
            for value, axis in zip(p2_vector, x_axis)
        ))

        local_points = [Vec2(*profiles[0].initial[:2])]
        local_points.extend(Vec2(values[1], values[2])
                            for _, values in profiles[0].operations)
        for local in local_points:
            reconstructed_absolute = tuple(
                face.p0[index]
                + local.x * x_axis[index]
                + local.y * y_axis[index]
                for index in range(3)
            )
            reconstructed_panel = (
                reconstructed_absolute[0], reconstructed_absolute[1],
                reconstructed_absolute[2] - panel.thickness,
            )
            expected = fictive_local_to_panel(original_frame, local)
            for actual, wanted in zip(reconstructed_panel, expected):
                self.assertAlmostEqual(actual, wanted, places=4)


if __name__ == "__main__":
    unittest.main()
