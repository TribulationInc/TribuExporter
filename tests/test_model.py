import unittest

from tribu_exporter.model import (
    Arc2D, CurveChain2D, Line2D, PanelIR, PlanarProfileIR,
    MachiningSide, StockAllowance, Vec2, chain_signature, profile_sort_key,
    profile_selection_key,
    same_geometric_depth,
)


def rectangle(name="rectangle", x0=0, y0=0, x1=100, y1=50):
    a, b, c, d = Vec2(x0, y0), Vec2(x1, y0), Vec2(x1, y1), Vec2(x0, y1)
    return CurveChain2D([
        Line2D(a, b), Line2D(b, c), Line2D(c, d), Line2D(d, a),
    ], True, name=name)


class ModelTests(unittest.TestCase):
    def test_closed_chain_is_valid(self):
        rectangle().validate()

    def test_open_chain_is_rejected_without_moving_endpoint(self):
        chain = rectangle()
        chain.segments[-1] = Line2D(chain.segments[-1].start, Vec2(0.002, 0))
        with self.assertRaisesRegex(ValueError, "closure gap"):
            chain.validate()
        self.assertEqual(chain.segments[-1].end, Vec2(0.002, 0))

    def test_connectivity_comparison_never_becomes_endpoint_snapping(self):
        chain = rectangle()
        original = Vec2(0.0005, 0)
        chain.segments[-1] = Line2D(chain.segments[-1].start, original)
        with self.assertRaisesRegex(ValueError, "closure gap"):
            chain.validate()
        self.assertEqual(chain.segments[-1].end, original)

    def test_signature_ignores_start_and_direction(self):
        chain = rectangle()
        rotated = CurveChain2D(chain.segments[2:] + chain.segments[:2], True)
        reversed_chain = CurveChain2D(
            [item.reversed() for item in reversed(chain.segments)], True,
        )
        self.assertEqual(chain_signature(chain), chain_signature(rotated))
        self.assertEqual(chain_signature(chain), chain_signature(reversed_chain))

    def test_undersized_explicit_stock_is_rejected(self):
        panel = PanelIR(100, 50, 18, StockAllowance(5, 5, 5, 5),
                        explicit_stock_width=109)
        with self.assertRaisesRegex(ValueError, "width"):
            panel.validate()

    def test_boundary_rounding_equal_to_tcn_stock_is_accepted(self):
        chain = rectangle(x1=100.00000004, y1=50.00000004)
        panel = PanelIR(
            100, 50, 18, StockAllowance(),
            [PlanarProfileIR(chain=chain, z_mm=0)],
        )
        panel.validate()

    def test_boundary_that_serializes_beyond_stock_is_rejected(self):
        chain = rectangle(x1=100.00006, y1=50)
        panel = PanelIR(
            100, 50, 18, StockAllowance(),
            [PlanarProfileIR(chain=chain, z_mm=0)],
        )
        with self.assertRaisesRegex(ValueError, "TCN 100.0001"):
            panel.validate()

    def test_arc_signature_is_direction_independent(self):
        arc = Arc2D(Vec2(10, 0), Vec2(0, 10), Vec2(0, 0), False)
        line = Line2D(Vec2(0, 10), Vec2(10, 0))
        chain = CurveChain2D([arc, line], True)
        reverse = CurveChain2D([line.reversed(), arc.reversed()], True)
        self.assertEqual(chain_signature(chain), chain_signature(reverse))

    def test_whole_body_silhouette_sorts_before_side1_inner_boundaries(self):
        silhouette = PlanarProfileIR(
            rectangle("silhouette"), 0, provenance="body_silhouette_outer",
        )
        inner = PlanarProfileIR(
            rectangle("inner", 20, 20, 30, 30), 0,
            provenance="side1_inner",
        )
        deeper = PlanarProfileIR(
            rectangle("deeper", 5, 5, 95, 45), -1,
            provenance="fusion_face_boundary",
        )
        ordered = sorted([inner, deeper, silhouette], key=profile_sort_key)
        self.assertEqual(
            [item.provenance for item in ordered],
            ["body_silhouette_outer", "side1_inner", "fusion_face_boundary"],
        )

    def test_selected_side1_outer_reference_boundary_is_not_exportable(self):
        profile = PlanarProfileIR(
            rectangle("selected-top-outer"), 0,
            provenance="side1_top_boundary",
        )
        with self.assertRaisesRegex(ValueError, "reference boundary"):
            profile.validate()

    def test_depth_planes_never_merge_across_tolerance(self):
        self.assertTrue(same_geometric_depth(-1.0, -1.009, 0.01))
        self.assertFalse(same_geometric_depth(-1.0, 0.0, 0.01))
        self.assertFalse(same_geometric_depth(-1.0, -1.011, 0.01))

    def test_face_derived_profile_cannot_merge_source_faces(self):
        profile = PlanarProfileIR(
            rectangle("illegal-union"), -1.0,
            profile_id="illegal-union",
            source_face_ids=("face-z1-left", "face-z1-right"),
            provenance="fusion_face_boundary",
        )
        with self.assertRaisesRegex(ValueError, "merges multiple Fusion BRepFaces"):
            profile.validate()

    def test_synthetic_body_silhouette_has_no_source_face_owner(self):
        profile = PlanarProfileIR(
            rectangle("silhouette"), 0.0,
            profile_id="body_silhouette_outer",
            source_face_ids=(),
            provenance="body_silhouette_outer",
        )
        profile.validate()
        self.assertIsNone(profile.source_face_id)

    def test_profile_selection_key_does_not_change_with_stock_allowance(self):
        no_margin = PanelIR(100, 50, 18, StockAllowance())
        with_margin = PanelIR(100, 50, 18, StockAllowance(5, 5, 5, 5))
        source = PlanarProfileIR(
            rectangle("side6", 10, 0, 40, 18), 0,
            machining_side=MachiningSide.SIDE6,
        )
        shifted = PlanarProfileIR(
            rectangle("side6", 15, 0, 45, 18), -5,
            machining_side=MachiningSide.SIDE6,
        )
        self.assertEqual(
            profile_selection_key(no_margin, source),
            profile_selection_key(with_margin, shifted),
        )

    def test_profile_selection_key_distinguishes_side_and_depth(self):
        panel = PanelIR(100, 50, 18, StockAllowance())
        chain = rectangle("region", 10, 0, 40, 18)
        side3 = PlanarProfileIR(chain, 0, machining_side=MachiningSide.SIDE3)
        side5 = PlanarProfileIR(chain, 0, machining_side=MachiningSide.SIDE5)
        deeper = PlanarProfileIR(chain, -1, machining_side=MachiningSide.SIDE3)
        self.assertNotEqual(
            profile_selection_key(panel, side3),
            profile_selection_key(panel, side5),
        )
        self.assertNotEqual(
            profile_selection_key(panel, side3),
            profile_selection_key(panel, deeper),
        )


if __name__ == "__main__":
    unittest.main()
