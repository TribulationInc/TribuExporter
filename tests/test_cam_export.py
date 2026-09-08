from math import cos, pi, sin
import unittest

from tribu_exporter.cam_export import (
    CamExportOptions, Point3, PostedArcXY, PostedHelixXY, PostedMove,
    PostedToolpath, StockBox, append_cam_profile_to_tcn, fit_xy_arcs,
    merge_exact_collinear_moves, optimize_toolpath, parse_toolpath_dump,
    render_experimental_tcn, simplify_3d_moves, tcn_physical_line_count,
)


class CamExportTests(unittest.TestCase):
    def synthetic(self):
        return PostedToolpath(
            operation_name="Synthetic 3-axis",
            stock=StockBox(Point3(0, 0, -18), Point3(100, 80, 0)),
            start=Point3(10, 10, 5),
            moves=(
                PostedMove("linear", Point3(10, 10, -1), 500, 0, "plunge"),
                PostedMove("linear", Point3(30, 10, -2), 500, 0),
                PostedMove("linear", Point3(50, 20, -5), 500, 0),
                PostedMove("linear", Point3(70, 40, -10), 500, 0),
                PostedMove("rapid", Point3(70, 40, 5), None, 0),
            ),
        )

    def test_side1_z_remains_negative_into_material(self):
        output = render_experimental_tcn(self.synthetic())
        self.assertIn("#1=10 #2=10 #3=-1", output)
        self.assertIn("#1=30 #2=10 #3=-2", output)
        self.assertIn("#1=70 #2=40 #3=-10", output)
        self.assertNotIn("#1=10 #2=10 #3=1 ", output)

    def test_stock_lower_corner_translates_to_tpa_origin(self):
        path = PostedToolpath(
            "offset", StockBox(Point3(-50, -40, -18), Point3(50, 40, 0)),
            Point3(-40, -30, 1),
            (PostedMove("linear", Point3(-20, -30, -2), 100, 1),),
        )
        output = render_experimental_tcn(path)
        self.assertIn("DL=100 DH=80 DS=18", output)
        self.assertIn("#8121=10 #8122=10 #8123=1", output)
        self.assertIn("#1=30 #2=10 #3=-2", output)

    def test_one_open_profile_and_ordered_l01_chain(self):
        output = render_experimental_tcn(self.synthetic())
        self.assertNotIn("W#89{", output)
        self.assertEqual(output.count("W#2201{"), 5)
        self.assertEqual(output.count("#8121="), 1)
        self.assertIn("#8121=10 #8122=10 #8123=5", output)
        self.assertLess(output.index("#1=30 #2=10 #3=-2"),
                        output.index("#1=50 #2=20 #3=-5"))

    def test_dump_parser_retains_rapid_and_feed(self):
        dump = """TRIBU_TOOLPATH_DUMP|1
UNITS|MM
STOCK|0|0|-18|100|80|0
SECTION|1
START|10|10|5
RAPID|10|10|2||0|rapid
LINEAR|10|10|-1|500|11|cutting
"""
        path = parse_toolpath_dump(dump, "Adaptive1")
        self.assertEqual(path.operation_name, "Adaptive1")
        self.assertEqual(path.rapid_count, 1)
        self.assertEqual(path.linear_count, 1)
        self.assertEqual(path.moves[1].feed_mm_min, 500)
        self.assertEqual(path.moves[1].fusion_movement, 11)
        self.assertEqual(path.moves[1].fusion_movement_name, "cutting")

    def test_zero_length_move_is_not_emitted_or_moved(self):
        path = self.synthetic()
        duplicate = PostedMove("rapid", path.start, None, 0, "rapid")
        duplicated = PostedToolpath(
            path.operation_name, path.stock, path.start,
            (duplicate,) + path.moves,
        )
        output = render_experimental_tcn(duplicated)
        self.assertEqual(output.count("W#2201{"), len(path.moves))
        self.assertIn("#8121=10 #8122=10 #8123=5 #1=10 #2=10 #3=-1", output)

    def test_multiple_post_sections_fail_loudly(self):
        dump = """TRIBU_TOOLPATH_DUMP|1
UNITS|MM
STOCK|0|0|-18|100|80|0
SECTION|1
START|10|10|5
LINEAR|10|10|-1|500|11
SECTION|2
START|20|20|5
"""
        with self.assertRaisesRegex(ValueError, "exactly one"):
            parse_toolpath_dump(dump)

    def test_native_xy_arc_is_preserved_as_a01(self):
        dump = """TRIBU_TOOLPATH_DUMP|1
UNITS|MM
STOCK|0|0|-18|100|80|0
SECTION|1
START|10|0|-2
ARC_XY|0|0|0|-2|0|10|-2|800|11|cutting|0|1.570796327|native
"""
        path = parse_toolpath_dump(dump)
        self.assertEqual(path.native_arc_count, 1)
        output = render_experimental_tcn(path)
        self.assertEqual(output.count("W#2101{"), 1)
        self.assertNotIn("W#2201{", output)
        self.assertIn("#34=1 #31=-10 #32=0 #3=-2", output)

    def test_native_arc_is_preserved_when_fallback_fitting_is_disabled(self):
        path = PostedToolpath(
            "native arc", StockBox(Point3(0, 0, -18), Point3(100, 80, 0)),
            Point3(10, 0, -2),
            (PostedArcXY(Point3(0, 10, -2), Point3(0, 0, -2), False,
                         500, 11, "cutting", False, pi / 2),),
        )
        result = optimize_toolpath(path, CamExportOptions(fit_arcs=False))
        self.assertIsInstance(result.toolpath.moves[0], PostedArcXY)
        self.assertEqual(render_experimental_tcn(path, result).count("W#2101{"), 1)

    def test_line_warning_threshold_never_changes_geometry(self):
        low = optimize_toolpath(
            self.synthetic(), CamExportOptions(tcn_line_warning_limit=1),
        )
        high = optimize_toolpath(
            self.synthetic(), CamExportOptions(tcn_line_warning_limit=100000),
        )
        self.assertEqual(low.toolpath, high.toolpath)

    def test_150_segment_small_circle_becomes_four_a01(self):
        count = 150
        start = Point3(25, 15, -3)
        moves = tuple(
            PostedMove(
                "linear",
                Point3(15 + 10 * cos(2 * pi * index / count),
                       15 + 10 * sin(2 * pi * index / count), -3),
                600, 11, "cutting",
            )
            for index in range(1, count + 1)
        )
        path = PostedToolpath(
            "small circle", StockBox(Point3(0, 0, -18), Point3(100, 80, 0)),
            start, moves,
        )
        fitted = fit_xy_arcs(path)
        self.assertEqual(fitted.fitted_arc_count, 4)
        self.assertEqual(fitted.remaining_line_count, 0)
        self.assertEqual(len(fitted.toolpath.moves), 4)
        output = render_experimental_tcn(path)
        self.assertEqual(output.count("W#2101{"), 4)
        self.assertEqual(output.count("#8121="), 1)
        self.assertLessEqual(fitted.maximum_residual_mm, 0.05)

    def test_dense_closed_circle_uses_single_fit_candidate(self):
        count = 4096
        start = Point3(25, 15, -3)
        path = PostedToolpath(
            "dense circle", StockBox(Point3(0, 0, -18), Point3(100, 80, 0)),
            start, tuple(
                PostedMove(
                    "linear",
                    Point3(15 + 10 * cos(2 * pi * index / count),
                           15 + 10 * sin(2 * pi * index / count), -3),
                    600, 11, "cutting",
                )
                for index in range(1, count + 1)
            ),
        )
        pulses = []
        fitted = fit_xy_arcs(path, yield_callback=lambda: pulses.append(1))
        self.assertEqual(fitted.candidate_count, 1)
        self.assertEqual(fitted.fitted_arc_count, 4)
        self.assertEqual(fitted.remaining_line_count, 0)
        self.assertGreaterEqual(len(pulses), 2)

    def test_non_circular_cutting_run_has_bounded_fit_search(self):
        count = 5000
        path = PostedToolpath(
            "adaptive-like path",
            StockBox(Point3(0, 0, -18), Point3(200, 200, 0)),
            Point3(0, 0, -2),
            tuple(
                PostedMove(
                    "linear",
                    Point3(index * 0.1, float(index % 2), -2),
                    600, 11, "cutting",
                )
                for index in range(1, count + 1)
            ),
        )
        fitted = fit_xy_arcs(path)
        self.assertLess(fitted.candidate_count, count * 4)
        self.assertEqual(fitted.remaining_line_count, count)

    def test_small_circle_with_more_than_15_degree_chords_is_fitted(self):
        count = 16  # 22.5 degrees between points: exactly the jerky case.
        start = Point3(12, 10, -2)
        path = PostedToolpath(
            "coarse small circle",
            StockBox(Point3(0, 0, -18), Point3(30, 30, 0)), start,
            tuple(
                PostedMove(
                    "linear",
                    Point3(10 + 2 * cos(2 * pi * index / count),
                           10 + 2 * sin(2 * pi * index / count), -2),
                    500, 11, "cutting",
                )
                for index in range(1, count + 1)
            ),
        )
        fitted = fit_xy_arcs(path)
        self.assertEqual(fitted.fitted_arc_count, 4)
        self.assertEqual(fitted.remaining_line_count, 0)

    def test_arc_fit_never_crosses_a_rapid_boundary(self):
        path = PostedToolpath(
            "two chains", StockBox(Point3(0, 0, -18), Point3(100, 80, 0)),
            Point3(10, 0, -2),
            (
                PostedMove("linear", Point3(9, 4, -2), 500, 11, "cutting"),
                PostedMove("linear", Point3(7, 7, -2), 500, 11, "cutting"),
                PostedMove("rapid", Point3(20, 0, 5), None, 0, "rapid"),
                PostedMove("linear", Point3(19, 4, -2), 500, 11, "cutting"),
                PostedMove("linear", Point3(17, 7, -2), 500, 11, "cutting"),
            ),
        )
        fitted = fit_xy_arcs(path)
        self.assertIn("rapid", [getattr(item, "kind", None) for item in fitted.toolpath.moves])
        self.assertGreaterEqual(fitted.remaining_line_count, 1)

    def test_varying_z_polyline_is_not_fitted(self):
        path = PostedToolpath(
            "helix", StockBox(Point3(0, 0, -18), Point3(100, 80, 0)),
            Point3(10, 0, -1), tuple(
                PostedMove("linear", Point3(x, y, z), 500, 11, "cutting")
                for x, y, z in ((9, 4, -1.1), (7, 7, -1.2), (4, 9, -1.3))
            ),
        )
        fitted = fit_xy_arcs(path)
        self.assertEqual(fitted.fitted_arc_count, 0)
        self.assertEqual(fitted.remaining_line_count, 3)

    def test_xy_arc_candidate_over_tolerance_falls_back_to_lines(self):
        count = 12
        points = [
            Point3(10 * cos(pi * index / count),
                   10 * sin(pi * index / count), -2)
            for index in range(count + 1)
        ]
        points[6] = Point3(points[6].x, points[6].y + 0.2, -2)
        path = PostedToolpath(
            "bad arc", StockBox(Point3(-20, -20, -18), Point3(20, 20, 0)),
            points[0], tuple(
                PostedMove("linear", point, 500, 11, "cutting")
                for point in points[1:]
            ),
        )
        fitted = fit_xy_arcs(path)
        # It may conservatively fit unaffected sub-arcs, but it must retain the
        # displaced point exactly instead of absorbing it into a dubious arc.
        self.assertIn(points[6], [motion.end for motion in fitted.toolpath.moves])
        self.assertLessEqual(fitted.maximum_residual_mm, 0.05)

    def test_cam_disabled_append_is_byte_identical(self):
        original = "HEADER\nSIDE#1{\n}SIDE\n"
        self.assertIs(append_cam_profile_to_tcn(original, None), original)

    def test_cam_append_is_one_new_profile_without_setup(self):
        base = (
            "HEADER\nSIDE#1{\n"
            "W#2201{ ::WTl #8015=0 #8121=0 #8122=0 #8123=0 #1=1 #2=0 #3=0 }W\n"
            "}SIDE\nSIDE#2{\n}SIDE\n"
        )
        combined = append_cam_profile_to_tcn(base, fit_xy_arcs(self.synthetic()))
        self.assertEqual(combined.count("#8121="), 2)
        self.assertNotIn("W#89{", combined)
        self.assertLess(combined.index("#1=1 #2=0 #3=0"),
                        combined.index("#8121=10 #8122=10 #8123=5"))

    def test_native_full_circle_is_four_continuous_quarters(self):
        path = PostedToolpath(
            "native circle", StockBox(Point3(0, 0, -18), Point3(100, 80, 0)),
            Point3(25, 15, -3),
            (PostedArcXY(Point3(25, 15, -3), Point3(15, 15, -3), False,
                         600, 11, "cutting", True, 2 * pi),),
        )
        fitted = fit_xy_arcs(path)
        self.assertEqual(len(fitted.toolpath.moves), 4)
        output = render_experimental_tcn(path)
        self.assertEqual(output.count("W#2101{"), 4)
        self.assertEqual(output.count("#8121="), 1)

    def test_native_xy_helix_is_one_exact_helicoidal_a01(self):
        dump = """TRIBU_TOOLPATH_DUMP|2
UNITS|MM
STOCK|0|0|-18|100|80|0
SECTION|1
START|20|10|1
HELIX_XY|0|10|10|1|20|10|-2|500|12|ramp_helix|1|6.283185307|native
"""
        raw = parse_toolpath_dump(dump, "helix")
        result = optimize_toolpath(raw)
        self.assertEqual(result.original_native_helix_count, 1)
        self.assertIsInstance(result.toolpath.moves[0], PostedHelixXY)
        output = render_experimental_tcn(raw, result)
        self.assertEqual(output.count("W#2101{"), 1)
        self.assertEqual(output.count("W#2201{"), 0)
        self.assertIn("#8123=1", output)
        self.assertIn("#31=-10 #32=0 #3=-2", output)

    def test_spiral_is_retained_by_dump_then_explicitly_linearized(self):
        dump = """TRIBU_TOOLPATH_DUMP|2
UNITS|MM
STOCK|0|0|-18|100|80|0
SECTION|1
START|10|0|0
SPIRAL_XY|0|0|0|0|20|0|-2|500|12|ramp_helix|6.283185307|10|20|native
"""
        raw = parse_toolpath_dump(dump, "spiral")
        self.assertEqual(raw.native_spiral_count, 1)
        result = optimize_toolpath(raw, CamExportOptions(
            fit_arcs=False, post_linearization_tolerance_mm=0.02,
        ))
        self.assertEqual(result.original_native_spiral_count, 1)
        self.assertGreater(result.spiral_linearized_segment_count, 4)
        self.assertEqual(result.toolpath.native_spiral_count, 0)
        output = render_experimental_tcn(raw, result)
        self.assertNotIn("SPIRAL", output)
        self.assertEqual(output.count("W#2201{"), len(result.toolpath.moves))

    def test_exact_collinear_merge_reduces_lines_without_moving_endpoint(self):
        path = PostedToolpath(
            "straight", StockBox(Point3(0, 0, -18), Point3(100, 80, 0)),
            Point3(0, 0, 0), tuple(
                PostedMove("linear", Point3(index, index * 2, -index), 500, 11,
                           "cutting")
                for index in range(1, 51)
            ),
        )
        merged, removed = merge_exact_collinear_moves(path)
        self.assertEqual(removed, 49)
        self.assertEqual(len(merged.moves), 1)
        self.assertEqual(merged.moves[0].end, path.moves[-1].end)

    def test_collinear_merge_never_crosses_feed_or_movement_boundary(self):
        path = PostedToolpath(
            "boundaries", StockBox(Point3(0, 0, -18), Point3(100, 80, 0)),
            Point3(0, 0, 0), (
                PostedMove("linear", Point3(1, 0, 0), 500, 11, "cutting"),
                PostedMove("linear", Point3(2, 0, 0), 600, 11, "cutting"),
                PostedMove("rapid", Point3(3, 0, 0), None, 0, "rapid"),
            ),
        )
        merged, removed = merge_exact_collinear_moves(path)
        self.assertEqual(removed, 0)
        self.assertEqual(merged.moves, path.moves)

    def test_optional_3d_simplification_reports_measured_bound(self):
        points = tuple(
            PostedMove(
                "linear", Point3(index, 0.02 * sin(index / 4), -index * 0.01),
                500, 11, "cutting",
            )
            for index in range(1, 101)
        )
        path = PostedToolpath(
            "gentle 3d", StockBox(Point3(0, -10, -18), Point3(120, 10, 1)),
            Point3(0, 0, 0), points,
        )
        simplified, removed, deviation = simplify_3d_moves(path, 0.05)
        self.assertGreater(removed, 0)
        self.assertLessEqual(deviation, 0.05)
        self.assertEqual(simplified.moves[-1].end, path.moves[-1].end)

    def test_complete_tcn_line_count_includes_headers_and_side_blocks(self):
        output = render_experimental_tcn(self.synthetic())
        workings = output.count("W#2201{") + output.count("W#2101{")
        self.assertGreater(tcn_physical_line_count(output), workings)


if __name__ == "__main__":
    unittest.main()
