"""감사에서 나온 결함들 — 전부 "에러 없이 틀린 결과"를 내던 것들이라 테스트로 못박는다.

공통 성질: 실패해도 아무도 안 죽는다. 검색이 빈 결과를 내고, 중지 버튼이 안 듣고,
CI가 초록으로 통과하고, 되돌리기가 조용히 실패한다. 그래서 테스트가 유일한 방어선이다.
"""
import subprocess
import tempfile
import unittest
from pathlib import Path

from xgen_maker.kg.graph import Graph
from xgen_maker.kg.companions import companion_files, as_prompt_block as companion_block
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
        self.assertIn("landing = _fuse(search(self.graph, query", source,
                      "확장어 병합이 다시 대체 방식으로 돌아갔다")
        self.assertIn("search(self.graph, keyword_query", source,
                      "확장어 결과가 융합 재료에서 빠졌다")

    def test_landing_head_comes_from_the_users_own_words(self):
        """착지점은 사람이 쓴 말로 잡는다.

        큰 표본(머지된 MR 256건)에서 재 보니 1위 정확도는 원문이 확실히 낫고
        (R@1 0.371 vs 확장어 0.312), 확장어는 상위 10 안에 정답을 넣어 주는 쪽에서
        값을 한다(R@10 0.633 → 0.715). 작은 표본에서는 이게 뒤집혀 보였다.
        """
        source = Path("xgen_maker/loop/pipeline.py").read_text(encoding="utf-8")
        head = source.index("landing = _fuse(search(self.graph, query")
        tail = source.index("k=8)", head)
        self.assertIn("keyword_query", source[head:tail],
                      "융합 재료가 원문 하나뿐이면 확장어의 회수 이득이 사라진다")


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


class TestLearnedVocabularyBridge(unittest.TestCase):
    """사람이 쓰는 말과 코드가 쓰는 말이 다르다. LLM의 일반 영어로 메우면 어긋난다
    ("음성 인식"→speech recognition을 냈는데 코드는 audio다).

    그래프 자체가 병렬 코퍼스다 — 한 노드 안에 한글 요약과 영문 식별자가 함께 있다.
    그 공기(co-occurrence)로 이 코드베이스가 그 개념을 뭐라 부르는지 배운다.
    """

    def _nodes(self):
        # '전사'가 stt/transcribe와 함께 나오는 노드들 + 무관한 노드들
        nodes = []
        for i in range(4):
            nodes.append({"id": f"a{i}", "kind": "function", "name": f"stt_transcribe_{i}",
                          "repo": "r", "path": f"service/stt/transcribe_{i}.py",
                          "meta": {"summary": "전사 결과를 정제한다"}})
        for i in range(4):
            nodes.append({"id": f"b{i}", "kind": "function", "name": f"billing_invoice_{i}",
                          "repo": "r", "path": f"service/billing/invoice_{i}.py",
                          "meta": {"summary": "청구 금액을 계산한다"}})
        return nodes

    def test_learns_this_codebase_words(self):
        from xgen_maker.kg.lexicon import build_lexicon
        lex = build_lexicon(self._nodes())
        self.assertTrue(any(w in lex.get("전사", []) for w in ("stt", "transcribe")),
                        "이 코드가 '전사'를 뭐라 부르는지 못 배웠다")
        self.assertNotIn("invoice", lex.get("전사", []))

    def test_bridge_adds_code_words_for_korean(self):
        from xgen_maker.kg.lexicon import build_lexicon, bridge_terms
        lex = build_lexicon(self._nodes())
        added = bridge_terms(lex, "전사 정제 고쳐줘")
        self.assertTrue(added, "한글 질의에 코드 어휘가 안 붙었다")
        self.assertTrue(all(w.isascii() for w in added.split()))

    def test_english_query_is_untouched(self):
        from xgen_maker.kg.lexicon import build_lexicon, bridge_terms
        lex = build_lexicon(self._nodes())
        self.assertEqual(bridge_terms(lex, "fix transcribe pipeline"), "")

    def test_lexicon_follows_the_graph(self):
        """사전을 손으로 적지 않는다 — 코드가 바뀌면 사전도 바뀐다.

        대조가 있어야 배운다. 모든 노드가 같은 말을 가지면 그 말은 아무것도
        가리지 못하므로(PMI 0) 대응으로 치지 않는다 — 그게 옳은 동작이다.
        """
        from xgen_maker.kg.search import lexicon
        g = Graph()
        for i in range(4):
            g.add_node(f"r:pay{i}.py#refund_{i}", "function", f"refund_{i}", "r",
                       f"pay/refund_{i}.py", 1, summary="환불 요청을 처리한다")
        for i in range(4):
            g.add_node(f"r:auth{i}.py#login_{i}", "function", f"login_{i}", "r",
                       f"auth/login_{i}.py", 1, summary="사용자 인증을 확인한다")
        lex = lexicon(g)
        self.assertIn("환불", lex)
        self.assertTrue(any("refund" in w for w in lex["환불"]))
        self.assertFalse(any("login" in w for w in lex["환불"]))

    def test_pipeline_uses_the_learned_bridge(self):
        source = Path("xgen_maker/loop/pipeline.py").read_text(encoding="utf-8")
        self.assertIn("bridge_terms", source)


class TestTunedValuesCarryTheirEvidence(unittest.TestCase):
    """작은 표본으로 정한 값이 큰 표본에서 뒤집힌 일이 실제로 있었다.

    머지된 MR 78건에서 최적으로 보이던 길이 정규화 값이 256건에서는 더 나쁜 쪽이었다.
    그래서 이 값들은 근거(어느 표본에서 무엇을 쟀는지)를 코드에 달아 둔다 — 다음 사람이
    작은 표본으로 다시 흔들지 않게.
    """

    def test_length_normalisation_records_its_evidence(self):
        source = Path("xgen_maker/kg/rank.py").read_text(encoding="utf-8")
        self.assertIn("_B = 0.0", source)
        self.assertIn("2,412", source, "이 값을 어느 표본에서 정했는지 근거가 없다")
        self.assertIn("작은 표본으로 이 값을 정하지 말 것", source)

    def test_refs_cap_records_its_evidence(self):
        source = Path("xgen_maker/kg/refs.py").read_text(encoding="utf-8")
        self.assertIn("_MAX_REFS = 400", source)
        self.assertIn("R@10", source)

    def test_lexical_fusion_keeps_the_raw_head(self):
        """어휘끼리 합칠 때는 원문 머리 1개를 지킨다 — 큰 표본에서 R@10 0.742 → 0.746."""
        source = Path("xgen_maker/loop/pipeline.py").read_text(encoding="utf-8")
        self.assertIn("k=_LEXICAL_MATERIAL, head=1", source)

    def test_semantic_fusion_drops_the_head(self):
        """의미 검색이 섞이면 어휘 머리를 보존하지 않는다 — R@1 0.415 → 0.483.

        임베딩의 1위가 더 정확해서다. 어휘끼리 합칠 때와 반대라 헷갈리기 쉬우니
        두 규칙을 나란히 못박는다.
        """
        source = Path("xgen_maker/loop/pipeline.py").read_text(encoding="utf-8")
        self.assertIn("landing = _fuse(landing[:_LEXICAL_MATERIAL], semantic, k=8, head=0)",
                      source)
        self.assertIn("0.483", source, "근거 수치를 남긴다")

    def test_fusion_material_is_evidence_backed(self):
        """재료 개수는 훑어서 정했다 — 많이 넣을수록 좋을 것 같지만 아니다."""
        from xgen_maker.loop.pipeline import _LEXICAL_MATERIAL
        self.assertEqual(_LEXICAL_MATERIAL, 16)
        source = Path("xgen_maker/loop/pipeline.py").read_text(encoding="utf-8")
        self.assertIn("2,412", source, "어느 표본에서 정했는지 근거가 없다")


class TestLandingStaysSpecific(unittest.TestCase):
    """착지는 '고칠 자리'여야 한다 — 파일 노드에는 줄 번호가 없다.

    파일 노드가 참조 식별자까지 담게 되면서 신호가 늘었고, 가중을 올리면 상위 10 안에
    정답이 들어올 확률은 오른다. 그런데 너무 올리면 착지 1위가 파일이 되어(10%→31%)
    에이전트가 열 자리를 잃는다. 그 균형을 값과 함께 못박는다.
    """

    def test_symbols_still_outrank_their_own_file(self):
        from xgen_maker.kg.rank import _KIND_BOOST
        self.assertLess(_KIND_BOOST["file"], _KIND_BOOST["function"])
        self.assertLess(_KIND_BOOST["file"], _KIND_BOOST["class"])

    def test_file_boost_records_its_evidence(self):
        source = Path("xgen_maker/kg/rank.py").read_text(encoding="utf-8")
        self.assertIn('"file": 1.6', source)
        self.assertIn("줄 번호가 없어", source, "왜 더 올리지 않았는지 근거가 없다")

    def test_containers_are_still_not_landing_sites(self):
        """저장소·기능은 여전히 좌표가 아니다."""
        from xgen_maker.kg.rank import _KIND_BOOST
        self.assertLess(_KIND_BOOST["repo"], 1.0)
        self.assertLess(_KIND_BOOST["feature"], 1.0)


class TestConfigFilesAreAddressable(unittest.TestCase):
    """회귀: 코드만 그래프에 담아 설정·문서는 착지할 좌표가 아예 없었다.

    실제로 머지된 MR 294건을 세어 보니 그래프가 파일을 하나도 모르는 것이 32건이었고,
    그중 22건은 코드가 아닌 파일만 고친 것이었다(pyproject.toml 버전 범프, README 갱신,
    배포 스크립트, 라우팅 yaml). 그런 요청이 오면 갈 곳이 없다.
    """

    def _repo(self, tmp: str):
        root = Path(tmp)
        (root / "pyproject.toml").write_text(
            "[project]\nname = \"demo\"\nversion = \"1.2.3\"\n", encoding="utf-8")
        (root / "deploy.sh").write_text(
            "#!/bin/sh\nbuild_image() {\n  echo build\n}\n", encoding="utf-8")
        (root / "README.md").write_text("# Demo\n## 설치 방법\n", encoding="utf-8")
        (root / "app.py").write_text("def run():\n    return 1\n", encoding="utf-8")
        subprocess.run(["git", "init", "-q"], cwd=root, capture_output=True)
        return root

    def test_config_and_doc_files_become_nodes(self):
        from xgen_maker.kg.build import build_repo
        with tempfile.TemporaryDirectory() as tmp:
            g = build_repo("r", self._repo(tmp))
        paths = {n.get("path") for n in g.nodes.values() if n["kind"] == "file"}
        for expected in ("pyproject.toml", "deploy.sh", "README.md", "app.py"):
            self.assertIn(expected, paths, f"{expected}에 착지할 좌표가 없다")

    def test_config_keys_are_searchable(self):
        """무엇이 적혀 있는지로도 그 파일을 찾을 수 있어야 한다."""
        from xgen_maker.kg.build import build_repo
        with tempfile.TemporaryDirectory() as tmp:
            g = build_repo("r", self._repo(tmp))
            top = search(g, "version project", k=1)
        self.assertEqual(top[0]["path"], "pyproject.toml")

    def test_config_list_values_are_searchable(self):
        """라우팅 설정에서 '어느 모듈이 등록됐나'의 답은 키가 아니라 리스트 값이다.

        실측: 실제 services.yaml에서 키만 담으면 20개, 값까지 담으면 69개가 잡히고
        모듈명(admin 등)은 값 쪽에만 있다. 사람은 그 모듈명으로 묻는다.
        """
        from xgen_maker.kg.extract_config import _names
        got = _names("services.yaml",
                     "services:\n  core:\n    modules:\n      - admin\n      - ocr\n"
                     "    mode: passthrough\n    debug: true\n")
        self.assertIn("admin", got)
        self.assertIn("ocr", got)
        self.assertIn("passthrough", got)
        self.assertNotIn("true", got, "불리언은 이름이 아니다")

    def test_dependency_names_are_searchable(self):
        """의존성 갱신 요청은 패키지 이름으로 온다 — 그 이름은 키가 아니라 값에 있다.

        총합 지표는 거의 안 움직였다(MRR +0.002). 그래도 남긴다 — 이름이 색인에
        없으면 그 요청은 어떤 순위 조정으로도 그 파일에 닿을 수 없다.
        """
        from xgen_maker.kg.extract_config import _names
        got = _names("pyproject.toml",
                     '[project]\nname = "svc"\ndependencies = [\n'
                     '  "orjson==3.11.9",\n  "python-dotenv>=1.2",\n]\n')
        self.assertIn("orjson", got)
        self.assertIn("python-dotenv", got)
        self.assertNotIn("orjson==3.11.9", got, "버전 지정자는 이름이 아니다")

    def test_generated_lockfiles_do_not_flood_the_index(self):
        """큰 생성물은 이름만 남긴다 — 색인을 지배하면 안 된다."""
        from xgen_maker.kg.extract_config import extract_config_file, _MAX_BYTES
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "huge.json").write_text('{"k": 1}' + "x" * (_MAX_BYTES + 10),
                                            encoding="utf-8")
            g = Graph()
            g.add_node("r", "repo", "r", "r", str(root))
            extract_config_file(g, "r", root, "huge.json")
            node = next(n for n in g.nodes.values() if n["kind"] == "file")
        self.assertNotIn("refs", node.get("meta") or {})

    def test_incremental_sync_covers_config(self):
        """빌드가 담는 확장자를 sync가 안 보면 그 파일은 조용히 낡는다."""
        from xgen_maker.kg.sync import _relevant
        got = _relevant({"a.py", "conf/services.yaml", "pyproject.toml",
                         "README.md", "logo.png"}, None)
        self.assertIn("conf/services.yaml", got)
        self.assertIn("pyproject.toml", got)
        self.assertNotIn("logo.png", got)


class CompanionFilesTest(unittest.TestCase):
    """MR은 파일 하나로 끝나지 않는다 — 이어진 파일을 에이전트가 봐야 한다."""

    def _graph(self):
        g = Graph()
        g.add_node("r", "repo", "r", "r", "")
        for path in ("api/view.py", "core/service.py", "core/model.py", "far/away.py"):
            g.add_node(f"r:{path}", "file", path.split("/")[-1], "r", path)
            g.add_edge("r", f"r:{path}", "contains")
        g.add_edge("r:api/view.py", "r:core/service.py", "imports")
        g.add_edge("r:core/service.py", "r:core/model.py", "calls")
        return g

    def test_neighbors_of_landing_are_offered(self):
        got = companion_files(self._graph(), [{"path": "api/view.py", "id": "r:api/view.py"}])
        self.assertEqual([c["path"] for c in got], ["core/service.py"])
        self.assertEqual(got[0]["relations"], ["imports"])

    def test_landed_files_are_not_repeated(self):
        landing = [{"path": "api/view.py"}, {"path": "core/service.py"}]
        got = {c["path"] for c in companion_files(self._graph(), landing)}
        self.assertNotIn("api/view.py", got)
        self.assertIn("core/model.py", got, "착지 밖 이웃은 나와야 한다")

    def test_repo_containment_is_not_a_neighbor(self):
        """contains를 이웃으로 세면 같은 저장소 전체가 이웃이 된다."""
        got = {c["path"] for c in companion_files(self._graph(), [{"path": "api/view.py"}])}
        self.assertNotIn("far/away.py", got)

    def test_cap_is_honored(self):
        g = Graph()
        g.add_node("r:hub.py", "file", "hub.py", "r", "hub.py")
        for i in range(30):
            g.add_node(f"r:n{i}.py", "file", f"n{i}.py", "r", f"n{i}.py")
            g.add_edge("r:hub.py", f"r:n{i}.py", "imports")
        self.assertEqual(len(companion_files(g, [{"path": "hub.py"}], cap=5)), 5)

    def test_prompt_block_does_not_order_edits(self):
        """'고쳐라'가 아니라 '이어져 있다'는 단서다 — 지시로 읽히면 무관한 파일을 건드린다."""
        block = companion_block([{"path": "core/service.py", "repo": "r",
                                  "relations": ["imports"], "score": 1.0}])
        self.assertIn("core/service.py", block)
        self.assertIn("고쳐야 한다는 뜻이 아니다", block)


class RepoSizeDampingWasSampleBiasTest(unittest.TestCase):
    """다시 하지 말 것: 크기 보정은 표본 치우침을 메우던 값이었다.

    IDF는 코퍼스 전체에서 재므로 노드가 많은 저장소는 어떤 점수대에서든 뽑힐 기회가
    많다. 실측에서 노드 35.1%인 저장소가 정답은 12.5%인데 1위는 28.7%였다.
    """

    def _graph(self):
        g = Graph()
        for repo, count in (("big", 60), ("small", 2)):
            g.add_node(repo, "repo", repo, repo, "")
            for i in range(count):
                path = f"mod{i}/handler.py"
                g.add_node(f"{repo}:{path}#upload_document_handler",
                           "function", "upload_document_handler", repo, path, 1)
        return g

    def test_the_knob_is_gone(self):
        """다시 하지 말 것 — 쓰지 않는 손잡이를 남기면 다음 사람이 다시 켠다."""
        import xgen_maker.kg.rank as rank_mod
        self.assertFalse(hasattr(rank_mod, "_REPO_SIZE_DAMP"))

    def test_the_reason_is_recorded(self):
        """294건에서는 이득이었다 — 그 표본이 작은 저장소에 치우쳐 있었을 뿐이다."""
        source = Path("xgen_maker/kg/rank.py").read_text(encoding="utf-8")
        self.assertIn("표본 치우침을 메우던 값", source)
        self.assertIn("2,412", source)


class ClaudeCliPromptSurvivesNewlinesTest(unittest.TestCase):
    """회귀: 여러 줄 프롬프트가 첫 줄만 모델에 닿았다.

    Windows에서 claude는 .CMD 심이라 cmd /c 를 거치는데, cmd는 인자 안의 줄바꿈에서
    잘라 버린다. 에러도 없고 응답도 그럴듯해서 조용히 틀린다 — 판정 LLM이
    "[request] 한 줄"만 보고 diff 없이 점수를 매기고 있었고, 코드 요약도 첫 줄만 봤다.
    """

    def _capture(self, messages):
        import xgen_maker.llm as llm_mod
        seen = {}

        class _Result:
            returncode, stdout, stderr = 0, '{"ok": 1}', ""

        def fake_run(command, **kw):
            seen["command"] = command
            seen["input"] = kw.get("input")
            return _Result()

        original = llm_mod.subprocess.run
        llm_mod.subprocess.run = fake_run
        try:
            llm_mod._chat_claude_cli(messages, timeout=5)
        finally:
            llm_mod.subprocess.run = original
        return seen

    def test_multiline_user_prompt_goes_through_stdin(self):
        body = "[request]\nfix the thing\n\n[diff]\n- a\n+ b"
        seen = self._capture([{"role": "user", "content": body}])
        if not seen:
            self.skipTest("claude CLI 없음")
        self.assertEqual(seen["input"], body, "본문이 stdin으로 안 갔다")
        for arg in seen["command"]:
            self.assertNotIn("\n", arg, "줄바꿈이 있는 인자는 cmd에서 잘린다")

    def test_multiline_system_prompt_is_not_passed_as_argument(self):
        seen = self._capture([{"role": "system", "content": "one\ntwo"},
                              {"role": "user", "content": "q"}])
        if not seen:
            self.skipTest("claude CLI 없음")
        self.assertNotIn("--system-prompt", seen["command"])
        self.assertIn("one\ntwo", seen["input"], "여러 줄 system이 통째로 사라졌다")

    def test_single_line_system_still_overrides_agent_prompt(self):
        """한 줄이면 --system-prompt로 넘겨야 한다 — 기본 에이전트 프롬프트 대체가 목적."""
        seen = self._capture([{"role": "system", "content": "reply json only"},
                              {"role": "user", "content": "q"}])
        if not seen:
            self.skipTest("claude CLI 없음")
        self.assertIn("--system-prompt", seen["command"])

    def test_judge_prompt_is_actually_multiline(self):
        """판정 프롬프트가 여러 줄이라는 사실 자체를 못박는다 — 이 결함의 진입점이었다."""
        source = Path("xgen_maker/loop/judge.py").read_text(encoding="utf-8")
        self.assertIn("[diff (truncated)]", source)
        self.assertIn("\n", source, "판정 프롬프트는 줄바꿈으로 구획을 나눈다")


class CostCountsEveryLlmCallTest(unittest.TestCase):
    """회귀: CostTracker.add_llm이 정의만 되고 아무도 안 불렀다.

    비용 화면은 코딩 에이전트분만 세고 어휘변환·의도분류·판정의 LLM 호출은 전부
    빠져 있었다. 숫자가 있으니 맞는 줄 알지, 없는 줄은 모른다.
    """

    def test_judge_records_its_llm_call(self):
        from xgen_maker.config import MakerConfig
        from xgen_maker.loop.cost import CostTracker
        from xgen_maker.loop import judge as judge_mod
        cfg = MakerConfig()
        cfg.llm_enabled = True
        tracker = CostTracker()
        original = judge_mod.llm.json_chat
        judge_mod.llm.json_chat = lambda *a, **k: {"score": 0.8, "reasons": ["ok"]}
        try:
            judge_mod.judge(cfg, "q", "diff --git a b\n+x", ["a.py"], cost=tracker)
        finally:
            judge_mod.llm.json_chat = original
        self.assertEqual(tracker.llm_calls, 1, "판정의 LLM 호출이 비용에 안 잡힌다")
        self.assertGreater(tracker.est_input, 0)

    def test_intent_records_its_llm_call(self):
        from xgen_maker.loop.cost import CostTracker
        from xgen_maker.loop import intent as intent_mod
        tracker = CostTracker()
        original = intent_mod.llm.json_chat
        intent_mod.llm.json_chat = lambda *a, **k: {"intent": "bug"}
        try:
            # 휴리스틱이 못 가리는 문장이어야 LLM 경로로 간다
            intent_mod.classify("그거", "claude_cli", "cli", cost=tracker)
        finally:
            intent_mod.llm.json_chat = original
        self.assertEqual(tracker.llm_calls, 1, "의도 분류의 LLM 호출이 비용에 안 잡힌다")

    def test_pipeline_records_the_expansion_call(self):
        source = Path("xgen_maker/loop/pipeline.py").read_text(encoding="utf-8")
        self.assertIn("cost.add_llm", source, "어휘 변환 호출이 비용에 안 잡힌다")
        self.assertIn("cost=cost", source)


class RepoHintGuidesButDoesNotFilterTest(unittest.TestCase):
    """바깥 라우팅 신호(LLM이 고른 저장소)는 절반쯤만 맞다 — 거르면 안 된다.

    실측(실제 머지된 MR 265건): 검색 1위 저장소 적중 52.1% · LLM 47.9%.
    서로 다르게 틀린다(LLM만 맞힘 19.6% · 검색만 맞힘 23.8%)는 게 값의 원천이라,
    고르는 데 쓰면 그 값이 사라진다.
    """

    def _graph(self):
        g = Graph()
        for repo in ("alpha", "beta"):
            g.add_node(repo, "repo", repo, repo, "")
            g.add_node(f"{repo}:svc/upload.py#upload_document", "function",
                       "upload_document", repo, "svc/upload.py", 1)
        return g

    def test_wrong_hint_does_not_hide_the_answer(self):
        g = self._graph()
        hits = search(g, "upload document", k=10, hint_repo="alpha")
        self.assertIn("beta", {h["repo"] for h in hits},
                      "추측이 틀린 저장소를 가리켰을 때 정답이 사라지면 안 된다")

    def test_hint_actually_moves_the_ranking(self):
        g = self._graph()
        top = search(g, "upload document", k=1, hint_repo="beta")
        self.assertEqual(top[0]["repo"], "beta", "가중이 순위에 반영되지 않는다")

    def test_hint_weight_is_evidence_backed(self):
        from xgen_maker.kg import search as search_mod
        self.assertEqual(search_mod._REPO_HINT, 1.3)
        source = Path("xgen_maker/kg/search.py").read_text(encoding="utf-8")
        self.assertIn("네 분할", source, "근거 없이 상수를 두지 않는다")


class RepoProfilesComeFromTheGraphTest(unittest.TestCase):
    """프로필을 손으로 적으면 저장소가 늘 때마다 조용히 낡는다."""

    def test_profile_lists_paths_and_symbols(self):
        from xgen_maker.kg.profiles import repo_profiles, profile_block
        g = Graph()
        for repo, path in (("alpha", "service/audio/stt.py"), ("beta", "src/routes/proxy.rs")):
            g.add_node(repo, "repo", repo, repo, "")
            g.add_node(f"{repo}:{path}#handle_request", "function", "handle_request",
                       repo, path, 1)
        profiles = repo_profiles(g)
        self.assertIn("service/audio", profiles["alpha"]["dirs"])
        self.assertIn("handle_request", profiles["alpha"]["names"])
        self.assertIn("alpha", profile_block(g))

    def test_single_repo_has_nothing_to_choose(self):
        from xgen_maker.kg.profiles import profile_block
        g = Graph()
        g.add_node("only", "repo", "only", "only", "")
        g.add_node("only:a.py#f", "function", "f", "only", "a.py", 1)
        self.assertEqual(profile_block(g), "", "고를 것이 없으면 프롬프트를 늘리지 않는다")


class ShellMetacharactersNeverReachTheShellTest(unittest.TestCase):
    """회귀: cmd /c 경유라 프롬프트의 `|`가 파이프로 해석됐다.

    의도 분류 프롬프트에 "bug|feature|refactor|question"이 들어 있어 cmd가
    'feature'를 명령으로 실행하려다 종료코드 255로 죽었다. 그 LLM 보정은 줄곧
    죽어 있었고, 애매한 변경 요청이 전부 '질문'으로 빠져 답만 하고 끝났다.
    """

    def test_claude_command_avoids_cmd_when_real_exe_exists(self):
        from xgen_maker.auth import claude_command
        command = claude_command(["-p"])
        if command is None:
            self.skipTest("claude CLI 없음")
        self.assertNotEqual(command[0].lower(), "cmd",
                            "셸을 거치면 인자가 셸 문법으로 해석된다")

    def test_resolve_shim_reads_the_target_from_the_shim(self):
        from xgen_maker.auth import resolve_shim
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "node_modules" / "pkg" / "bin").mkdir(parents=True)
            real = root / "node_modules" / "pkg" / "bin" / "tool.exe"
            real.write_bytes(b"x")
            shim = root / "tool.cmd"
            shim.write_text('@ECHO off\n'
                            + r'"%dp0%\node_modules\pkg\bin\tool.exe"   %*' + '\n',
                            encoding="utf-8")
            self.assertEqual(resolve_shim(shim), real)

    def test_resolve_shim_returns_none_when_target_missing(self):
        """없는 경로를 지어내면 조용히 못 찾는 실행이 된다 — 그때는 cmd로 폴백한다."""
        from xgen_maker.auth import resolve_shim
        with tempfile.TemporaryDirectory() as tmp:
            shim = Path(tmp) / "tool.cmd"
            shim.write_text(r'"%dp0%\nope\tool.exe" %*' + '\n', encoding="utf-8")
            self.assertIsNone(resolve_shim(shim))

    def test_intent_prompt_still_contains_the_pipe(self):
        """이 결함의 진입점이 그대로 있음을 못박는다 — 프롬프트를 바꿔 가리지 말 것."""
        source = Path("xgen_maker/loop/intent.py").read_text(encoding="utf-8")
        self.assertIn("bug|feature|refactor|question", source)


class TempCleanupNeverDiscardsAnAnswerTest(unittest.TestCase):
    """회귀: 임시 디렉토리 삭제 실패로 이미 받은 응답을 통째로 버렸다.

    Windows에서 자식이 그 디렉토리를 cwd로 잡고 있으면 삭제가 WinError 32로 실패하고,
    그 OSError가 '실행 실패'로 잡혀 답이 날아갔다. 호출을 3개만 겹쳐도 절반이 그렇게
    사라졌다 — 의미층 주입이 사실상 불가능했던 이유.
    """

    def test_cleanup_errors_are_ignored(self):
        source = Path("xgen_maker/llm.py").read_text(encoding="utf-8")
        self.assertEqual(source.count("ignore_cleanup_errors=True"), 2,
                         "claude CLI 호출과 화면 판정 둘 다 정리 실패를 무시해야 한다")
        self.assertNotIn("with tempfile.TemporaryDirectory() as neutral",
                         source, "정리 실패가 응답을 버리는 경로가 남아 있다")


class EnrichRunsInParallelTest(unittest.TestCase):
    """요약은 서로 독립인데 줄을 세워 의미층이 못 채워졌다(순차 3건/분 → 6워커 38건/분)."""

    def _graph(self):
        g = Graph()
        g.add_node("r", "repo", "r", "r", "/r")
        for i in range(8):        # enrich 대상은 file/route/endpoint/feature다
            g.add_node(f"r:m{i}.py", "file", f"m{i}.py", "r", f"m{i}.py")
            g.add_edge("r", f"r:m{i}.py", "contains")
        return g

    def test_all_targets_get_summaries(self):
        from xgen_maker.kg.enrich import enrich_llm
        g = self._graph()
        calls = []

        def fake(base, model, messages, **kw):
            calls.append(1)
            return {"summary": "요약"}

        stats = enrich_llm(g, "b", "m", {"r": "/r"}, limit=8, chat_fn=fake, workers=4)
        self.assertEqual(stats["llm_done"], 8)
        self.assertEqual(len(calls), 8, "건마다 한 번씩만 불러야 한다")

    def test_dead_endpoint_stops_after_one_call(self):
        """엔드포인트가 죽었으면 limit만큼 실패를 쌓지 않는다."""
        from xgen_maker.kg.enrich import enrich_llm
        g = self._graph()
        calls = []

        def dead(*a, **k):
            calls.append(1)
            return None

        stats = enrich_llm(g, "b", "m", {"r": "/r"}, limit=8, chat_fn=dead, workers=4)
        self.assertEqual(len(calls), 1, "죽은 엔드포인트에 8번 매달렸다")
        self.assertIn("aborted", stats)

    def test_summaries_invalidate_the_search_index(self):
        """요약은 검색 점수에 들어간다 — 색인을 안 갈면 새 요약이 검색에 안 잡힌다."""
        from xgen_maker.kg.enrich import enrich_llm
        g = self._graph()
        before = g.rev
        enrich_llm(g, "b", "m", {"r": "/r"}, limit=2,
                   chat_fn=lambda *a, **k: {"summary": "결제 취소 처리"}, workers=2)
        self.assertGreater(g.rev, before)
        self.assertTrue(search(g, "결제 취소", k=3), "새 요약이 검색에 안 잡힌다")


class DeterministicSummariesAreOptInTest(unittest.TestCase):
    """회귀: `kg enrich`가 검색을 깎는 결정론 요약을 항상 먼저 주입했다.

    실측(실제 머지된 MR 265건, 전체 노드 주입): R@10 0.800 → 0.774 · MRR 0.542 → 0.523.
    상투구("config 파일 — 심볼 없음")가 수만 노드에 같은 말을 붙여 변별력을 없앤다.
    """

    def test_cli_does_not_fill_them_by_default(self):
        source = Path("xgen_maker/cli.py").read_text(encoding="utf-8")
        self.assertIn("args.deterministic", source,
                      "결정론 요약이 명시 옵션 뒤에 있어야 한다")
        index = source.index("def cmd_kg_enrich")
        body = source[index:index + 1200]
        self.assertNotIn("filled = enrich_deterministic(graph)\n    print", body,
                         "기본 경로에서 무조건 주입하면 안 된다")

    def test_the_cost_is_written_next_to_the_function(self):
        source = Path("xgen_maker/kg/enrich.py").read_text(encoding="utf-8")
        self.assertIn("검색에는 해롭다", source,
                      "다음 사람이 모르고 다시 켜지 않도록 근거를 남긴다")


class NonAsciiPathsReachTheCommitTest(unittest.TestCase):
    """회귀: 한글·공백이 든 파일명이 커밋에서 통째로 빠졌다.

    git은 그런 경로를 C 스타일로 감싸 내보낸다. 줄 단위로 읽으면 그 따옴표째가
    경로가 되고, stage_all은 실재하지 않는 파일을 add 하려 든다 — 에이전트가 고친
    코드가 MR에 없다. 화면에는 성공이라 뜬다.
    """

    def _repo(self, root: Path):
        subprocess.run(["git", "init", "-q"], cwd=root, capture_output=True)
        (root / "a.py").write_text("x=1\n", encoding="utf-8")
        subprocess.run(["git", "add", "-A"], cwd=root, capture_output=True)
        subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t",
                        "commit", "-qm", "i"], cwd=root, capture_output=True)

    def test_korean_and_spaced_filenames_are_staged(self):
        from xgen_maker.loop.git_ops import GitRepo
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._repo(root)
            repo = GitRepo(root)
            (root / "정산 보고서.py").write_text("def calc():\n    return 1\n",
                                              encoding="utf-8")
            self.assertIn("정산 보고서.py", repo.changed_files())
            repo.stage_all()
            self.assertIn("정산 보고서.py", repo.staged_files(),
                          "고친 파일이 커밋에 안 들어간다")

    def test_build_artifacts_are_still_excluded(self):
        """경로 파싱을 바꾸면서 부산물 제외가 깨지지 않아야 한다."""
        from xgen_maker.loop.git_ops import GitRepo
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._repo(root)
            (root / "__pycache__").mkdir()
            (root / "__pycache__" / "a.cpython-312.pyc").write_bytes(b"\x00\x01")
            (root / "b.py").write_text("y=1\n", encoding="utf-8")
            got = GitRepo(root).changed_files()
            self.assertIn("b.py", got)
            self.assertFalse([f for f in got if f.endswith(".pyc")])


class SecretsAreMaskedOutsideUrlsTest(unittest.TestCase):
    """자격은 늘 URL 형태로만 새지 않는다 — 헤더 문자열·설정 덤프·에러 본문에도 섞인다."""

    def test_bare_tokens_are_masked(self):
        from xgen_maker.loop.git_ops import redact
        for secret in ("glpat-ABCDEFGHIJKLMNOP1234",
                       "ghp_abcdefghijklmnopqrstuvwxyz012345",
                       "github_pat_11AZZ3PKY0Bb_dRZESX5WLK"):
            self.assertNotIn(secret, redact(f"PRIVATE-TOKEN: {secret}"))
            self.assertNotIn(secret, redact(f'{{"token": "{secret}"}}'))

    def test_url_credentials_still_masked(self):
        from xgen_maker.loop.git_ops import redact
        got = redact("https://user:glpat-ABCDEFGHIJKLMNOP1234@gitlab.example.com/g/r.git")
        self.assertNotIn("glpat-ABCDEFGHIJKLMNOP1234", got)
        self.assertIn("user:***@", got)

    def test_ordinary_text_is_left_alone(self):
        """가리기가 과하면 로그가 못 읽게 된다."""
        from xgen_maker.loop.git_ops import redact
        text = "fix/login-timeout 브랜치에서 테스트 3건 실패"
        self.assertEqual(redact(text), text)


class SemanticSummariesAreNotForSearchTest(unittest.TestCase):
    """직관과 반대라 근거를 코드에 못박는다 — 없으면 누구든 다시 켠다.

    중심성 상위 800개 노드에 실제로 LLM 요약을 채워 재 봤다:
      요약 전 R@10 0.796 · MRR 0.531 → 요약 후 R@10 0.781 · MRR 0.511.
    요약을 받은 파일만 따로 봐도 나빠졌다(0.914 → 0.877) — 부분 커버리지 탓이 아니다.
    """

    def test_the_measurement_is_recorded_next_to_the_function(self):
        source = Path("xgen_maker/kg/enrich.py").read_text(encoding="utf-8")
        self.assertIn("검색을 위해 돌리지 말 것", source)
        self.assertIn("0.914", source, "요약된 파일만 봐도 나빠졌다는 근거를 남긴다")

    def test_summaries_are_still_indexed_when_present(self):
        """쓰지 말라는 것과 없는 셈 치는 것은 다르다 — 있으면 검색은 그대로 쓴다."""
        from xgen_maker.kg.rank import _META_KEYS
        self.assertIn("summary", _META_KEYS)


class DenseSearchDegradesGracefullyTest(unittest.TestCase):
    """의미 검색은 있으면 좋은 층이지, 없으면 안 되는 층이 아니다.

    사내 임베딩 서버는 점프호스트 뒤에 있어 늘 닿는다고 볼 수 없다. 주소가 없거나
    죽었을 때 착지가 통째로 실패하면, 어제 되던 일이 오늘 안 된다.
    """

    def _graph(self):
        g = Graph()
        g.add_node("r", "repo", "r", "r", "")
        g.add_node("r:svc/stt.py#transcribe", "function", "transcribe", "r",
                   "svc/stt.py", 1, doc="음성 파일을 텍스트로 바꾼다")
        return g

    def test_no_endpoint_means_skipped_not_crashed(self):
        from xgen_maker.kg.dense import build, DenseIndex
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "v.npz"
            self.assertIn("skipped", build(self._graph(), path, "", "m"))
            self.assertFalse(DenseIndex(path).ready)

    def test_dead_endpoint_does_not_raise(self):
        from xgen_maker.kg.dense import build
        with tempfile.TemporaryDirectory() as tmp:
            stats = build(self._graph(), Path(tmp) / "v.npz",
                          "http://127.0.0.1:1/v1", "m")
            self.assertEqual(stats["added"], 0)

    def test_search_without_index_returns_nothing(self):
        from xgen_maker.kg.dense import DenseIndex
        with tempfile.TemporaryDirectory() as tmp:
            index = DenseIndex(Path(tmp) / "missing.npz")
            self.assertEqual(index.search(self._graph(), "질의", "http://x/v1", "m"), [])

    def test_node_text_carries_what_the_model_can_read(self):
        from xgen_maker.kg.dense import node_text
        text = node_text(self._graph().nodes["r:svc/stt.py#transcribe"])
        self.assertIn("transcribe", text)
        self.assertIn("svc/stt.py", text)
        self.assertIn("음성 파일", text, "문서가 있으면 그것이 가장 강한 신호다")

    def test_query_gets_an_instruction_prefix(self):
        """Qwen3 계열 표준 사용법 — 실측 R@1 0.447 → 0.518."""
        source = Path("xgen_maker/kg/dense.py").read_text(encoding="utf-8")
        self.assertIn("Instruct:", source)
        self.assertIn("0.518", source, "근거 수치를 남긴다")


class DenseIndexFileHandlingTest(unittest.TestCase):
    """색인 파일을 다루다 조용히 낡거나 날아가는 길을 막는다."""

    def _index(self, path):
        from xgen_maker.kg.dense import DenseIndex
        return DenseIndex(path)

    def test_save_is_atomic_and_leaves_no_debris(self):
        """200MB를 목적지에 직접 쓰면 쓰는 동안 읽는 쪽이 반쪽 파일을 만난다."""
        import numpy as np
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "v.npz"
            index = self._index(path)
            index.save(["a"], np.zeros((1, 4), dtype="float32"), {"a": "x"})
            self.assertTrue(path.exists())
            self.assertFalse(list(Path(tmp).glob("*staging*")), "임시 파일이 남았다")

    def test_reload_then_save_again_does_not_lock(self):
        """읽으며 열어 둔 핸들이 남으면 다음 저장이 교체에서 막힌다(Windows)."""
        import numpy as np
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "v.npz"
            self._index(path).save(["a"], np.zeros((1, 4), dtype="float32"), {"a": "x"})
            loaded = self._index(path)
            self.assertTrue(loaded.ready)
            loaded.save(["a", "b"], np.zeros((2, 4), dtype="float32"),
                        {"a": "x", "b": "y"})
            self.assertEqual(len(self._index(path).ids), 2)

    def test_old_format_without_digests_is_treated_as_absent(self):
        """형식이 다른 파일을 반쯤 읽어 쓰면 갱신 판단이 어긋난다 — 없는 셈 친다."""
        import numpy as np
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "v.npz"
            np.savez_compressed(path, ids=np.array(["a"], dtype=object),
                                vectors=np.zeros((1, 4), dtype="float16"))
            self.assertFalse(self._index(path).ready)


class PipelineRunsSemanticLayerTest(unittest.TestCase):
    """의미 검색 단계가 코드에만 있고 화면·카탈로그에 없으면 '안 도는' 것으로 보인다."""

    def test_step_is_catalogued(self):
        from xgen_maker.codes import ALL_EVENTS
        self.assertIn("dense_search", ALL_EVENTS)

    def test_step_is_on_the_coverage_screen(self):
        source = Path("xgen_maker/web.py").read_text(encoding="utf-8")
        self.assertIn('("dense_search", "의미 검색"', source)


class EmbedHintStaysOutOfLexicalIndexTest(unittest.TestCase):
    """임베딩 전용 자리는 어휘 색인에 새면 안 된다 — 새는 순간 검색이 나빠진다."""

    def test_lexical_index_does_not_read_it(self):
        from xgen_maker.kg.rank import _META_KEYS
        self.assertNotIn("embed_hint", _META_KEYS)

    def test_dense_reads_it_first(self):
        from xgen_maker.kg.dense import node_text
        node = {"kind": "file", "name": "a.py", "repo": "r", "path": "a.py",
                "meta": {"embed_hint": "결제 취소 처리", "doc": "무시될 문서"}}
        self.assertIn("결제 취소 처리", node_text(node))
        self.assertNotIn("무시될 문서", node_text(node))

    def test_the_null_result_is_recorded(self):
        """이득이 없었다는 사실을 남긴다 — 없으면 같은 실험을 또 한다."""
        source = Path("xgen_maker/kg/dense.py").read_text(encoding="utf-8")
        self.assertIn("상관이지 지렛대가 아니었다", source)


class EmbedBuildReportsAbortTest(unittest.TestCase):
    """회귀: 임베딩 서버가 죽으면 `kg embed`가 조용히 성공처럼 끝났다.

    added 0으로 종료코드 0이 나오면 색인이 최신인 줄 안다. 그 뒤로 의미 검색은
    옛 좌표를 계속 준다 — 틀린 답을 자신 있게 준다.
    """

    def _graph(self):
        g = Graph()
        g.add_node("r", "repo", "r", "r", "")
        for i in range(3):
            g.add_node(f"r:m{i}.py#f{i}", "function", f"f{i}", "r", f"m{i}.py", 1)
        return g

    def test_dead_endpoint_is_reported(self):
        from xgen_maker.kg.dense import build
        with tempfile.TemporaryDirectory() as tmp:
            stats = build(self._graph(), Path(tmp) / "v.npz",
                          "http://127.0.0.1:1/v1", "m")
            self.assertIn("aborted", stats)
            self.assertEqual(stats["missing"], 3)

    def test_cli_exits_nonzero_on_abort(self):
        source = Path("xgen_maker/cli.py").read_text(encoding="utf-8")
        self.assertIn('stats.get("aborted")', source)
        self.assertIn("SystemExit", source)


class VersionLiteralsSurviveTokenizationTest(unittest.TestCase):
    """회귀: "1.33.0"이 ["1","33","0"]으로 쪼개져 가장 변별력 높은 단서가 사라졌다.

    그 숫자들은 어디에나 있지만 "1.33.0"은 거의 없다. 의존성 갱신 요청에서 남는
    단서가 그것뿐인 경우가 실제로 있었고, 그런 MR 세 건이 통째로 안 잡혔다.
    양쪽(질의·매니페스트)에 버전을 통째로 담자 2~3위로 올라왔다.
    실측 265건: R@10 0.845 → 0.857 · R@1 0.483 → 0.491 (네 분할 모두 개선/유지).
    """

    def test_query_keeps_the_version_whole(self):
        from xgen_maker.kg.rank import tokenize
        got = tokenize("xgen-sdk 1.33.0 (align to latest SDK)")
        self.assertIn("1.33.0", got)
        self.assertIn("sdk", got, "쪼갠 단어도 그대로 남는다")

    def test_manifest_keeps_the_pinned_version(self):
        from xgen_maker.kg.extract_config import _names
        got = _names("pyproject.toml",
                     '[project]\ndependencies = ["xgen-sdk==1.35.1", "orjson>=3.11.9"]\n')
        self.assertIn("1.35.1", got)
        self.assertIn("xgen-sdk", got)

    def test_plain_numbers_are_not_versions(self):
        """401·30초까지 버전으로 보면 흔한 숫자가 희소 토큰인 척한다."""
        from xgen_maker.kg.rank import tokenize
        got = tokenize("로그인 실패 시 401 반환, 30초 타임아웃")
        self.assertNotIn("401", [t for t in got if "." in t])
        self.assertFalse([t for t in got if "." in t])

    def test_paths_are_not_versions(self):
        from xgen_maker.kg.rank import tokenize
        self.assertFalse([t for t in tokenize("service/v1.2/handler.py") if t == "1.2"])


class KoreanUiWordsAreIndexedTest(unittest.TestCase):
    """사용자는 화면에 적힌 말로 요청하는데, 그 말이 색인에 하나도 없었다.

    "다운로드 센터 목록을 사용자 화면 기준으로 정렬"의 정답 파일에는 다운로드·목록·
    사용자·삭제가 문자 그대로 있었다. 그런데 refs 규칙이 ASCII 식별자만 담아
    통째로 놓쳤다 — 프론트 파일의 67%가 한글을 담고 있는데도.
    실측 265건: R@1 0.491 → 0.525 · R@10 0.857 → 0.868 (네 분할 모두 개선).
    """

    def test_labels_are_collected_from_source(self):
        from xgen_maker.kg.refs import collect_labels
        got = collect_labels('const t = "다운로드 센터";  // 목록 정렬\nconst u = "사용자 화면";')
        for word in ("다운로드", "센터", "목록", "사용자"):
            self.assertIn(word, got)

    def test_frequent_words_come_first(self):
        """자주 나오는 말일수록 그 파일의 주제에 가깝다 — 상한에 걸릴 때 살아남아야 한다."""
        from xgen_maker.kg.refs import collect_labels
        got = collect_labels("결제 결제 결제 배송")
        self.assertEqual(got[0], "결제")

    def test_lexical_index_reads_them(self):
        from xgen_maker.kg.rank import _META_KEYS
        self.assertIn("labels", _META_KEYS)

    def test_cap_is_evidence_backed(self):
        from xgen_maker.kg.refs import _MAX_LABELS
        self.assertEqual(_MAX_LABELS, 400)
        source = Path("xgen_maker/kg/refs.py").read_text(encoding="utf-8")
        self.assertIn("0.525", source, "고른 근거를 값 옆에 남긴다")

    def test_extractors_wire_it(self):
        for name in ("extract_python", "extract_typescript", "extract_rust"):
            source = Path(f"xgen_maker/kg/{name}.py").read_text(encoding="utf-8")
            self.assertIn("collect_labels", source, f"{name}이 한글을 안 담는다")


class DenseAlsoReadsKoreanLabelsTest(unittest.TestCase):
    """이름·경로가 영어라 의미 모델이 한글 요청과 이을 다리가 없었다.

    admin-install-files ↔ "다운로드 센터"를 이어 주는 것은 그 파일 안의 UI 문구뿐이다.
    실측 265건: R@1 0.525 → 0.547 · R@10 0.868 → 0.883 (분할 A·C·D 개선, B 유지).
    """

    def test_labels_reach_the_embedding_text(self):
        from xgen_maker.kg.dense import node_text
        text = node_text({"kind": "file", "name": "AdminInstallFiles.tsx", "repo": "fe",
                          "path": "features/admin-install-files/src/index.tsx",
                          "meta": {"labels": "다운로드 센터 목록 사용자"}})
        self.assertIn("다운로드 센터", text)
        self.assertIn("admin-install-files", text, "경로도 그대로 남는다")

    def test_labels_do_not_replace_the_docstring(self):
        """둘 다 신호다 — 하나가 다른 하나를 밀어내면 안 된다."""
        from xgen_maker.kg.dense import node_text
        text = node_text({"kind": "function", "name": "f", "repo": "r", "path": "a.py",
                          "meta": {"doc": "결제를 취소한다", "labels": "취소 버튼"}})
        self.assertIn("결제를 취소한다", text)
        self.assertIn("취소 버튼", text)

    def test_char_budget_is_evidence_backed(self):
        from xgen_maker.kg.dense import _LABEL_CHARS
        self.assertEqual(_LABEL_CHARS, 300)
        source = Path("xgen_maker/kg/dense.py").read_text(encoding="utf-8")
        self.assertIn("0.547", source)


class ConfigFilesGetKoreanTooTest(unittest.TestCase):
    """회귀: 한글 색인을 코드 추출기 3개에만 붙이고 설정·문서를 빠뜨렸다.

    README·서비스 설명·설정 주석이 한글인데 그 파일들만 조용히 안 잡혔다.
    127개 파일이 한글을 갖게 되자 R@10 0.883 → 0.902 (네 분할 모두 개선/유지).
    """

    def test_markdown_korean_is_indexed(self):
        from xgen_maker.kg.extract_config import extract_config_file
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "README.md").write_text(
                "# 다운로드 센터\n\n설치 파일 목록을 관리한다.\n", encoding="utf-8")
            g = Graph()
            g.add_node("r", "repo", "r", "r", str(root))
            extract_config_file(g, "r", root, "README.md")
            labels = (g.nodes["r:README.md"]["meta"] or {}).get("labels", "")
        self.assertIn("다운로드", labels)
        self.assertIn("설치", labels)

    def test_every_extractor_collects_labels(self):
        """하나라도 빠지면 그 종류의 파일만 조용히 검색에서 사라진다."""
        for name in ("extract_python", "extract_typescript", "extract_rust",
                     "extract_config"):
            source = Path(f"xgen_maker/kg/{name}.py").read_text(encoding="utf-8")
            self.assertIn("collect_labels", source, f"{name}이 한글을 안 담는다")


class LabelWeightIsTunedTest(unittest.TestCase):
    """화면 문구는 사용자가 실제로 쓰는 말이라 다른 메타보다 조금 더 무겁다."""

    def test_weight_is_two_with_evidence(self):
        from xgen_maker.kg.rank import _LABEL_WEIGHT
        self.assertEqual(_LABEL_WEIGHT, 2)
        source = Path("xgen_maker/kg/rank.py").read_text(encoding="utf-8")
        self.assertIn("0.906", source)

    def test_other_meta_keeps_its_own_weight(self):
        """문구 무게를 올리면서 요약·문서까지 같이 올리면 다른 실험을 덮어쓴다."""
        from xgen_maker.kg.rank import _FIELD_WEIGHT
        self.assertEqual(_FIELD_WEIGHT["meta"], 1)


class InterpolationIsCodeNotStringTest(unittest.TestCase):
    """회귀: 주석·문자열을 걷어내면서 보간식 안의 진짜 호출까지 지웠다.

    `총 ${total.toFixed(2)}원`의 toFixed, f"결과 {compute(x)}"의 compute는 문자열
    안에 있지만 그 자리에서 실행되는 코드다. 통째로 지우니 호출 엣지 56개가 사라졌다.
    """

    def test_template_literal_interpolation_survives(self):
        from xgen_maker.kg.calls import scan_call_names
        got = scan_call_names('const s = `총 ${total.toFixed(2)}원`;\n'
                              'const t = "설명 문구(참고)";')
        self.assertIn("toFixed", got)
        self.assertNotIn("참고", got)

    def test_fstring_interpolation_survives(self):
        from xgen_maker.kg.calls import scan_call_names
        got = scan_call_names('msg = f"결과 {compute(x)}"\nlog = "그냥 문자열(주의)"')
        self.assertIn("compute", got)

    def test_prose_is_still_stripped(self):
        """되살리면서 산문까지 되살리면 원래 결함으로 돌아간다."""
        from xgen_maker.kg.calls import scan_call_names
        got = scan_call_names('# 반환 dict 에서 꺼낼 key (없으면 전체)\n'
                              '"""annotations: MCP annotations (읽기)"""\n'
                              'real_call(1)')
        self.assertIn("real_call", got)
        self.assertNotIn("key", got)
        self.assertNotIn("annotations", got)


class InfraVetoCoversRealConventionsTest(unittest.TestCase):
    """회귀: 배포를 바꾸는 파일이 통과하고 있었다.

    패턴이 `helm/`·`k8s/`만 알아서 k3s·argocd·terraform·차트 값 파일이 그대로
    통과했다. README에는 "charts를 vetoes"라고 적혀 있었는데 코드가 안 그랬다.
    이건 안전 문제다 — 에이전트가 고치고 MR까지 나간다.
    """

    def test_deployment_definitions_are_blocked(self):
        from xgen_maker.config import infra_files
        for path in ("k3s/helm-chart/values/api.yaml", "k3s/argocd/projects/app.yaml",
                     "helm-chart/values/api.yaml", "charts/api/values.yaml",
                     "terraform/main.tf", "infra/main.tfvars", "ansible/site.yml",
                     "kustomization.yaml", "deploy/main.tf",
                     "dockerfiles/gw/Dockerfile", ".gitlab-ci.yml",
                     ".github/workflows/ci.yml"):
            self.assertTrue(infra_files([path]), f"통과하면 안 된다: {path}")

    def test_source_that_merely_mentions_infra_is_allowed(self):
        """과하게 막으면 기능 코드를 못 고친다 — 이름만 닮은 것은 통과해야 한다."""
        from xgen_maker.config import infra_files
        for path in ("controller/chart_service.py", "features/charting/src/index.tsx",
                     "service/values_helper.py", "service/k8sclient.py",
                     "packages/api-client/src/charts.ts",
                     "editor/nodes/flux_node.py", "tests/test_terraform_parser.py",
                     "config/services.yaml"):
            self.assertFalse(infra_files([path]), f"막으면 안 된다: {path}")

    def test_readme_claim_matches_code(self):
        """문서가 막는다고 적은 것을 코드가 실제로 막는지 — 드리프트를 못박는다."""
        from xgen_maker.config import infra_files
        self.assertTrue(infra_files(["charts/x/values.yaml"]))
        readme = Path("README.md").read_text(encoding="utf-8")
        self.assertIn("charts", readme)


class TestPenaltyIsNotMetricGamedTest(unittest.TestCase):
    """지표가 오르는 것과 일을 잘하는 것은 다르다.

    테스트 감점을 풀면 전체 R@1이 오른다(0.35→0.458 · 0.8→0.467). 벤치마크가
    "그 MR이 바꾼 파일 중 아무거나" 찾으면 맞춘 것으로 세기 때문이다 — 테스트
    파일을 더 잘 찾는 것이 점수가 된다. 정답에서 테스트를 빼면 정반대다
    (0.455 → 0.448 → 0.442). 감점을 풀면 에이전트는 고칠 자리 대신 테스트로 간다.
    """

    def test_penalty_stays_strong(self):
        from xgen_maker.kg.rank import _TEST_PENALTY
        self.assertEqual(_TEST_PENALTY, 0.35)

    def test_the_trap_is_written_down(self):
        source = Path("xgen_maker/kg/rank.py").read_text(encoding="utf-8")
        self.assertIn("지표만 보고 올리지 말 것", source)
        self.assertIn("구현 파일만", source)

    def test_tests_still_win_when_asked_for(self):
        """감점은 기본값일 뿐이다 — 요청이 테스트를 지목하면 그쪽으로 가야 한다."""
        g = Graph()
        g.add_node("r", "repo", "r", "r", "")
        g.add_node("r:svc/upload.py#upload", "function", "upload_document",
                   "r", "svc/upload.py", 1)
        g.add_node("r:tests/test_upload.py#test_upload", "function",
                   "test_upload_document", "r", "tests/test_upload.py", 1)
        plain = search(g, "upload document", k=2)
        asked = search(g, "upload document test", k=2)
        self.assertFalse(plain[0]["path"].startswith("tests/"))
        self.assertTrue(any(h["path"].startswith("tests/") for h in asked))
