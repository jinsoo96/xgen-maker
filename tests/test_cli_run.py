import argparse
import unittest
from unittest.mock import patch

from xgen_maker import cli


class _FakeLoop:
    """MakerLoop 대체 — 생성 시 config를 캡처하고 run은 no-op."""
    captured = None

    def __init__(self, config):
        _FakeLoop.captured = config

    def run(self, query):
        return {"outcome": "planned"}


def _run(mode):
    args = argparse.Namespace(query="q", config=None, kg=None, mode=mode)
    with patch("xgen_maker.loop.pipeline.MakerLoop", _FakeLoop), \
         patch("builtins.print"):
        cli.cmd_run(args)
    return _FakeLoop.captured


class TestCmdRunModeMapping(unittest.TestCase):
    """B1 회귀 — maker run --mode 가 웹/chat과 동일하게 allow_write를 켜야 함."""

    def test_plan_disables_write(self):
        cfg = _run("plan")
        self.assertFalse(cfg.allow_write)

    def test_observe_enables_write_local(self):
        cfg = _run("observe")
        self.assertTrue(cfg.allow_write)   # 로컬 브랜치+커밋 가능
        self.assertEqual(cfg.mode, "observe")

    def test_act_enables_write_and_push_mode(self):
        cfg = _run("act")
        self.assertTrue(cfg.allow_write)   # 예전엔 mode만 바뀌고 write는 꺼져 push 불가였음
        self.assertEqual(cfg.mode, "act")

    def test_no_mode_defaults_to_observe(self):
        """모드를 나누지 않는다 — 기본은 MR 전단계까지(observe): 로컬 커밋·초안, 푸시 없음."""
        cfg = _run(None)
        self.assertEqual(cfg.mode, "observe")
        self.assertTrue(cfg.allow_write)      # 로컬 브랜치·커밋은 한다(원격은 안 나감)


if __name__ == "__main__":
    unittest.main()
