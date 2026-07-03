"""MAKER 루프 오케스트레이터 — 쿼리 1개의 생애(기획서 §3.2 ①~⑩).

안전 기본값: allow_write=False → 실레포를 건드리지 않고 계획+MR초안까지만(plan-only).
observe 모드 → 로컬 브랜치·커밋·MR초안까지, 푸시/MR 생성 없음.
act 모드 → 기능 브랜치 푸시 + GitLab MR 생성(머지는 사람).
"""
from __future__ import annotations

from pathlib import Path

from .. import llm
from ..codes import Outcome, ErrorCode
from ..config import MakerConfig
from ..kg.graph import Graph
from ..kg.search import search, impact
from ..kg.build import refresh_files
from .intent import classify
from .git_ops import GitRepo, GitOpsError
from .implement import build_prompt, run_agent
from .judge import judge
from .journal import Journal
from .deploy import plan_deploy, trigger_deploy
from .mr import build_mr_draft, save_draft, create_gitlab_mr
from .testing import run_checks
from .verify import verify


class MakerLoop:
    def __init__(self, config: MakerConfig, graph: Graph | None = None):
        self.config = config
        self.graph = graph if graph is not None else Graph.load(config.kg_path)
        if graph is None:
            # 사람 편집(오버레이) 반영 — deprecated 노드 착지 회피 등 (R8)
            from ..kg.overlay import load_overlay, apply_overlay
            overlay = load_overlay(Path(config.kg_path).parent / "overlay.json")
            if overlay["node_overrides"] or overlay["custom_edges"]:
                apply_overlay(self.graph, overlay)

    # ---- 단계 구현 ----
    def _answer_question(self, query: str, landing: list[dict], journal: Journal) -> dict:
        lines = [f"- [{n['kind']}] {n['name']} — `{n['repo']}:{n['path']}`" +
                 (f":{n['line']}" if n.get("line") else "")
                 for n in landing[:10]]
        answer = "지식그래프 검색 결과:\n" + ("\n".join(lines) if lines else "(일치 없음)")
        journal.event("answer", "ok", hits=len(landing))
        journal.close("answered")
        return {"outcome": "answered", "answer": answer, "landing": landing[:10]}

    def _legacy_notes(self, landing: list[dict], repo_path: Path | None) -> str:
        """④ 레거시 확인 — KG는 지도, 코드가 권위. 착지 파일 실코드 발췌."""
        if repo_path is None:
            return ""
        notes: list[str] = []
        seen: set[str] = set()
        for node in landing:
            rel = node.get("path", "")
            if not rel or rel in seen or node["kind"] == "repo":
                continue
            seen.add(rel)
            file_path = repo_path / rel
            if not file_path.is_file():
                continue
            try:
                head = "\n".join(file_path.read_text(encoding="utf-8", errors="ignore")
                                 .splitlines()[:40])
            except OSError:
                continue
            notes.append(f"### {rel} (선두 40줄)\n```\n{head}\n```")
            if len(notes) >= 3:
                break
        return "\n\n".join(notes)

    # ---- 메인 ----
    def run(self, query: str) -> dict:
        config = self.config
        journal = Journal(config.worklogs_dir, query, verbose=config.verbose)
        report: dict = {"query": query, "session_dir": str(journal.dir)}

        # ② intent
        intent_info = classify(
            query,
            config.llm_base if config.llm_enabled else None,
            config.llm_model if config.llm_enabled else None)
        intent = intent_info["intent"]
        journal.event("intent", "ok", **intent_info)
        report["intent"] = intent

        # ③ KG 검색 (착지점) — 한글 쿼리는 코드 심볼과 어휘가 달라 LLM 키워드 확장 폴백
        landing = search(self.graph, query, k=8)
        if not landing and config.llm_enabled:
            expanded = llm.json_chat(config.llm_base, config.llm_model, [
                {"role": "system", "content":
                 'Extract English code-search keywords from the dev request. '
                 'Reply JSON only: {"keywords": ["...", "..."]}'},
                {"role": "user", "content": query}], max_tokens=100, timeout=30)
            if expanded and expanded.get("keywords"):
                keyword_query = " ".join(str(k) for k in expanded["keywords"][:8])
                journal.event("query_expand", "ok", keywords=keyword_query)
                landing = search(self.graph, keyword_query, k=8)
        journal.event("kg_search", "ok" if landing else "empty",
                      hits=[{"id": n["id"], "kind": n["kind"], "score": n["score"]}
                            for n in landing[:8]])
        if intent == "question":
            result = self._answer_question(query, landing, journal)
            report.update(result)
            return report
        if not landing:
            journal.close("no_landing")
            report["outcome"] = "no_landing"
            return report

        top = landing[0]
        repo = top["repo"]
        repo_path = Path(config.repos[repo]) if repo in config.repos else None
        impact_nodes = impact(self.graph, top["id"], depth=3)
        journal.event("impact", "ok", target=top["id"], affected=len(impact_nodes))

        # ④ 레거시 확인
        legacy_notes = self._legacy_notes(landing, repo_path)
        journal.event("legacy_check", "ok" if legacy_notes else "skipped",
                      bytes=len(legacy_notes))

        branch = intent_info["branch_prefix"] + journal.slug
        report["branch"] = branch
        report["repo"] = repo

        # plan-only 경로: 실레포 미접촉
        if not config.allow_write or repo_path is None:
            title, body = build_mr_draft(
                query, intent, f"{branch} (계획)", config.target_branch,
                [], "", impact_nodes,
                {"score": "plan-only", "theta": config.theta, "source": "none", "reasons": []})
            draft = save_draft(journal.dir, title, body)
            journal.event("plan_only", "ok", draft=str(draft),
                          reason="allow_write=False" if repo_path else f"repos에 '{repo}' 경로 없음")
            journal.close("planned")
            report.update({"outcome": "planned", "mr_draft": str(draft)})
            return report

        # ⑤ 브랜치
        try:
            repo_git = GitRepo(repo_path)
            base_branch = repo_git.current_branch()
            if not repo_git.is_clean():
                raise GitOpsError("워킹트리가 깨끗하지 않음 — 사람 확인 필요")
            repo_git.create_branch(branch)
        except GitOpsError as error:
            journal.event("branch", "fail", error=str(error))
            journal.close("branch_failed")
            report.update({"outcome": Outcome.BRANCH_FAILED.value,
                           "code": ErrorCode.GIT_DIRTY.value, "error": str(error)})
            return report
        journal.event("branch", "ok", branch=branch, base=base_branch)

        # ⑥ 구현 (코딩에이전트)
        prompt = build_prompt(query, intent, landing, legacy_notes)
        agent_result = run_agent(repo_path, prompt, journal.dir,
                                 config.agent_cmd, config.agent_timeout)
        journal.event("implement", "ok" if agent_result["ok"] else "fail",
                      error=agent_result.get("error"))
        if not agent_result["ok"]:
            journal.close("implement_failed")
            report.update({"outcome": Outcome.IMPLEMENT_FAILED.value,
                           "code": ErrorCode.AGENT_EXIT.value,
                           "error": agent_result.get("error")})
            return report

        repo_git.stage_all()
        changed = repo_git.staged_files(base_branch)
        diff_text = repo_git.staged_diff(base_branch)

        # ⑦-1 자동 검증(checks) — 회귀를 기계가 먼저 잡는다. 실패 시 MR 진행 차단.
        checks = run_checks(repo_path, changed, test_timeout=config.check_timeout)
        journal.event("checks", "fail" if checks["blocked"] else "ok", **checks["summary"])
        report["checks"] = checks["summary"]
        if checks["blocked"]:
            failed_detail = [r for r in checks["checks"] if r["status"] == "failed"]
            journal.event("checks_detail", "fail", detail=failed_detail)
            journal.close("checks_failed")
            syntax_failed = any(r["name"] == "py_syntax" for r in failed_detail)
            report.update({"outcome": Outcome.CHECKS_FAILED.value,
                           "code": (ErrorCode.CHECKS_SYNTAX if syntax_failed
                                    else ErrorCode.CHECKS_TEST).value,
                           "failed": failed_detail,
                           "note": f"브랜치 {branch} 보존 — 검증 실패 원인 조사용"})
            return report

        # ⑦-2 로컬 프리뷰 검증 (리소스 가드 내장)
        verify_report = verify(config.enable_verify, [repo], journal.dir, config.preview_base)
        journal.event("verify", "skipped" if verify_report.get("skipped") else "ok",
                      **verify_report)

        # ⑧ judge 게이트
        judge_result = judge(config, query, diff_text, changed, checks=checks["summary"])
        journal.event("judge", "pass" if judge_result["passed"] else "fail", **judge_result)
        report["judge"] = judge_result
        if not judge_result["passed"]:
            journal.close("judge_failed")
            report.update({"outcome": Outcome.JUDGE_FAILED.value,
                           "code": (ErrorCode.JUDGE_INFRA_VETO.value if judge_result.get("veto")
                                    else ErrorCode.JUDGE_BELOW_THETA.value),
                           "note": f"브랜치 {branch}는 조사용으로 보존"})
            return report

        # ⑨ MR 준비
        diff_stat = "\n".join(diff_text.splitlines()[:60])
        title, body = build_mr_draft(query, intent, branch, config.target_branch,
                                     changed, diff_stat, impact_nodes, judge_result,
                                     agent_summary=agent_result["output"][:500],
                                     checks=checks["checks"])
        draft = save_draft(journal.dir, title, body)
        commit = repo_git.commit_all(title, body)
        journal.event("commit", "ok", sha=commit[:12], files=len(changed))
        report["mr_draft"] = str(draft)

        if config.mode == "act":
            try:
                repo_git.push(branch)
                journal.event("push", "ok", branch=branch)
            except GitOpsError as error:
                journal.event("push", "fail", error=str(error))
                journal.close("push_failed")
                report.update({"outcome": Outcome.PUSH_FAILED.value,
                               "code": ErrorCode.GIT_PROTECTED_PUSH.value,
                               "error": str(error)})
                return report
            mr_result = create_gitlab_mr(config, repo, branch, title, body)
            journal.event("mr_create", "ok" if mr_result["ok"] else "fail", **mr_result)
            report["mr"] = mr_result

            # 배포 단계 (R20) — 기본 off. dry_run은 보낼 요청을 기록만, live는 이중 인터록.
            deploy_plan = plan_deploy(config, repo, branch, mr_result.get("url", ""))
            deploy_result = trigger_deploy(config, deploy_plan)
            journal.event("deploy", deploy_result["status"],
                          **{k: v for k, v in deploy_result.items() if k != "status"})
            report["deploy"] = deploy_result
        else:
            journal.event("mr_ready", "observe", draft=str(draft),
                          note="observe 모드 — 푸시/MR 생성 안 함")

        # ⑩ 사후: KG 증분 갱신 + journal
        try:
            refreshed = refresh_files(self.graph, repo, repo_path, changed)
            self.graph.save(config.kg_path)
            journal.event("kg_refresh", "ok", files=refreshed)
        except (OSError, KeyError) as error:
            journal.event("kg_refresh", "fail", error=str(error))
        journal.close("mr_prepared")
        report["outcome"] = "mr_prepared"
        return report
