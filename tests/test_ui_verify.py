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

    def test_vision_judge_none_without_key(self):
        import os
        from xgen_maker import llm
        os.environ.pop("ANTHROPIC_API_KEY", None)
        with tempfile.TemporaryDirectory() as tmp:
            png = Path(tmp) / "x.png"
            png.write_bytes(b"\x89PNG\r\n")
            self.assertIsNone(llm.vision_judge(str(png), "ok?"))


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
