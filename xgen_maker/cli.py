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
from .kg.search import search, impact
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


def cmd_kg_merge(args) -> None:
    graphs = [Graph.load(p) for p in args.inputs]
    merged, links = merge_and_link(graphs)
    merged.save(args.out)
    print(f"[kg merge] {json.dumps(merged.stats(), ensure_ascii=False)}")
    print(f"[kg merge] crossrepo resolves_to 링크 {links}개 → {args.out}")
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


def cmd_login(args) -> None:
    from .auth import Auth, save_auth, claude_cli_status, load_auth
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
    path = save_auth(auth)
    print(f"✓ 로그인 저장: provider={provider} model={auth.resolved_model()} → {path}")
    print("  이제 `maker chat` / `maker run` 이 이 로그인으로 코딩+판단+요약을 전부 처리합니다.")


def cmd_whoami(args) -> None:
    from .auth import load_auth, claude_cli_status, AUTH_FILE
    auth = load_auth()
    print(f"provider : {auth.provider}")
    print(f"model    : {auth.resolved_model()}")
    print(f"base     : {auth.resolved_base()}")
    print(f"key set  : {'yes' if auth.api_key else 'no (구독 로그인)' if auth.provider=='claude_cli' else 'no'}")
    print(f"저장위치 : {AUTH_FILE} ({'있음' if AUTH_FILE.exists() else '없음(기본 claude_cli)'})")
    if auth.provider == "claude_cli":
        status = claude_cli_status()
        print(f"claude CLI: {'✓ 인증됨' if status['authenticated'] else '✗ ' + status['reason']}")


def cmd_doctor(args) -> None:
    from .doctor import run_doctor
    ok = run_doctor(args.config)
    if not ok:
        sys.exit(1)


def main(argv: list[str] | None = None) -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
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
    p.add_argument("--mode", choices=["observe", "act"], default=None)
    p.set_defaults(func=cmd_run)

    p = sub.add_parser("mcp", help="KG MCP 서버 (stdio) — kg_* 4툴 + maker_plan")
    p.add_argument("--kg", default="kg/merged.json")
    p.add_argument("--config", default=None, help="maker_plan용 MakerConfig json")
    p.set_defaults(func=cmd_mcp)

    p = sub.add_parser("chat", help="대화형 터미널 (openxgen 스타일) — KG 1회 로드, 연속 쿼리")
    p.add_argument("--config", default=None)
    p.set_defaults(func=cmd_chat)

    p = sub.add_parser("login", help="로그인 — Claude 하나로 코딩+판단+요약 통합 (API 키 불필요)")
    p.add_argument("--provider", choices=["claude_cli", "anthropic", "vllm"], default=None)
    p.add_argument("--api-key", default=None)
    p.add_argument("--model", default=None)
    p.add_argument("--base", default=None)
    p.set_defaults(func=cmd_login)

    p = sub.add_parser("whoami", help="현재 로그인/프로바이더 상태")
    p.set_defaults(func=cmd_whoami)

    p = sub.add_parser("doctor", help="자가검증 — MAKER 목적(R1~R20)이 실제로 되는지 점검")
    p.add_argument("--config", default=None)
    p.set_defaults(func=cmd_doctor)

    args = parser.parse_args(argv)
    args.func(args)
