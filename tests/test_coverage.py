import json
import tempfile
import unittest
from pathlib import Path

import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "core"))
import coverage as coverage_module


class TestEnterCoverage(unittest.TestCase):
    def test_enter_child_requires_probe(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "root"
            parent = root / "parent"
            child = parent / "nested"
            probe = Path(tmp) / "probe"
            root.mkdir()
            parent.mkdir()
            child.mkdir()
            probe.mkdir()
            (probe / "probe_parent.json").write_text(json.dumps({
                "path": str(parent), "action": "enter",
                "enter_children": [{"path": str(child)}],
            }), encoding="utf-8")
            rows = coverage_module.check(root, probe)
            child_row = next(row for row in rows if row.get("node", "").endswith("nested"))
            self.assertEqual(child_row["status"], "MISSING")
            self.assertIn("S1探针", child_row["missing"])

    def test_sub_container_inventory_child_remains_compatible(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "root"
            parent = root / "parent"
            child = parent / "nested"
            probe = Path(tmp) / "probe"
            root.mkdir()
            parent.mkdir()
            child.mkdir()
            probe.mkdir()
            (probe / "units_parent.json").write_text(json.dumps({
                "path": str(parent), "total_units": 0,
                "sub_containers": [{"path": str(child)}],
            }), encoding="utf-8")
            rows = coverage_module.check(root, probe)
            child_row = next(row for row in rows if row.get("node", "").endswith("nested"))
            self.assertEqual(child_row["status"], "ok")


if __name__ == "__main__":
    unittest.main()
