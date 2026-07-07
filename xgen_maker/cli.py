"""XGEN MAKER CLI.

사용:
  python -m xgen_maker kg build --repo NAME=PATH[::SCOPE] ... --out kg/
  python -m xgen_maker kg merge --out kg/merged.json kg/*.repo.json
  python -m xgen_maker kg dashboard --kg kg/merged.json --out kg/dashboard.html
  python -m xgen_maker kg search --kg kg/merged.json "질의" [-k 10]
  python -m xgen_maker kg impact --kg kg/merged.json NODE_ID [--depth 3]
  python -m xgen_maker run "쿼리" --config maker.config.json
  python -m xgen_maker mcp --kg kg/merged.json
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .config import MakerConfig
from .kg.graph import Graph
from .kg.build import build_repo, merge_and_link
from .kg.search import search, impact, retrieve_chain
from .kg.dashboard import render_dashboard


def _overlay_path(kg_path: str) -> Path:
    return Path(kg_path).parent / "overlay.json"


def _apply_overlay_and_save(graph: Graph, kg_path: str) -> None:
    from .kg.overlay import load_overlay, apply_overlay
    overlay = load_overlay(_overlay_path(kg_path))
    if overlay["node_overrides"] or overlay["custom_edges"]:
        result = apply_overlay(graph, overlay)
        graph.save(kg_path)
        print(f"[kg overlay] 사람 편집 {result['applied']}건 재적용"
              + (f" (미존재 {len(result['missing'])}건)" if result["missing"] else ""))


def _parse_repo_spec(spec: str) -> tuple[str, str, str | None]:
    """NAME=PATH 또는 NAME=PATH::SCOPE (윈도우 드라이브 콜론과 충돌 없게 '::' 사용)."""
    name, _, rest = spec.partition("=")
    if not rest:
        raise SystemExit(f"--repo 형식 오류: {spec} (NAME=PATH[::SCOPE])")
    path, _, scope = rest.partition("::")
    return name, path, scope or None


def cmd_kg_build(args) -> None:
    out_dir = Path(args.out)
    for spec in args.repo:
        name, path, scope = _parse_repo_spec(spec)
        graph = build_repo(name, path, scope, max_files=args.max_files)
        out_path = out_dir / f"{name}.repo.json"
        graph.save(out_path)
        print(f"[kg build] {name}: {json.dumps(graph.stats(), ensure_ascii=False)}"
              f" scope={scope or '-'} → {out_path}")


def cmd_kg_infra(args) -> None:
    import os
    from .kg.extract_infra import extract_infra
    path = args.path or os.environ.get("XGEN_MAKER_INFRA_PATH", "")
    if not path:
        print("[kg infra] 인프라 경로 미지정 — --path 또는 XGEN_MAKER_INFRA_PATH 설정")
        return
    graph = extract_infra(path)
    out = args.out or "kg/infra.repo.json"
    graph.save(out)
    print(f"[kg infra] {json.dumps(graph.stats()['nodes_by_kind'], ensure_ascii=False)} → {out}")
    for p in graph.nodes_by_kind("deploy_project"):
        print(f"  · {p['name']}: ns={p['meta']['namespace']} domains={p['meta']['domains']}")


def cmd_kg_merge(args) -> None:
    from .kg.extract_infra import link_infra_to_code
    graphs = [Graph.load(p) for p in args.inputs]
    merged, links = merge_and_link(graphs)
    infra_links = link_infra_to_code(merged)  # helm_app ↔ 코드 레포 연결
    merged.meta["infra_code_links"] = infra_links
    merged.save(args.out)
    print(f"[kg merge] {json.dumps(merged.stats(), ensure_ascii=False)}")
    print(f"[kg merge] crossrepo {links} · infra→code {infra_links} 링크 → {args.out}")
    _apply_overlay_and_save(merged, args.out)


def cmd_kg_dashboard(args) -> None:
    graph = Graph.load(args.kg)
    _apply_overlay_and_save(graph, args.kg) if False else None  # 오버레이는 로드 시 별도 적용
    from .kg.overlay import load_overlay, apply_overlay
    overlay = load_overlay(_overlay_path(args.kg))
    if overlay["node_overrides"] or overlay["custom_edges"]:
        apply_overlay(graph, overlay)
    out = render_dashboard(graph, args.out, max_nodes=args.max_nodes)
    print(f"[kg dashboard] {out} ({out.stat().st_size:,} bytes)")
    if not args.no_open:
        import webbrowser
        webbrowser.open(out.resolve().as_uri())
        print(f"[kg dashboard] 브라우저에서 열기 → {out.resolve().as_uri()}")


def cmd_kg_search(args) -> None:
    graph = Graph.load(args.kg)
    for hit in search(graph, args.query, k=args.k):
        print(f"{hit['score']:>6}  [{hit['kind']}] {hit['name']}  {hit['repo']}:{hit['path']}")


def cmd_kg_chain(args) -> None:
    graph = Graph.load(args.kg)
    result = retrieve_chain(graph, args.query, k=args.k, hops=args.hops)
    print(f"[seeds] {len(result['seeds'])}")
    for hit in result["seeds"]:
        print(f"  {hit['score']:>6}  [{hit['kind']}] {hit['name']}  {hit['repo']}:{hit['path']}")
    print(f"[chain — RRF 융합, 관계: {list(result['by_relation'])}]")
    for node in result["chain"]:
        if node["hop"] > 0:
            print(f"  hop{node['hop']} ({'/'.join(node['relation'])})  [{node['kind']}] "
                  f"{node['name']}  {node['repo']}:{node['path']}")


def cmd_kg_impact(args) -> None:
    graph = Graph.load(args.kg)
    results = impact(graph, args.node_id, depth=args.depth)
    if not results:
        print("(영향 노드 없음 또는 노드 미존재)")
    for node in results:
        print(f"d={node['distance']}  [{node['kind']}] {node['name']}  {node['repo']}:{node['path']}")


def cmd_kg_enrich(args) -> None:
    from .kg.enrich import enrich_deterministic, enrich_llm
    graph = Graph.load(args.kg)
    filled = enrich_deterministic(graph)
    print(f"[kg enrich] 결정론 요약 {filled}개 주입")
    if not args.no_llm:
        config = MakerConfig.from_file(args.config) if args.config else MakerConfig()
        stats = enrich_llm(graph, config.llm_base, config.llm_model, config.repos,
                           limit=args.limit)
        print(f"[kg enrich] LLM 요약: {json.dumps(stats, ensure_ascii=False)}")
    graph.save(args.kg)
    print(f"[kg enrich] 저장 → {args.kg}")


def cmd_kg_domains(args) -> None:
    from .kg.domains import build_domains, render_domain_map
    graph = Graph.load(args.kg)
    created = build_domains(graph)
    graph.save(args.kg)
    out = render_domain_map(graph, args.out)
    print(f"[kg domains] 도메인 {created}개 생성 → {args.kg}")
    print(f"[kg domains] 도메인 맵 → {out} ({out.stat().st_size:,} bytes)")
    if not args.no_open:
        import webbrowser
        webbrowser.open(out.resolve().as_uri())


def cmd_kg_tour(args) -> None:
    from .kg.tour import render_tour
    graph = Graph.load(args.kg)
    out = render_tour(graph, args.repo, args.out, limit=args.limit)
    print(f"[kg tour] {args.repo} 읽기 순서 → {out}")


def cmd_kg_sync(args) -> None:
    from .kg.sync import sync_all
    from .kg.enrich import enrich_deterministic
    graph = Graph.load(args.kg)
    results = sync_all(graph)
    total = sum(r.get("changed", 0) for r in results)
    if total or any(r.get("action") for r in results):
        enrich_deterministic(graph)  # 새 노드에만 요약 채움
        graph.save(args.kg)
        _apply_overlay_and_save(graph, args.kg)
    if not args.quiet:
        for result in results:
            print(f"[kg sync] {json.dumps(result, ensure_ascii=False)}")
        print(f"[kg sync] 총 {total}개 파일 증분 반영 → {args.kg}")


def cmd_kg_annotate(args) -> None:
    from .kg.overlay import annotate, add_custom_edge, load_overlay
    overlay_path = _overlay_path(args.kg)
    if args.list:
        overlay = load_overlay(overlay_path)
        print(json.dumps(overlay, ensure_ascii=False, indent=1))
        return
    if args.edge_to:
        add_custom_edge(overlay_path, args.node_id, args.edge_to,
                        kind=args.edge_kind, note=args.note or "")
        print(f"[kg annotate] 커스텀 엣지 {args.node_id} -{args.edge_kind}-> {args.edge_to}")
    else:
        deprecated = True if args.deprecate else (False if args.undeprecate else None)
        edits = annotate(overlay_path, args.node_id, summary=args.summary,
                         note=args.note, deprecated=deprecated,
                         redirect=args.redirect,
                         tags=args.tag if args.tag else None)
        print(f"[kg annotate] {args.node_id}: {json.dumps(edits, ensure_ascii=False)}")
    graph = Graph.load(args.kg)
    _apply_overlay_and_save(graph, args.kg)


def cmd_kg_hook(args) -> None:
    from .kg.sync import install_hooks, remove_hooks
    maker_dir = Path(__file__).resolve().parent.parent
    kg_abs = Path(args.kg).resolve()
    if args.hook_action == "install":
        results = install_hooks(args.repo_path, maker_dir, kg_abs, args.python)
    else:
        results = remove_hooks(args.repo_path)
    for line in results:
        print(f"[kg hook] {args.repo_path}: {line}")


def cmd_run(args) -> None:
    from .loop.pipeline import MakerLoop
    config = MakerConfig.from_file(args.config) if args.config else MakerConfig()
    if args.kg:
        config.kg_path = args.kg
    if args.mode:
        # 웹/chat과 동일 매핑: plan=쓰기없음(분석·MR초안만), observe/act=쓰기(로컬/푸시)
        if args.mode == "plan":
            config.allow_write = False
        else:
            config.allow_write = True
            config.mode = args.mode
    loop = MakerLoop(config)
    report = loop.run(args.query)
    print(json.dumps(report, ensure_ascii=False, indent=2, default=str))
    if report.get("outcome") in ("judge_failed", "branch_failed", "implement_failed",
                                 "push_failed", "no_landing"):
        sys.exit(1)


def cmd_mcp(args) -> None:
    from .mcp_server import main as mcp_main
    mcp_main(args.kg, args.config)


def cmd_chat(args) -> None:
    from .chat import run_chat
    run_chat(args.config)


def cmd_web(args) -> None:
    from .web import serve
    if args.open:
        import webbrowser, threading
        threading.Timer(1.2, lambda: webbrowser.open(f"http://{args.host}:{args.port}")).start()
    serve(args.config, args.host, args.port)


def cmd_ui(args) -> None:
    from .loop.ui_verify import ui_verify, affected_routes
    from pathlib import Path as _P
    config = MakerConfig.from_file(args.config) if args.config else MakerConfig()
    if args.kg:
        config.kg_path = args.kg
    if args.preview:
        config.preview_base = args.preview
    graph = Graph.load(config.kg_path)
    changed = args.changed or []
    if args.ui_action == "routes":
        routes = affected_routes(graph, changed, args.repo)
        print(json.dumps([r["route"] for r in routes], ensure_ascii=False))
        return
    out_dir = _P(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    if args.ui_action == "baseline":
        # 현재 스택 화면을 baseline으로 저장
        from .loop.verify import playwright_snapshot, http_reachable
        baseline_dir = _P(config.kg_path).parent / "ui-baselines"
        baseline_dir.mkdir(parents=True, exist_ok=True)
        if not http_reachable(config.preview_base, timeout=6):
            print(f"[ui baseline] {config.preview_base} 미도달"); return
        targets = [r["route"] for r in affected_routes(graph, changed, args.repo)] \
            if changed else [args.route or "/"]
        for rp in targets:
            if "[" in rp:
                continue
            slug = rp.strip("/").replace("/", "_") or "root"
            url = config.preview_base.rstrip("/") + (rp if rp != "/" else "")
            snap = playwright_snapshot(url, baseline_dir / f"{slug}.png")
            print(f"[ui baseline] {rp} → {snap.get('ok')}")
        return
    # verify
    report = ui_verify(config, graph, changed, args.repo, out_dir, vision=not args.no_vision)
    print(json.dumps(report, ensure_ascii=False, indent=2, default=str))


def cmd_undo(args) -> None:
    from .loop.rollback import last_action, undo
    config = MakerConfig.from_file(args.config) if args.config else MakerConfig()
    action = last_action(config.worklogs_dir)
    if action is None:
        print("[undo] 되돌릴 MAKER 액션 없음(브랜치 만든 세션 없음)")
        return
    print(f"[undo] 대상: {action['repo']} 브랜치 {action['branch']} "
          f"(base {action['base']}, pushed={action['pushed']}, MR={action['mr'] or '-'})")
    if not args.yes:
        print("  실제 되돌리려면 --yes (원격 브랜치까지 삭제하려면 --remote 추가)")
        return
    result = undo(config, action, delete_remote=args.remote)
    for s in result["steps"]:
        print(f"  ✓ {s}")
    for e in result["errors"]:
        print(f"  ✗ {e}")
    if result.get("mr_note"):
        print(f"  ! {result['mr_note']}")


def cmd_login(args) -> None:
    from .auth import (Auth, save_auth, claude_cli_status, load_auth,
                       gitlab_login_password, gitlab_verify_token)
    # GitLab 로그인 서브흐름 — 이메일/비번 or 토큰. 한 번 저장하면 push·MR 재입력 불필요.
    if args.gitlab_token or args.gitlab_user or args.gitlab_password:
        auth = load_auth()
        url = args.gitlab_url or auth.gitlab_url
        token = args.gitlab_token
        if not token and args.gitlab_user and args.gitlab_password:
            print("GitLab 이메일/비번 → 토큰 교환 시도(OAuth)…")
            res = gitlab_login_password(url, args.gitlab_user, args.gitlab_password)
            if res["ok"]:
                token = res["token"]
            else:
                print(f"✗ 비번 로그인 실패: {res['reason']}")
                print("  → 대신 개인 액세스 토큰: maker login --gitlab-token <PAT>")
                return
        if not token:
            print("✗ --gitlab-token 또는 --gitlab-user+--gitlab-password 필요")
            return
        verify = gitlab_verify_token(url, token)
        if not verify["ok"]:
            print(f"✗ GitLab 토큰 검증 실패: {verify['reason']}")
            return
        auth.gitlab_url = url
        auth.gitlab_user = args.gitlab_user or verify.get("user", "")
        auth.gitlab_token = token
        path = save_auth(auth)
        print(f"✓ GitLab 로그인 저장: user={verify['user']} (id {verify['id']}) → {path}")
        print("  이제 push·MR이 이 로그인으로 재입력 없이 됩니다.")
        return

    provider = args.provider
    if provider is None:
        # 자동: claude CLI 로그인돼 있으면 그걸로, 아니면 안내
        status = claude_cli_status()
        if status["authenticated"]:
            provider = "claude_cli"
            print("✓ claude CLI 로그인 감지 — provider=claude_cli (API 키 불필요)")
        else:
            print("claude CLI 미인증. 다음 중 하나:")
            print("  1) 터미널에서 `claude` 실행해 로그인 후 `maker login` 재실행")
            print("  2) `maker login --provider anthropic --api-key sk-ant-...`")
            print("  3) `maker login --provider vllm --base http://... --model ...`")
            return
    auth = Auth(provider=provider, model=args.model or "",
                api_key=args.api_key or "", base=args.base or "")
    if provider == "claude_cli":
        status = claude_cli_status()
        if not status["authenticated"]:
            print(f"✗ claude CLI 미인증: {status['reason']}\n  터미널에서 `claude` 로그인 후 재시도.")
            return
    elif provider == "anthropic" and not auth.api_key:
        print("✗ --api-key 필요 (anthropic)")
        return
    auth.gitlab_url = load_auth().gitlab_url
    auth.gitlab_user = load_auth().gitlab_user
    auth.gitlab_token = load_auth().gitlab_token  # 기존 GitLab 로그인 보존
    path = save_auth(auth)
    print(f"✓ 로그인 저장: provider={provider} model={auth.resolved_model()} → {path}")
    print("  Claude는 claude CLI 세션이 지속됩니다(클로드 코드처럼 당분간 로그인 유지).")
    print("  GitLab push·MR도 쓰려면: maker login --gitlab-user <이메일> --gitlab-password <비번>")


def cmd_whoami(args) -> None:
    from .auth import load_auth, claude_cli_status, gitlab_verify_token, AUTH_FILE
    auth = load_auth()
    print(f"provider : {auth.provider}")
    print(f"model    : {auth.resolved_model()}")
    print(f"base     : {auth.resolved_base()}")
    print(f"key set  : {'yes' if auth.api_key else 'no (구독 로그인)' if auth.provider=='claude_cli' else 'no'}")
    print(f"저장위치 : {AUTH_FILE} ({'있음' if AUTH_FILE.exists() else '없음(기본 claude_cli)'})")
    if auth.provider == "claude_cli":
        status = claude_cli_status()
        print(f"Claude   : {'✓ claude CLI 로그인 유지됨' if status['authenticated'] else '✗ ' + status['reason']}")
    if auth.gitlab_token:
        v = gitlab_verify_token(auth.gitlab_url, auth.gitlab_token)
        print(f"GitLab   : {'✓ ' + str(v.get('user')) + ' 로그인 유지됨' if v['ok'] else '✗ ' + v['reason']} "
              f"({auth.gitlab_url})")
    else:
        print("GitLab   : 미로그인 — maker login --gitlab-user <이메일> --gitlab-password <비번>")


def cmd_doctor(args) -> None:
    from .doctor import run_doctor
    ok = run_doctor(args.config)
    if not ok:
        sys.exit(1)


def cmd_sdk(args) -> None:
    from .sdk_check import self_check, maker_catalog
    r = self_check()
    v = {"ok": "✓ 최신·호환", "drift": "! 동작하나 최신 아님(업그레이드 시 재검증)",
         "broken": "✗ 계약 깨짐 — 엔진 API 변경"}[r["verdict"]]
    print(f"═══ SDK 자가검증: {r['verdict']} — {v} ═══")
    for pkg, d in r["drift"].items():
        mark = "↑뒤짐" if d["behind"] else "최신"
        print(f"  {pkg}: 설치 {d['installed']} / PyPI {d['latest']}  [{mark}]")
    c = r["contract"]
    print(f"  계약: engine={c['engine']} · present {len(c['present'])} · "
          f"missing {c['missing'] or '없음'} · sandbox {'OK' if c.get('sandbox_ok') else 'X'}")
    if args.catalog:
        print("\n═══ MAKER 자기 카탈로그 ═══")
        print(json.dumps(maker_catalog(), ensure_ascii=False, indent=1))
    if r["verdict"] == "broken":
        sys.exit(1)


def cmd_engine(args) -> None:
    from .engine_stage import register, run_via_engine, STAGE_ID
    if args.engine_action == "run":
        if not args.query:
            print("✗ engine run엔 쿼리 필요: maker engine run \"...\"")
            sys.exit(1)
        r = run_via_engine(args.query, args.config, allow_write=False)  # 엔진 경유는 plan-only
        if not r["ok"]:
            print(f"✗ {r['reason']}"); sys.exit(1)
        print(f"✓ 엔진이 MAKER 구동(R3 Level B) — outcome={r['outcome']}")
        print(f"  loop_decision={r['engine_state']['loop_decision']} · "
              f"session_saved={r['engine_state']['session_saved']}")
        print(f"  {r['engine_state']['final_output']}")
        return
    r = register()
    if r["ok"]:
        print(f"✓ MAKER 스테이지 '{r['stage_id']}' 등록됨 → {r['engine']} {r['version']}")
        print("  엔진 Pipeline이 MAKER를 정식 stage로 인지·실행 (phase=act, role=maker)")
    else:
        print(f"✗ 등록 실패: {r['reason']}")
        sys.exit(1)


def cmd_status(args) -> None:
    """read-only 관측 — Jenkins 빌드 + ArgoCD 배포 상태. MAKER는 트리거 안 함."""
    from .loop import jenkins, argocd
    from .loop.release import ladder
    print("═══ 배포 상태 (read-only — MAKER는 배포 안 함, 사용자 수동) ═══\n")
    print("릴리즈 사다리:")
    for s in ladder():
        print(f"  {s['branch']:8} → {s['env']:4} {s.get('url',''):32} Jenkins={s.get('jenkins','')}")
    print("\nJenkins jobs:", end=" ")
    if jenkins.available():
        jobs = jenkins.list_jobs()
        print(f"{len(jobs)}개")
        for j in jobs:
            print(f"  · {j['name']:24} env={j['env'] or '-':4} [{j['color']}]")
    else:
        print("미설정 (.env에 XGEN_MAKER_JENKINS_URL/USER/TOKEN)")
    print("\nArgoCD apps:", end=" ")
    if argocd.available():
        apps = argocd.list_apps()
        print(f"{len(apps)}개")
        for a in apps[:20]:
            print(f"  · {a['name']:28} sync={a['sync']:10} health={a['health']}")
    else:
        print("미설정 (.env에 XGEN_MAKER_ARGOCD_URL/USER/TOKEN)")


def cmd_mrs(args) -> None:
    from .loop.gitlab_observe import my_mrs, maker_mrs
    config = MakerConfig.from_file(args.config) if args.config else MakerConfig()
    which = maker_mrs(config) if args.maker else my_mrs(config, args.state)
    label = "MAKER가 만든 MR" if args.maker else f"내 MR ({args.state})"
    print(f"═══ {label} ═══")
    if not which:
        print("  (없음 — 토큰 미설정이면 .env의 XGEN_MAKER_GITLAB_TOKEN 확인)")
    for m in which:
        print(f"  !{m['iid']} [{m['state']:6}] {m['source']}→{m['target']} · {m['updated']}")
        print(f"       {m['title'][:70]}")
        if m.get("url"):
            print(f"       {m['url']}")


def cmd_branches(args) -> None:
    from .loop.gitlab_observe import branches
    config = MakerConfig.from_file(args.config) if args.config else MakerConfig()
    b = branches(config, args.repo)
    if "error" in b:
        print(f"[branches] {b['error']}")
        return
    print(f"═══ {args.repo} 브랜치 ({b['total']}개) ═══")
    print(f"  release: {b['release']}  ·  보호: {b['protected']}")
    print(f"  작업 브랜치(최근 {len(b['work_recent'])}):")
    for w in b["work_recent"]:
        merged = "✓머지" if w["merged"] else "     "
        print(f"    {merged} {w['name'][:50]:50} {w['when']} {w['author']}")


def cmd_learn(args) -> None:
    from .loop.learnings import record, retrieve, _all
    config = MakerConfig.from_file(args.config) if args.config else MakerConfig()
    if args.note:  # 기록
        record(config.learnings_dir, args.repo, args.area or "general",
               args.kind, args.note)
        print(f"[learn] 기록됨 → {config.learnings_dir} ({args.repo}/{args.area})")
    else:  # 조회
        entries = _all(config.learnings_dir, args.repo)
        print(f"═══ {args.repo} 학습 {len(entries)}건 ═══")
        for e in entries[-20:]:
            print(f"  ({e['kind']}) {e.get('area','')}: {e['note']}")


def cmd_history(args) -> None:
    from .loop.history import read_sessions
    config = MakerConfig.from_file(args.config) if args.config else MakerConfig()
    sessions = read_sessions(config.worklogs_dir, args.limit)
    print(f"═══ MAKER 작업 이력 ({len(sessions)}세션) ═══")
    if not sessions:
        print("  (worklogs 없음)")
    for s in sessions:
        print(f"  [{s['outcome']:12}] {s['query'][:52]}")
        detail = " · ".join(filter(None, [
            f"브랜치 {s['branch']}" if s["branch"] else "",
            f"env {s['env']}" if s["env"] else "",
            f"MR {s['mr']}" if s["mr"] else ""]))
        if detail:
            print(f"       {detail}")


def cmd_release(args) -> None:
    from .loop.release import release_view, render_ladder_md
    config = MakerConfig.from_file(args.config) if args.config else MakerConfig()
    if args.kg:
        config.kg_path = args.kg
    graph = Graph.load(config.kg_path)
    view = release_view(graph, args.repo, args.branch or config.target_branch, config)
    print(f"[release] {args.repo}: 이 변경은 '{args.branch or config.target_branch}' "
          f"→ 환경 '{view['lands_on_env']}'")
    print(f"[release] 승격 경로: {' → '.join(view['promotion_remaining'])}")
    print(render_ladder_md(view))


def cmd_deploy(args) -> None:
    from .loop.deploy import deploy_render_test, app_for_repo
    config = MakerConfig.from_file(args.config) if args.config else MakerConfig()
    if args.infra:
        config.infra_path = args.infra
    if args.deploy_action == "test":
        result = deploy_render_test(config, args.repo)
        print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
        if result["status"] == "failed":
            sys.exit(1)
    elif args.deploy_action == "apps":
        from .loop.deploy import _REPO_TO_APP
        print(json.dumps(_REPO_TO_APP, ensure_ascii=False, indent=2))


def main(argv: list[str] | None = None) -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    from .dotenv import ensure_loaded
    ensure_loaded()  # .env의 GitLab 토큰·LLM 키 등을 환경변수로 자동 주입
    parser = argparse.ArgumentParser(prog="xgen-maker", description="XGEN MAKER")
    sub = parser.add_subparsers(dest="command", required=True)

    kg = sub.add_parser("kg", help="지식그래프 빌드/검색/대시보드")
    kg_sub = kg.add_subparsers(dest="kg_command", required=True)

    p = kg_sub.add_parser("build")
    p.add_argument("--repo", action="append", required=True, help="NAME=PATH[::SCOPE]")
    p.add_argument("--out", default="kg")
    p.add_argument("--max-files", type=int, default=20000)
    p.set_defaults(func=cmd_kg_build)

    p = kg_sub.add_parser("merge")
    p.add_argument("inputs", nargs="+")
    p.add_argument("--out", default="kg/merged.json")
    p.set_defaults(func=cmd_kg_merge)

    p = kg_sub.add_parser("infra", help="인프라 KG 추출(ArgoCD·Helm·도메인) — LLM이 배포 토폴로지 인지")
    p.add_argument("--path", default=None, help="인프라 레포 경로(미지정 시 XGEN_MAKER_INFRA_PATH)")
    p.add_argument("--out", default="kg/infra.repo.json")
    p.set_defaults(func=cmd_kg_infra)

    p = kg_sub.add_parser("dashboard")
    p.add_argument("--kg", default="kg/merged.json")
    p.add_argument("--out", default="kg/dashboard.html")
    p.add_argument("--max-nodes", type=int, default=1200)
    p.add_argument("--no-open", action="store_true", help="브라우저 자동 열기 비활성화")
    p.set_defaults(func=cmd_kg_dashboard)

    p = kg_sub.add_parser("search")
    p.add_argument("query")
    p.add_argument("--kg", default="kg/merged.json")
    p.add_argument("-k", type=int, default=10)
    p.set_defaults(func=cmd_kg_search)

    p = kg_sub.add_parser("impact")
    p.add_argument("node_id")
    p.add_argument("--kg", default="kg/merged.json")
    p.add_argument("--depth", type=int, default=3)
    p.set_defaults(func=cmd_kg_impact)

    p = kg_sub.add_parser("chain", help="체인 검색 — 단일 매치가 아니라 워크플로우 체인(graph-tool-call wRRF)")
    p.add_argument("query")
    p.add_argument("--kg", default="kg/merged.json")
    p.add_argument("-k", type=int, default=6)
    p.add_argument("--hops", type=int, default=2)
    p.set_defaults(func=cmd_kg_chain)

    p = kg_sub.add_parser("enrich", help="의미층 주입 (결정론 + LLM 요약)")
    p.add_argument("--kg", default="kg/merged.json")
    p.add_argument("--no-llm", action="store_true")
    p.add_argument("--limit", type=int, default=200)
    p.add_argument("--config", default=None)
    p.set_defaults(func=cmd_kg_enrich)

    p = kg_sub.add_parser("domains", help="도메인/플로우 뷰 생성 (UI/UX KG)")
    p.add_argument("--kg", default="kg/merged.json")
    p.add_argument("--out", default="kg/domain-map.html")
    p.add_argument("--no-open", action="store_true")
    p.set_defaults(func=cmd_kg_domains)

    p = kg_sub.add_parser("tour", help="가이드 투어(의존성 읽기 순서) 생성")
    p.add_argument("--repo", required=True)
    p.add_argument("--kg", default="kg/merged.json")
    p.add_argument("--out", default="kg/TOUR.md")
    p.add_argument("--limit", type=int, default=30)
    p.set_defaults(func=cmd_kg_tour)

    p = kg_sub.add_parser("annotate", help="그래프 사람 편집 — 오버레이 영속(R8 수정가능)")
    p.add_argument("node_id", nargs="?", default="")
    p.add_argument("--kg", default="kg/merged.json")
    p.add_argument("--summary", default=None)
    p.add_argument("--note", default=None)
    p.add_argument("--deprecate", action="store_true")
    p.add_argument("--undeprecate", action="store_true")
    p.add_argument("--redirect", default=None)
    p.add_argument("--tag", action="append", default=None)
    p.add_argument("--edge-to", default=None, help="커스텀 엣지 대상 노드")
    p.add_argument("--edge-kind", default="relates_to")
    p.add_argument("--list", action="store_true", help="오버레이 전체 출력")
    p.set_defaults(func=cmd_kg_annotate)

    p = kg_sub.add_parser("sync", help="git 기준 증분 동기화 (변경 파일만 재추출)")
    p.add_argument("--kg", default="kg/merged.json")
    p.add_argument("--quiet", action="store_true")
    p.set_defaults(func=cmd_kg_sync)

    p = kg_sub.add_parser("hook", help="레포에 자동 sync 훅 설치/제거 (post-commit/merge/checkout)")
    p.add_argument("hook_action", choices=["install", "remove"])
    p.add_argument("--repo-path", required=True)
    p.add_argument("--kg", default="kg/merged.json")
    p.add_argument("--python", default="python")
    p.set_defaults(func=cmd_kg_hook)

    p = sub.add_parser("run", help="MAKER 루프 실행 (쿼리 1개)")
    p.add_argument("query")
    p.add_argument("--config", default=None)
    p.add_argument("--kg", default=None)
    p.add_argument("--mode", choices=["plan", "observe", "act"], default=None,
                   help="plan=분석·MR초안만(레포 미접촉) · observe=로컬 브랜치+커밋(푸시X) · "
                        "act=푸시+MR(인가 게이트 통과 필요). 미지정 시 config 기본(안전=plan-only)")
    p.set_defaults(func=cmd_run)

    p = sub.add_parser("mcp", help="KG MCP 서버 (stdio) — kg_* 4툴 + maker_plan")
    p.add_argument("--kg", default="kg/merged.json")
    p.add_argument("--config", default=None, help="maker_plan용 MakerConfig json")
    p.set_defaults(func=cmd_mcp)

    p = sub.add_parser("chat", help="대화형 터미널 (openxgen 스타일) — KG 1회 로드, 연속 쿼리")
    p.add_argument("--config", default=None)
    p.set_defaults(func=cmd_chat)

    p = sub.add_parser("web", help="웹 UI — 브라우저에서 쿼리 치면 MAKER 루프 실행(실시간 로그)")
    p.add_argument("--config", default=None)
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8760)
    p.add_argument("--open", action="store_true", help="브라우저 자동 열기")
    p.set_defaults(func=cmd_web)

    p = sub.add_parser("ui", help="UI/UX 검증 — 라우트 매핑 + 스냅샷 + 픽셀diff + 비전판정")
    p.add_argument("ui_action", choices=["routes", "baseline", "verify"])
    p.add_argument("--repo", default="xgen-frontend-features")
    p.add_argument("--changed", nargs="*", help="변경 파일(레포 상대경로)")
    p.add_argument("--route", default=None, help="baseline 대상 라우트(변경 미지정 시)")
    p.add_argument("--config", default=None)
    p.add_argument("--kg", default=None)
    p.add_argument("--preview", default=None, help="preview_base override")
    p.add_argument("--out", default="worklogs/ui-verify")
    p.add_argument("--no-vision", action="store_true")
    p.set_defaults(func=cmd_ui)

    p = sub.add_parser("login", help="로그인 — Claude/GitLab 한 번 저장하면 지속(재입력 불필요)")
    p.add_argument("--provider", choices=["claude_cli", "anthropic", "vllm"], default=None)
    p.add_argument("--api-key", default=None)
    p.add_argument("--model", default=None)
    p.add_argument("--base", default=None)
    p.add_argument("--gitlab-user", default=None, help="GitLab 이메일/username")
    p.add_argument("--gitlab-password", default=None, help="GitLab 비번(OAuth 교환)")
    p.add_argument("--gitlab-token", default=None, help="GitLab PAT(비번 그랜트 막힌 경우)")
    p.add_argument("--gitlab-url", default=None)
    p.set_defaults(func=cmd_login)

    p = sub.add_parser("whoami", help="현재 로그인/프로바이더 상태")
    p.set_defaults(func=cmd_whoami)

    p = sub.add_parser("undo", help="롤백 — MAKER가 만든 마지막 브랜치·커밋·푸시 되돌림")
    p.add_argument("--yes", action="store_true", help="실제 실행(없으면 미리보기)")
    p.add_argument("--remote", action="store_true", help="원격 브랜치도 삭제")
    p.add_argument("--config", default=None)
    p.set_defaults(func=cmd_undo)

    p = sub.add_parser("doctor", help="자가검증 — MAKER 목적(R1~R20)이 실제로 되는지 점검")
    p.add_argument("--config", default=None)
    p.set_defaults(func=cmd_doctor)

    p = sub.add_parser("sdk", help="SDK 자가검증 — 엔진 버전 드리프트 + 계약 호환 확인")
    p.add_argument("--catalog", action="store_true", help="MAKER 자기 카탈로그도 출력")
    p.set_defaults(func=cmd_sdk)

    p = sub.add_parser("engine", help="MAKER를 xgen-harness 엔진 stage로 등록(R3 Level A)/구동(Level B)")
    p.add_argument("engine_action", nargs="?", default="register", choices=["register", "run"])
    p.add_argument("query", nargs="?", default=None, help="engine run용 쿼리")
    p.add_argument("--config", default=None)
    p.set_defaults(func=cmd_engine)

    p = sub.add_parser("status", help="배포 상태 관측(read-only) — Jenkins·ArgoCD. MAKER는 배포 안 함")
    p.set_defaults(func=cmd_status)

    p = sub.add_parser("mrs", help="MR 이력 관측 — 본인 MR / MAKER가 만든 MR (read-only)")
    p.add_argument("--maker", action="store_true", help="MAKER가 만든 MR만")
    p.add_argument("--state", default="all", choices=["all", "opened", "merged", "closed"])
    p.add_argument("--config", default=None)
    p.set_defaults(func=cmd_mrs)

    p = sub.add_parser("branches", help="레포 브랜치 관측 — release·보호·작업 브랜치 (read-only)")
    p.add_argument("--repo", default="xgen-frontend-features")
    p.add_argument("--config", default=None)
    p.set_defaults(func=cmd_branches)

    p = sub.add_parser("learn", help="작업 학습 메모리 — 기록/조회 (하네스가 다음 작업 시 참고, 실수방지)")
    p.add_argument("--repo", default="xgen-workflow")
    p.add_argument("--area", default=None)
    p.add_argument("--kind", default="note", choices=["pitfall", "fix", "convention", "note"])
    p.add_argument("--note", default=None, help="기록할 학습(없으면 조회)")
    p.add_argument("--config", default=None)
    p.set_defaults(func=cmd_learn)

    p = sub.add_parser("history", help="MAKER 본인 작업 이력 (worklogs journal)")
    p.add_argument("--limit", type=int, default=20)
    p.add_argument("--config", default=None)
    p.set_defaults(func=cmd_history)

    p = sub.add_parser("release", help="릴리즈 사다리 — 이 변경이 develop→stg→main 어디에 놓이나")
    p.add_argument("--repo", default="xgen-core")
    p.add_argument("--branch", default=None, help="타깃 브랜치(기본 develop)")
    p.add_argument("--config", default=None)
    p.add_argument("--kg", default=None)
    p.set_defaults(func=cmd_release)

    p = sub.add_parser("deploy", help="배포 렌더 검증(T1, tmp 격리 helm template) — MR 전 배포통과 확인")
    p.add_argument("deploy_action", choices=["test", "apps"])
    p.add_argument("--repo", default="xgen-core")
    p.add_argument("--config", default=None)
    p.add_argument("--infra", default=None, help="xgen-infra 경로 override")
    p.set_defaults(func=cmd_deploy)

    args = parser.parse_args(argv)
    args.func(args)
