"""End to end browser acceptance for the generated PackTidy review board.

Run with: python -m pip install playwright && playwright install chromium
"""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

try:
    from playwright.sync_api import sync_playwright
except ImportError:  # Keep the ordinary unit suite dependency-free.
    sync_playwright = None


PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT / "core"))
from ui import group_key


@unittest.skipIf(sync_playwright is None, "install the browser extra: pip install playwright")
class BrowserRegression(unittest.TestCase):
    def test_review_board_to_receipted_apply_and_undo(self):
        with tempfile.TemporaryDirectory(prefix=".packtidy-browser-", dir=PROJECT) as temp:
            root = Path(temp) / "library"
            source = root / "incoming"
            source.mkdir(parents=True)
            a, b = source / "part-a.bin", source / "part-b.bin"
            a.write_bytes(b"part A fixture\0")
            b.write_bytes(b"part B fixture\0")
            (root / "archive").mkdir()
            (root / "archive" / "existing.txt").write_text("existing destination fixture", encoding="utf-8")
            probe = root / "_organize" / "probe"
            probe.mkdir(parents=True)
            (probe / "group_fixture.json").write_text(json.dumps({
                "path": str(source), "groups": [{"name": "Sample Set", "units": ["part-a.bin", "part-b.bin"]}]
            }), encoding="utf-8")
            (probe / "units_fixture.json").write_text(json.dumps({
                "path": str(source), "loose_files_as_units": [str(a), str(b)]
            }), encoding="utf-8")
            fingerprint = group_key({"name": "Sample Set", "units": ["part-a.bin", "part-b.bin"]})
            decisions = root / "decisions.json"
            decisions.write_text(json.dumps({
                "root": str(root), "nodes": [{"path": str(source), "groups": [{
                    "id": f"{fingerprint}:0", "fingerprint": fingerprint,
                    "suggest": {"mode": "move", "target": str(root / "archive"),
                                "targetLabel": "archive", "tier": "direct"}
                }]}]
            }), encoding="utf-8")
            board = Path(temp) / "review.html"
            env = os.environ.copy()
            if env.get("PACKTIDY_BROWSER_USE_INSTALLED") != "1":
                env["PYTHONPATH"] = str(PROJECT) + os.pathsep + env.get("PYTHONPATH", "")
            env["LOCALAPPDATA"] = str(Path(temp) / "appdata")
            env["XDG_STATE_HOME"] = str(Path(temp) / "state")
            generated = subprocess.run([sys.executable, "-m", "packtidy", "ui", str(root),
                                        "--decisions", str(decisions), "--out", str(board)],
                                       cwd=temp, env=env, capture_output=True, text=True, encoding="utf-8")
            self.assertEqual(generated.returncode, 0, generated.stderr)
            self.assertTrue(board.is_file())
            originals = {p: hashlib.sha256(p.read_bytes()).hexdigest() for p in (a, b)}

            with sync_playwright() as p:
                chrome = Path(r"C:\Program Files\Google\Chrome\Application\chrome.exe")
                edge = Path(r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe")
                executable = chrome if chrome.is_file() else edge if edge.is_file() else None
                browser = p.chromium.launch(executable_path=str(executable) if executable else None)
                page = browser.new_page(accept_downloads=True)
                page.set_default_timeout(5000)
                page.goto(board.as_uri())
                archive_target = page.locator("[data-target]").filter(has=page.locator(".target-label", has_text="archive"))
                page.locator("#groupSearch").fill("Sample Set")
                card = page.locator("article.card").filter(has=page.get_by_role("heading", name="Sample Set"))
                card.locator("summary").click()
                card.locator("button.port[data-source][data-unit]").first.click()
                page.locator('[data-target="@keep"] .target-port').click()
                self.assertIn('"linked":1', page.evaluate("JSON.stringify(window.foState())"))

                card.locator("button.port.group-port[data-source]").click()
                page.locator("#groupSearch").fill("no matching group")
                self.assertIn("已取消选择", page.locator("#hint").inner_text())
                archive_target.first.locator(".target-port").click()
                self.assertEqual(page.evaluate("window.foState().linked"), 1)
                page.locator("#groupSearch").fill("Sample Set")

                page.locator("#reviewButton").click()
                self.assertIn("待连", page.locator("#reviewText").inner_text())
                page.locator("#review form button").click()
                # The ordinary group action fills the unassigned unit while
                # retaining the first unit's explicit Keep assignment.
                card.locator("button.port.group-port[data-source]").click()
                page.locator("#targetSearch").fill("archive")
                archive_target.first.locator(".target-port").click()
                state = page.evaluate("window.foState()")
                self.assertEqual(state["linked"], 2)
                self.assertCountEqual(list(state["placements"].values()), ["@keep", str(root / "archive")])

                # Exercise the visible action history controls without relying on internals.
                undo = page.locator("#undoButton")
                redo = page.locator("#redoButton")
                undo.click()
                self.assertEqual(page.evaluate("window.foState().linked"), 1)
                redo.click()
                self.assertEqual(page.evaluate("window.foState().linked"), 2)

                card.locator('button[data-action="override-group"]').click()
                page.once("dialog", lambda dialog: dialog.accept())
                archive_target.first.locator(".target-port").click()
                self.assertEqual(list(page.evaluate("Object.values(window.foState().placements)")),
                                 [str(root / "archive")] * 2)
                undo.click()
                self.assertCountEqual(page.evaluate("Object.values(window.foState().placements)"),
                                      ["@keep", str(root / "archive")])

                page.locator("#reviewButton").click()
                correction = page.locator(".correction-row").filter(has=page.locator('[data-correction="what"]'))
                self.assertEqual(correction.count(), 1)
                correction.locator('[data-correction="what"]').fill("user-confirmed retained sample")
                with page.expect_download() as correction_download:
                    page.locator("#downloadCorrections").click()
                corrections_file = Path(temp) / "corrections.tsv"
                correction_download.value.save_as(corrections_file)
                correction_text = corrections_file.read_text(encoding="utf-8")
                self.assertIn("user-confirmed retained sample", correction_text)
                self.assertIn("Sample Set", correction_text)
                self.assertIn("incoming", correction_text)
                self.assertNotIn(str(a), correction_text)
                with page.expect_download() as download_info:
                    page.locator("#downloadActions").click()
                action_file = Path(temp) / "actions.tsv"
                download_info.value.save_as(action_file)
                self.assertIn("part-b.bin", action_file.read_text(encoding="utf-8"))
                self.assertNotIn("part-a.bin", action_file.read_text(encoding="utf-8"))
                receipt = Path(temp) / "receipt.json"
                cli = lambda *args: subprocess.run([sys.executable, "-m", "packtidy", *map(str, args)],
                                                    cwd=temp, env=env, check=True, capture_output=True,
                                                    text=True, encoding="utf-8")
                cli("apply", root, "--actions", action_file, "--dry-run", "--receipt", receipt)
                cli("apply", root, "--actions", action_file, "--receipt", receipt)
                self.assertTrue(a.exists())
                self.assertFalse(b.exists())
                self.assertEqual(hashlib.sha256(a.read_bytes()).hexdigest(), originals[a])
                self.assertEqual(hashlib.sha256((root / "archive" / b.name).read_bytes()).hexdigest(), originals[b])
                cli("undo", "--journal", root / "_organize" / "journal.tsv", "--last")
                self.assertEqual(hashlib.sha256(a.read_bytes()).hexdigest(), originals[a])
                self.assertEqual(hashlib.sha256(b.read_bytes()).hexdigest(), originals[b])
                browser.close()


if __name__ == "__main__":
    unittest.main()
