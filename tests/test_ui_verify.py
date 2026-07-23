import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from xgen_maker.kg.graph import Graph
from xgen_maker.kg.crossrepo import link_feature_packages
from xgen_maker.loop.ui_verify import affected_routes, pixel_diff, ui_verify


def app_features_graph() -> Graph:
    g = Graph()
    # app scope: route → page → imports feature package node
    g.add_node("app:route:/ontology", "route", "/ontology", "app", "app/ontology/page.tsx")
    g.add_node("app:app/ontology/page.tsx", "file", "page.tsx", "app", "app/ontology/page.tsx")
    g.add_node("app:feature:@x/onto", "feature", "@x/onto", "app", "")
    g.add_edge("app:route:/ontology", "app:app/ontology/page.tsx", "route_of")
    g.add_edge("app:app/ontology/page.tsx", "app:feature:@x/onto", "imports")
    # features scope: feature package with real component file
    g.add_node("feat:feature:@x/onto", "feature", "@x/onto", "feat", "features/onto")
    g.add_node("feat:features/onto/graph.tsx", "file", "graph.tsx", "feat",
               "features/onto/graph.tsx")
    g.add_edge("feat:feature:@x/onto", "feat:features/onto/graph.tsx", "contains")
    return g


class TestFeatureLinkAndRoutes(unittest.TestCase):
    def test_cross_scope_link_and_route_mapping(self):
        g = app_features_graph()
        # 링크 전엔 매핑 실패
        self.assertEqual(affected_routes(g, ["features/onto/graph.tsx"], "feat"), [])
        n = link_feature_packages(g)
        self.assertGreaterEqual(n, 1)
        routes = affected_routes(g, ["features/onto/graph.tsx"], "feat")
        self.assertEqual([r["route"] for r in routes], ["/ontology"])


class TestPixelDiff(unittest.TestCase):
    def test_identical_and_diff(self):
        try:
            from PIL import Image
        except ImportError:
            self.skipTest("Pillow 없음")
        with tempfile.TemporaryDirectory() as tmp:
            t = Path(tmp)
            Image.new("RGB", (40, 40), (255, 255, 255)).save(t / "a.png")
            Image.new("RGB", (40, 40), (255, 255, 255)).save(t / "b_same.png")
            img = Image.new("RGB", (40, 40), (255, 255, 255))
            for x in range(20):
                for y in range(40):
                    img.putpixel((x, y), (0, 0, 0))  # 왼쪽 절반 검정
            img.save(t / "b_diff.png")
            same = pixel_diff(t / "a.png", t / "b_same.png", t / "d1.png")
            diff = pixel_diff(t / "a.png", t / "b_diff.png", t / "d2.png")
            self.assertEqual(same["status"], "identical")
            self.assertEqual(diff["status"], "diff")
            self.assertGreater(diff["changed_ratio"], 0.4)  # ~절반
            self.assertTrue(Path(diff["diff_png"]).exists())


class TestUiVerifyGuards(unittest.TestCase):
    def test_skips_when_no_preview(self):
        from xgen_maker.config import MakerConfig
        g = app_features_graph()
        cfg = MakerConfig(kg_path="kg.json", preview_base="")
        with tempfile.TemporaryDirectory() as tmp:
            r = ui_verify(cfg, g, [], "feat", Path(tmp))
        self.assertTrue(r["skipped"])

    def test_skips_when_unreachable(self):
        from xgen_maker.config import MakerConfig
        g = app_features_graph()
        cfg = MakerConfig(kg_path="kg.json", preview_base="http://localhost:59999")
        with tempfile.TemporaryDirectory() as tmp:
            with patch("xgen_maker.loop.ui_verify.http_reachable", return_value=False):
                r = ui_verify(cfg, g, ["features/onto/graph.tsx"], "feat", Path(tmp))
        self.assertTrue(r["skipped"])
        self.assertIn("미도달", r["reason"])

    def test_vision_judge_uses_subscription_without_key(self):
        """API 키가 없으면 구독(claude CLI) 경로로 넘어가야 한다.

        예전엔 키가 없으면 무조건 None이었다 — 구독 사용자는 비전 검증을 아예 못 썼다.
        이제 키가 없으면 _vision_judge_cli를 호출한다. CLI 자체는 여기서 스텁으로 막고,
        '넘어가는가'만 확인한다(실제 CLI 판정은 통합 검증에서 실측했다).
        """
        import os
        from unittest import mock
        from xgen_maker import llm
        os.environ.pop("ANTHROPIC_API_KEY", None)
        with tempfile.TemporaryDirectory() as tmp:
            png = Path(tmp) / "x.png"
            png.write_bytes(b"\x89PNG\r\n")
            with mock.patch.object(llm, "_vision_judge_cli",
                                   return_value={"renders_ok": True}) as cli:
                result = llm.vision_judge(str(png), "ok?")
            cli.assert_called_once()
            self.assertEqual(result, {"renders_ok": True})


if __name__ == "__main__":
    unittest.main()


class TestAuthedSnapshot(unittest.TestCase):
    def test_guard_when_node_missing(self):
        from xgen_maker.loop.ui_verify import authed_snapshot
        from unittest.mock import patch
        with patch("shutil.which", return_value=None):
            with tempfile.TemporaryDirectory() as tmp:
                r = authed_snapshot("http://x", "e", "p", ["/"], Path(tmp))
        self.assertFalse(r["ok"])
        self.assertIn("미설치", r["reason"])


class TestUiVerifyAutoRuns(unittest.TestCase):
    """프리뷰 주소가 있으면 화면 검증이 자동으로 돈다 — 초기 목적이었다."""

    def test_pipeline_gate_triggers_on_preview_base(self):
        """게이트가 별도 boolean이 아니라 preview_base 존재로 열려야 한다."""
        from pathlib import Path
        src = Path(__file__).parent.parent.joinpath(
            "xgen_maker", "loop", "pipeline.py").read_text(encoding="utf-8")
        # ui_verify 진입 조건이 preview_base여야 한다(enable_ui_verify 하드게이트 제거)
        self.assertIn("if not config.preview_base:", src)
        self.assertNotIn('reason="enable_ui_verify=False"', src)

    def test_unreachable_preview_skips_with_reason(self):
        """주소가 있어도 스택이 안 떠 있으면 사유와 함께 건너뛴다(멈추지 않는다)."""
        from xgen_maker.config import MakerConfig
        from xgen_maker.kg.graph import Graph
        from xgen_maker.loop.ui_verify import ui_verify
        cfg = MakerConfig()
        cfg.preview_base = "http://127.0.0.1:9"     # 아무도 안 듣는 포트
        with tempfile.TemporaryDirectory() as tmp:
            g = Graph()
            g.add_node("r", "repo", "r", "r", "/r")
            rep = ui_verify(cfg, g, ["a.tsx"], "r", Path(tmp))
        self.assertTrue(rep["skipped"])
        self.assertIn("미도달", rep["reason"])
