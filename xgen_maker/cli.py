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


def cmd_kg_dashboard(args) -> None:
    graph = Graph.load(args.kg)
    out = render_dashboard(graph, args.out, max_nodes=args.max_nodes)
    print(f"[kg dashboard] {out} ({out.stat().st_size:,} bytes)")


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
    if not args.quiet:
        for result in results:
            print(f"[kg sync] {json.dumps(result, ensure_ascii=False)}")
        print(f"[kg sync] 총 {total}개 파일 증분 반영 → {args.kg}")


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
    mcp_main(args.kg)


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
    p.set_defaults(func=cmd_kg_domains)

    p = kg_sub.add_parser("tour", help="가이드 투어(의존성 읽기 순서) 생성")
    p.add_argument("--repo", required=True)
    p.add_argument("--kg", default="kg/merged.json")
    p.add_argument("--out", default="kg/TOUR.md")
    p.add_argument("--limit", type=int, default=30)
    p.set_defaults(func=cmd_kg_tour)

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

    p = sub.add_parser("mcp", help="KG MCP 서버 (stdio)")
    p.add_argument("--kg", default="kg/merged.json")
    p.set_defaults(func=cmd_mcp)

    args = parser.parse_args(argv)
    args.func(args)
