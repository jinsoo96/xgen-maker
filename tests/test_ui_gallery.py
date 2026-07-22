"""캡처한 화면 찾기 — 장수로 자르지 않고, 무엇을 언제 찍었는지 알 수 있어야 한다."""
import json
import tempfile
import unittest
from pathlib import Path

from xgen_maker.config import MakerConfig
from xgen_maker.kg.graph import Graph
from xgen_maker import web

PNG = bytes.fromhex("89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c489"
                    "0000000a49444154789c6300010000050001)".replace(")", ""))


class TestScreenshotGallery(unittest.TestCase):
    def _handler(self, base: Path):
        (base / "kg" / "ui-baselines").mkdir(parents=True)
        (base / "kg" / "ui-snaps").mkdir(parents=True)
        (base / "wl" / "s1").mkdir(parents=True)
        (base / "kg" / "ui-baselines" / "ontology.png").write_bytes(PNG)
        (base / "kg" / "ui-snaps" / "ontology.png").write_bytes(PNG)
        (base / "wl" / "s1" / "diff_ontology.png").write_bytes(PNG)
        (base / "wl" / "s1" / "login.png").write_bytes(PNG)
        graph = Graph()
        graph.add_node("r", "repo", "r", "r", "/r")
        kg = base / "kg" / "merged.json"
        graph.save(kg)
        cfg = MakerConfig()
        cfg.kg_path = str(kg)
        cfg.worklogs_dir = str(base / "wl")
        h = web.MakerWebHandler.__new__(web.MakerWebHandler)
        h.config = cfg
        h.graph = graph
        return h

    def test_collects_every_kind(self):
        with tempfile.TemporaryDirectory() as tmp:
            h = self._handler(Path(tmp))
            kinds = {s["kind"] for s in h._ui_images()}
            self.assertEqual(kinds, {"baseline", "snapshot", "diff"})

    def test_nothing_is_dropped(self):
        """세션 수·장수로 자르지 않는다 — 찾으려고 보는 화면이다."""
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            h = self._handler(base)
            for i in range(30):                      # 옛 상한(12·20)을 넘겨 본다
                d = base / "wl" / f"s{i + 2}"
                d.mkdir(parents=True)
                (d / f"diff_route{i}.png").write_bytes(PNG)
            self.assertEqual(len(h._ui_images()), 4 + 30)

    def test_search_narrows(self):
        with tempfile.TemporaryDirectory() as tmp:
            h = self._handler(Path(tmp))
            self.assertEqual(len(h._ui_images("diff")), 1)
            self.assertEqual(len(h._ui_images("ontology")), 3)
            self.assertEqual(h._ui_images("login")[0]["session"], "s1")
            self.assertEqual(h._ui_images("없는것"), [])

    def test_each_image_says_what_and_when(self):
        with tempfile.TemporaryDirectory() as tmp:
            h = self._handler(Path(tmp))
            shot = h._ui_images("diff")[0]
            for field in ("kind", "route", "session", "when", "bytes", "url"):
                self.assertIn(field, shot)
            self.assertEqual(shot["route"], "ontology")   # diff_ 접두사를 벗긴 화면 이름
            self.assertTrue(shot["url"].startswith("/api/ui-image?f="))
