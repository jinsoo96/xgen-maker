import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from xgen_maker.loop import verify as V


class TestShimCommand(unittest.TestCase):
    def test_wraps_cmd_shim(self):
        with patch("shutil.which", return_value="C:\\npm\\npx.cmd"):
            cmd = V._shim_command("npx", ["-y", "playwright"])
        self.assertEqual(cmd[:2], ["cmd", "/c"])
        self.assertIn("playwright", cmd)

    def test_plain_on_posix(self):
        with patch("shutil.which", return_value="/usr/bin/npx"):
            cmd = V._shim_command("npx", ["--version"])
        self.assertEqual(cmd, ["/usr/bin/npx", "--version"])

    def test_none_when_missing(self):
        with patch("shutil.which", return_value=None):
            self.assertIsNone(V._shim_command("npx", []))


class TestVerifyPreview(unittest.TestCase):
    def test_reachable_triggers_snapshot(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(V, "http_reachable", return_value=True), \
                 patch.object(V, "playwright_snapshot",
                              return_value={"ok": True, "snapshot": "x.png", "bytes": 123}):
                report = V.verify(True, ["svc-frontend"], Path(tmp),
                                  preview_base="http://localhost:3100")
        self.assertTrue(report["preview_reachable"])
        self.assertEqual(report["snapshots"][0]["ok"], True)

    def test_unreachable_records_note(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(V, "http_reachable", return_value=False):
                report = V.verify(True, ["svc-frontend"], Path(tmp),
                                  preview_base="http://localhost:59999")
        self.assertFalse(report["preview_reachable"])
        self.assertIn("자동 기동", report["note"])
        self.assertEqual(report["snapshots"], [])


if __name__ == "__main__":
    unittest.main()
