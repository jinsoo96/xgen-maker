import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from xgen_maker import auth
from xgen_maker.config import MakerConfig


class TestAuth(unittest.TestCase):
    def test_resolved_provider_defaults(self):
        self.assertEqual(auth.Auth().provider, "claude_cli")
        self.assertEqual(auth.Auth().resolved_base(), "claude_cli")
        self.assertEqual(auth.Auth(provider="anthropic").resolved_base(), "anthropic")
        self.assertEqual(auth.Auth(provider="anthropic").resolved_model(),
                         auth.DEFAULT_ANTHROPIC_MODEL)
        self.assertEqual(auth.Auth(provider="vllm").resolved_base(), auth.DEFAULT_VLLM_BASE)

    def test_save_and_load_roundtrip(self):
        with tempfile.TemporaryDirectory() as tmp:
            fake = Path(tmp) / "auth.json"
            with patch.object(auth, "AUTH_FILE", fake), \
                 patch.object(auth, "AUTH_DIR", Path(tmp)):
                auth.save_auth(auth.Auth(provider="anthropic", api_key="sk-test", model="m"))
                loaded = auth.load_auth()
        self.assertEqual(loaded.provider, "anthropic")
        self.assertEqual(loaded.api_key, "sk-test")

    def test_claude_command_wraps_cmd_shim(self):
        with patch("shutil.which", return_value="C:\\npm\\claude.cmd"):
            cmd = auth.claude_command(["-p", "hi"])
        self.assertEqual(cmd[:2], ["cmd", "/c"])
        self.assertIn("-p", cmd)

    def test_claude_command_none_when_missing(self):
        with patch("shutil.which", return_value=None):
            self.assertIsNone(auth.claude_command(["-p", "hi"]))

    def test_apply_to_env(self):
        import os
        os.environ.pop("ANTHROPIC_API_KEY", None)
        auth.apply_to_env(auth.Auth(provider="anthropic", api_key="sk-xyz"))
        self.assertEqual(os.environ.get("ANTHROPIC_API_KEY"), "sk-xyz")


class TestDoctor(unittest.TestCase):
    def test_doctor_runs_without_kg(self):
        from xgen_maker.doctor import run_doctor
        import io
        from contextlib import redirect_stdout
        buf = io.StringIO()
        with tempfile.TemporaryDirectory() as tmp:
            cfg = Path(tmp) / "cfg.json"
            cfg.write_text(f'{{"kg_path": "{tmp}/none.json", "llm_enabled": false}}',
                           encoding="utf-8")
            with patch("xgen_maker.auth.claude_cli_status",
                       return_value={"authenticated": True, "reason": ""}):
                with redirect_stdout(buf):
                    run_doctor(str(cfg))
        out = buf.getvalue()
        self.assertIn("보호브랜치 가드", out)
        self.assertIn("자동검증 게이트", out)
        self.assertIn("배포 인터록", out)


if __name__ == "__main__":
    unittest.main()
