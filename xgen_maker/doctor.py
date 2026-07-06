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

    # 목적 2: 코드베이스 이해 — KG 로드 + 검색 + 영향분석
    from .config import MakerConfig
    from .kg.graph import Graph
    from .kg.search import search, impact
    config = None
    graph = None
    try:
        config = MakerConfig.from_file(config_path) if config_path else MakerConfig()
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

    # 목적 8: MCP 노출
    try:
        from .mcp_server import TOOLS
        names = [t["name"] for t in TOOLS]
        check.ok("MCP 노출", f"{len(names)}개 툴: {', '.join(names)}")
    except Exception as e:
        check.fail("MCP 노출", str(e)[:80])

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
