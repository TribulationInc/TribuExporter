import unittest

from tribu_exporter.model import (
    CurveChain2D, FaceFactIR, FaceOwnershipIR, Line2D, MachiningFrameIR,
    MachiningFrameKind, MachiningSide, OwnershipState, PanelIR,
    PlanarProfileIR, StockAllowance, Vec2,
)


class OwnershipTests(unittest.TestCase):
    @staticmethod
    def _profile(source_id="face-1", side=MachiningSide.SIDE1):
        a, b, c, d = Vec2(0, 0), Vec2(10, 0), Vec2(10, 10), Vec2(0, 10)
        chain = CurveChain2D([
            Line2D(a, b), Line2D(b, c), Line2D(c, d), Line2D(d, a),
        ], True, name="face-boundary")
        return PlanarProfileIR(
            chain, 0.0, machining_side=side, profile_id="face-boundary",
            source_face_ids=(source_id,), provenance="fusion_face_boundary",
        )

    def test_complete_unique_inventory_is_valid(self):
        fact = FaceFactIR(
            "face-1", "Plane", (0, 0, 1), "side1", MachiningSide.SIDE1,
            None, (), True,
        )
        ownership = FaceOwnershipIR(
            "face-1", OwnershipState.EXPOSED, "side1",
            MachiningSide.SIDE1, 0.0, ("operator_selected_side1",),
        )
        panel = PanelIR(
            100, 50, 18, StockAllowance(),
            face_facts=[fact], face_ownership=[ownership],
        )
        panel.validate()

    def test_every_face_requires_exactly_one_ownership_state(self):
        fact = FaceFactIR(
            "face-1", "Plane", (0, 0, 1), "side1", MachiningSide.SIDE1,
            None,
        )
        panel = PanelIR(100, 50, 18, StockAllowance(), face_facts=[fact])
        with self.assertRaisesRegex(ValueError, "Every inventoried"):
            panel.validate()

    def test_duplicate_ownership_is_rejected(self):
        fact = FaceFactIR(
            "face-1", "Plane", (0, 0, 1), "side1", MachiningSide.SIDE1,
            None,
        )
        owner = FaceOwnershipIR(
            "face-1", OwnershipState.AMBIGUOUS, "side1",
            MachiningSide.SIDE1, -1.0,
        )
        panel = PanelIR(
            100, 50, 18, StockAllowance(), face_facts=[fact],
            face_ownership=[owner, owner],
        )
        with self.assertRaisesRegex(ValueError, "more than one ownership"):
            panel.validate()

    def test_fictive_frame_can_be_represented_without_becoming_a_profile(self):
        frame = MachiningFrameIR(
            "future-inclined-1", MachiningFrameKind.FICTIVE_FACE, None,
            (0, 0, 0), (1, 0, 0), (0, 0.8, 0.6), (0, -0.6, 0.8),
            100, 40, 18, "operator approval required",
        )
        panel = PanelIR(
            100, 50, 18, StockAllowance(), machining_frames=[frame],
        )
        panel.validate()
        self.assertEqual(panel.profiles, [])

    def test_extracted_profile_has_exactly_one_exposed_face_owner(self):
        fact = FaceFactIR(
            "face-1", "Plane", (0, 0, 1), "side1", MachiningSide.SIDE1,
            None, (), True,
        )
        ownership = FaceOwnershipIR(
            "face-1", OwnershipState.EXPOSED, "side1",
            MachiningSide.SIDE1, 0.0,
        )
        panel = PanelIR(
            10, 10, 18, StockAllowance(), [self._profile()],
            face_facts=[fact], face_ownership=[ownership],
        )
        panel.validate()
        self.assertEqual(panel.profiles[0].source_face_id, "face-1")

    def test_profile_cannot_use_covered_face_ownership(self):
        fact = FaceFactIR(
            "face-1", "Plane", (0, 0, 1), "side1", MachiningSide.SIDE1,
            None,
        )
        ownership = FaceOwnershipIR(
            "face-1", OwnershipState.COVERED, None, None, 0.0,
        )
        panel = PanelIR(
            10, 10, 18, StockAllowance(), [self._profile()],
            face_facts=[fact], face_ownership=[ownership],
        )
        with self.assertRaisesRegex(ValueError, "not owned by an exposed face"):
            panel.validate()


if __name__ == "__main__":
    unittest.main()
