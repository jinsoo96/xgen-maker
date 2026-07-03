import tempfile
import unittest
from pathlib import Path

from xgen_maker.kg.build import build_repo, refresh_files
from xgen_maker.kg.overlay import annotate, add_custom_edge, load_overlay, apply_overlay
from xgen_maker.kg.search import search


class TestOverlay(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / "legacy.py").write_text(
            "def graph_loader():\n    return 'old'\n", encoding="utf-8")
        (self.root / "modern.py").write_text(
            "def graph_loader_v2():\n    return 'new'\n", encoding="utf-8")
        self.graph = build_repo("r", self.root)
        self.overlay_path = self.root / "overlay.json"

    def tearDown(self):
        self.tmp.cleanup()

    def test_annotate_and_apply(self):
        annotate(self.overlay_path, "r:legacy.py", summary="레거시 로더 — 쓰지 말 것",
                 note="신규 작업은 modern.py로", deprecated=True, redirect="r:modern.py")
        result = apply_overlay(self.graph, load_overlay(self.overlay_path))
        self.assertEqual(result["applied"], 1)
        node = self.graph.nodes["r:legacy.py"]
        self.assertEqual(node["meta"]["summary"], "레거시 로더 — 쓰지 말 것")
        self.assertEqual(node["meta"]["summary_src"], "human")
        self.assertTrue(node["meta"]["deprecated"])
        self.assertEqual(node["meta"]["redirect"], "r:modern.py")

    def test_deprecated_search_penalty(self):
        baseline = search(self.graph, "graph_loader")
        self.assertEqual(baseline[0]["id"], "r:legacy.py#graph_loader")
        annotate(self.overlay_path, "r:legacy.py#graph_loader", deprecated=True)
        apply_overlay(self.graph, load_overlay(self.overlay_path))
        after = search(self.graph, "graph_loader")
        self.assertNotEqual(after[0]["id"], "r:legacy.py#graph_loader")

    def test_edit_survives_refresh(self):
        annotate(self.overlay_path, "r:legacy.py", note="유지할 메모", deprecated=True)
        apply_overlay(self.graph, load_overlay(self.overlay_path))
        (self.root / "legacy.py").write_text(
            "def graph_loader():\n    return 'old-v2'\n", encoding="utf-8")
        refresh_files(self.graph, "r", self.root, ["legacy.py"])
        self.assertNotIn("note", self.graph.nodes["r:legacy.py"]["meta"])  # 재추출로 유실
        apply_overlay(self.graph, load_overlay(self.overlay_path))          # 재적용으로 복원
        self.assertEqual(self.graph.nodes["r:legacy.py"]["meta"]["note"], "유지할 메모")

    def test_custom_edge_and_missing_report(self):
        add_custom_edge(self.overlay_path, "r:legacy.py", "r:modern.py",
                        kind="superseded_by", note="이관됨")
        annotate(self.overlay_path, "r:ghost.py", note="없는 노드")
        result = apply_overlay(self.graph, load_overlay(self.overlay_path))
        edges = [e for e in self.graph.edges if e["kind"] == "superseded_by"]
        self.assertEqual(len(edges), 1)
        self.assertTrue(edges[0]["meta"]["human"])
        self.assertIn("r:ghost.py", result["missing"])


if __name__ == "__main__":
    unittest.main()
