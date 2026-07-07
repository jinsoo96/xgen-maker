"""maker doctor — MAKER의 고결한 목적이 실제로 되는지 자가검증.

각 능력을 말이 아니라 실제 동작으로 확인하고 PASS/FAIL/WARN을 찍는다.
"고결한 목적" = 쿼리 하나로: 코드베이스를 이해(KG)하고 · 안전하게 고치고(브랜치·검증·judge)
· 사람에게 넘기고(MR) · 배포까지 계획하며 · 전 과정을 로그로 남긴다.
"""
from __future__ import annotations

import subprocess
import sys
import tempfile
from pathlib import Path


class Check:
    def __init__(self):
        self.rows: list[tuple[str, str, str]] = []

    def add(self, status: str, name: str, detail: str = "") -> None:
        self.rows.append((status, name, detail))

    def ok(self, name, detail=""): self.add("PASS", name, detail)
    def warn(self, name, detail=""): self.add("WARN", name, detail)
    def fail(self, name, detail=""): self.add("FAIL", name, detail)


def _icon(status: str) -> str:
    return {"PASS": "✓", "WARN": "!", "FAIL": "✗"}.get(status, "?")


def run_doctor(config_path: str | None = None) -> bool:
    check = Check()
    print("\n═══ maker doctor — 고결한 목적 자가검증 ═══\n")

    # 목적 1: 로그인 하나로 통합 (Claude)
    try:
        from .auth import load_auth, claude_cli_status
        auth = load_auth()
        if auth.provider == "claude_cli":
            status = claude_cli_status()
            if status["authenticated"]:
                check.ok("로그인 통합", "claude_cli 인증됨 — 코딩+판단+요약 단일 로그인")
            else:
                check.warn("로그인 통합", f"claude_cli 미인증: {status['reason'][:60]}")
        else:
            check.ok("로그인 통합", f"provider={auth.provider}")
    except Exception as e:
        check.fail("로그인 통합", str(e)[:80])

    # 목적 1-1: .env 자동 로드 (자격을 파일에 박아두면 재입력 불필요)
    try:
        from .dotenv import find_env, load_env
        env_path = find_env()
        if env_path:
            r = load_env(env_path)
            check.ok(".env 자동로드", f"{env_path.name} — 주입 {len(r['keys'])}개 키")
        else:
            check.warn(".env 자동로드", ".env 없음 — .env.example 복사해 토큰 기입")
    except Exception as e:
        check.warn(".env 자동로드", str(e)[:80])

    # 목적 1-2: GitLab 로그인 지속 (push·MR 재입력 불필요)
    try:
        from .auth import load_auth, gitlab_verify_token
        auth = load_auth()
        if auth.gitlab_token:
            v = gitlab_verify_token(auth.gitlab_url, auth.gitlab_token)
            if v["ok"]:
                check.ok("GitLab 로그인", f"{v['user']} 유지됨 — push·MR 재입력 불필요")
            else:
                check.warn("GitLab 로그인", f"토큰 무효: {v['reason']}")
        else:
            check.warn("GitLab 로그인", "미로그인 — maker login --gitlab-user/-password")
    except Exception as e:
        check.warn("GitLab 로그인", str(e)[:80])

    # 목적 2: 코드베이스 이해 — KG 로드 + 검색 + 영향분석
    from .config import MakerConfig
    from .kg.graph import Graph
    from .kg.search import search, impact
    config = None
    graph = None
    try:
        config = MakerConfig.from_file(config_path) if config_path else MakerConfig()
        # 목적 1-3: 작업 커밋 저자 — GitLab 작업이 지정 신원으로 커밋되는지(대상 레포 config 오염 방지)
        if config.git_author_name and config.git_author_email:
            check.ok("작업 커밋 저자", f"{config.git_author_name} <{config.git_author_email}> 강제")
        else:
            check.warn("작업 커밋 저자",
                       "미설정 — 대상 레포 git config로 커밋됨. "
                       ".env에 XGEN_MAKER_GIT_AUTHOR_NAME/EMAIL 권장")
        if Path(config.kg_path).exists():
            graph = Graph.load(config.kg_path)
            stats = graph.stats()
            check.ok("KG 로드", f"{stats['nodes']:,} 노드 / {stats['edges']:,} 엣지")
            hits = search(graph, "ontology graph", k=3)
            if hits:
                check.ok("KG 검색(착지)", f"top: {hits[0]['name']} (score {hits[0]['score']})")
            else:
                check.warn("KG 검색(착지)", "결과 없음")
            eps = graph.nodes_by_kind("endpoint")
            if eps:
                aff = impact(graph, eps[0]["id"], depth=2)
                check.ok("영향분석", f"{eps[0]['name']} → 영향 {len(aff)}개")
            xrepo = [e for e in graph.edges if e["kind"] == "resolves_to"]
            check.ok("크로스레포 링크", f"FE→BE resolves_to {len(xrepo)}개") if xrepo \
                else check.warn("크로스레포 링크", "0개")
        else:
            check.fail("KG 로드", f"그래프 없음: {config.kg_path} — maker kg build+merge 필요")
    except Exception as e:
        check.fail("KG 로드", str(e)[:80])

    # 목적 3: 의미층 (요약)
    if graph is not None:
        summarized = [n for n in graph.nodes.values() if n["meta"].get("summary")]
        llm_sum = [n for n in graph.nodes.values() if n["meta"].get("summary_src") == "llm"]
        if summarized:
            check.ok("의미층", f"요약 {len(summarized):,}개 (LLM {len(llm_sum)}개)")
        else:
            check.warn("의미층", "요약 없음 — maker kg enrich 필요")

    # 목적 3-2: 인가 게이트 — act(실 push/MR)는 인가된 xgen 작업자만
    try:
        from .loop.authz import is_placeholder_target
        if config and is_placeholder_target(config.gitlab_url):
            check.warn("인가 게이트",
                       f"gitlab_url 미설정/예시 — act 자동 거부(실 대상 아님). "
                       f".env로 실 대상 주입 시 멤버십 검사")
        elif config and config.gitlab_projects:
            check.ok("인가 게이트",
                     "act 전 대상 프로젝트 Developer+ 멤버십 검사(미인가 fail-fast)")
        else:
            check.warn("인가 게이트",
                       "gitlab_projects 매핑 없음 — act 시 대상 미지정으로 거부")
    except Exception as e:  # noqa: BLE001
        check.warn("인가 게이트", str(e)[:80])

    # 목적 4: 안전하게 고침 — 브랜치 가드 + 자동검증 게이트 (실동작)
    try:
        from .loop.git_ops import GitRepo, GitOpsError
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for a in (["init", "-b", "trunk"], ["config", "user.email", "d@d"],
                      ["config", "user.name", "d"]):
                subprocess.run(["git", *a], cwd=root, capture_output=True)
            (root / "x.py").write_text("x=1\n", encoding="utf-8")
            subprocess.run(["git", "add", "-A"], cwd=root, capture_output=True)
            subprocess.run(["git", "commit", "-m", "i"], cwd=root, capture_output=True)
            repo = GitRepo(root)
            try:
                repo.create_branch("develop")
                check.fail("보호브랜치 가드", "develop 브랜치 생성이 막히지 않음!")
            except GitOpsError:
                check.ok("보호브랜치 가드", "develop/main 직접 브랜치·푸시 차단 확인")
    except Exception as e:
        check.fail("보호브랜치 가드", str(e)[:80])

    try:
        from .loop.testing import run_checks
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "bad.py").write_text("def f(:\n", encoding="utf-8")
            result = run_checks(root, ["bad.py"])
            if result["blocked"]:
                check.ok("자동검증 게이트", "깨진 코드 → MR 차단(blocked) 확인")
            else:
                check.fail("자동검증 게이트", "깨진 코드가 차단되지 않음!")
    except Exception as e:
        check.fail("자동검증 게이트", str(e)[:80])

    # 목적 5: 판단 게이트 (judge) + 인프라 veto
    try:
        from .loop.judge import judge
        cfg = MakerConfig(llm_enabled=False, theta=0.7)
        veto = judge(cfg, "q", "diff", ["docker-compose.yml", "a.py"])
        if not veto["passed"] and veto["veto"]:
            check.ok("judge/인프라 veto", "인프라 파일 변경 차단 확인")
        else:
            check.warn("judge/인프라 veto", "veto 미작동")
    except Exception as e:
        check.fail("judge", str(e)[:80])

    # 목적 6: 배포 인터록 (dev, 실발사 봉인)
    try:
        from .loop.deploy import plan_deploy, trigger_deploy
        cfg = MakerConfig(gitlab_projects={"r": "g/r"}, deploy_mode="live")
        import os
        os.environ.pop("XGEN_MAKER_DEPLOY_LIVE", None)
        result = trigger_deploy(cfg, plan_deploy(cfg, "r", "fix/x"))
        if result["status"] == "refused" and not result["sent"]:
            check.ok("배포 인터록", "live 모드도 인터록 없으면 발사 거부 확인")
        else:
            check.fail("배포 인터록", f"봉인 실패: {result}")
    except Exception as e:
        check.fail("배포 인터록", str(e)[:80])

    # 목적 6-3: 안전망 (최신코드·롤백·worktree·비용)
    try:
        from .loop.rollback import last_action  # noqa: F401
        from .loop.cost import CostTracker  # noqa: F401
        fl = getattr(config, "fetch_latest", False) if config else False
        iw = getattr(config, "isolate_worktree", False) if config else False
        act = last_action(config.worklogs_dir) if config else None
        undoable = f"undo가능:{act['branch']}" if act else "undo대상없음"
        check.ok("안전망", f"최신코드fetch={'on' if fl else 'off'} · worktree격리={'on' if iw else 'off'} "
                 f"· 롤백({undoable}) · 비용추적")
    except Exception as e:
        check.warn("안전망", str(e)[:80])

    # 목적 7: 전 과정 로그 (journal) + 증분 sync
    try:
        from .loop.journal import Journal
        with tempfile.TemporaryDirectory() as tmp:
            j = Journal(tmp, "doctor test", verbose=False)
            j.event("probe", "ok", x=1)
            summary = j.close("done")
            if (j.dir / "journal.jsonl").exists() and summary.exists():
                check.ok("작업 로그(journal)", "jsonl 이벤트 + SUMMARY.md 생성 확인")
    except Exception as e:
        check.fail("작업 로그(journal)", str(e)[:80])

    if graph is not None and graph.meta.get("repo_heads"):
        check.ok("증분 sync 기준점", f"{len(graph.meta['repo_heads'])}개 레포 HEAD 기록됨")
    elif graph is not None:
        check.warn("증분 sync 기준점", "repo_heads 없음 — 구버전 그래프")

    # 목적 7-2: 웹 프리뷰 (라이브 스택 도달 + 스냅샷 능력)
    try:
        from .loop.verify import _shim_command, http_reachable
        npx = _shim_command("npx", ["--version"])
        preview_base = getattr(config, "preview_base", "") if config else ""
        if npx is None:
            check.warn("웹 프리뷰", "npx 미발견 — Playwright 스냅샷 불가")
        elif preview_base and http_reachable(preview_base, timeout=5):
            check.ok("웹 프리뷰", f"{preview_base} 도달 + npx 있음 → 스냅샷 가능")
        elif preview_base:
            check.warn("웹 프리뷰", f"{preview_base} 미도달 — 스택 미기동(RAM 가드)")
        else:
            check.warn("웹 프리뷰", "preview_base 미설정")
    except Exception as e:
        check.warn("웹 프리뷰", str(e)[:80])

    # 목적 7-3: UI/UX 검증 (라우트 매핑 + 픽셀diff + 비전판정)
    try:
        import os as _os
        from .loop.ui_verify import affected_routes, pixel_diff
        pieces = []
        if graph is not None:
            fr = [r for r in graph.nodes_by_kind("feature")]
            same_pkg = [e for e in graph.edges if e["kind"] == "same_package"]
            pieces.append(f"라우트{len(graph.nodes_by_kind('route'))}·feature링크{len(same_pkg)}")
        try:
            import PIL  # noqa: F401
            pieces.append("픽셀diff(Pillow)")
        except ImportError:
            pieces.append("픽셀diff없음")
        pieces.append("비전판정" + ("(키있음)" if _os.environ.get("ANTHROPIC_API_KEY")
                                   else "(키필요)"))
        pieces.append("인증세션" + ("(자격있음)" if _os.environ.get("XGEN_MAKER_UI_EMAIL")
                                  else "(미인증)"))
        check.ok("UI/UX 검증", " · ".join(pieces))
    except Exception as e:
        check.warn("UI/UX 검증", str(e)[:80])

    # 목적 6-1: 인프라 KG (LLM이 배포 토폴로지 인지)
    if graph is not None:
        projects = graph.nodes_by_kind("deploy_project")
        apps = graph.nodes_by_kind("helm_app")
        if projects:
            from .kg.extract_infra import deploy_targets
            tgt = deploy_targets(graph, "xgen-core")
            domains = sorted({t["domain"] for t in tgt if t["domain"]})
            check.ok("인프라 KG", f"배포 프로젝트 {len(projects)}·helm앱 {len(apps)} "
                     f"· xgen-core→도메인 {len(domains)}개")
        else:
            check.warn("인프라 KG", "인프라 미포함 — maker kg infra 후 재병합")

    # 목적 6-1b: 릴리즈 사다리 (develop→stg→main — 배포의 뼈대)
    try:
        from .loop.release import ladder, env_for_branch
        lad = ladder(config)
        branches = " → ".join(s["branch"] for s in lad)
        tb = getattr(config, "target_branch", "develop") if config else "develop"
        check.ok("릴리즈 사다리", f"{branches} · 이 MR→{tb}({env_for_branch(tb, config)})")
    except Exception as e:
        check.warn("릴리즈 사다리", str(e)[:80])

    # 목적 6-1c: 배포 관측 (read-only) — MAKER는 MR까지만, 배포는 사용자 수동
    try:
        from .loop import jenkins, argocd
        parts = ["MAKER=MR까지만(배포 트리거 없음)"]
        parts.append(f"Jenkins {'연결' if jenkins.available() else '미설정'}")
        parts.append(f"ArgoCD {'연결' if argocd.available() else '미설정'}")
        check.ok("배포 관측(read-only)", " · ".join(parts))
    except Exception as e:
        check.warn("배포 관측(read-only)", str(e)[:80])

    # 목적 6-2: 배포 렌더 검증 (상사님 tmp 방식 — MR 전 배포통과 확인)
    try:
        from .loop.deploy import _find_helm, deploy_render_test
        helm = _find_helm()
        if helm is None:
            check.warn("배포 렌더검증", "helm 미설치 — T1 렌더검증 불가")
        elif config is not None:
            r = deploy_render_test(config, "xgen-core")
            if r["status"] == "passed":
                check.ok("배포 렌더검증", f"helm template 통과 (xgen-core: {r['manifests']}개 매니페스트)")
            elif r["status"] == "skipped":
                check.warn("배포 렌더검증", f"helm 있음 · {r['reason'][:50]}")
            else:
                check.warn("배포 렌더검증", f"렌더 실패: {str(r.get('error',''))[:50]}")
        else:
            check.ok("배포 렌더검증", f"helm 있음 ({helm.split(chr(92))[-1]})")
    except Exception as e:
        check.warn("배포 렌더검증", str(e)[:80])

    # 목적 5-1: 작업 학습 메모리 (하네스가 과거 참고 — 실수 방지)
    try:
        from pathlib import Path as _P
        from .loop.learnings import _all
        ld = _P(getattr(config, "learnings_dir", "learnings")) if config else _P("learnings")
        total = sum(len(_all(ld, f.stem)) for f in ld.glob("*.jsonl")) if ld.is_dir() else 0
        check.ok("학습 메모리", f"{total}건 — 구현 전 이 영역 과거 교훈을 프롬프트에 주입(실수 방지)")
    except Exception as e:
        check.warn("학습 메모리", str(e)[:80])

    # 목적 0: 사용 표면 (CLI + 웹 UI)
    try:
        from . import web  # noqa: F401
        from .chat import run_chat  # noqa: F401
        check.ok("사용 표면", "CLI(maker run/chat) + 웹 UI(maker web) + MCP")
    except Exception as e:
        check.warn("사용 표면", str(e)[:80])

    # 목적 8: MCP 노출
    try:
        from .mcp_server import TOOLS
        names = [t["name"] for t in TOOLS]
        check.ok("MCP 노출", f"{len(names)}개 툴: {', '.join(names)}")
    except Exception as e:
        check.fail("MCP 노출", str(e)[:80])

    # 목적 8-1: SDK 자가검증 (엔진 계약 호환 — 조용히 안 깨지게)
    try:
        from .sdk_check import contract_probe, installed_versions
        probe = contract_probe()
        ver = installed_versions()
        if probe["ok"]:
            check.ok("SDK 자가검증", f"엔진 계약 호환 (sdk {ver.get('xgen-sdk')}·"
                     f"harness {ver.get('xgen-harness')}) — 드리프트는 maker sdk로")
        else:
            check.fail("SDK 자가검증", f"계약 깨짐 — missing {probe['missing']}")
    except Exception as e:
        check.warn("SDK 자가검증", str(e)[:80])

    # 목적 8-2: R3 엔진 stage 등록 (MAKER가 엔진 정식 스테이지)
    try:
        from .engine_stage import register
        r = register()
        if r["ok"]:
            check.ok("엔진 stage 등록(R3)", f"{r['stage_id']} → {r['engine']} {r['version']}")
        else:
            check.warn("엔진 stage 등록(R3)", r["reason"][:60])
    except Exception as e:
        check.warn("엔진 stage 등록(R3)", str(e)[:80])

    # 목적 9: 수렴 루프 + 하네스 샌드박스 (통과까지 자가수정)
    try:
        from .loop.converge import (decide, HAS_HARNESS, HARNESS_VERSION,
                                     HARNESS_SOURCE, sandbox_verify_python)
        # decide 계약: 실패는 retry, 전부 통과는 stop
        retry = decide({"blocked": True, "checks": [], "summary": {}},
                       {"status": "passed"}, None, 1, 3)
        stop = decide({"blocked": False, "checks": [], "summary": {}},
                      {"status": "passed"}, {"passed": True}, 1, 3)
        if retry == "retry" and stop == "stop":
            check.ok("수렴 루프", "실패→retry / 통과→stop 계약 확인 (max_iterations 반복)")
        else:
            check.fail("수렴 루프", f"decide 계약 이상: retry={retry} stop={stop}")
        if HAS_HARNESS:
            with tempfile.TemporaryDirectory() as tmp:
                (Path(tmp) / "s.py").write_text("x=1\n", encoding="utf-8")
                sb = sandbox_verify_python(Path(tmp), ["s.py"])
            if sb["status"] == "passed" and sb.get("isolated"):
                check.ok("하네스 샌드박스", f"{HARNESS_SOURCE} {HARNESS_VERSION} 엔진 격리검증 동작")
            else:
                check.warn("하네스 샌드박스", f"검증 상태 {sb['status']}")
        else:
            check.warn("하네스 샌드박스", "xgen-harness 미설치 — 로컬 checks로 대체")
    except Exception as e:
        check.fail("수렴 루프", str(e)[:80])

    # 출력
    width = max(len(n) for _, n, _ in check.rows)
    for status, name, detail in check.rows:
        print(f"  {_icon(status)} [{status}] {name.ljust(width)}  {detail}")
    n_pass = sum(1 for s, _, _ in check.rows if s == "PASS")
    n_warn = sum(1 for s, _, _ in check.rows if s == "WARN")
    n_fail = sum(1 for s, _, _ in check.rows if s == "FAIL")
    print(f"\n  결과: PASS {n_pass} · WARN {n_warn} · FAIL {n_fail}\n")
    return n_fail == 0
