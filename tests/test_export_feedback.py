"""反馈摘要默认不保留输入文本、私密标记、路径或原始文件名。"""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "export-feedback.py"


class TestExportFeedback(unittest.TestCase):
    def test_cli_exports_only_aggregates_and_safe_enums(self):
        with tempfile.TemporaryDirectory(prefix="feedback-private-check-") as tmp:
            root = Path(tmp)
            organize = root / "_organize"
            organize.mkdir()
            private_name = "SyntheticSecretFilename_9c6f4a.txt"
            marker = "PRIVATE_MARKER_7f2f9d"
            json_path = root / "SyntheticPrivateFolder" / private_name
            private_windows_path = str(json_path)
            private_posix_path = "/home/FakeUser/PrivateZone/KeepMe.txt"
            private_unc_path = r"\\FakeServer\PrivateShare\KeepMe.txt"

            (organize / "probe.json").write_text(
                json.dumps({
                    "path": private_windows_path,
                    "marker": marker,
                    "explanation": private_posix_path,
                    "note": private_unc_path,
                    "kind": private_windows_path,
                    "signal": marker,
                    "result": private_unc_path,
                    "action": "ask",
                    "status": "pending",
                    "confidence": "low",
                }),
                encoding="utf-8",
            )
            (organize / "inventory.tsv").write_text(
                "path\tname\n%s\t%s\n" % (private_unc_path, private_name),
                encoding="utf-8",
            )
            (organize / "notes.md").write_text(
                "Private note: %s %s" % (marker, private_posix_path),
                encoding="utf-8",
            )

            # 必须显式指定 UTF-8——Windows 默认 cp936，
            # 脚本输出中文时会 UnicodeEncodeError
            env = os.environ.copy()
            env["PYTHONIOENCODING"] = "utf-8"
            completed = subprocess.run(
                [sys.executable, str(SCRIPT), "--root", str(root)],
                capture_output=True,
                check=False,
                env=env,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr.decode("utf-8", "replace"))

            output = (organize / "feedback-summary.md").read_text(encoding="utf-8")
            for secret in (
                str(root), private_windows_path, private_posix_path, private_unc_path,
                private_name, marker, "FakeUser", "FakeServer", "PrivateShare",
            ):
                self.assertNotIn(secret, output)
            self.assertIn("产物数量：3", output)
            self.assertIn("JSON：1", output)
            self.assertIn("TSV：1", output)
            self.assertIn("文本：1", output)
            self.assertIn("action=ask：1", output)
            self.assertIn("status=pending：1", output)
            self.assertIn("confidence=low：1", output)


if __name__ == "__main__":
    unittest.main()
