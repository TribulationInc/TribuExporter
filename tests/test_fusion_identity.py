import unittest

from tribu_exporter.fusion_identity import same_contextual_entity


class _NativeEntity:
    def __init__(self, key):
        self.key = key

    def __eq__(self, other):
        return isinstance(other, _NativeEntity) and self.key == other.key


class _Occurrence:
    def __init__(self, path):
        self.fullPathName = path


class _Proxy:
    def __init__(self, native, path):
        self.nativeObject = native
        self.assemblyContext = _Occurrence(path)


class FusionIdentityTests(unittest.TestCase):
    def test_distinct_wrappers_for_same_native_entity_and_occurrence_match(self):
        left = _Proxy(_NativeEntity("body-a"), "devastator:1")
        right = _Proxy(_NativeEntity("body-a"), "devastator:1")
        self.assertIsNot(left, right)
        self.assertTrue(same_contextual_entity(left, right))

    def test_same_native_entity_in_different_occurrences_does_not_match(self):
        native = _NativeEntity("body-a")
        left = _Proxy(native, "devastator:1")
        right = _Proxy(native, "devastator:2")
        self.assertFalse(same_contextual_entity(left, right))

    def test_different_native_entities_in_same_occurrence_do_not_match(self):
        left = _Proxy(_NativeEntity("body-a"), "devastator:1")
        right = _Proxy(_NativeEntity("body-b"), "devastator:1")
        self.assertFalse(same_contextual_entity(left, right))


if __name__ == "__main__":
    unittest.main()
