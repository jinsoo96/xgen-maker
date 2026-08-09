import subprocess
import tempfile
import unittest
from pathlib import Path

from xgen_maker.kg.graph import Graph
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


class TestRefBasedSyncStaysOnRef(unittest.TestCase):
    """그래프를 만든 기준과 갱신 기준은 같아야 한다.

    회귀: 빌드는 origin/develop에서 뽑고 sync는 워킹트리를 읽어, 한 그래프 안에 두
    시점의 코드가 섞였다. 게다가 빌드가 '로컬 HEAD'를 기준으로 기록해, 다음 sync가
    로컬 범위를 diff하고 "변경 없음"이라 판단 — origin이 앞서가도 영영 안 갱신됐다.
    """

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / "r"
        self.root.mkdir()
        init_repo(self.root)
        git(self.root, "tag", "v1")          # 고정 기준(origin/develop 대역)

    def tearDown(self):
        self.tmp.cleanup()

    def test_build_records_the_commit_the_content_came_from(self):
        from xgen_maker.kg.build import git_head
        # v1에서 뽑았으면 기록도 v1의 sha여야 한다(로컬 HEAD가 아니라)
        git(self.root, "commit", "--allow-empty", "-m", "moves local head")
        g = build_repo("demo", self.root, ref="v1")
        self.assertEqual(g.meta["git_head"], git_head(self.root, "v1"))
        self.assertNotEqual(g.meta["git_head"], git_head(self.root, "HEAD"))

    def test_worktree_edit_does_not_leak_into_ref_graph(self):
        from xgen_maker.kg.build import refresh_files
        g = build_repo("demo", self.root, ref="v1")
        # 워킹트리에만 있는 심볼 — v1 기준 그래프에 들어오면 안 된다
        (self.root / "alpha.py").write_text(
            "def alpha():\n    return 1\n\ndef only_in_worktree():\n    return 9\n",
            encoding="utf-8")
        refresh_files(g, "demo", self.root, ["alpha.py"], ref="v1")
        names = {n["name"] for n in g.nodes.values()}
        self.assertIn("alpha", names)
        self.assertNotIn("only_in_worktree", names, "ref 기준 그래프에 워킹트리 심볼이 샜다")
        # ref 없이 부르면(워킹트리 기반 그래프) 그때는 반영돼야 한다
        refresh_files(g, "demo", self.root, ["alpha.py"])
        self.assertIn("only_in_worktree", {n["name"] for n in g.nodes.values()})

    def test_ref_sync_ignores_uncommitted_and_follows_the_ref(self):
        merged, _ = merge_and_link([build_repo("demo", self.root, ref="trunk")])
        source = merged.meta["sources"][0]
        self.assertEqual(source["ref"], "trunk")
        # 미커밋 변경만 있으면 ref는 안 움직였으므로 0건
        (self.root / "beta.py").write_text("def beta():\n    return 99\n", encoding="utf-8")
        self.assertEqual(sync_source(merged, source)["changed"], 0)
        # 커밋하면 ref(trunk)가 전진하므로 반영된다
        git(self.root, "add", "-A")
        git(self.root, "commit", "-m", "beta changed")
        result = sync_source(merged, source)
        self.assertEqual(result["changed"], 1)
        self.assertEqual(result["basis"], "trunk")


class TestRustIsSynced(unittest.TestCase):
    """회귀: sync가 py/ts만 걸러 Rust(.rs) 변경이 증분 반영되지 않았다 — 게이트웨이가 Rust다."""

    def test_rust_file_is_relevant(self):
        from xgen_maker.kg.sync import _relevant
        got = _relevant({"src/main.rs", "a.py", "b.tsx", "readme.md"}, None)
        self.assertIn("src/main.rs", got)

    def test_rust_change_reaches_the_graph(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "r"
            (root / "src").mkdir(parents=True)
            git_init = ["init", "-b", "trunk"]
            subprocess.run(["git", *git_init], cwd=root, capture_output=True, check=True)
            git(root, "config", "user.email", "t@t.local")
            git(root, "config", "user.name", "t")
            (root / "src" / "main.rs").write_text("pub fn serve() {}\n", encoding="utf-8")
            git(root, "add", "-A"); git(root, "commit", "-m", "init")
            merged, _ = merge_and_link([build_repo("gw", root)])
            source = merged.meta["sources"][0]
            (root / "src" / "main.rs").write_text(
                "pub fn serve() {}\n\npub fn validate_token() {}\n", encoding="utf-8")
            git(root, "add", "-A"); git(root, "commit", "-m", "add validate")
            result = sync_source(merged, source)
        self.assertEqual(result["changed"], 1, "Rust 변경이 sync 대상에 안 잡힘")
        self.assertIn("validate_token", {n["name"] for n in merged.nodes.values()})


class GatewayRoutesSurviveIncrementalRefreshTest(unittest.TestCase):
    """회귀: 라우팅 설정을 증분 갱신하면 게이트웨이 라우트가 통째로 사라졌다.

    그 노드들은 path가 설정 파일이라 갱신 대상으로 함께 걷히는데, TS·Rust 라우트와
    달리 재생성이 없었다. 하필 새 모듈이 붙어 설정이 바뀌는 순간에 표가 비었다.
    """

    def _repo(self, tmp, modules):
        root = Path(tmp)
        (root / "config").mkdir(exist_ok=True)
        (root / "app.py").write_text("def handle():\n    return 1\n", encoding="utf-8")
        (root / "config" / "services.yaml").write_text(
            "services:\n  core:\n    modules:\n"
            + "".join(f"      - {m}\n" for m in modules), encoding="utf-8")
        subprocess.run(["git", "init", "-q"], cwd=root, capture_output=True)
        return root

    def test_refresh_matches_full_rebuild(self):
        from xgen_maker.kg.build import build_repo, refresh_files
        with tempfile.TemporaryDirectory() as tmp:
            root = self._repo(tmp, ["admin"])
            graph = build_repo("r", root)
            if not [n for n in graph.nodes if "gwroute" in n]:
                self.skipTest("PyYAML 없음 — 라우팅 표를 못 읽는다")
            self._repo(tmp, ["admin", "vision"])
            refresh_files(graph, "r", root, ["config/services.yaml"])
            full = build_repo("r", root)
            self.assertEqual(sorted(graph.nodes), sorted(full.nodes),
                             "증분 갱신이 풀리빌드와 다른 그래프를 남겼다")
            self.assertIn("r:gwroute:/vision", graph.nodes, "새 모듈 라우트가 안 잡혔다")

    def test_repeated_refresh_does_not_wear_the_graph(self):
        """같은 파일을 몇 번 갱신해도 표가 닳으면 안 된다."""
        from xgen_maker.kg.build import build_repo, refresh_files
        with tempfile.TemporaryDirectory() as tmp:
            root = self._repo(tmp, ["admin", "ocr"])
            graph = build_repo("r", root)
            if not [n for n in graph.nodes if "gwroute" in n]:
                self.skipTest("PyYAML 없음")
            before = sorted(graph.nodes)
            for _ in range(5):
                refresh_files(graph, "r", root, ["config/services.yaml", "app.py"])
            self.assertEqual(sorted(graph.nodes), before)


class NonAsciiPathsSurviveSyncTest(unittest.TestCase):
    """회귀: git이 ASCII 밖 경로를 C 스타일로 감싸는 걸 못 풀어 경로가 뭉개졌다.

    '정산.py'가 '/354/240/225/...'가 됐고, 그런 경로는 실재하지 않으므로 증분 갱신이
    조용히 건너뛴다 — 그 파일의 노드는 영영 낡은 채로 남는다. -z로 받으면 git이
    애초에 감싸지 않는다.
    """

    def _repo(self, tmp):
        root = Path(tmp)
        git(root, "init", "-q")
        (root / "정산.py").write_text("def calc_settlement():\n    return 1\n",
                                     encoding="utf-8")
        (root / "plain.py").write_text("def g():\n    return 2\n", encoding="utf-8")
        git(root, "add", "-A")
        git(root, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "i")
        return root

    def test_uncommitted_change_to_korean_filename_is_seen(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self._repo(tmp)
            sha = git(root, "rev-parse", "HEAD").strip()
            (root / "정산.py").write_text("def calc_settlement():\n    return 99\n",
                                         encoding="utf-8")
            self.assertIn("정산.py", changed_files(root, sha))

    def test_rename_reports_the_new_path_only(self):
        """옛 경로는 이미 없는 파일이라 갱신 대상이 아니다."""
        with tempfile.TemporaryDirectory() as tmp:
            root = self._repo(tmp)
            sha = git(root, "rev-parse", "HEAD").strip()
            git(root, "mv", "plain.py", "일반.py")
            got = changed_files(root, sha)
            self.assertIn("일반.py", got)

    def test_refresh_actually_rereads_the_file(self):
        from xgen_maker.kg.build import build_repo, refresh_files
        with tempfile.TemporaryDirectory() as tmp:
            root = self._repo(tmp)
            graph = build_repo("r", root)
            (root / "정산.py").write_text("def calc_settlement_v2():\n    return 5\n",
                                         encoding="utf-8")
            refresh_files(graph, "r", root, ["정산.py"])
            names = {n.get("name") for n in graph.nodes.values()}
            self.assertIn("calc_settlement_v2", names)
            self.assertNotIn("calc_settlement", names)


class SyncDoesNotInventGatewayRoutesTest(unittest.TestCase):
    """회귀: sync가 빌드에 없는 라우팅표를 만들어, 동기화 한 번에 그래프가 달라졌다.

    인프라 저장소에도 배포용 사본(services.docker.yaml)이 있다. 빌드(extract_infra)는
    그걸 안 읽는데 sync만 읽어, 같은 라우팅표가 두 벌 생겼다. 중복 라우트 27개가
    들어가면 검색도 깎인다(실측 R@10 0.845 → 0.834).
    """

    def _infra(self, tmp):
        root = Path(tmp)
        (root / "dockerfiles" / "gw" / "config").mkdir(parents=True)
        (root / "dockerfiles" / "gw" / "config" / "services.docker.yaml").write_text(
            "base_path: /api\nservices:\n  core:\n    host: http://core:8000\n"
            "    modules:\n      - admin\n", encoding="utf-8")
        return root

    def test_infra_plane_is_skipped(self):
        from xgen_maker.kg.extract_gateway import extract_gateway_routes, find_services_file
        with tempfile.TemporaryDirectory() as tmp:
            root = self._infra(tmp)
            if find_services_file(root) is None:
                self.skipTest("PyYAML 없음")
            graph = Graph()
            graph.add_node("infra", "repo", "infra", "infra", str(root), plane="infra")
            self.assertEqual(extract_gateway_routes(graph, "infra", root), 0,
                             "인프라 저장소에서 라우팅표를 또 만들면 안 된다")

    def test_code_repo_still_extracted(self):
        """막는 것과 안 하는 것은 다르다 — 관문 저장소에서는 그대로 뽑아야 한다."""
        from xgen_maker.kg.extract_gateway import extract_gateway_routes, find_services_file
        with tempfile.TemporaryDirectory() as tmp:
            root = self._infra(tmp)
            if find_services_file(root) is None:
                self.skipTest("PyYAML 없음")
            graph = Graph()
            graph.add_node("gw", "repo", "gw", "gw", str(root))
            self.assertGreater(extract_gateway_routes(graph, "gw", root), 0)
