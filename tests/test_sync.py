import subprocess
import tempfile
import unittest
from pathlib import Path

from xgen_maker.kg.build import build_repo, merge_and_link
from xgen_maker.kg.sync import sync_all, sync_source, changed_files, install_hooks, remove_hooks


def git(root: Path, *args: str) -> str:
    result = subprocess.run(["git", *args], cwd=root, capture_output=True, text=True,
                            encoding="utf-8", errors="replace", check=True)
    return result.stdout


def init_repo(root: Path) -> None:
    git(root, "init", "-b", "trunk")
    git(root, "config", "user.email", "t@t.local")
    git(root, "config", "user.name", "t")
    (root / "alpha.py").write_text('"""알파 모듈."""\n\ndef alpha():\n    return 1\n',
                                   encoding="utf-8")
    (root / "beta.py").write_text("def beta():\n    return 2\n", encoding="utf-8")
    git(root, "add", "-A")
    git(root, "commit", "-m", "init")


def build_merged(root: Path):
    graph = build_repo("demo", root)
    merged, _ = merge_and_link([graph])
    return merged


class TestSync(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        init_repo(self.root)
        self.graph = build_merged(self.root)

    def tearDown(self):
        self.tmp.cleanup()

    def test_merge_records_sources_and_heads(self):
        self.assertEqual(self.graph.meta["sources"][0]["repo"], "demo")
        self.assertIn("demo", self.graph.meta["repo_heads"])
        self.assertEqual(len(self.graph.meta["repo_heads"]["demo"]), 40)

    def test_no_change_syncs_zero(self):
        results = sync_all(self.graph)
        self.assertEqual(results[0]["changed"], 0)

    def test_committed_change_picked_up(self):
        (self.root / "alpha.py").write_text(
            '"""알파 모듈 v2."""\n\ndef alpha_renamed():\n    return 1\n', encoding="utf-8")
        git(self.root, "add", "-A")
        git(self.root, "commit", "-m", "rename alpha")
        results = sync_all(self.graph)
        self.assertEqual(results[0]["changed"], 1)
        self.assertIn("demo:alpha.py#alpha_renamed", self.graph.nodes)
        self.assertNotIn("demo:alpha.py#alpha", self.graph.nodes)
        # 기준점 갱신 → 재sync는 0
        self.assertEqual(sync_all(self.graph)[0]["changed"], 0)

    def test_working_tree_change_picked_up(self):
        (self.root / "beta.py").write_text("def beta_two():\n    return 22\n",
                                           encoding="utf-8")
        results = sync_all(self.graph)
        self.assertEqual(results[0]["changed"], 1)
        self.assertIn("demo:beta.py#beta_two", self.graph.nodes)

    def test_new_and_deleted_files(self):
        (self.root / "gamma.py").write_text("def gamma():\n    return 3\n", encoding="utf-8")
        (self.root / "beta.py").unlink()
        git(self.root, "add", "-A")
        git(self.root, "commit", "-m", "add gamma, drop beta")
        sync_all(self.graph)
        self.assertIn("demo:gamma.py#gamma", self.graph.nodes)
        self.assertNotIn("demo:beta.py", self.graph.nodes)
        self.assertNotIn("demo:beta.py#beta", self.graph.nodes)

    def test_scope_filter(self):
        source = {"repo": "demo", "root": str(self.root), "scope": "sub"}
        (self.root / "alpha.py").write_text("def x():\n    return 0\n", encoding="utf-8")
        result = sync_source(self.graph, source)
        self.assertEqual(result["changed"], 0)  # scope 밖 변경은 무시

    def test_missing_baseline_signals_rebuild(self):
        self.assertIsNone(changed_files(self.root, None))
        graph_no_meta = build_repo("demo", self.root)  # merge 안 함 → sources 없음
        results = sync_all(graph_no_meta)
        self.assertEqual(results[0]["action"], "full_rebuild_needed")


class TestHooks(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        init_repo(self.root)

    def tearDown(self):
        self.tmp.cleanup()

    def test_install_and_remove(self):
        results = install_hooks(self.root, "D:/xgen-maker", "D:/xgen-maker/kg/merged.json")
        self.assertEqual(len(results), 3)
        hook = self.root / ".git" / "hooks" / "post-commit"
        content = hook.read_text(encoding="utf-8")
        self.assertIn("xgen-maker-kg-sync", content)
        self.assertIn("kg sync", content)
        removed = remove_hooks(self.root)
        self.assertEqual(len(removed), 3)
        self.assertFalse(hook.exists())

    def test_existing_foreign_hook_not_overwritten(self):
        hooks_dir = self.root / ".git" / "hooks"
        hooks_dir.mkdir(exist_ok=True)
        (hooks_dir / "post-commit").write_text("#!/bin/sh\necho mine\n", encoding="utf-8")
        results = install_hooks(self.root, "D:/xgen-maker", "kg.json")
        self.assertTrue(any("건너뜀" in r for r in results))
        self.assertIn("echo mine",
                      (hooks_dir / "post-commit").read_text(encoding="utf-8"))
        # 제거도 남의 훅은 건드리지 않음
        removed = remove_hooks(self.root)
        self.assertFalse(any("post-commit" in r for r in removed))


if __name__ == "__main__":
    unittest.main()


class TestBomAndDanglingRepair(unittest.TestCase):
    """8회차 검수: BOM 파일이 통째로 누락돼 끊긴 엣지가 생기던 문제.

    ast.parse가 U+FEFF에서 SyntaxError를 내면 파일 노드조차 안 만들고 return했다.
    → 그 파일을 import하는 쪽 엣지가 갈 곳을 잃고(실측 45개), 증분 sync는 '변경된
      파일'만 읽으므로 그 파일이 다시 바뀌기 전까지 영영 복구되지 않았다.
    """

    def test_bom_file_still_becomes_a_node(self):
        from xgen_maker.kg.graph import Graph
        from xgen_maker.kg.extract_python import extract_python_file
        with tempfile.TemporaryDirectory() as t:
            root = Path(t)
            rel = "shim.py"
            # BOM + 재export만 있는 shim(함수·클래스 0개) — 실제로 있던 형태
            (root / rel).write_bytes(
                b"\xef\xbb\xbf" + b'"""shim."""\nfrom pkg.base import BaseModel\n')
            g = Graph()
            extract_python_file(g, "demo", root, rel, {rel})
            self.assertIn("demo:shim.py", g.nodes, "BOM 파일이 그래프에서 누락됨")
            self.assertFalse(g.nodes["demo:shim.py"]["meta"].get("parse_error"),
                             "BOM만 벗기면 정상 파싱돼야 함")

    def test_unparsable_file_still_becomes_a_node(self):
        # 파싱 자체가 불가해도 '파일이 존재한다'는 사실은 남겨야 끊긴 엣지가 안 생긴다
        from xgen_maker.kg.graph import Graph
        from xgen_maker.kg.extract_python import extract_python_file
        with tempfile.TemporaryDirectory() as t:
            root = Path(t)
            (root / "broken.py").write_text("def (:\n", encoding="utf-8")
            g = Graph()
            extract_python_file(g, "demo", root, "broken.py", {"broken.py"})
            self.assertIn("demo:broken.py", g.nodes)
            self.assertTrue(g.nodes["demo:broken.py"]["meta"].get("parse_error"))

    def test_sync_repairs_dangling_edges_without_losing_them(self):
        # 노드가 빠진 파일이 실재하면 재추출해 엣지를 살린다(버리지 않는다)
        from xgen_maker.kg.graph import Graph
        from xgen_maker.kg.sync import repair_dangling
        with tempfile.TemporaryDirectory() as t:
            root = Path(t)
            (root / "target.py").write_text("def f():\n    return 1\n", encoding="utf-8")
            g = Graph()
            g.add_node("demo", "repo", "demo", "demo", str(root))  # 실그래프처럼 레포 노드 존재
            g.add_node("demo:user.py", "file", "user.py", "demo", "user.py")
            g.add_edge("demo:user.py", "demo:target.py", "imports")  # dst 노드 없음
            ids = set(g.nodes)
            self.assertTrue([e for e in g.edges if e["dst"] not in ids])  # 끊긴 상태
            r = repair_dangling(g, [{"repo": "demo", "root": str(root)}])
            ids = set(g.nodes)
            self.assertEqual([e for e in g.edges if e["dst"] not in ids], [])  # 복구됨
            self.assertIn("demo:target.py", g.nodes)
            self.assertEqual(r["dropped"], 0, "실재 파일이면 엣지를 버리지 말아야")
            # 원래의 imports 관계가 살아남았는가(버려서 0으로 만든 게 아님)
            self.assertTrue(any(e["kind"] == "imports" and e["dst"] == "demo:target.py"
                                for e in g.edges))

    def test_sync_drops_edge_when_file_really_gone(self):
        # 파일이 진짜 없으면 그때는 끊긴 엣지를 버린다
        from xgen_maker.kg.graph import Graph
        from xgen_maker.kg.sync import repair_dangling
        with tempfile.TemporaryDirectory() as t:
            g = Graph()
            g.add_node("demo:user.py", "file", "user.py", "demo", "user.py")
            g.add_edge("demo:user.py", "demo:deleted.py", "imports")
            r = repair_dangling(g, [{"repo": "demo", "root": t}])
            ids = set(g.nodes)
            self.assertEqual([e for e in g.edges if e["dst"] not in ids], [])
            self.assertEqual(r["dropped"], 1)
