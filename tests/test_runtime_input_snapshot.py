import tempfile
import unittest
from pathlib import Path

from mower.config_source import _immutable_snapshot as mower_snapshot
from occupancy.runtime_source import _immutable_snapshot as occupancy_snapshot


class RuntimeInputSnapshotTests(unittest.TestCase):
    def test_mower_snapshots_keep_two_validated_pairs(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first = mower_snapshot(root, b'{"v":1}', b"BEGIN:VCALENDAR\nA")
            second = mower_snapshot(root, b'{"v":2}', b"BEGIN:VCALENDAR\nB")
            self.assertNotEqual(first, second)
            self.assertEqual(first[0].read_bytes(), b'{"v":1}')
            self.assertEqual(first[1].read_bytes(), b"BEGIN:VCALENDAR\nA")
            self.assertEqual(second[0].read_bytes(), b'{"v":2}')
            self.assertEqual(second[1].read_bytes(), b"BEGIN:VCALENDAR\nB")

    def test_occupancy_snapshots_are_content_addressed(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first = occupancy_snapshot(root, b'{"matches": [1]}')
            second = occupancy_snapshot(root, b'{"matches": [2]}')
            self.assertNotEqual(first, second)
            self.assertEqual(first.read_bytes(), b'{"matches": [1]}')
            self.assertEqual(second.read_bytes(), b'{"matches": [2]}')


if __name__ == "__main__":
    unittest.main()
