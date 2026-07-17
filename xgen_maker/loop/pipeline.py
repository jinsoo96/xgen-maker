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
from ..kg.search import search, impact, retrieve_chain
from ..kg.build import refresh_files, git_head
from .intent import classify
from .converge import converge
from .git_ops import GitRepo, GitOpsError
from .journal import Journal
from .mr import build_mr_draft, save_draft, create_gitlab_mr
from .verify import verify


class MakerLoop:
    def __init__(self, config: MakerConfig, graph: Graph | None = None,
                 journal_factory=None):
        self.config = config
        # journal 주입(웹 SSE 등) — 전역 몽키패치 없이 인스턴스별로 스트리밍(동시성 안전)
        self._journal_factory = journal_factory or Journal
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
        # KG는 지도, 코드가 권위 — 착지 파일 실코드 발췌를 함께 첨부(그래프+실데이터 참조)
        repo = landing[0]["repo"] if landing else ""
        repo_path = Path(self.config.repos[repo]) if repo in self.config.repos else None
        legacy = self._legacy_notes(landing, repo_path)
        if legacy:
            answer += "\n\n실제 코드(권위):\n" + legacy
        journal.event("answer", "ok", hits=len(landing), code_cited=bool(legacy))
        journal.close("answered")
        return {"outcome": "answered", "answer": answer,
                "landing": landing[:10], "code_cited": bool(legacy)}

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
    def _cleanup_worktree(self) -> None:
        """격리 worktree를 어떤 종료 경로에서도 반드시 정리(브랜치·커밋은 보존됨).

        누수되면 (a) temp 디스크 축적 (b) 브랜치가 체크아웃 상태로 잡혀 rollback의
        `git branch -D`가 실패 (c) 같은 브랜치명 재실행 충돌.
        """
        path, main_git = self._worktree, self._main_git
        self._worktree = self._main_git = None
        if path is None:
            return None  # 정리할 worktree 없음
        removed = True
        try:
            if main_git is not None:
                main_git.remove_worktree(path)
        except Exception:  # noqa: BLE001 — 정리 실패가 결과를 덮지 않게
            removed = False
        try:
            import shutil
            shutil.rmtree(path, ignore_errors=True)
        except Exception:  # noqa: BLE001
            pass
        return removed

    def run(self, query: str) -> dict:
        self._worktree = None
        self._main_git = None
        try:
            return self._run_inner(query)
        finally:
            self._cleanup_worktree()

    def _run_inner(self, query: str) -> dict:
        config = self.config
        journal = self._journal_factory(config.worklogs_dir, query, verbose=config.verbose)
        from .cost import CostTracker
        cost = CostTracker()
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
        # 검색·LLM확장이 수 초~십수 초 걸릴 수 있어, 진입 즉시 진행 신호를 흘려 '멈춤'처럼 안 보이게.
        journal.event("kg_search", "start")
        landing = search(self.graph, query, k=8)
        if not landing and config.llm_enabled:
            journal.event("query_expand", "start", note="한글 쿼리 → 코드 키워드 확장(LLM)")
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
                            for n in landing[:8]],
                      landing=[{"id": n.get("id"), "name": n.get("name"), "kind": n.get("kind"),
                                "repo": n.get("repo"), "path": n.get("path"),
                                "line": n.get("line"), "score": n.get("score")}
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

        # ③-2 체인 검색 (graph-tool-call wRRF 차용) — 착지 파일의 워크플로우 체인
        chain_result = retrieve_chain(self.graph, query, k=6, hops=2)
        chain_nodes = chain_result["chain"]
        journal.event("chain", "ok", nodes=len(chain_nodes),
                      relations=list(chain_result["by_relation"].keys()))

        # ④ 레거시 확인
        legacy_notes = self._legacy_notes(landing, repo_path)
        journal.event("legacy_check", "ok" if legacy_notes else "skipped",
                      bytes=len(legacy_notes))

        # ④-2 과거 학습 조회 — 이 영역에서 겪은 실수/교훈을 꺼내 구현에 주입(실수 방지)
        from .learnings import retrieve, as_prompt_block, area_of, record
        area = area_of(landing)
        past = retrieve(config.learnings_dir, repo,
                        [top["name"], area, *query.split()], limit=5)
        if past:
            legacy_notes = (as_prompt_block(past) + "\n\n" + legacy_notes).strip()
            journal.event("learnings", "ok", count=len(past), area=area)

        # 브랜치명: 착지점(예: ontology-graph-section) 기반으로 의미있게 (팀 규칙: js·251205 금지)
        from ..config import suggest_branch, branch_name_issue
        prefix = intent_info["branch_prefix"] or "fix/"
        landing_kw = [top["name"], *(n["name"] for n in landing[1:3])]
        branch = suggest_branch(prefix, landing_kw)
        if branch_name_issue(branch):  # 착지명이 부실하면 쿼리 확장 키워드로 폴백
            branch = suggest_branch(prefix, query.split())
        if branch_name_issue(branch):
            branch = prefix + journal.slug
        report["branch"] = branch
        report["repo"] = repo

        # 인가 게이트 — act(실 push/MR)는 인가된 xgen 작업자만(대상 프로젝트 Developer+ 멤버십).
        # public 저장소라 코드는 누구나 받지만, 실 인프라 작업은 여기서 fail-fast 차단.
        if config.mode == "act":
            from .authz import authorize
            authz = authorize(config, repo, repo_path=repo_path)
            journal.event("authorize", "ok" if authz["ok"] else "denied",
                          **{k: v for k, v in authz.items() if k != "ok"})
            report["authorize"] = authz
            if not authz["ok"]:
                journal.close("unauthorized")
                report["outcome"] = "unauthorized"
                return report

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

        # ⑤ 브랜치 — 항상 최신 GitLab 코드에서 분기 + KG 최신화
        try:
            repo_git = GitRepo(repo_path)
            base_branch = repo_git.current_branch()
            if not repo_git.is_clean():
                raise GitOpsError("워킹트리가 깨끗하지 않음 — 사람 확인 필요")
            base_ref, changed_since = "", []
            fetch_sha = ""
            if getattr(config, "fetch_latest", False):
                try:
                    fetch_sha = repo_git.fetch(config.target_branch, token=config.gitlab_token)
                    base_ref = "FETCH_HEAD"
                    changed_since = repo_git.diff_names(base_branch, "FETCH_HEAD")[:200]
                except GitOpsError as fe:
                    journal.event("fetch_latest", "skipped", reason=str(fe)[:100])
            worktree_path = None
            main_git = repo_git
            self._main_git = main_git
            if getattr(config, "isolate_worktree", False):
                import tempfile
                worktree_path = Path(tempfile.mkdtemp(prefix="maker-wt-"))
                self._worktree = worktree_path  # 실패/조기return에도 finally가 정리
                repo_git = main_git.add_worktree(worktree_path, branch, base_ref or "HEAD")
                repo_path = worktree_path  # 이후 구현·검증은 격리 사본에서
                journal.event("worktree", "ok", path=str(worktree_path))
            else:
                repo_git.create_branch(branch, base_ref=base_ref)  # 최신 base에서 분기
            relanded_ok = False
            if base_ref and changed_since:
                refresh_files(self.graph, repo, repo_path, changed_since)
                # "최신 그래프로 작업" — 그래프가 갱신됐으니 착지 지도를 최신 그래프로 재계산.
                # 단, 브랜치·repo_path는 이미 원래 repo로 고정됐으므로 재착지 top이 '같은 repo'일
                # 때만 반영한다. 다른 repo로 튀면 landing/legacy_notes가 repo/worktree와 어긋나
                # 코딩에이전트에게 엉뚱한 레포 파일을 지시하게 되므로 원래 착지를 유지한다.
                relanded = search(self.graph, query, k=8)
                if relanded and relanded[0]["repo"] == repo:
                    landing = relanded
                    top = landing[0]
                    impact_nodes = impact(self.graph, top["id"], depth=3)
                    chain_nodes = retrieve_chain(self.graph, query, k=6, hops=2)["chain"]
                    # 최신 코드(FETCH_HEAD 체크아웃)에서 다시 발췌 — "코드가 권위"
                    legacy_notes = self._legacy_notes(landing, repo_path)
                    if past:  # 학습 메모리 재주입(재발췌로 덮였으므로)
                        legacy_notes = (as_prompt_block(past) + "\n\n" + legacy_notes).strip()
                    relanded_ok = True
                journal.event("fetch_latest", "ok", target=config.target_branch,
                              sha=fetch_sha[:12], kg_refreshed=len(changed_since),
                              relanded=relanded_ok)
        except GitOpsError as error:
            journal.event("branch", "fail", error=str(error))
            journal.close("branch_failed")
            report.update({"outcome": Outcome.BRANCH_FAILED.value,
                           "code": ErrorCode.GIT_DIRTY.value, "error": str(error)})
            return report
        journal.event("branch", "ok", branch=branch, base=base_branch, repo=repo)
        report["branch"] = branch  # 재착지로 top이 바뀌어도 브랜치명은 최초 결정 유지

        # diff 기준은 '실제로 분기한 지점'(fetch한 최신 target sha). 진입 시 current_branch()는
        # 이전 실행이 남긴 feature 브랜치일 수 있어, 그걸 기준 삼으면 변경 목록이 오염된다.
        diff_base = fetch_sha or base_branch

        # ⑥~⑧ 수렴 루프 — 구현 → 샌드박스+checks → judge → 실패 시 되먹여 재시도(통과까지)
        conv = converge(config, repo_path, repo, query, intent, landing, chain_nodes,
                        legacy_notes, diff_base, repo_git, journal, cost=cost,
                        graph=self.graph)
        report["iterations"] = conv["iterations"]
        report["converged"] = conv["converged"]

        if conv.get("stopped") == "implement_failed":
            journal.close("implement_failed")
            report.update({"outcome": Outcome.IMPLEMENT_FAILED.value,
                           "code": ErrorCode.AGENT_EXIT.value,
                           "error": conv.get("agent_error")})
            return report

        checks = conv["checks"]
        sandbox = conv["sandbox"]
        changed = conv["changed"]
        diff_text = conv["diff"]
        judge_result = conv.get("judge")
        report["checks"] = checks["summary"]
        report["sandbox"] = sandbox["status"]
        report["sandbox_isolated"] = bool(sandbox.get("isolated"))
        report["regression"] = checks.get("regression")  # verified|unverified|failed|none

        if not conv["converged"]:
            # 수렴 실패 — 마지막 실패 원인으로 분기(브랜치는 조사용 보존)
            if sandbox["status"] == "failed" or checks["blocked"]:
                failed_detail = ([sandbox] if sandbox["status"] == "failed" else []) + \
                    [r for r in checks["checks"] if r["status"] == "failed"]
                journal.event("checks_detail", "fail", detail=failed_detail)
                # 실수 방지 학습 기록 — 다음에 이 영역 작업 시 참고됨
                fnames = ", ".join(r.get("name", "?") for r in failed_detail)
                record(config.learnings_dir, repo, area, "pitfall",
                       f"{conv['iterations']}회 시도해도 검증({fnames}) 미통과 — 이 영역 변경 시 해당 검증 먼저 확인",
                       query)
                journal.close("checks_failed")
                syntax_failed = sandbox["status"] == "failed" or \
                    any(r["name"] == "py_syntax" for r in failed_detail)
                report.update({"outcome": Outcome.CHECKS_FAILED.value,
                               "code": (ErrorCode.CHECKS_SYNTAX if syntax_failed
                                        else ErrorCode.CHECKS_TEST).value,
                               "failed": failed_detail,
                               "note": f"{conv['iterations']}회 시도 후에도 검증 미통과 — 브랜치 {branch} 보존"})
                return report
            record(config.learnings_dir, repo, area, "pitfall",
                   f"품질 게이트 미달(judge {(judge_result or {}).get('score')}) — 이 영역은 "
                   f"{', '.join((judge_result or {}).get('reasons', [])[:2])}", query)
            journal.close("judge_failed")
            report["judge"] = judge_result
            report.update({"outcome": Outcome.JUDGE_FAILED.value,
                           "code": (ErrorCode.JUDGE_INFRA_VETO.value
                                    if (judge_result or {}).get("veto")
                                    else ErrorCode.JUDGE_BELOW_THETA.value),
                           "note": f"{conv['iterations']}회 시도 후에도 품질 미달 — 브랜치 {branch} 보존"})
            return report

        report["judge"] = judge_result

        # ⑦-2 로컬 프리뷰 검증 (리소스 가드 내장)
        verify_report = verify(config.enable_verify, [repo], journal.dir,
                               config.preview_base, config)
        journal.event("verify", "skipped" if verify_report.get("skipped") else "ok",
                      **verify_report)

        # ⑦-3 UI/UX 검증 — 영향 라우트 스냅샷 + 픽셀diff + 비전판정 (Visual Feedback Loop)
        if config.enable_ui_verify:
            from .ui_verify import ui_verify
            ui_report = ui_verify(config, self.graph, changed, repo, journal.dir)
            journal.event("ui_verify", "skipped" if ui_report.get("skipped")
                          else ("fail" if ui_report.get("problems") else "ok"),
                          **{k: v for k, v in ui_report.items() if k != "results"})
            report["ui_verify"] = {k: v for k, v in ui_report.items() if k != "results"}
            # UI 문제를 신호화 — 학습 기록 + report 플래그(다음 작업 참고)
            if ui_report.get("problems"):
                probs = "; ".join(
                    "; ".join(r["vision"]["issues"])
                    for r in ui_report.get("results", [])
                    if r.get("vision") and not r["vision"].get("renders_ok"))
                record(config.learnings_dir, repo, area, "pitfall",
                       f"UI 검증: {probs[:200]}" if probs else "UI 렌더/픽셀 회귀 감지", query)
                report["ui_problems"] = ui_report["problems"]

        # ⑦-4 배포 렌더 검증 (T1, 상사님 tmp 방식) — "코드 통과 + 배포 통과 → 자신 있게 MR"
        deploy_test = {"name": "deploy_render", "status": "skipped",
                       "reason": "enable_deploy_test=False"}
        if config.enable_deploy_test:
            from .deploy import deploy_render_test
            from ..kg.extract_infra import deploy_targets
            targets = deploy_targets(self.graph, repo, config.deploy_app_map)
            if targets:
                journal.event("deploy_test", "targets",
                              domains=[t["domain"] for t in targets if t["domain"]])
                report["deploy_targets"] = targets
            deploy_test = deploy_render_test(config, repo)
            journal.event("deploy_test", deploy_test["status"], **deploy_test)
            report["deploy_test"] = deploy_test
            if deploy_test["status"] == "failed":
                journal.close("deploy_test_failed")
                report.update({"outcome": Outcome.CHECKS_FAILED.value,
                               "code": ErrorCode.CHECKS_TEST.value,
                               "failed": [deploy_test],
                               "note": f"배포 렌더 실패 — 브랜치 {branch} 보존(MR 안 냄)"})
                return report

        # ⑨ MR 준비 — 릴리즈 사다리(develop→stg→main) 뷰 포함
        from .release import release_view, render_ladder_md
        rel_view = release_view(self.graph, repo, config.target_branch, config)
        report["release"] = {"lands_on_env": rel_view["lands_on_env"],
                             "promotion_remaining": rel_view["promotion_remaining"]}
        journal.event("release", "ok", env=rel_view["lands_on_env"],
                      promotion=rel_view["promotion_remaining"])
        diff_stat = "\n".join(diff_text.splitlines()[:60])
        title, body = build_mr_draft(query, intent, branch, config.target_branch,
                                     changed, diff_stat, impact_nodes, judge_result,
                                     agent_summary=conv.get("agent_summary", ""),
                                     checks=checks["checks"] + [sandbox, deploy_test],
                                     release_md=render_ladder_md(rel_view),
                                     regression=checks.get("regression", ""),
                                     sandbox_isolated=bool(sandbox.get("isolated")))
        # 정직성 신호 — 레거시가 미검증인 채 MR이 준비되면 명시(오해 방지)
        if checks.get("regression") == "unverified":
            journal.event("mr_ready", "warn", regression="unverified",
                          note="레거시 회귀 미검증(테스트 환경 없음)")
            report["regression_warning"] = "레거시 회귀 미검증 — 테스트 환경에서 재검증 필요"
        draft = save_draft(journal.dir, title, body)
        commit = repo_git.commit_all(title, body,
                                     author_name=config.git_author_name,
                                     author_email=config.git_author_email)
        journal.event("commit", "ok", sha=commit[:12], files=len(changed))
        report["mr_draft"] = str(draft)

        if config.mode == "act":
            try:
                repo_git.push(branch, token=config.gitlab_token)
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

        # ⑨-끝: MAKER의 자동화는 여기까지 — MR 준비. 이후(머지·빌드·ArgoCD sync·배포)는 사용자 수동.
        journal.event("mr_ready",
                      "observe" if config.mode != "act" else "act",
                      draft=str(draft),
                      next_manual=[f"MR 리뷰·머지 → {config.target_branch}",
                                   f"Jenkins 빌드({rel_view['lands_on_env']})",
                                   "ArgoCD 수동 sync → k3s"],
                      note="MAKER는 MR 준비까지. 배포는 사용자 몫(로그/상태는 maker status로 관측)")
        report["next_manual"] = "머지 → Jenkins 빌드 → ArgoCD sync (사용자 수동)"

        # ⑩ 사후: KG 증분 갱신 + journal
        try:
            refreshed = refresh_files(self.graph, repo, repo_path, changed)
            # repo_heads 전진 — 반드시 **메인 클론**의 HEAD로. worktree의 feature HEAD를 넣으면
            # 다음 kg sync가 메인 클론에서 그 sha를 못 봐 역방향 diff로 KG를 되돌려버린다.
            new_head = git_head(main_git.path)
            if new_head:
                self.graph.meta.setdefault("repo_heads", {})[repo] = new_head
            self.graph.save(config.kg_path)
            journal.event("kg_refresh", "ok", files=refreshed, head=(new_head or "")[:12])
        except (OSError, KeyError) as error:
            journal.event("kg_refresh", "fail", error=str(error))
        # 성공 학습 — 이 영역에서 통과한 접근 기록(다음 작업 참고)
        record(config.learnings_dir, repo, area, "fix",
               f"'{query[:60]}' → {conv['iterations']}회 수렴 통과, 변경 {len(changed)}파일", query)
        # 격리 worktree 정리(브랜치·커밋은 보존됨) — 실패 경로는 run()의 finally가 처리
        if worktree_path is not None:
            removed = self._cleanup_worktree()
            journal.event("worktree", "removed" if removed else "remove_failed",
                          path=str(worktree_path))
        report["cost"] = cost.summary()
        journal.event("cost", "ok", **cost.summary())
        journal.close("mr_prepared")
        report["outcome"] = "mr_prepared"
        return report
