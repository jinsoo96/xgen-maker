"""감사에서 나온 결함들 — 전부 "에러 없이 틀린 결과"를 내던 것들이라 테스트로 못박는다.

공통 성질: 실패해도 아무도 안 죽는다. 검색이 빈 결과를 내고, 중지 버튼이 안 듣고,
CI가 초록으로 통과하고, 되돌리기가 조용히 실패한다. 그래서 테스트가 유일한 방어선이다.
"""
import subprocess
import tempfile
import unittest
from pathlib import Path

from xgen_maker.kg.graph import Graph
from xgen_maker.kg.search import search


class TestSearchIndexInvalidation(unittest.TestCase):
    """회귀: 검색 색인을 노드 '개수'로 무효화해, 개수가 같은 변경(개명)을 놓쳤다.

    갱신 뒤 재착지가 조용히 빈 결과를 냈다 — 그래프는 최신인데 색인만 옛것이었다.
    """

    def _graph(self) -> Graph:
        g = Graph()
        g.add_node("r", "repo", "r", "r", "/r")
        g.add_node("r:a.py", "file", "a.py", "r", "a.py")
        g.add_node("r:a.py#oldname", "function", "oldname", "r", "a.py", 1)
        return g

    def test_rename_keeping_node_count_is_visible(self):
        g = self._graph()
        self.assertTrue(search(g, "oldname", k=3))       # 색인 워밍업
        del g.nodes["r:a.py#oldname"]
        g.touch()                                        # 메서드 밖 변경 → 명시적 무효화
        g.add_node("r:a.py#brandnew", "function", "brandnew", "r", "a.py", 1)
        self.assertEqual(len(g.nodes), 3)                # 개수는 그대로 — 옛 방식이면 못 잡는다
        self.assertFalse(search(g, "oldname", k=3), "사라진 심볼이 계속 검색된다")
        self.assertTrue(search(g, "brandnew", k=3), "새 심볼이 검색에 안 잡힌다")

    def test_rev_moves_on_content_change(self):
        g = self._graph()
        before = g.rev
        g.add_node("r:a.py#more", "function", "more", "r", "a.py", 2)
        self.assertGreater(g.rev, before)
        mid = g.rev
        g.add_edge("r:a.py", "r:a.py#more", "contains")
        self.assertGreater(g.rev, mid)

    def test_refresh_files_invalidates_index(self):
        """실제 증분 갱신 경로에서도 색인이 갈린다(파생 캐시가 손으로 안 꺼져도)."""
        from xgen_maker.kg.build import build_repo, refresh_files
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "m.py").write_text("def before_name():\n    return 1\n", encoding="utf-8")
            subprocess.run(["git", "init", "-q"], cwd=root, capture_output=True)
            g = build_repo("r", root)
            self.assertTrue(search(g, "before_name", k=3))     # 색인 워밍업
            # 심볼 하나를 개명 — 노드 수는 그대로
            (root / "m.py").write_text("def after_name():\n    return 1\n", encoding="utf-8")
            refresh_files(g, "r", root, ["m.py"])
            self.assertTrue(search(g, "after_name", k=3), "갱신된 심볼이 검색에 안 잡힌다")


class TestEngineCancelReachesAgent(unittest.TestCase):
    """회귀: 웹을 엔진 경유로 돌리면서 취소 신호가 끊겼다.

    수렴 루프는 getattr(journal, "cancelled", None)로 집어간다. 다리 저널이 그 메서드를
    빠뜨리면 중지 버튼이 단계 경계에서만 듣고, 오래 도는 에이전트는 타임아웃까지
    레포를 계속 고친다.
    """

    def test_bridge_journal_exposes_cancelled(self):
        import inspect
        from xgen_maker.engine_stage import build_maker_stage
        src = inspect.getsource(build_maker_stage)
        self.assertIn("def cancelled", src,
                      "_BridgeJournal이 cancelled()를 노출하지 않으면 중지가 에이전트에 안 닿는다")

    def test_converge_reads_cancel_from_journal(self):
        import inspect
        from xgen_maker.loop import converge
        self.assertIn("should_cancel", inspect.getsource(converge))


class TestFailureOutcomesAreComplete(unittest.TestCase):
    """회귀: 테스트 실패(checks_failed)·인가 실패(unauthorized)에도 CLI가 exit 0이었다.

    실패 문자열을 호출부가 각자 나열하면 새 결과가 생겼을 때 조용히 빠진다.
    """

    def test_known_failure_outcomes_are_all_listed(self):
        from xgen_maker.codes import FAILURE_OUTCOMES, Outcome
        for name in ("CHECKS_FAILED", "UNAUTHORIZED", "JUDGE_FAILED",
                     "BRANCH_FAILED", "IMPLEMENT_FAILED", "PUSH_FAILED", "NO_LANDING"):
            self.assertIn(getattr(Outcome, name).value, FAILURE_OUTCOMES)

    def test_success_outcomes_are_not_failures(self):
        from xgen_maker.codes import FAILURE_OUTCOMES, Outcome
        for value in (Outcome.ANSWERED.value, Outcome.PLANNED.value,
                      "committed_local", "mr_created"):
            self.assertNotIn(value, FAILURE_OUTCOMES)

    def test_cli_uses_the_shared_set(self):
        source = Path("xgen_maker/cli.py").read_text(encoding="utf-8")
        self.assertIn("FAILURE_OUTCOMES", source)
        self.assertNotIn('"judge_failed", "branch_failed"', source,
                         "실패 목록을 CLI에 다시 나열하면 또 어긋난다")


class TestUndoUsesRealRef(unittest.TestCase):
    """회귀: 되돌리기가 화면용 문자열("develop(최신)")을 git checkout에 넘겨 죽었다."""

    def test_branch_event_base_is_a_real_ref(self):
        source = Path("xgen_maker/loop/pipeline.py").read_text(encoding="utf-8")
        self.assertIn("base_label=", source, "꾸민 이름은 별도 필드에 담아야 한다")
        self.assertNotIn('base=(f"{config.target_branch}(최신)"', source,
                         "base에 표시용 문자열을 넣으면 되돌리기가 checkout에 실패한다")

    def test_rollback_prefers_checked_out(self):
        source = Path("xgen_maker/loop/rollback.py").read_text(encoding="utf-8")
        self.assertIn('branch_ev.get("checked_out")', source)


class TestIncrementalRefreshKeepsWorkspaceEdges(unittest.TestCase):
    """회귀: 증분 갱신이 TS 해석기를 안 써서, 파일이 바뀔 때마다 별칭·워크스페이스
    import 엣지가 지워지고 다시 생기지 않았다(동기화할수록 닳는다)."""

    def test_workspace_import_edge_survives_refresh(self):
        import json
        from xgen_maker.kg.build import build_repo, refresh_files
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "packages" / "ui" / "src").mkdir(parents=True)
            (root / "apps" / "web" / "src").mkdir(parents=True)
            (root / "pnpm-workspace.yaml").write_text(
                "packages:\n  - 'packages/*'\n", encoding="utf-8")
            (root / "packages" / "ui" / "package.json").write_text(
                json.dumps({"name": "@acme/ui"}), encoding="utf-8")
            (root / "packages" / "ui" / "src" / "index.ts").write_text(
                "export function Button(){}\n", encoding="utf-8")
            page = root / "apps" / "web" / "src" / "page.tsx"
            page.write_text("import { Button } from '@acme/ui'\n"
                            "export function Page(){ return Button() }\n", encoding="utf-8")
            subprocess.run(["git", "init", "-q"], cwd=root, capture_output=True)
            g = build_repo("fe", root)

            def workspace_edges():
                return [e for e in g.edges
                        if e["kind"] == "imports" and "feature:" in e["dst"]]

            self.assertTrue(workspace_edges(), "빌드가 워크스페이스 import를 안 잡았다")
            refresh_files(g, "fe", root, ["apps/web/src/page.tsx"])
            self.assertTrue(workspace_edges(),
                            "증분 갱신이 워크스페이스 import 엣지를 지우고 복구하지 않았다")


class TestJsonExtractionIsNotGreedy(unittest.TestCase):
    """회귀: `{.*}` 탐욕 매칭이라 JSON 앞뒤에 중괄호가 섞이면 통째로 파싱 실패했다.

    이 파싱이 실패하면 착지 어휘 변환이 날아가고 검색이 엉뚱한 서비스로 샌다.
    """

    def test_extracts_first_balanced_object(self):
        from xgen_maker.llm import _first_json_object
        self.assertEqual(_first_json_object('{"a": 1}'), {"a": 1})
        self.assertEqual(_first_json_object('앞 {잡음} 뒤 {"a": 1}'), {"a": 1})
        self.assertEqual(_first_json_object('{"a": 1} 그리고 {잡음}'), {"a": 1})
        self.assertEqual(_first_json_object('{"a": "값에 } 포함"}'), {"a": "값에 } 포함"})
        self.assertEqual(_first_json_object('```json\n{"a": 1}\n```'), {"a": 1})

    def test_truncated_object_is_none_not_garbage(self):
        from xgen_maker.llm import _first_json_object
        self.assertIsNone(_first_json_object('{"a": 1'))
        self.assertIsNone(_first_json_object("no json here"))


class TestGraphFreshnessComparesLikeForLike(unittest.TestCase):
    """회귀: 신선도 패널이 ref 기준 기록값을 '로컬 HEAD'와 비교해 늘 낡음으로 떴다.

    항상 빨간 지표는 진짜 낡았을 때를 가려버린다.
    """

    def test_panel_compares_against_the_graph_basis(self):
        source = Path("xgen_maker/web.py").read_text(encoding="utf-8")
        self.assertIn('git_head(root, ref) if ref else git_head(root)', source,
                      "기준(ref)이 있으면 그 기준의 현재 커밋과 비교해야 한다")


class TestCommitContainsOnlyWhatWeReported(unittest.TestCase):
    """회귀: 커밋이 `add -A`로 워킹트리를 통째로 담아, 요청과 무관한 파일과 검증이
    만들어 낸 부산물까지 MR에 들어갔다(실측: 바이트코드 2개). 화면에는 "N개 파일
    수정"이라 떠 있는데 실제 커밋은 그보다 많았다 — 리뷰어가 먼저 보는 게 쓰레기가 된다.
    """

    def _repo(self, root: Path) -> None:
        for args in (["init", "-q", "-b", "main"],
                     ["config", "user.email", "t@t.local"],
                     ["config", "user.name", "t"]):
            subprocess.run(["git", *args], cwd=root, capture_output=True, check=True)
        (root / "keep.py").write_text("x = 1\n", encoding="utf-8")
        (root / "gone.py").write_text("y = 2\n", encoding="utf-8")
        subprocess.run(["git", "add", "-A"], cwd=root, capture_output=True, check=True)
        subprocess.run(["git", "commit", "-qm", "init"], cwd=root, capture_output=True, check=True)

    def test_build_artifacts_stay_out_of_the_commit(self):
        from xgen_maker.loop.git_ops import GitRepo
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._repo(root)
            (root / "keep.py").write_text("x = 2\n", encoding="utf-8")
            # 검증(pytest)이 만들어 내는 부산물 — .gitignore가 없는 레포에서도 안 담겨야
            (root / "__pycache__").mkdir()
            (root / "__pycache__" / "keep.cpython-312.pyc").write_bytes(b"\x00bin")
            repo = GitRepo(root)
            self.assertEqual(repo.changed_files(), ["keep.py"])
            repo.commit_all("fix: x", "", author_name="t", author_email="t@t.local")
            names = subprocess.run(["git", "show", "--name-only", "--format=", "HEAD"],
                                   cwd=root, capture_output=True, text=True).stdout.split()
        self.assertEqual(names, ["keep.py"], "커밋에 부산물이 딸려 들어갔다")

    def test_add_modify_delete_all_staged(self):
        """부산물을 걸러내면서도 삭제·신규를 놓치면 안 된다."""
        from xgen_maker.loop.git_ops import GitRepo
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._repo(root)
            (root / "keep.py").write_text("x = 2\n", encoding="utf-8")   # 수정
            (root / "new.py").write_text("z = 3\n", encoding="utf-8")    # 신규
            (root / "gone.py").unlink()                                   # 삭제
            repo = GitRepo(root)
            repo.commit_all("chore: mix", "", author_name="t", author_email="t@t.local")
            out = subprocess.run(["git", "show", "--name-status", "--format=", "HEAD"],
                                 cwd=root, capture_output=True, text=True).stdout.split()
        self.assertIn("M", out); self.assertIn("keep.py", out)
        self.assertIn("A", out); self.assertIn("new.py", out)
        self.assertIn("D", out); self.assertIn("gone.py", out)
