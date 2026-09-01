import unittest

from tribu_exporter.model import (
    MachiningSide, Vec2, classify_orthogonal_normal,
    panel_to_side_coordinates,
)


class SideCoordinateTests(unittest.TestCase):
    def assert_mapping(self, side, expected_point, expected_depth):
        point, depth = panel_to_side_coordinates(side, 5, 20, -7, 100, 80, 18)
        self.assertEqual(point, expected_point)
        self.assertEqual(depth, expected_depth)

    def test_public_side_mappings(self):
        self.assert_mapping(MachiningSide.SIDE1, Vec2(5, 20), -7)
        self.assert_mapping(MachiningSide.SIDE3, Vec2(5, 11), -20)
        self.assert_mapping(MachiningSide.SIDE5, Vec2(5, 11), -60)
        self.assert_mapping(MachiningSide.SIDE4, Vec2(20, 11), -95)
        self.assert_mapping(MachiningSide.SIDE6, Vec2(20, 11), -5)

    def test_all_lateral_faces_use_bottom_zero_top_ds(self):
        for side in (
            MachiningSide.SIDE3, MachiningSide.SIDE4,
            MachiningSide.SIDE5, MachiningSide.SIDE6,
        ):
            top, _ = panel_to_side_coordinates(side, 5, 20, 0, 100, 80, 18)
            bottom, _ = panel_to_side_coordinates(side, 5, 20, -18, 100, 80, 18)
            self.assertEqual(top.y, 18)
            self.assertEqual(bottom.y, 0)

    def test_side6_five_mm_inward_has_minus_five_depth(self):
        _, depth = panel_to_side_coordinates(
            MachiningSide.SIDE6, 5, 30, -4, 100, 80, 18,
        )
        self.assertEqual(depth, -5)

    def test_a_top_pocket_floor_is_side1(self):
        self.assertEqual(classify_orthogonal_normal(0, 0, 1), MachiningSide.SIDE1)

    def test_b_stepped_joint_splits_floor_and_lateral_wall(self):
        floor = classify_orthogonal_normal(0, 0, 1)
        wall = classify_orthogonal_normal(0, -1, 0)
        self.assertEqual((floor, wall), (MachiningSide.SIDE1, MachiningSide.SIDE3))

    def test_c_one_feature_can_have_multiple_machining_sides(self):
        sides = {
            classify_orthogonal_normal(0, 0, 1),
            classify_orthogonal_normal(-1, 0, 0),
        }
        self.assertEqual(sides, {MachiningSide.SIDE1, MachiningSide.SIDE6})

    def test_d_lateral_face_below_top_never_becomes_side1(self):
        side = classify_orthogonal_normal(1, 0, 0)
        self.assertEqual(side, MachiningSide.SIDE4)
        self.assertNotEqual(side, MachiningSide.SIDE1)


if __name__ == "__main__":
    unittest.main()
