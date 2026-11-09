"""End-to-end checks for reviewed apply receipts.

These tests deliberately use small synthetic trees and the public CLI.  The
receipt binds the preview to the exact action file and filesystem state, so a
reviewed plan must fail closed if either changes before apply.
"""
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


PROJECT = Path(__file__).resolve().parents[1]
CLI = PROJECT / "packtidy.py"


class ApplyReceiptCase(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix=".packtidy-receipt-", dir=PROJECT)
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.root = self.base / "root"
        self.root.mkdir()
        self.actions = self.base / "actions.tsv"
        self.receipt = self.base / "review.json"
        self.env = os.environ.copy()
        self.env["LOCALAPPDATA"] = str(self.base / "local")
        self.env["PYTHONIOENCODING"] = "utf-8"

    def write_actions(self, rows):
        lines = ["op\tsrc\tdst\treason"]
        lines.extend("\t".join(map(str, row)) for row in rows)
        self.actions.write_text("\n".join(lines) + "\n", encoding="utf-8")

    def run_cli(self, *args):
        return subprocess.run([sys.executable, str(CLI), *map(str, args)],
                              cwd=PROJECT, env=self.env, text=True,
                              encoding="utf-8", capture_output=True)

    def preview(self, root=None, receipt=None):
        return self.run_cli("apply", root or self.root, "--actions", self.actions,
                            "--dry-run", "--receipt", receipt or self.receipt)

    def consume(self, root=None, receipt=None):
        return self.run_cli("apply", root or self.root, "--actions", self.actions,
                            "--receipt", receipt or self.receipt)

    def simple_move(self):
        source = self.root / "source.txt"
        source.write_text("synthetic payload", encoding="utf-8")
        destination = self.root / "destination"
        destination.mkdir()
        self.write_actions([("move", source, destination, "test move")])
        return source, destination / source.name

    def assert_rejected_without_mutation(self, source, target):
        result = self.consume()
        self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertTrue(source.exists(), result.stdout + result.stderr)
        self.assertFalse(target.exists(), result.stdout + result.stderr)

    def test_preview_apply_and_undo(self):
        source, target = self.simple_move()
        result = self.preview()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertTrue(self.receipt.is_file())
        result = self.consume()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertFalse(source.exists())
        self.assertTrue(target.exists())
        result = self.run_cli("undo", "--journal", self.root / "_organize" / "journal.tsv", "--last")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertTrue(source.exists())
        self.assertFalse(target.exists())

    def test_preview_does_not_overwrite_an_existing_receipt(self):
        source, target = self.simple_move()
        self.receipt.write_bytes(b'previous receipt')
        result = self.preview()
        self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.receipt.read_bytes(), b'previous receipt')
        self.assertTrue(source.exists())
        self.assertFalse(target.exists())

    def test_destination_suffix_created_after_preview_is_stale(self):
        source, target = self.simple_move()
        self.assertEqual(self.preview().returncode, 0)
        target.write_text("appeared after review", encoding="utf-8")
        result = self.consume()
        self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertTrue(source.exists())
        self.assertEqual(target.read_text(encoding="utf-8"), "appeared after review")
        self.assertFalse(target.with_name("source_1.txt").exists())

    def test_destination_directory_replaced_after_preview_is_stale(self):
        source, target = self.simple_move()
        destination = target.parent
        self.assertEqual(self.preview().returncode, 0)
        old = self.root / "destination-old"
        destination.rename(old)
        destination.mkdir()
        self.assert_rejected_without_mutation(source, target)
        self.assertFalse((destination / source.name).exists())

    def test_shared_missing_parent_steps_are_reused(self):
        destination = self.root / "new" / "nested" / "destination"
        sources = [self.root / "one.txt", self.root / "two.txt"]
        for source in sources:
            source.write_text(source.stem, encoding="utf-8")
        self.write_actions([("move", source, destination, "same new parent") for source in sources])
        result = self.preview()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        result = self.consume()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(sorted(p.name for p in destination.iterdir()), ["one.txt", "two.txt"])
        journal = (self.root / "_organize" / "journal.tsv").read_text(encoding="utf-8")
        # Each of the three missing ancestors has one prepare and one done
        # journal row; no second action repeats those mkdir transactions.
        self.assertEqual(journal.count("\tmkdir\t"), 6)

    def test_mkdir_then_move_dependency_is_reviewable_and_applies(self):
        source = self.root / "payload.txt"
        source.write_text("dependency", encoding="utf-8")
        destination = self.root / "created-by-plan"
        self.write_actions([("mkdir", "-", destination, "create destination"),
                            ("move", source, destination, "move after mkdir")])
        result = self.preview()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertFalse(destination.exists())
        result = self.consume()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertTrue((destination / source.name).exists())
        self.assertFalse(source.exists())

    def test_source_replaced_after_preview_is_stale(self):
        source, target = self.simple_move()
        self.assertEqual(self.preview().returncode, 0)
        source.unlink()
        source.write_text("replacement object", encoding="utf-8")
        self.assert_rejected_without_mutation(source, target)

    def test_nested_child_content_change_after_preview_is_stale(self):
        source = self.root / "source-dir"
        source.mkdir()
        child = source / "nested.txt"
        child.write_text("before", encoding="utf-8")
        destination = self.root / "destination"
        destination.mkdir()
        self.write_actions([("move", source, destination, "move directory")])
        result = self.preview()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        child.write_text("different size after review", encoding="utf-8")
        result = self.consume()
        self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertTrue(child.exists())
        self.assertEqual(child.read_text(encoding="utf-8"), "different size after review")
        self.assertFalse((destination / source.name).exists())

    def test_action_file_change_after_preview_is_stale(self):
        source, target = self.simple_move()
        self.assertEqual(self.preview().returncode, 0)
        self.write_actions([("move", source, self.root / "other", "changed intent")])
        self.assert_rejected_without_mutation(source, target)
        self.assertFalse((self.root / "other" / source.name).exists())

    def test_blocked_rows_fail_preview_without_receipt_or_mutation(self):
        good = self.root / "good.txt"
        good.write_text("safe", encoding="utf-8")
        destination = self.root / "destination"
        destination.mkdir()
        missing = self.root / "missing.txt"
        self.write_actions([("move", good, destination, "good"),
                            ("move", missing, destination, "blocked")])
        result = self.preview()
        self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertFalse(self.receipt.exists())
        self.assertTrue(good.exists())
        self.assertFalse((destination / good.name).exists())
        # Even if a receipt path is supplied after a failed preview, apply
        # must not partially execute the valid row.
        result = self.consume()
        self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertTrue(good.exists())
        self.assertFalse((destination / good.name).exists())

    def test_action_blocked_after_review_stops_the_whole_batch(self):
        destination = self.root / "destination"
        destination.mkdir()
        first, second = self.root / "first.txt", self.root / "second.txt"
        for source in (first, second):
            source.write_text(source.name, encoding="utf-8")
        self.write_actions([("move", source, destination, "review both") for source in (first, second)])
        result = self.preview()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        second.unlink()
        self.assert_rejected_without_mutation(first, destination / first.name)

    def test_malformed_receipt_fails_closed(self):
        source, target = self.simple_move()
        self.receipt.write_text("{broken", encoding="utf-8")
        self.assert_rejected_without_mutation(source, target)

    def test_receipt_inside_source_directory_is_refused(self):
        source_dir = self.root / "source-dir"
        source_dir.mkdir()
        source = source_dir / "payload.txt"
        source.write_text("synthetic", encoding="utf-8")
        destination = self.root / "destination"
        destination.mkdir()
        self.write_actions([("move", source_dir, destination, "test")])
        receipt_inside = source_dir / "receipt.json"
        result = self.preview(receipt=receipt_inside)
        self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertFalse(receipt_inside.exists())
        self.assertTrue(source.exists())
        self.assertTrue(source_dir.exists())
        self.assertFalse((destination / source_dir.name).exists())

    def test_receipt_for_different_root_is_rejected(self):
        source, target = self.simple_move()
        self.assertEqual(self.preview().returncode, 0)
        other_root = self.base / "other-root"
        other_root.mkdir()
        result = self.consume(root=other_root)
        self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertTrue(source.exists())
        self.assertFalse(target.exists())


if __name__ == "__main__":
    unittest.main()
