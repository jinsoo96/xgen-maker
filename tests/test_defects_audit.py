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


class TestRemainingAuditFixes(unittest.TestCase):
    """감사 2차 — 화면·설정·그래프 순회에서 조용히 어긋나던 것들."""

    def test_ui_verify_switch_is_actually_read(self):
        """회귀: 대시보드의 '화면 검증' 토글이 장식이었다(코드가 안 읽어 항상 돌았다)."""
        source = Path("xgen_maker/loop/pipeline.py").read_text(encoding="utf-8")
        self.assertIn('getattr(config, "enable_ui_verify", True)', source)

    def test_isolate_worktree_is_tristate_everywhere(self):
        """회귀: 3상태(auto/on/off)를 웹이 bool로 편집해 auto를 복구할 수 없었고,
        문자열 "false"가 bool()로 참이 되어 꺼도 켜진 채 돌았다."""
        web = Path("xgen_maker/web.py").read_text(encoding="utf-8")
        self.assertIn('"choice:auto,true,false"', web)
        pipe = Path("xgen_maker/loop/pipeline.py").read_text(encoding="utf-8")
        self.assertIn('token in ("1", "true", "on", "yes")', pipe)

    def test_python_endpoint_links_handler_as_route_of(self):
        """회귀: 같은 관계를 Python은 calls, Rust는 route_of로 불러 앵커 확장이
        언어에 따라 달랐다(Python 라우트는 핸들러에 못 닿았다)."""
        from xgen_maker.kg.build import build_repo
        from xgen_maker.kg.anchor import find_anchors, expand
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "api.py").write_text(
                'from fastapi import APIRouter\nrouter = APIRouter()\n\n'
                '@router.get("/users/profile")\ndef read_profile():\n    return {}\n',
                encoding="utf-8")
            subprocess.run(["git", "init", "-q"], cwd=root, capture_output=True)
            g = build_repo("svc", root)
            kinds = {e["kind"] for e in g.edges if "#EP GET /users/profile" in e["src"]}
            self.assertIn("route_of", kinds)
            anchors = find_anchors(g, "/users/profile 고쳐줘")
            scope = expand(g, anchors)
            names = {(n.get("name") if isinstance(n, dict) else str(n)) for n in scope}
        self.assertTrue(any("read_profile" in str(x) for x in names),
                        "라우트를 지목했는데 핸들러가 확장 범위에 없다")

    def test_impact_crosses_package_boundary(self):
        """회귀: same_package를 안 타서 프론트 스코프 간 사용처가 안 보였다 —
        에이전트에게 건네는 '여기가 깨진다' 목록에서 통째로 빠졌다."""
        from xgen_maker.kg.search import impact
        g = Graph()
        g.add_node("lib:feature:@x/ui", "feature", "@x/ui", "lib", "")
        g.add_node("app:feature:@x/ui", "feature", "@x/ui", "app", "")
        g.add_node("lib:btn.ts#Button", "function", "Button", "lib", "btn.ts", 1)
        g.add_edge("lib:feature:@x/ui", "lib:btn.ts#Button", "contains")
        g.add_edge("app:feature:@x/ui", "lib:feature:@x/ui", "same_package")
        found = {d["id"] for d in impact(g, "lib:btn.ts#Button", depth=3)}
        self.assertTrue(any(i.startswith("app:") for i in found),
                        "다른 스코프의 사용처에 도달하지 못했다")

    def test_loop_reapplies_overlay_after_refresh(self):
        """회귀: 루프 사후 갱신이 오버레이를 다시 안 씌워, 사람이 단 deprecated
        표시가 사라지고 바로 다음 질의가 그 자리로 착지할 수 있었다."""
        source = Path("xgen_maker/loop/pipeline.py").read_text(encoding="utf-8")
        after = source.split("⑩ 사후", 1)[1][:900]
        self.assertIn("apply_overlay", after, "사후 갱신 뒤 오버레이 재적용이 없다")

    def test_retry_feedback_includes_veto_reason(self):
        """회귀: veto(빈 diff·인프라 파일)는 reasons와 별도 필드라, 재시도 프롬프트에
        사유가 빈 채로 나가 에이전트가 같은 실수를 반복했다."""
        source = Path("xgen_maker/loop/converge.py").read_text(encoding="utf-8")
        self.assertIn('judge_result.get("veto")', source)

    def test_history_ignores_failed_branch_event(self):
        source = Path("xgen_maker/loop/history.py").read_text(encoding="utf-8")
        self.assertIn('e.get("status") == "ok"', source)

    def test_no_phantom_resource_guard(self):
        """회귀: 스택을 자동 기동하지 않는데 '가드가 막는다'고 적힌 죽은 함수가 있었다."""
        import xgen_maker.loop.verify as verify_mod
        self.assertFalse(hasattr(verify_mod, "docker_guard"))
        self.assertNotIn("추가 기동을 거부한다", verify_mod.__doc__ or "")


class TestTokenNeverLeavesItsHost(unittest.TestCase):
    """회귀: 저장소 원격이 어느 호스트인지 보지 않고 토큰을 붙였다.

    실측 — GitLab 토큰이 github.com 원격으로 전송됐다. 인증 실패는 눈에 띄지만
    (fetch가 조용히 skip됐다) 시크릿이 남의 서버에 남는 건 아무도 못 본다.
    """

    def _repo(self, tmp: str, origin: str):
        from xgen_maker.loop.git_ops import GitRepo
        root = Path(tmp)
        for args in (["init", "-q", "-b", "main"],
                     ["config", "user.email", "t@t.local"],
                     ["config", "user.name", "t"],
                     ["remote", "add", "origin", origin]):
            subprocess.run(["git", *args], cwd=root, capture_output=True, check=True)
        (root / "f.txt").write_text("x", encoding="utf-8")
        subprocess.run(["git", "add", "-A"], cwd=root, capture_output=True, check=True)
        subprocess.run(["git", "commit", "-qm", "i"], cwd=root, capture_output=True, check=True)
        return GitRepo(root)

    def test_matching_host_uses_token(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = self._repo(tmp, "https://git.example.com/g/p.git")
            self.assertTrue(repo.token_host_matches("https://git.example.com"))

    def test_other_host_never_gets_the_token(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = self._repo(tmp, "https://github.com/org/p.git")
            self.assertFalse(repo.token_host_matches("https://git.example.com"),
                             "다른 호스트 원격에 토큰을 붙이면 시크릿이 새어 나간다")

    def test_push_to_other_host_builds_no_auth_url(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = self._repo(tmp, "https://github.com/org/p.git")
            repo.create_branch("fix/host-check")
            seen = []
            original = repo._run

            def spy(*args, **kw):
                seen.append(args)
                return "" if "push" in args else original(*args, **kw)

            repo._run = spy
            repo.push("fix/host-check", token="SECRET", token_host="https://git.example.com")
        joined = " ".join(" ".join(c) for c in seen if "push" in c)
        self.assertNotIn("SECRET", joined, "다른 호스트로 가는 명령에 토큰이 들어갔다")


class TestPerRepoTargetBranch(unittest.TestCase):
    """회귀: 통합 브랜치를 전역 설정 하나로 강요해, 그 브랜치가 없는 저장소에서
    최신 받기가 실패하고 MR 초안이 존재하지 않는 브랜치를 대상으로 적혔다."""

    def _repo(self, tmp: str, default_branch: str):
        from xgen_maker.loop.git_ops import GitRepo
        root = Path(tmp) / "r"
        origin = Path(tmp) / "origin"
        for args in (["init", "-q", "-b", default_branch, str(origin)],):
            subprocess.run(["git", *args], capture_output=True, check=True)
        subprocess.run(["git", "config", "user.email", "t@t"], cwd=origin, capture_output=True)
        subprocess.run(["git", "config", "user.name", "t"], cwd=origin, capture_output=True)
        (origin / "a.txt").write_text("x", encoding="utf-8")
        subprocess.run(["git", "add", "-A"], cwd=origin, capture_output=True, check=True)
        subprocess.run(["git", "commit", "-qm", "i"], cwd=origin, capture_output=True, check=True)
        subprocess.run(["git", "clone", "-q", str(origin), str(root)], capture_output=True, check=True)
        return GitRepo(root)

    def test_falls_back_to_repo_default_when_preferred_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = self._repo(tmp, "main")
            self.assertEqual(repo.resolve_target_branch("develop"), "main")

    def test_keeps_preferred_when_it_exists(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = self._repo(tmp, "develop")
            self.assertEqual(repo.resolve_target_branch("develop"), "develop")

    def test_pipeline_uses_resolved_branch_everywhere(self):
        source = Path("xgen_maker/loop/pipeline.py").read_text(encoding="utf-8")
        body = source.split("resolve_target_branch", 1)[1]
        self.assertNotIn("config.target_branch, config)", body,
                         "릴리즈 뷰가 아직 설정값을 그대로 쓴다")
        self.assertIn("build_mr_draft(query, intent, branch, target_branch", source)


class TestUiVerifyDefaultsOn(unittest.TestCase):
    """회귀: 스위치를 읽게 고치면서 기본값이 off라 화면 검증이 통째로 꺼졌다.

    볼 화면이 없으면 ui_verify가 알아서 사유를 남기고 건너뛰므로, 기본은 켜 두는 게
    "화면도 봐야 한다"는 요구에 맞다.
    """

    def test_default_is_on(self):
        from xgen_maker.config import MakerConfig
        self.assertTrue(MakerConfig().enable_ui_verify)


class TestMrNeverGoesToTheWrongHost(unittest.TestCase):
    """회귀: MR 생성이 설정 매핑만 보고 호스트를 안 봤다.

    저장소 하나가 다른 호스트(GitHub)에 살고 있었는데 gitlab_projects에는 들어 있어,
    act 모드였다면 GitLab에 그 이름으로 MR을 만들려 하고 토큰까지 함께 나갔을 것이다.
    같은 이름의 프로젝트가 거기 있으면 엉뚱한 저장소에 MR이 열린다 — 실패보다 나쁘다.
    """

    def _repo(self, tmp: str, origin: str):
        root = Path(tmp)
        for args in (["init", "-q", "-b", "main"],
                     ["config", "user.email", "t@t"], ["config", "user.name", "t"],
                     ["remote", "add", "origin", origin]):
            subprocess.run(["git", *args], cwd=root, capture_output=True, check=True)
        return root

    def test_other_host_repo_is_refused_before_any_request(self):
        from xgen_maker.config import MakerConfig
        from xgen_maker.loop.mr import create_gitlab_mr
        with tempfile.TemporaryDirectory() as tmp:
            root = self._repo(tmp, "https://github.com/org/p.git")
            cfg = MakerConfig(gitlab_url="https://git.example.com",
                              gitlab_projects={"p": "grp/p"},
                              repos={"p": str(root)})
            result = create_gitlab_mr(cfg, "p", "fix/x", "t", "b",
                                      target_branch="main", repo_root=str(root))
        self.assertFalse(result["ok"])
        self.assertIn("이 GitLab이 아닙니다", result["error"])

    def test_mr_targets_the_resolved_branch(self):
        source = Path("xgen_maker/loop/mr.py").read_text(encoding="utf-8")
        self.assertIn('"target_branch": target_branch or config.target_branch', source,
                      "MR 대상이 전역 설정값으로 고정되면 develop 없는 저장소에서 틀린다")
        pipe = Path("xgen_maker/loop/pipeline.py").read_text(encoding="utf-8")
        self.assertIn("target_branch=target_branch", pipe)


class TestDomainWordDoesNotHijackRepo(unittest.TestCase):
    """회귀: 질의 단어가 저장소 이름과 겹치면 그 저장소로 검색이 쏠렸다.

    실측 — "harness bridge judge"가 6/6 xgen-harness-executor로 나오고, 정작 경로가
    harness_bridge/ 인 다른 저장소 코드는 하나도 안 나왔다. 도메인 용어가 우연히
    저장소 이름이라는 이유로 진짜 답이 통째로 가려진다.
    """

    def _graph(self) -> Graph:
        g = Graph()
        # 이름에 'widget'이 든 저장소 — 관련 코드가 많다
        for i in range(12):
            g.add_node(f"widget-svc:core/w{i}.py#widget_helper_{i}", "function",
                       f"widget_helper_{i}", "widget-svc", f"core/w{i}.py", 1)
        # 다른 저장소인데 경로가 바로 widget_bridge/ — 질의 세 단어를 다 가진다
        g.add_node("app-svc:widget_bridge/relay.py#widget_bridge_relay", "function",
                   "widget_bridge_relay", "app-svc", "widget_bridge/relay.py", 1)
        return g

    def test_path_carrier_beats_name_only_repo(self):
        g = self._graph()
        top = search(g, "widget bridge relay", k=1)[0]
        self.assertEqual(top["repo"], "app-svc",
                         "경로에 그 단어를 직접 가진 코드가 이름만 겹친 저장소에 밀렸다")

    def test_exclusivity_is_measured_from_the_graph(self):
        from xgen_maker.kg.search import _index
        g = self._graph()
        idx = _index(g)
        # 'widget'은 두 저장소에 걸쳐 있으므로 배타성이 1.0보다 작아야 한다
        self.assertLess(idx.token_exclusivity.get("widget", 1.0), 1.0)
        self.assertGreater(idx.token_exclusivity.get("widget", 0.0), 0.0)


class TestOnlyRelatedTestsRun(unittest.TestCase):
    """회귀: 큰 저장소는 전체 스위트가 타임아웃돼 검증이 통째로 안 돌았다.

    한 파일 주석 정리에 600초를 쓰고도 'unverified'로 끝났다 — 검증했다고 말할 수
    없는 상태가 조용히 지나간다.
    """

    def test_picks_tests_by_name_and_by_import(self):
        from xgen_maker.loop.testenv import related_tests
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "pkg").mkdir(parents=True)
            (root / "tests").mkdir()
            (root / "pkg" / "payments.py").write_text("def charge(): pass\n", encoding="utf-8")
            (root / "pkg" / "unrelated.py").write_text("x = 1\n", encoding="utf-8")
            # 이름으로 닮은 것
            (root / "tests" / "test_payments.py").write_text("def test_a(): pass\n",
                                                             encoding="utf-8")
            # import 로 실제로 쓰는 것(이름은 안 닮았다)
            (root / "tests" / "test_flow.py").write_text(
                "from pkg.payments import charge\ndef test_b(): pass\n", encoding="utf-8")
            # 무관한 것
            (root / "tests" / "test_other.py").write_text("def test_c(): pass\n",
                                                          encoding="utf-8")
            got = related_tests(root, ["pkg/payments.py"])
        self.assertIn("tests/test_payments.py", got)
        self.assertIn("tests/test_flow.py", got)
        self.assertNotIn("tests/test_other.py", got)

    def test_no_match_falls_back_to_whole_suite(self):
        from xgen_maker.loop.testenv import related_tests
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "tests").mkdir(parents=True)
            (root / "tests" / "test_x.py").write_text("def test(): pass\n", encoding="utf-8")
            self.assertEqual(related_tests(root, ["totally/unrelated.py"]), [],
                             "못 찾으면 빈 리스트여야 전체 스위트로 폴백한다")


class TestPartialVerificationIsNotClaimedAsFull(unittest.TestCase):
    """회귀: 관련 테스트만 돌리도록 바꾸면서 결과 라벨은 'verified'로 두었다.

    MR 초안에는 "레포 테스트 스위트가 실제로 통과(레거시 안 깨짐 확인)"이라 적힌다 —
    실제로는 몇 개만 돌았는데 전체를 보증하는 문장이 나간다. 과대 주장이다.
    """

    def test_related_only_is_partial_not_verified(self):
        from xgen_maker.loop.testing import regression_verdict
        self.assertEqual(regression_verdict(
            [{"name": "pytest", "status": "passed", "scope": "related"}]), "partial")

    def test_full_suite_is_verified(self):
        from xgen_maker.loop.testing import regression_verdict
        self.assertEqual(regression_verdict(
            [{"name": "pytest", "status": "passed", "scope": "full"}]), "verified")

    def test_failure_still_wins(self):
        from xgen_maker.loop.testing import regression_verdict
        self.assertEqual(regression_verdict(
            [{"name": "pytest", "status": "failed", "scope": "related"}]), "failed")

    def test_mr_draft_says_partial_is_not_a_guarantee(self):
        from xgen_maker.loop.mr import build_mr_draft
        _, body = build_mr_draft("q", "refactor", "b", "develop", ["a.py"], "",
                                 [], {"score": 1.0, "theta": 0.7, "source": "heuristic"},
                                 regression="partial")
        self.assertIn("부분 검증", body)
        self.assertIn("보증하지 않음", body)


class TestFusionActuallyMerges(unittest.TestCase):
    """회귀: '병합'이라 부르면서 실제로는 한쪽이 다른 쪽을 통째로 대체했다.

    _prefer(primary, fallback, k)는 primary가 k개를 채우면 fallback이 한 자리도
    못 들어간다. 실제 머지된 MR 79건으로 재 보니 원문 검색 결과가 통째로 버려지고
    있었고, 그 결과 R@10이 0.582에 머물렀다(융합 시 0.658).
    """

    def _hits(self, prefix, n):
        return [{"id": f"{prefix}{i}", "name": f"{prefix}{i}"} for i in range(n)]

    def test_both_sides_appear(self):
        from xgen_maker.loop.pipeline import _fuse
        got = _fuse(self._hits("a", 8), self._hits("b", 8), k=8)
        ids = [h["id"] for h in got]
        self.assertTrue(any(i.startswith("a") for i in ids))
        self.assertTrue(any(i.startswith("b") for i in ids),
                        "두 번째 목록이 한 자리도 못 들어갔다 — 병합이 아니라 대체다")

    def test_head_is_preserved_for_landing(self):
        """착지점(1위)은 코드 용어 검색이 가장 정확했다 — 앞은 그대로 둔다."""
        from xgen_maker.loop.pipeline import _fuse
        got = _fuse(self._hits("kw", 8), self._hits("raw", 8), k=8, head=2)
        self.assertEqual([h["id"] for h in got[:2]], ["kw0", "kw1"])

    def test_empty_side_is_safe(self):
        from xgen_maker.loop.pipeline import _fuse
        self.assertEqual(len(_fuse(self._hits("a", 3), [], k=8)), 3)
        self.assertEqual(len(_fuse([], self._hits("b", 3), k=8)), 3)

    def test_pipeline_uses_fusion_not_replacement(self):
        source = Path("xgen_maker/loop/pipeline.py").read_text(encoding="utf-8")
        self.assertIn("_fuse(search(self.graph, keyword_query", source,
                      "확장어 병합이 다시 대체 방식으로 돌아갔다")


class TestGatewayConfigIsAddressable(unittest.TestCase):
    """회귀: 관문 설정 파일에 file 노드가 없어 그 파일 자체를 지목할 좌표가 없었다.

    다른 추출기(py·ts·rust)는 모두 파일 노드를 만든다. 관문만 라우트 노드만 만들어서,
    "어느 모듈이 어디로 가는지 적힌 그 파일을 고쳐라"라는 요청이 착지할 곳이 없었다.
    추출기 자신도 파일 노드를 전제한 contains 엣지를 만들려다 조용히 저장소로 폴백했다.
    """

    def _repo(self, tmp: str):
        root = Path(tmp)
        (root / "config").mkdir(parents=True)
        (root / "config" / "services.yaml").write_text(
            "base_path: /api\n"
            "services:\n"
            "  billing-svc:\n"
            "    host: http://billing\n"
            "    modules: [invoice, payment]\n"
            "  audit-svc:\n"
            "    host: http://audit\n"
            "    modules: [trail]\n", encoding="utf-8")
        return root

    def test_file_node_exists_with_route_nodes(self):
        from xgen_maker.kg.extract_gateway import extract_gateway_routes
        g = Graph()
        with tempfile.TemporaryDirectory() as tmp:
            root = self._repo(tmp)
            g.add_node("gw", "repo", "gw", "gw", str(root))
            extract_gateway_routes(g, "gw", root)
        kinds = {n["kind"] for n in g.nodes.values()
                 if (n.get("path") or "").endswith("services.yaml")}
        self.assertIn("gateway_route", kinds)
        self.assertIn("file", kinds, "설정 파일 자체를 가리킬 좌표가 없다")

    def test_file_node_carries_registered_names(self):
        """무엇이 등록돼 있는지로도 그 파일을 찾을 수 있어야 한다."""
        from xgen_maker.kg.extract_gateway import extract_gateway_routes
        g = Graph()
        with tempfile.TemporaryDirectory() as tmp:
            root = self._repo(tmp)
            g.add_node("gw", "repo", "gw", "gw", str(root))
            extract_gateway_routes(g, "gw", root)
        node = next(n for n in g.nodes.values()
                    if n["kind"] == "file" and (n.get("path") or "").endswith("services.yaml"))
        summary = (node.get("meta") or {}).get("summary", "")
        self.assertIn("billing-svc", summary)
        self.assertIn("invoice", summary)

    def test_contains_edge_hangs_off_the_file(self):
        from xgen_maker.kg.extract_gateway import extract_gateway_routes
        g = Graph()
        with tempfile.TemporaryDirectory() as tmp:
            root = self._repo(tmp)
            g.add_node("gw", "repo", "gw", "gw", str(root))
            extract_gateway_routes(g, "gw", root)
        file_id = next(n["id"] for n in g.nodes.values()
                       if n["kind"] == "file" and (n.get("path") or "").endswith("services.yaml"))
        from_file = [e for e in g.edges if e["src"] == file_id and e["kind"] == "contains"]
        self.assertTrue(from_file, "라우트가 저장소가 아니라 그 설정 파일에 달려야 한다")


class TestFragmentAnchors(unittest.TestCase):
    """앵커는 걸릴 때 가장 강력하다 — 실측(머지된 MR)에서 걸린 건의 1위 적중이
    0.400 → 0.800. 그런데 이름이 정확히 같아야만 걸려서 13%에서만 발동했다.

    사람은 `parse_retry_context`라고 쓰지 않고 "retry_context 추가"라고 쓴다.
    그 개념을 다루는 함수가 바로 옆에 있는데 앵커가 안 걸렸다.
    """

    def _graph(self) -> Graph:
        g = Graph()
        g.add_node("r", "repo", "r", "r", "/r")
        for name in ("parse_retry_context", "build_retry_context", "sanitize_retry_context"):
            g.add_node(f"r:retry.py#{name}", "function", name, "r", "retry.py", 1)
        # 흔한 조각 — 많은 심볼이 공유하면 지목이 아니다
        for i in range(20):
            g.add_node(f"r:h{i}.py#handle_request_{i}", "function", f"handle_request_{i}",
                       "r", f"h{i}.py", 1)
        return g

    def test_fragment_shared_by_few_symbols_anchors(self):
        from xgen_maker.kg.anchor import find_anchors
        got = find_anchors(self._graph(), "retry_context 필드 추가")
        names = {n["name"] for n in got}
        self.assertIn("parse_retry_context", names)
        self.assertIn("build_retry_context", names)

    def test_common_fragment_does_not_anchor(self):
        """조각이 많은 심볼에 걸리면 범위를 못 좁히므로 지목으로 보지 않는다."""
        from xgen_maker.kg.anchor import find_anchors
        self.assertEqual(find_anchors(self._graph(), "handle_request 정리"), [])

    def test_exact_match_still_wins(self):
        """완전일치가 있으면 조각으로 넓히지 않는다 — 정밀도가 우선."""
        from xgen_maker.kg.anchor import find_anchors
        g = self._graph()
        g.add_node("r:exact.py#parse_retry_context", "function", "parse_retry_context",
                   "r", "exact.py", 1)
        got = find_anchors(g, "parse_retry_context 고쳐줘")
        self.assertTrue(all(n["name"] == "parse_retry_context" for n in got))

    def test_short_or_plain_words_are_not_fragments(self):
        from xgen_maker.kg.anchor import find_anchors
        g = Graph()
        g.add_node("r:a.py#update", "function", "update", "r", "a.py", 1)
        self.assertEqual(find_anchors(g, "update 처리"), [])


class TestReferencedIdentifiersAreIndexed(unittest.TestCase):
    """회귀: 그래프가 파일이 '정의한' 것만 색인해, 그 파일이 '다루는' 이름으로는
    찾을 수 없었다.

    "VectorDBContextV2 노출 바꿔줘"는 그 이름을 정의한 곳이 아니라 그 이름이 적혀 있는
    카탈로그 파일을 가리킨다. 실측 — 그 파일은 문자 그대로 그 이름을 담고 있는데
    검색에 전혀 안 잡혔다.
    """

    def test_referenced_name_is_findable(self):
        from xgen_maker.kg.build import build_repo
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "catalog.py").write_text(
                "CATALOG = {'VectorDBContextV2': {}, 'AudioStreamNode': {}}\n", encoding="utf-8")
            (root / "other.py").write_text("def unrelated():\n    return 1\n", encoding="utf-8")
            subprocess.run(["git", "init", "-q"], cwd=root, capture_output=True)
            g = build_repo("r", root)
            top = search(g, "VectorDBContextV2", k=1)
        self.assertTrue(top)
        self.assertEqual(top[0]["path"], "catalog.py",
                         "파일이 다루는 이름으로 그 파일을 못 찾는다")

    def test_defined_names_are_not_double_counted(self):
        """정의부는 심볼 노드가 이미 담는다 — refs에 또 넣으면 그 파일만 두 번 유리해진다."""
        from xgen_maker.kg.refs import collect_refs
        src = "def parse_payload():\n    return handle_response(payload_schema)\n"
        refs = collect_refs(src, {"parse_payload"})
        self.assertNotIn("parse_payload", refs)
        self.assertIn("handle_response", refs)

    def test_plain_short_words_are_not_refs(self):
        from xgen_maker.kg.refs import collect_refs
        refs = collect_refs("value = total + count\n", set())
        self.assertEqual(refs, [], "평범한 짧은 말은 식별자로 보지 않는다")

    def test_incremental_refresh_replaces_refs(self):
        """증분 갱신에서 옛 이름이 남으면 사라진 것으로도 계속 검색된다."""
        from xgen_maker.kg.build import build_repo, refresh_files
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "cat.py").write_text("CATALOG = {'VectorDBContextV2': 1}\n", encoding="utf-8")
            subprocess.run(["git", "init", "-q"], cwd=root, capture_output=True)
            g = build_repo("r", root)
            (root / "cat.py").write_text("CATALOG = {'RerankerNode': 1}\n", encoding="utf-8")
            refresh_files(g, "r", root, ["cat.py"])
            node = next(n for n in g.nodes.values()
                        if n["kind"] == "file" and n.get("path") == "cat.py")
            refs = (node.get("meta") or {}).get("refs", "")
        self.assertIn("RerankerNode", refs)
        self.assertNotIn("VectorDBContextV2", refs)


class TestIncrementalEqualsFullRebuild(unittest.TestCase):
    """증분 갱신의 유일한 정답은 '전체를 다시 만든 것과 같다'이다.

    이 성질이 깨지면 증상이 안 난다 — 그래프는 멀쩡해 보이는데 내용만 어긋나고,
    착지가 조용히 옛 코드를 가리킨다. 이번 세션에서 실제로 그런 결함을 여럿 고쳤다
    (들어오는 호출 엣지 소실, TS 해석기 누락, 참조 식별자 미갱신).
    """

    @staticmethod
    def _snap(g):
        return ({n["id"] for n in g.nodes.values()},
                {(e["src"], e["dst"], e["kind"]) for e in g.edges})

    @staticmethod
    def _git(root, *args):
        subprocess.run(["git", *args], cwd=root, capture_output=True, check=True)

    def _seed(self, root: Path):
        (root / "pkg").mkdir()
        (root / "pkg" / "a.py").write_text("def alpha():\n    return helper_one()\n",
                                           encoding="utf-8")
        (root / "pkg" / "b.py").write_text(
            "def helper_one():\n    return 1\ndef helper_two():\n    return 2\n", encoding="utf-8")
        (root / "pkg" / "gone.py").write_text("def doomed():\n    return 0\n", encoding="utf-8")
        self._git(root, "init", "-q")
        self._git(root, "config", "user.email", "t@t.local")
        self._git(root, "config", "user.name", "t")
        self._git(root, "add", "-A")
        self._git(root, "commit", "-qm", "init")

    def test_modify_add_delete_rename_all_converge(self):
        from xgen_maker.kg.build import build_repo, refresh_files
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._seed(root)
            incremental = build_repo("r", root)
            (root / "pkg" / "a.py").write_text("def alpha():\n    return helper_two()\n",
                                               encoding="utf-8")          # 수정
            (root / "pkg" / "c.py").write_text("def gamma():\n    return 3\n",
                                               encoding="utf-8")          # 신규
            (root / "pkg" / "gone.py").unlink()                           # 삭제
            (root / "pkg" / "b.py").write_text(
                "def helper_one():\n    return 1\ndef renamed_two():\n    return 2\n",
                encoding="utf-8")                                          # 개명
            refresh_files(incremental, "r", root,
                          ["pkg/a.py", "pkg/b.py", "pkg/c.py", "pkg/gone.py"])
            full = build_repo("r", root)
        self.assertEqual(self._snap(incremental), self._snap(full),
                         "증분 갱신 결과가 전체 재빌드와 다르다")

    def test_repeated_refresh_does_not_erode(self):
        """같은 파일을 여러 번 갱신해도 닳지 않아야 한다 — 한 번씩 잃으면 누적된다."""
        from xgen_maker.kg.build import build_repo, refresh_files
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._seed(root)
            incremental = build_repo("r", root)
            for i in range(10):
                (root / "pkg" / "a.py").write_text(
                    f"def alpha():\n    return helper_one()  # rev {i}\n", encoding="utf-8")
                refresh_files(incremental, "r", root, ["pkg/a.py"])
            full = build_repo("r", root)
        self.assertEqual(self._snap(incremental), self._snap(full),
                         "반복 갱신에서 그래프가 닳았다")


class TestAnchorDoesNotDiscardSearch(unittest.TestCase):
    """회귀: 지목(앵커) 결과가 검색 결과를 통째로 밀어냈다.

    확장어 병합에서 고친 것과 같은 결함이 앵커 병합에 남아 있었다. 앵커가 여덟 자리를
    다 채우면 검색이 3위로 찾아 둔 정답이 그대로 사라진다 — 실측: 실제 머지된 MR에서
    그 일이 일어났고, 그 케이스는 '못 찾음'으로 집계됐다.
    """

    def test_pipeline_fuses_anchor_results(self):
        source = Path("xgen_maker/loop/pipeline.py").read_text(encoding="utf-8")
        self.assertIn("landing = _fuse(ranked, landing", source,
                      "앵커 병합이 다시 대체 방식으로 돌아갔다")
        self.assertNotIn("landing = _prefer(ranked, landing", source)

    def test_search_result_survives_a_full_anchor_list(self):
        from xgen_maker.loop.pipeline import _fuse
        anchors = [{"id": f"anch{i}", "name": f"anch{i}"} for i in range(8)]
        found = [{"id": "real", "name": "real"}]
        merged = _fuse(anchors, found, k=8)
        self.assertIn("real", [h["id"] for h in merged],
                      "앵커가 자리를 다 채워 검색이 찾은 것이 사라졌다")

    def test_anchor_head_still_leads(self):
        """지목은 여전히 앞이다 — 정밀도를 잃자는 게 아니다."""
        from xgen_maker.loop.pipeline import _fuse
        anchors = [{"id": f"anch{i}", "name": f"anch{i}"} for i in range(8)]
        found = [{"id": f"f{i}", "name": f"f{i}"} for i in range(8)]
        merged = _fuse(anchors, found, k=8, head=2)
        self.assertEqual([h["id"] for h in merged[:2]], ["anch0", "anch1"])
