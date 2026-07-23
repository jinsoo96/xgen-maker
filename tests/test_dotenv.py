import os
import tempfile
import unittest
from pathlib import Path

from xgen_maker.dotenv import _parse, load_env, find_env


class TestDotenvParse(unittest.TestCase):
    def test_parse_forms(self):
        text = (
            "# comment\n"
            "XGEN_MAKER_GITLAB_TOKEN=glpat-abc\n"
            'ANTHROPIC_API_KEY="sk-ant-quoted"\n'
            "export FOO=bar\n"
            "WITH_COMMENT=value # inline\n"
            "EMPTY=\n"
            "  SPACED = spaced_val \n"
            "noequalsline\n"
        )
        out = _parse(text)
        self.assertEqual(out["XGEN_MAKER_GITLAB_TOKEN"], "glpat-abc")
        self.assertEqual(out["ANTHROPIC_API_KEY"], "sk-ant-quoted")
        self.assertEqual(out["FOO"], "bar")
        self.assertEqual(out["WITH_COMMENT"], "value")
        self.assertEqual(out["SPACED"], "spaced_val")
        self.assertNotIn("noequalsline", out)


class TestLoadEnv(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.env = Path(self.tmp.name) / ".env"
        os.environ.pop("XGEN_MAKER_TESTKEY", None)

    def tearDown(self):
        self.tmp.cleanup()
        os.environ.pop("XGEN_MAKER_TESTKEY", None)

    def test_loads_and_injects(self):
        self.env.write_text("XGEN_MAKER_TESTKEY=fromfile\n", encoding="utf-8")
        r = load_env(self.env)
        self.assertTrue(r["loaded"])
        self.assertIn("XGEN_MAKER_TESTKEY", r["keys"])
        self.assertEqual(os.environ["XGEN_MAKER_TESTKEY"], "fromfile")

    def test_existing_env_not_overwritten(self):
        os.environ["XGEN_MAKER_TESTKEY"] = "fromenv"
        self.env.write_text("XGEN_MAKER_TESTKEY=fromfile\n", encoding="utf-8")
        load_env(self.env)
        self.assertEqual(os.environ["XGEN_MAKER_TESTKEY"], "fromenv")  # env 우선

    def test_override_true(self):
        os.environ["XGEN_MAKER_TESTKEY"] = "fromenv"
        self.env.write_text("XGEN_MAKER_TESTKEY=fromfile\n", encoding="utf-8")
        load_env(self.env, override=True)
        self.assertEqual(os.environ["XGEN_MAKER_TESTKEY"], "fromfile")

    def test_missing_file(self):
        r = load_env(Path(self.tmp.name) / "nope.env")
        self.assertFalse(r["loaded"])

    def test_explicit_env_var_path(self):
        self.env.write_text("XGEN_MAKER_TESTKEY=viapath\n", encoding="utf-8")
        os.environ["XGEN_MAKER_ENV"] = str(self.env)
        try:
            self.assertEqual(find_env(), self.env)
        finally:
            os.environ.pop("XGEN_MAKER_ENV", None)


if __name__ == "__main__":
    unittest.main()


class TestWriteEnv(unittest.TestCase):
    """로그인·설정 저장이 .env에 자동 반영돼야 한다(초기 요구: .env 자동 작성)."""

    def test_update_preserve_add_delete(self):
        import tempfile
        from pathlib import Path
        from xgen_maker.dotenv import write_env, _parse
        with tempfile.TemporaryDirectory() as tmp:
            envp = Path(tmp) / ".env"
            envp.write_text("# comment\nKEEP=yes\nTOK=old\n", encoding="utf-8")
            write_env({"TOK": "new", "ADDED": "x", "GONE": ""}, envp)
            body = envp.read_text(encoding="utf-8")
            v = _parse(body)
            self.assertEqual(v["TOK"], "new")       # 갱신
            self.assertEqual(v["KEEP"], "yes")      # 보존
            self.assertEqual(v["ADDED"], "x")       # 추가
            self.assertNotIn("GONE", v)             # 빈 값 = 삭제
            self.assertIn("# comment", body)        # 주석 보존

    def test_save_auth_writes_env(self):
        """save_auth가 .env에 자격을 쓴다."""
        import tempfile
        from pathlib import Path
        from xgen_maker.auth import Auth, save_auth
        from xgen_maker.dotenv import _parse
        with tempfile.TemporaryDirectory() as tmp:
            envp = Path(tmp) / ".env"
            envp.write_text("", encoding="utf-8")
            import os
            os.environ["XGEN_MAKER_ENV"] = str(envp)
            try:
                save_auth(Auth(provider="claude_cli", gitlab_url="https://g.example.com",
                               gitlab_token="tok-123"))
                v = _parse(envp.read_text(encoding="utf-8"))
                self.assertEqual(v.get("XGEN_MAKER_GITLAB_TOKEN"), "tok-123")
                self.assertEqual(v.get("XGEN_MAKER_GITLAB_URL"), "https://g.example.com")
            finally:
                os.environ.pop("XGEN_MAKER_ENV", None)
