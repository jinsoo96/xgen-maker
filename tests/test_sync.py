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
